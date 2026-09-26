"""API v1 team_members endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_firm_user, require_api_role
from app.database import get_db
from app.models.models import FirmUser, TechnicalRole
from app.services import team_member_service

router = APIRouter(prefix="/team-members", tags=["api-v1-team-members"])


@router.get("")
def list_team_members(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    business_role: Optional[str] = None,
    branch_id: Optional[int] = None,
    firm_user: FirmUser = Depends(get_current_firm_user),
    db: Session = Depends(get_db),
):
    items, total = team_member_service.list_team_members(
        db, firm_id=firm_user.firm_id, limit=limit, offset=offset,
        q=q, is_active=is_active, business_role=business_role, branch_id=branch_id,
    )
    return {
        "items": [
            {
                "id": m.id, "name": m.name, "email": m.email,
                "employee_code": m.employee_code, "business_role": m.business_role.value,
                "is_active": m.is_active, "branch_id": m.branch_id,
                "seniority_level": m.seniority_level,
            }
            for m in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{member_id}")
def get_team_member(
    member_id: int,
    firm_user: FirmUser = Depends(get_current_firm_user),
    db: Session = Depends(get_db),
):
    m = team_member_service.get_team_member(db, member_id, firm_id=firm_user.firm_id)
    return {
        "id": m.id, "name": m.name, "email": m.email,
        "employee_code": m.employee_code, "business_role": m.business_role.value,
        "is_oversight_only": m.is_oversight_only, "seniority_level": m.seniority_level,
        "date_of_joining": str(m.date_of_joining) if m.date_of_joining else None,
        "date_of_relieving": str(m.date_of_relieving) if m.date_of_relieving else None,
        "is_active": m.is_active, "branch_id": m.branch_id,
    }


@router.post("", status_code=201)
def create_team_member(
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.team_member, OperationType.create, body)
    if result:
        return result

    m = team_member_service.create_team_member(db, body, firm_id=firm_user.firm_id)
    return {"id": m.id, "name": m.name, "email": m.email}


@router.patch("/{member_id}")
def update_team_member(
    member_id: int,
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.team_member, OperationType.update, body, resource_id=member_id)
    if result:
        return result

    m = team_member_service.update_team_member(db, member_id, body, firm_id=firm_user.firm_id)
    return {"id": m.id, "name": m.name, "email": m.email}


@router.delete("/{member_id}")
def delete_team_member(
    member_id: int,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.team_member, OperationType.delete, {}, resource_id=member_id)
    if result:
        return result

    team_member_service.soft_delete_team_member(db, member_id, firm_id=firm_user.firm_id)
    return {"detail": "Team member deactivated"}
