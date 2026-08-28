"""Extension request service — for allocating resources beyond original plan."""

from datetime import date
from typing import Optional

from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models.models import (
    ExtensionRequest, ExtensionStatus,
    Assignment, EngagementInstance, Leave, LeaveStatus, TeamMember,
)


def create_extension_request(
    db: Session,
    firm_id: int,
    user_id: int,
    team_member_id: int,
    engagement_instance_id: int,
    allocation_percent: int,
    start_date: date,
    end_date: date,
    role_on_engagement: Optional[str] = None,
    reason: Optional[str] = None,
) -> ExtensionRequest:
    """Create an extension request for additional allocation.

    Validates:
    - Team member exists and is active
    - Engagement instance exists
    - Dates fall within instance dates
    - Allocation percent is valid
    - end_date >= start_date
    """
    if allocation_percent < 1 or allocation_percent > 100:
        raise ValidationError("allocation_percent must be between 1 and 100")
    if end_date < start_date:
        raise ValidationError("end_date must be on or after start_date")

    member = db.query(TeamMember).filter(TeamMember.id == team_member_id).first()
    if not member:
        raise NotFoundError(f"TeamMember {team_member_id} not found")
    if not member.is_active:
        raise ValidationError(f"TeamMember {team_member_id} is not active")

    instance = db.query(EngagementInstance).filter(EngagementInstance.id == engagement_instance_id).first()
    if not instance:
        raise NotFoundError(f"EngagementInstance {engagement_instance_id} not found")

    if start_date < instance.start_date:
        raise ValidationError(f"Start date ({start_date}) is before instance start ({instance.start_date})")
    if end_date > instance.end_date:
        raise ValidationError(f"End date ({end_date}) is after instance end ({instance.end_date})")

    ext = ExtensionRequest(
        firm_id=firm_id,
        team_member_id=team_member_id,
        engagement_instance_id=engagement_instance_id,
        allocation_percent=allocation_percent,
        start_date=start_date,
        end_date=end_date,
        role_on_engagement=role_on_engagement,
        reason=reason,
        requested_by_user_id=user_id,
    )
    db.add(ext)
    db.commit()
    db.refresh(ext)
    return ext


def list_extension_requests(
    db: Session,
    firm_id: int,
    status: Optional[str] = None,
) -> list[ExtensionRequest]:
    """List extension requests for a firm."""
    query = (
        db.query(ExtensionRequest)
        .options(
            joinedload(ExtensionRequest.team_member),
            joinedload(ExtensionRequest.engagement_instance),
            joinedload(ExtensionRequest.requested_by),
        )
        .filter(ExtensionRequest.firm_id == firm_id)
    )
    if status:
        query = query.filter(ExtensionRequest.status == status)
    return query.order_by(ExtensionRequest.created_at.desc()).all()


def get_extension_request(db: Session, request_id: int, firm_id: int | None = None) -> ExtensionRequest:
    q = db.query(ExtensionRequest).options(
        joinedload(ExtensionRequest.team_member),
        joinedload(ExtensionRequest.engagement_instance),
    ).filter(ExtensionRequest.id == request_id)
    if firm_id is not None:
        q = q.filter(ExtensionRequest.firm_id == firm_id)
    ext = q.first()
    if not ext:
        raise NotFoundError(f"Extension request {request_id} not found")
    return ext


def approve_extension(db: Session, request_id: int, reviewer_id: int,
                      note: Optional[str] = None) -> ExtensionRequest:
    """Approve an extension request and create the assignment."""
    ext = get_extension_request(db, request_id)
    if ext.status != ExtensionStatus.pending:
        raise ValidationError(f"Request {request_id} is already {ext.status.value}")

    # Create the actual assignment
    from app.services.allocation_service import create_assignment
    assignment = create_assignment(
        db,
        firm_id=ext.firm_id,
        team_member_id=ext.team_member_id,
        engagement_instance_id=ext.engagement_instance_id,
        allocation_percent=ext.allocation_percent,
        start_date=ext.start_date,
        end_date=ext.end_date,
        role_on_engagement=ext.role_on_engagement,
        created_by_user_id=ext.requested_by_user_id,
    )

    ext.status = ExtensionStatus.approved
    ext.reviewed_by_user_id = reviewer_id
    ext.review_note = note
    db.commit()
    db.refresh(ext)
    return ext


def reject_extension(db: Session, request_id: int, reviewer_id: int,
                     note: Optional[str] = None) -> ExtensionRequest:
    """Reject an extension request."""
    ext = get_extension_request(db, request_id)
    if ext.status != ExtensionStatus.pending:
        raise ValidationError(f"Request {request_id} is already {ext.status.value}")

    ext.status = ExtensionStatus.rejected
    ext.reviewed_by_user_id = reviewer_id
    ext.review_note = note
    db.commit()
    db.refresh(ext)
    return ext
