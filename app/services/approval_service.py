"""Approval rules and approval requests service layer."""

from typing import Any

from sqlalchemy.orm import Session

from app.exceptions import NotFoundError, ValidationError
from app.models.models import (
    ApprovalRule, ApprovalRequest, ApprovalStatus,
    ResourceType, OperationType, TechnicalRole,
)


# ── Approval Rules ──

def list_approval_rules(db: Session, firm_id: int) -> list[ApprovalRule]:
    return db.query(ApprovalRule).filter(ApprovalRule.firm_id == firm_id).all()


def get_approval_rule(db: Session, firm_id: int, resource_type: ResourceType,
                      operation: OperationType) -> ApprovalRule | None:
    return db.query(ApprovalRule).filter(
        ApprovalRule.firm_id == firm_id,
        ApprovalRule.resource_type == resource_type,
        ApprovalRule.operation == operation,
    ).first()


def check_approval_needed(db: Session, firm_id: int, resource_type: ResourceType,
                          operation: OperationType) -> bool:
    """Check if approval is required for this operation in this firm."""
    rule = get_approval_rule(db, firm_id, resource_type, operation)
    return rule is not None and rule.is_enabled


def upsert_approval_rule(db: Session, firm_id: int, resource_type: ResourceType,
                         operation: OperationType, is_enabled: bool,
                         approver_role: TechnicalRole = TechnicalRole.admin) -> ApprovalRule:
    """Create or update an approval rule."""
    rule = get_approval_rule(db, firm_id, resource_type, operation)
    if rule:
        rule.is_enabled = is_enabled
        rule.approver_role = approver_role
    else:
        rule = ApprovalRule(
            firm_id=firm_id,
            resource_type=resource_type,
            operation=operation,
            is_enabled=is_enabled,
            approver_role=approver_role,
        )
        db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


# ── Approval Requests ──

def create_approval_request(db: Session, firm_id: int, resource_type: ResourceType,
                            operation: OperationType, user_id: int,
                            payload: dict[str, Any],
                            resource_id: int | None = None) -> ApprovalRequest:
    """Create a pending approval request."""
    req = ApprovalRequest(
        firm_id=firm_id,
        resource_type=resource_type,
        resource_id=resource_id,
        operation=operation,
        requested_by_user_id=user_id,
        payload=payload,
        status=ApprovalStatus.pending,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def list_pending_requests(db: Session, firm_id: int) -> list[ApprovalRequest]:
    """List all pending approval requests for a firm."""
    return db.query(ApprovalRequest).filter(
        ApprovalRequest.firm_id == firm_id,
        ApprovalRequest.status == ApprovalStatus.pending,
    ).order_by(ApprovalRequest.created_at.desc()).all()


def get_approval_request(db: Session, request_id: int, firm_id: int | None = None) -> ApprovalRequest:
    q = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id)
    if firm_id is not None:
        q = q.filter(ApprovalRequest.firm_id == firm_id)
    req = q.first()
    if not req:
        raise NotFoundError(f"Approval request {request_id} not found")
    return req


def approve_request(db: Session, request_id: int, reviewer_id: int,
                    note: str | None = None, firm_id: int | None = None,
                    commit: bool = True) -> ApprovalRequest:
    """Approve a pending request."""
    req = get_approval_request(db, request_id, firm_id=firm_id)
    if req.status != ApprovalStatus.pending:
        raise ValidationError(f"Request {request_id} is already {req.status.value}")
    req.status = ApprovalStatus.approved
    req.reviewed_by_user_id = reviewer_id
    req.review_note = note
    if commit:
        db.commit()
        db.refresh(req)
    return req


def reject_request(db: Session, request_id: int, reviewer_id: int,
                   note: str | None = None, firm_id: int | None = None) -> ApprovalRequest:
    """Reject a pending request."""
    req = get_approval_request(db, request_id, firm_id=firm_id)
    if req.status != ApprovalStatus.pending:
        raise ValidationError(f"Request {request_id} is already {req.status.value}")
    req.status = ApprovalStatus.rejected
    req.reviewed_by_user_id = reviewer_id
    req.review_note = note
    db.commit()
    db.refresh(req)
    return req
