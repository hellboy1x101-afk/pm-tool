"""User service layer."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models.models import Firm, FirmUser, TechnicalRole, User
from app.services.auth_service import hash_password
from app.services.license_tiers import check_user_limit


USER_EDITABLE_FIELDS = {"email", "display_name", "is_active"}


def list_users(
    db: Session,
    firm_id: int,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
):
    query = (
        db.query(User)
        .join(FirmUser, FirmUser.user_id == User.id)
        .filter(FirmUser.firm_id == firm_id, FirmUser.is_active == True)
    )
    if q:
        query = query.filter(
            or_(
                User.email.ilike(f"%{q}%"),
                User.display_name.ilike(f"%{q}%"),
            )
        )
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    total = query.count()
    items = query.order_by(User.display_name).limit(limit).offset(offset).all()
    return items, total


def list_deleted_users(
    db: Session,
    firm_id: int,
    limit: int = 50,
    offset: int = 0,
):
    """List deleted users for a firm."""
    # Get users that were deleted and had access to this firm
    query = (
        db.query(User)
        .join(FirmUser, FirmUser.user_id == User.id)
        .filter(
            FirmUser.firm_id == firm_id,
            or_(User.deleted_at.isnot(None), FirmUser.is_active == False),
        )
    )
    total = query.count()
    items = query.order_by(User.deleted_at.desc()).limit(limit).offset(offset).all()
    
    # Get deleter info
    result = []
    for user in items:
        deleter = None
        if user.deleted_by_user_id:
            deleter = db.query(User).filter(User.id == user.deleted_by_user_id).first()
        result.append({
            "user": user,
            "deleted_by": deleter,
        })
    
    return result, total


def get_user(db: Session, user_id: int, firm_id: int | None = None) -> User:
    query = db.query(User).filter(User.id == user_id)
    if firm_id is not None:
        query = query.join(FirmUser, FirmUser.user_id == User.id).filter(FirmUser.firm_id == firm_id)
    user = query.first()
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    return user


def belongs_to_other_firms(db: Session, user_id: int, firm_id: int) -> bool:
    """True if the user has an active membership in any firm other than firm_id.

    A firm admin must not change credentials (email/password) or the global account
    state of such a user, since that would also affect their access to the other firms.
    """
    return db.query(FirmUser).filter(
        FirmUser.user_id == user_id,
        FirmUser.firm_id != firm_id,
        FirmUser.is_active == True,
    ).count() > 0


def create_user(
    db: Session,
    data: dict,
    password: Optional[str] = None,
    firm_id: Optional[int] = None,
    firm_role: TechnicalRole = TechnicalRole.viewer,
) -> User:
    # Check license limit if firm_id provided
    if firm_id:
        firm = db.query(Firm).filter(Firm.id == firm_id).first()
        if firm and firm.license_tier:
            current_count = db.query(FirmUser).filter(
                FirmUser.firm_id == firm_id,
                FirmUser.is_active == True,
            ).count()
            if not check_user_limit(firm.license_tier, current_count):
                raise ValidationError(
                    f"User limit reached for {firm.license_tier} tier. "
                    f"Upgrade your license to add more users."
                )

    if not data.get("email"):
        raise ValidationError("Email is required")
    existing = db.query(User).filter(User.email == data["email"]).first()
    if existing:
        raise ValidationError(f"User with email {data['email']} already exists; send them an invitation instead")
    user = User(**{key: value for key, value in data.items() if key in ("email", "display_name", "azure_oid")})
    if password:
        user.password_hash = hash_password(password)
    db.add(user)
    db.flush()
    if firm_id:
        db.add(FirmUser(user_id=user.id, firm_id=firm_id, technical_role=firm_role))
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user_id: int, data: dict, firm_id: int) -> User:
    user = get_user(db, user_id, firm_id=firm_id)
    data = {key: value for key, value in data.items() if key in USER_EDITABLE_FIELDS and value is not None}
    shared = belongs_to_other_firms(db, user_id, firm_id)
    if "email" in data and data["email"] != user.email:
        if shared:
            raise ValidationError("This user also belongs to another firm; only they can change their email")
        existing = db.query(User).filter(User.email == data["email"], User.id != user_id).first()
        if existing:
            raise ValidationError(f"Email {data['email']} is already taken")
    if "is_active" in data and shared:
        raise ValidationError("This user also belongs to another firm; remove them from this firm instead")
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


def soft_delete_user(db: Session, user_id: int, deleted_by_user_id: int, firm_id: int) -> User:
    """Remove a user from the firm.

    If they belong to other firms only this firm's membership is deactivated; otherwise the
    account itself is soft-deleted (and can be restored from this firm).
    """
    user = get_user(db, user_id, firm_id=firm_id)
    if belongs_to_other_firms(db, user_id, firm_id):
        db.query(FirmUser).filter(FirmUser.user_id == user_id, FirmUser.firm_id == firm_id).update(
            {"is_active": False}, synchronize_session="fetch"
        )
    else:
        user.is_active = False
        user.deleted_at = datetime.now(timezone.utc)
        user.deleted_by_user_id = deleted_by_user_id
    db.commit()
    db.refresh(user)
    return user


def restore_user(db: Session, user_id: int, firm_id: int) -> User:
    """Restore a user removed from this firm."""
    user = get_user(db, user_id, firm_id=firm_id)
    db.query(FirmUser).filter(FirmUser.user_id == user_id, FirmUser.firm_id == firm_id).update(
        {"is_active": True}, synchronize_session="fetch"
    )
    if user.deleted_at is not None:
        user.is_active = True
        user.deleted_at = None
        user.deleted_by_user_id = None
    db.commit()
    db.refresh(user)
    return user
