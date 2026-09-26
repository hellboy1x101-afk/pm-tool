"""Email service — queue to outbox and send via SMTP."""

import logging
import smtplib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.models import Assignment, EmailOutbox, TeamMember

logger = logging.getLogger(__name__)


@contextmanager
def get_smtp_connection():
    """Return an SMTP connection context manager using config-driven SSL/TLS.

    Mirrors the port-based branching in the reference ZeptoMail helper script:
      - SMTP_USE_SSL=True  → SMTP_SSL (implicit TLS, port 465)
      - SMTP_USE_TLS=True  → SMTP + starttls() (explicit TLS, port 587)
      - both False         → plain SMTP (port 25)
    """
    if settings.SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
        if settings.SMTP_USE_TLS:
            server.ehlo()
            server.starttls()
    try:
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        yield server
    finally:
        server.quit()


def queue_email(
    db: Session,
    recipient_email: str,
    subject: str,
    body: str,
    assignment_id: Optional[int] = None,
) -> EmailOutbox:
    """Insert an email into the outbox for later delivery."""
    outbox = EmailOutbox(
        recipient_email=recipient_email,
        subject=subject,
        body=body,
        status="pending",
        assignment_id=assignment_id,
    )
    db.add(outbox)
    db.commit()
    db.refresh(outbox)
    return outbox


def queue_assignment_notification(
    db: Session,
    assignment: Assignment,
    team_member: TeamMember,
    engagement_name: str,
    created_by_name: Optional[str] = None,
) -> Optional[EmailOutbox]:
    """Queue an email notifying a team member about their new assignment."""
    subject = f"New Assignment: {engagement_name}"
    lines = [
        f"Hi {team_member.name},",
        "",
        f"You have been assigned to {engagement_name}.",
        "",
        f"  Allocation: {assignment.allocation_percent}%",
        f"  Period: {assignment.start_date} to {assignment.end_date}",
    ]
    if assignment.role_on_engagement:
        lines.append(f"  Role: {assignment.role_on_engagement}")
    if created_by_name:
        lines.append(f"  Assigned by: {created_by_name}")
    lines.extend(["", "Please review your schedule in splanly.", "", "— splanly"])
    body = "\n".join(lines)

    return queue_email(
        db,
        recipient_email=team_member.email,
        subject=subject,
        body=body,
        assignment_id=assignment.id,
    )


def _send_via_smtp(recipient: str, subject: str, body: str) -> None:
    """Send an email via SMTP. Raises on failure."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = recipient

    with get_smtp_connection() as server:
        refused = server.sendmail(settings.SMTP_FROM_EMAIL, [recipient], msg.as_string())
        if refused:
            raise RuntimeError(f"SMTP refused recipient(s): {', '.join(refused)}")


def send_outbox_entry(db: Session, outbox_id: int) -> bool:
    """Attempt to send a single outbox entry. Returns True on success."""
    entry = db.query(EmailOutbox).filter(EmailOutbox.id == outbox_id).first()
    if not entry:
        return False

    try:
        _send_via_smtp(entry.recipient_email, entry.subject, entry.body)
        entry.status = "sent"
        entry.sent_at = datetime.now(timezone.utc)
        entry.last_error = None
        db.commit()
        logger.info(f"Email sent to {entry.recipient_email} (outbox #{entry.id})")
        return True
    except Exception as e:
        entry.retry_count += 1
        entry.last_error = str(e)[:500]
        if entry.retry_count >= settings.EMAIL_MAX_RETRIES:
            entry.status = "failed"
        else:
            entry.status = "pending"
            # Exponential backoff: 1min, 5min, 15min
            backoff_minutes = [1, 5, 15]
            delay = backoff_minutes[min(entry.retry_count - 1, len(backoff_minutes) - 1)]
            entry.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=delay)
        db.commit()
        logger.warning(f"Email to {entry.recipient_email} failed (attempt {entry.retry_count}): {e}")
        return False


def process_outbox(db: Session, batch_size: int = 50) -> dict:
    """Process pending emails from the outbox.

    Returns dict with sent/failed/skipped counts.
    """
    now = datetime.now(timezone.utc)
    # Claim rows before sending so concurrent workers (one per web process) never pick up
    # the same email: lock with SKIP LOCKED, mark as "sending", commit. Rows left in
    # "sending" by a crashed worker are reclaimed after 10 minutes.
    pending = (
        db.query(EmailOutbox)
        .filter(
            or_(
                and_(
                    EmailOutbox.status == "pending",
                    or_(EmailOutbox.next_attempt_at.is_(None), EmailOutbox.next_attempt_at <= now),
                ),
                and_(EmailOutbox.status == "sending", EmailOutbox.updated_at <= now - timedelta(minutes=10)),
            ),
        )
        .order_by(EmailOutbox.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .all()
    )
    for entry in pending:
        entry.status = "sending"
    db.commit()

    sent = 0
    failed = 0
    for entry in pending:
        if send_outbox_entry(db, entry.id):
            sent += 1
        else:
            # Check if it transitioned to failed
            db.refresh(entry)
            if entry.status == "failed":
                failed += 1

    return {"sent": sent, "failed": failed, "processed": len(pending)}


def _firm_outbox(db: Session, firm_id: int):
    """Outbox entries belonging to a firm (via the assignment's team member)."""
    return (
        db.query(EmailOutbox)
        .join(Assignment, EmailOutbox.assignment_id == Assignment.id)
        .join(TeamMember, Assignment.team_member_id == TeamMember.id)
        .filter(TeamMember.firm_id == firm_id)
    )


def list_outbox(
    db: Session,
    firm_id: int,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    q: Optional[str] = None,
):
    """List a firm's outbox entries for admin viewing."""
    query = _firm_outbox(db, firm_id)
    if status:
        query = query.filter(EmailOutbox.status == status)
    if q:
        query = query.filter(
            or_(
                EmailOutbox.recipient_email.ilike(f"%{q}%"),
                EmailOutbox.subject.ilike(f"%{q}%"),
            )
        )
    total = query.count()
    items = query.order_by(EmailOutbox.created_at.desc()).limit(limit).offset(offset).all()
    return items, total


def retry_failed(db: Session, outbox_id: int, firm_id: int | None = None) -> bool:
    """Reset a failed email to pending for retry. firm_id limits it to that firm's emails."""
    query = _firm_outbox(db, firm_id) if firm_id is not None else db.query(EmailOutbox)
    entry = query.filter(
        EmailOutbox.id == outbox_id,
        EmailOutbox.status == "failed",
    ).first()
    if not entry:
        return False
    entry.status = "pending"
    entry.retry_count = 0
    entry.next_attempt_at = None
    entry.last_error = None
    db.commit()
    return True
