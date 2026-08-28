"""API v1 assignments endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_firm_user, require_api_role
from app.database import get_db
from app.models.models import FirmUser, TechnicalRole
from app.services import allocation_service

router = APIRouter(prefix="/assignments", tags=["api-v1-assignments"])


@router.get("")
def list_assignments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    team_member_id: Optional[int] = None,
    engagement_instance_id: Optional[int] = None,
    firm_user: FirmUser = Depends(get_current_firm_user),
    db: Session = Depends(get_db),
):
    items, total = allocation_service.list_assignments(
        db, firm_id=firm_user.firm_id, limit=limit, offset=offset,
        team_member_id=team_member_id, engagement_instance_id=engagement_instance_id,
    )
    return {
        "items": [
            {
                "id": a.id, "team_member_id": a.team_member_id,
                "team_member_name": a.team_member.name if a.team_member else None,
                "engagement_instance_id": a.engagement_instance_id,
                "role_on_engagement": a.role_on_engagement,
                "allocation_percent": a.allocation_percent,
                "start_date": str(a.start_date), "end_date": str(a.end_date),
            }
            for a in items
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.post("", status_code=201)
def create_assignment(
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.assignment, OperationType.create, body)
    if result:
        return result

    from datetime import date
    a = allocation_service.create_assignment(
        db,
        firm_id=firm_user.firm_id,
        team_member_id=body["team_member_id"],
        engagement_instance_id=body["engagement_instance_id"],
        allocation_percent=body["allocation_percent"],
        start_date=date.fromisoformat(body["start_date"]),
        end_date=date.fromisoformat(body["end_date"]),
        role_on_engagement=body.get("role_on_engagement"),
        created_by_user_id=firm_user.user_id,
    )
    return {"id": a.id, "team_member_id": a.team_member_id}


@router.patch("/{assignment_id}")
def update_assignment(
    assignment_id: int,
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.assignment, OperationType.update, body, resource_id=assignment_id)
    if result:
        return result

    from datetime import date
    a = allocation_service.update_assignment(
        db, firm_id=firm_user.firm_id, assignment_id=assignment_id,
        allocation_percent=body.get("allocation_percent"),
        start_date=date.fromisoformat(body["start_date"]) if body.get("start_date") else None,
        end_date=date.fromisoformat(body["end_date"]) if body.get("end_date") else None,
        role_on_engagement=body.get("role_on_engagement"),
    )
    return {"id": a.id, "team_member_id": a.team_member_id}
