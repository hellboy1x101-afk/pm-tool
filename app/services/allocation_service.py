from datetime import date
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session, joinedload

from app.exceptions import (
    ConflictWithLeaveError,
    NotFoundError,
    OverAllocationError,
    ValidationError,
)
from app.models.models import Assignment, EngagementInstance, Leave, LeaveStatus, TeamMember
from app.services.email_service import queue_assignment_notification


def get_assignment(db: Session, assignment_id: int) -> Assignment | None:
    return db.query(Assignment).filter(Assignment.id == assignment_id).first()


def get_member_allocations(db: Session, firm_id: int | None = None) -> dict:
    """Get current allocation percentage for all active team members."""
    today = date.today()
    query = (
        db.query(
            Assignment.team_member_id,
            func.coalesce(func.sum(Assignment.allocation_percent), 0).label("total"),
        )
        .filter(
            Assignment.start_date <= today,
            Assignment.end_date >= today,
        )
    )
    if firm_id is not None:
        query = query.join(TeamMember, Assignment.team_member_id == TeamMember.id)
        query = query.filter(TeamMember.firm_id == firm_id)
    results = query.group_by(Assignment.team_member_id).all()
    return {r.team_member_id: r.total for r in results}


def create_assignment(
    db: Session,
    firm_id: int,
    team_member_id: int,
    engagement_instance_id: int,
    allocation_percent: int,
    start_date: date,
    end_date: date,
    role_on_engagement: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> Assignment:
    """Create an assignment with concurrency-safe allocation checks.

    - Locks the TeamMember row (FOR UPDATE)
    - Verifies member exists and is active
    - Validates assignment dates fall within engagement instance dates
    - Sums overlapping assignment allocations in the date window
    - Rejects if total would exceed 100%
    - Rejects if overlapping approved leave exists
    - Creates + commits in one transaction
    """
    if allocation_percent < 1 or allocation_percent > 100:
        raise ValidationError("allocation_percent must be between 1 and 100")
    if end_date < start_date:
        raise ValidationError("end_date must be on or after start_date")

    # Lock the team member row
    member = (
        db.query(TeamMember)
        .filter(TeamMember.id == team_member_id)
        .with_for_update()
        .first()
    )
    if not member:
        raise NotFoundError(f"TeamMember {team_member_id} not found")
    if member.firm_id != firm_id:
        raise NotFoundError(f"TeamMember {team_member_id} not found")
    if not member.is_active:
        raise ValidationError(f"TeamMember {team_member_id} is not active")

    # Validate assignment dates fall within engagement instance dates
    instance = (
        db.query(EngagementInstance)
        .filter(EngagementInstance.id == engagement_instance_id)
        .first()
    )
    if not instance:
        raise NotFoundError(f"EngagementInstance {engagement_instance_id} not found")
    if not instance.engagement or instance.engagement.client.firm_id != firm_id:
        raise NotFoundError(f"EngagementInstance {engagement_instance_id} not found")
    if start_date < instance.start_date:
        raise ValidationError(
            f"Assignment start_date ({start_date}) cannot be before engagement instance start ({instance.start_date})"
        )
    if end_date > instance.end_date:
        raise ValidationError(
            f"Assignment end_date ({end_date}) cannot be after engagement instance end ({instance.end_date})"
        )

    # Sum overlapping existing allocations
    overlap_sum = (
        db.query(func.coalesce(func.sum(Assignment.allocation_percent), 0))
        .filter(
            Assignment.team_member_id == team_member_id,
            Assignment.start_date <= end_date,
            Assignment.end_date >= start_date,
        )
        .scalar()
    )

    if overlap_sum + allocation_percent > 100:
        raise OverAllocationError(
            f"Total allocation would be {overlap_sum + allocation_percent}% (max 100%)"
        )

    # Check for overlapping approved leave
    leave_overlap = (
        db.query(Leave)
        .filter(
            Leave.team_member_id == team_member_id,
            Leave.status == LeaveStatus.approved,
            Leave.start_date <= end_date,
            Leave.end_date >= start_date,
        )
        .first()
    )
    if leave_overlap:
        raise ConflictWithLeaveError(
            f"TeamMember has approved {leave_overlap.leave_type.value} leave "
            f"from {leave_overlap.start_date} to {leave_overlap.end_date}"
        )

    assignment = Assignment(
        team_member_id=team_member_id,
        engagement_instance_id=engagement_instance_id,
        role_on_engagement=role_on_engagement,
        allocation_percent=allocation_percent,
        start_date=start_date,
        end_date=end_date,
        created_by_user_id=created_by_user_id,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    # Queue notification email (best-effort, don't fail the assignment)
    try:
        instance = (
            db.query(EngagementInstance)
            .filter(EngagementInstance.id == engagement_instance_id)
            .first()
        )
        engagement_name = instance.engagement.name if instance and instance.engagement else "Assignment"
        created_by_name = None
        if created_by_user_id:
            from app.models.models import User

            creator = db.query(User).filter(User.id == created_by_user_id).first()
            if creator:
                created_by_name = creator.display_name
        queue_assignment_notification(db, assignment, member, engagement_name, created_by_name)
    except Exception:
        pass  # Email queueing is non-blocking

    return assignment


def update_assignment(
    db: Session,
    firm_id: int,
    assignment_id: int,
    allocation_percent: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    role_on_engagement: Optional[str] = None,
) -> Assignment:
    """Update an assignment with re-validation of allocation and leave conflicts."""
    assignment = (
        db.query(Assignment)
        .options(joinedload(Assignment.team_member))
        .filter(Assignment.id == assignment_id)
        .first()
    )
    if not assignment:
        raise NotFoundError(f"Assignment {assignment_id} not found")
    if not assignment.team_member or assignment.team_member.firm_id != firm_id:
        raise NotFoundError(f"Assignment {assignment_id} not found")

    new_start = start_date if start_date is not None else assignment.start_date
    new_end = end_date if end_date is not None else assignment.end_date
    new_alloc = (
        allocation_percent if allocation_percent is not None else assignment.allocation_percent
    )

    if new_alloc < 1 or new_alloc > 100:
        raise ValidationError("allocation_percent must be between 1 and 100")
    if new_end < new_start:
        raise ValidationError("end_date must be on or after start_date")

    # Validate assignment dates fall within engagement instance dates
    instance = (
        db.query(EngagementInstance)
        .filter(EngagementInstance.id == assignment.engagement_instance_id)
        .first()
    )
    if instance:
        if not instance.engagement or instance.engagement.client.firm_id != firm_id:
            raise NotFoundError(f"Assignment {assignment_id} not found")
        if new_start < instance.start_date:
            raise ValidationError(
                f"Assignment start_date ({new_start}) cannot be before engagement instance start ({instance.start_date})"
            )
        if new_end > instance.end_date:
            raise ValidationError(
                f"Assignment end_date ({new_end}) cannot be after engagement instance end ({instance.end_date})"
            )

    # Lock the team member row
    member = (
        db.query(TeamMember)
        .filter(TeamMember.id == assignment.team_member_id)
        .with_for_update()
        .first()
    )
    if not member or not member.is_active:
        raise ValidationError(f"TeamMember {assignment.team_member_id} is not active")

    # Sum overlapping excluding current assignment
    overlap_sum = (
        db.query(func.coalesce(func.sum(Assignment.allocation_percent), 0))
        .filter(
            Assignment.team_member_id == assignment.team_member_id,
            Assignment.id != assignment_id,
            Assignment.start_date <= new_end,
            Assignment.end_date >= new_start,
        )
        .scalar()
    )

    if overlap_sum + new_alloc > 100:
        raise OverAllocationError(
            f"Total allocation would be {overlap_sum + new_alloc}% (max 100%)"
        )

    # Check leave conflict
    leave_overlap = (
        db.query(Leave)
        .filter(
            Leave.team_member_id == assignment.team_member_id,
            Leave.status == LeaveStatus.approved,
            Leave.start_date <= new_end,
            Leave.end_date >= new_start,
        )
        .first()
    )
    if leave_overlap:
        raise ConflictWithLeaveError(
            f"TeamMember has approved {leave_overlap.leave_type.value} leave "
            f"from {leave_overlap.start_date} to {leave_overlap.end_date}"
        )

    if allocation_percent is not None:
        assignment.allocation_percent = allocation_percent
    if start_date is not None:
        assignment.start_date = start_date
    if end_date is not None:
        assignment.end_date = end_date
    if role_on_engagement is not None:
        assignment.role_on_engagement = role_on_engagement

    db.commit()
    db.refresh(assignment)
    return assignment


def list_assignments(
    db: Session,
    firm_id: int,
    limit: int = 50,
    offset: int = 0,
    team_member_id: Optional[int] = None,
    engagement_instance_id: Optional[int] = None,
):
    query = (
        db.query(Assignment)
        .options(
            joinedload(Assignment.team_member),
            joinedload(Assignment.engagement_instance),
        )
        .join(TeamMember, Assignment.team_member_id == TeamMember.id)
        .filter(TeamMember.firm_id == firm_id)
    )
    if team_member_id:
        query = query.filter(Assignment.team_member_id == team_member_id)
    if engagement_instance_id:
        query = query.filter(Assignment.engagement_instance_id == engagement_instance_id)

    total = query.count()
    items = query.order_by(Assignment.id).limit(limit).offset(offset).all()
    return items, total
