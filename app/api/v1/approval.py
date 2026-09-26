"""API v1 approval request endpoints — list, approve, reject."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_firm_user, require_api_role
from app.database import get_db
from app.models.models import FirmUser, TechnicalRole
from app.services import approval_service

router = APIRouter(prefix="/approval-requests", tags=["api-v1-approval"])


class ReviewRequest(BaseModel):
    note: str | None = None


@router.get("")
def list_pending_requests(
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin, TechnicalRole.moderator)),
    db: Session = Depends(get_db),
):
    """List all pending approval requests for the firm."""
    items = approval_service.list_pending_requests(db, firm_user.firm_id)
    return {
        "items": [
            {
                "id": r.id,
                "resource_type": r.resource_type.value,
                "resource_id": r.resource_id,
                "operation": r.operation.value,
                "requested_by_user_id": r.requested_by_user_id,
                "payload": r.payload,
                "status": r.status.value,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in items
        ],
        "total": len(items),
    }


@router.post("/{request_id}/approve")
def approve_request(
    request_id: int,
    body: ReviewRequest | None = None,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin)),
    db: Session = Depends(get_db),
):
    """Approve a pending request and apply the change."""
    note = body.note if body else None
    req = approval_service.approve_request(
        db, request_id, firm_user.user_id, note,
        firm_id=firm_user.firm_id, commit=False,
    )

    try:
        _apply_approved_change(db, req)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {"id": req.id, "status": "approved", "detail": "Request approved and applied"}


@router.post("/{request_id}/reject")
def reject_request(
    request_id: int,
    body: ReviewRequest | None = None,
    firm_user: FirmUser = Depends(require_api_role(TechnicalRole.admin)),
    db: Session = Depends(get_db),
):
    """Reject a pending request."""
    note = body.note if body else None
    req = approval_service.reject_request(
        db, request_id, firm_user.user_id, note, firm_id=firm_user.firm_id,
    )
    return {"id": req.id, "status": "rejected", "detail": "Request rejected"}


def _apply_approved_change(db: Session, req) -> None:
    """Apply the change from an approved request."""
    from app.models.models import ResourceType, OperationType
    from app.services import team_member_service, client_service, engagement_service, leave_service
    from app.services import allocation_service

    payload = req.payload.copy()
    # Inject firm_id into payload for resources that need it
    payload["firm_id"] = req.firm_id
    rid = req.resource_id

    if req.resource_type == ResourceType.team_member:
        if req.operation == OperationType.create:
            team_member_service.create_team_member(db, payload, firm_id=req.firm_id)
        elif req.operation == OperationType.update and rid:
            team_member_service.update_team_member(db, rid, payload, firm_id=req.firm_id)
        elif req.operation == OperationType.delete and rid:
            team_member_service.soft_delete_team_member(db, rid, firm_id=req.firm_id)

    elif req.resource_type == ResourceType.client:
        if req.operation == OperationType.create:
            client_service.create_client(db, payload, firm_id=req.firm_id)
        elif req.operation == OperationType.update and rid:
            client_service.update_client(db, rid, payload, firm_id=req.firm_id)
        elif req.operation == OperationType.delete and rid:
            client_service.soft_delete_client(db, rid, firm_id=req.firm_id)

    elif req.resource_type == ResourceType.engagement:
        if req.operation == OperationType.create:
            engagement_service.create_engagement(db, payload, firm_id=req.firm_id)
        elif req.operation == OperationType.update and rid:
            engagement_service.update_engagement(db, rid, payload, firm_id=req.firm_id)
        elif req.operation == OperationType.delete and rid:
            engagement_service.soft_delete_engagement(db, rid, firm_id=req.firm_id)

    elif req.resource_type == ResourceType.leave:
        if req.operation == OperationType.create:
            leave_service.create_leave(db, payload, firm_id=req.firm_id)
        elif req.operation == OperationType.update and rid:
            leave_service.update_leave(db, rid, payload, firm_id=req.firm_id)

    elif req.resource_type == ResourceType.assignment:
        if req.operation == OperationType.create:
            from datetime import date as _date
            allocation_service.create_assignment(
                db,
                firm_id=req.firm_id,
                team_member_id=payload["team_member_id"],
                engagement_instance_id=payload["engagement_instance_id"],
                allocation_percent=payload["allocation_percent"],
                start_date=_date.fromisoformat(payload["start_date"]),
                end_date=_date.fromisoformat(payload["end_date"]),
                role_on_engagement=payload.get("role_on_engagement"),
                created_by_user_id=req.requested_by_user_id,
            )
        elif req.operation == OperationType.update and rid:
            from datetime import date as _date
            allocation_service.update_assignment(
                db, firm_id=req.firm_id, assignment_id=rid,
                allocation_percent=payload.get("allocation_percent"),
                start_date=_date.fromisoformat(payload["start_date"]) if payload.get("start_date") else None,
                end_date=_date.fromisoformat(payload["end_date"]) if payload.get("end_date") else None,
                role_on_engagement=payload.get("role_on_engagement"),
            )
