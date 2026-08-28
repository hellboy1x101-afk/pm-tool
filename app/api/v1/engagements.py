"""API v1 engagements endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_firm_user, require_api_role
from app.database import get_db
from app.models.models import FirmUser, TechnicalRole
from app.services import engagement_service

router = APIRouter(prefix="/engagements", tags=["api-v1-engagements"])


@router.get("")
def list_engagements(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    status: Optional[str] = None,
    firm_user: FirmUser = Depends(get_current_firm_user),
    db: Session = Depends(get_db),
):
    items, total = engagement_service.list_engagements(
        db, firm_id=firm_user.firm_id, limit=limit, offset=offset,
        q=q, is_active=is_active, status=status,
    )
    return {
        "items": [
            {
                "id": e.id, "name": e.name, "client_id": e.client_id,
                "client_name": e.client.name if e.client else None,
                "engagement_type": e.engagement_type.value,
                "status": e.status.value, "start_date": str(e.start_date),
                "end_date": str(e.end_date) if e.end_date else None,
                "is_active": e.is_active,
            }
            for e in items
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/{engagement_id}")
def get_engagement(
    engagement_id: int,
    firm_user: FirmUser = Depends(get_current_firm_user),
    db: Session = Depends(get_db),
):
    e = engagement_service.get_engagement(db, engagement_id, firm_id=firm_user.firm_id)
    return {
        "id": e.id, "name": e.name, "client_id": e.client_id,
        "client_name": e.client.name if e.client else None,
        "engagement_type": e.engagement_type.value,
        "recurrence_pattern": e.recurrence_pattern.value,
        "status": e.status.value, "start_date": str(e.start_date),
        "end_date": str(e.end_date) if e.end_date else None,
        "is_active": e.is_active,
    }


@router.post("", status_code=201)
def create_engagement(
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.engagement, OperationType.create, body)
    if result:
        return result

    e = engagement_service.create_engagement(db, body, firm_id=firm_user.firm_id)
    return {"id": e.id, "name": e.name}


@router.patch("/{engagement_id}")
def update_engagement(
    engagement_id: int,
    body: dict,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.engagement, OperationType.update, body, resource_id=engagement_id)
    if result:
        return result

    e = engagement_service.update_engagement(db, engagement_id, body, firm_id=firm_user.firm_id)
    return {"id": e.id, "name": e.name}


@router.delete("/{engagement_id}")
def delete_engagement(
    engagement_id: int,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin)),
    db: Session = Depends(get_db),
):
    from app.approval_check import check_approval
    from app.models.models import ResourceType, OperationType
    result = check_approval(db, firm_user.firm_id, firm_user.user_id,
                            ResourceType.engagement, OperationType.delete, {}, resource_id=engagement_id)
    if result:
        return result

    engagement_service.soft_delete_engagement(db, engagement_id, firm_id=firm_user.firm_id)
    return {"detail": "Engagement deactivated"}
