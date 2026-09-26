from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models.models import Firm, TeamMember
from app.services.license_tiers import check_team_member_limit


def list_team_members(
    db: Session,
    firm_id: int,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    business_role: Optional[str] = None,
    branch_id: Optional[int] = None,
):
    query = db.query(TeamMember).filter(TeamMember.firm_id == firm_id)
    if q:
        query = query.filter(
            or_(
                TeamMember.name.ilike(f"%{q}%"),
                TeamMember.email.ilike(f"%{q}%"),
                TeamMember.employee_code.ilike(f"%{q}%"),
            )
        )
    if is_active is not None:
        query = query.filter(TeamMember.is_active == is_active)
    if business_role:
        query = query.filter(TeamMember.business_role == business_role)
    if branch_id:
        query = query.filter(TeamMember.branch_id == branch_id)

    total = query.count()
    items = query.order_by(TeamMember.name).limit(limit).offset(offset).all()
    return items, total


def get_team_member(db: Session, member_id: int, firm_id: int | None = None) -> TeamMember:
    query = db.query(TeamMember).filter(TeamMember.id == member_id)
    if firm_id is not None:
        query = query.filter(TeamMember.firm_id == firm_id)
    member = query.first()
    if not member:
        raise NotFoundError(f"TeamMember {member_id} not found")
    return member


def create_team_member(db: Session, data: dict, firm_id: int | None = None) -> TeamMember:
    firm_id = firm_id if firm_id is not None else data.get("firm_id")
    if firm_id is None:
        raise ValidationError("An active firm is required")
    data = {**data, "firm_id": firm_id}

    # Check license limit
    firm = db.query(Firm).filter(Firm.id == firm_id).first()
    if firm and firm.license_tier:
        current_count = db.query(TeamMember).filter(
            TeamMember.firm_id == firm_id,
            TeamMember.is_active == True,
        ).count()
        if not check_team_member_limit(firm.license_tier, current_count):
            raise ValidationError(
                f"Team member limit reached for {firm.license_tier} tier. "
                f"Upgrade your license to add more team members."
            )

    existing = db.query(TeamMember).filter(TeamMember.email == data["email"]).first()
    if existing:
        raise ValidationError(f"Email {data['email']} already exists")
    member = TeamMember(**data)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def update_team_member(db: Session, member_id: int, data: dict, firm_id: int) -> TeamMember:
    member = get_team_member(db, member_id, firm_id=firm_id)
    for key, value in data.items():
        if key != "firm_id" and value is not None:
            setattr(member, key, value)
    db.commit()
    db.refresh(member)
    return member


def soft_delete_team_member(db: Session, member_id: int, firm_id: int) -> TeamMember:
    member = get_team_member(db, member_id, firm_id=firm_id)
    member.is_active = False
    db.commit()
    db.refresh(member)
    return member


def bulk_deactivate(db: Session, member_ids: list[int], firm_id: int) -> int:
    count = (
        db.query(TeamMember)
        .filter(TeamMember.id.in_(member_ids), TeamMember.firm_id == firm_id)
        .update({"is_active": False}, synchronize_session="fetch")
    )
    db.commit()
    return count
