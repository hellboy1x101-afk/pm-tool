"""API v1 leaves endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_firm_user, require_api_role
from app.database import get_db
from app.models.models import FirmUser, TechnicalRole
from app.services import leave_service

router = APIRouter(prefix="/leaves", tags=["api-v1-leaves"])


@router.get("")
def list_leaves(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    team_member_id: Optional[int] = None,
    status: Optional[str] = None,
    firm_user: FirmUser = Depends(get_current_firm_user),
    db: Session = Depends(get_db),
):
    items, total = leave_service.list_leaves(
        db, firm_id=firm_user.firm_id, limit=limit, offset=offset,
        team_member_id=team_member_id, status=status,
    )
    return {
        "items": [
            {
                "id": l.id, "team_member_id": l.team_member_id,
                "team_member_name": l.team_member.name if l.team_member else None,
                "leave_type": l.leave_type.value, "status": l.status.value,
                "start_date": str(l.start_date), "end_date": str(l.end_date),
                "reason": l.reason,
            }
            for l in items
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.post("", status_code=201)
def create_leave(
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.leave, OperationType.create, body)
    if result:
        return result

    l = leave_service.create_leave(db, body, firm_id=firm_user.firm_id)
    return {"id": l.id, "team_member_id": l.team_member_id}


@router.patch("/{leave_id}")
def update_leave(
    leave_id: int,
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.leave, OperationType.update, body, resource_id=leave_id)
    if result:
        return result

    l = leave_service.update_leave(db, leave_id, body, firm_id=firm_user.firm_id)
    return {"id": l.id, "status": l.status.value}
