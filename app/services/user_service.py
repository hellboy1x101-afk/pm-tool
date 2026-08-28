"""User service layer."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models.models import Firm, FirmUser, TechnicalRole, User
from app.services.auth_service import hash_password
from app.services.license_tiers import check_user_limit


def list_users(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
):
    query = db.query(User)
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
            User.deleted_at.isnot(None),
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


def get_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise NotFoundError(f"User {user_id} not found")
    return user


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
        raise ValidationError(f"User with email {data['email']} already exists")
    user = User(**data)
    if password:
        user.password_hash = hash_password(password)
    db.add(user)
    db.flush()
    if firm_id:
        db.add(FirmUser(user_id=user.id, firm_id=firm_id, technical_role=firm_role))
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user_id: int, data: dict) -> User:
    user = get_user(db, user_id)
    if "email" in data and data["email"]:
        existing = db.query(User).filter(User.email == data["email"], User.id != user_id).first()
        if existing:
            raise ValidationError(f"Email {data['email']} is already taken")
    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


def soft_delete_user(db: Session, user_id: int, deleted_by_user_id: int) -> User:
    """Soft delete a user and track who deleted them."""
    user = get_user(db, user_id)
    user.is_active = False
    user.deleted_at = datetime.now(timezone.utc)
    user.deleted_by_user_id = deleted_by_user_id
    db.commit()
    db.refresh(user)
    return user


def restore_user(db: Session, user_id: int) -> User:
    """Restore a soft-deleted user."""
    user = get_user(db, user_id)
    user.is_active = True
    user.deleted_at = None
    user.deleted_by_user_id = None
    db.commit()
    db.refresh(user)
    return user
