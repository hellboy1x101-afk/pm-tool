from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.exceptions import NotFoundError
from app.models.models import Leave, LeaveStatus, TeamMember


def list_leaves(
    db: Session,
    firm_id: int,
    limit: int = 50,
    offset: int = 0,
    team_member_id: Optional[int] = None,
    status: Optional[str] = None,
):
    query = (
        db.query(Leave)
        .options(joinedload(Leave.team_member))
        .join(TeamMember, Leave.team_member_id == TeamMember.id)
        .filter(TeamMember.firm_id == firm_id)
    )
    if team_member_id:
        query = query.filter(Leave.team_member_id == team_member_id)
    if status:
        query = query.filter(Leave.status == status)
    total = query.count()
    items = query.order_by(Leave.start_date.desc()).limit(limit).offset(offset).all()
    return items, total


def get_leave(db: Session, leave_id: int, firm_id: int | None = None) -> Leave:
    query = (
        db.query(Leave)
        .options(joinedload(Leave.team_member))
        .filter(Leave.id == leave_id)
    )
    if firm_id is not None:
        query = query.join(TeamMember, Leave.team_member_id == TeamMember.id).filter(TeamMember.firm_id == firm_id)
    leave = query.first()
    if not leave:
        raise NotFoundError(f"Leave {leave_id} not found")
    return leave


def _require_member_in_firm(db: Session, team_member_id, firm_id: int) -> None:
    member = db.query(TeamMember).filter(
        TeamMember.id == team_member_id, TeamMember.firm_id == firm_id
    ).first()
    if not member:
        raise NotFoundError(f"TeamMember {team_member_id} not found")


def create_leave(db: Session, data: dict, firm_id: int) -> Leave:
    _require_member_in_firm(db, data.get("team_member_id"), firm_id)
    leave = Leave(**{key: value for key, value in data.items() if key != "firm_id"})
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


def update_leave(db: Session, leave_id: int, data: dict, firm_id: int) -> Leave:
    leave = get_leave(db, leave_id, firm_id=firm_id)
    if data.get("team_member_id") is not None:
        _require_member_in_firm(db, data["team_member_id"], firm_id)
    for key, value in data.items():
        if key != "firm_id" and value is not None:
            setattr(leave, key, value)
    db.commit()
    db.refresh(leave)
    return leave
