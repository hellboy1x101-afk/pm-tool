"""Invitation service for user invitations."""

import secrets
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.invitation import Invitation
from app.models.models import Firm, User, FirmUser, TechnicalRole

logger = logging.getLogger(__name__)

INVITATION_EXPIRY_DAYS = 7
MAX_SUPER_ADMINS = 2


def create_invitation(db: Session, firm_id: int, email: str, role: str, invited_by_user_id: int) -> Invitation:
    """Create a new invitation."""
    # Check super admin limit
    if role == "super_admin":
        current_super_admins = db.query(FirmUser).filter(
            FirmUser.firm_id == firm_id,
            FirmUser.technical_role == TechnicalRole.super_admin,
            FirmUser.is_active == True,
        ).count()
        if current_super_admins >= MAX_SUPER_ADMINS:
            raise ValueError(f"Maximum of {MAX_SUPER_ADMINS} super admins allowed per firm")
    
    # Check if invitation already exists for this email/firm
    existing = db.query(Invitation).filter(
        Invitation.firm_id == firm_id,
        Invitation.email == email,
        Invitation.is_used == False,
        Invitation.expires_at > datetime.now(timezone.utc),
    ).first()
    
    if existing:
        return existing
    
    # Create new invitation
    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        firm_id=firm_id,
        email=email,
        role=role,
        invited_by_user_id=invited_by_user_id,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITATION_EXPIRY_DAYS),
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    
    # Send invitation email
    try:
        send_invitation_email(invitation)
    except Exception as e:
        logger.error(f"Failed to send invitation email: {e}")
    
    return invitation


def send_invitation_email(invitation: Invitation) -> None:
    """Send invitation email."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.warning("SMTP not configured, skipping invitation email")
        return
    
    invite_link = f"{settings.APP_BASE_URL.rstrip('/')}/auth/accept-invitation?token={invitation.token}"
    
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        firm = db.query(Firm).filter(Firm.id == invitation.firm_id).first()
        firm_name = firm.name if firm else "Unknown Firm"
    finally:
        db.close()
    
    role_names = {
        "super_admin": "Super Admin",
        "admin": "Admin",
        "moderator": "Moderator",
        "viewer": "Viewer",
    }
    role_display = role_names.get(invitation.role, invitation.role)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 500px; margin: 0 auto; padding: 20px; }}
            .button {{ display: inline-block; padding: 12px 24px; background: #14304d; color: white; text-decoration: none; border-radius: 6px; font-weight: 600; }}
            .footer {{ font-size: 12px; color: #666; margin-top: 30px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>You're Invited!</h2>
            <p>You've been invited to join <strong>{firm_name}</strong> on splanly as a <strong>{role_display}</strong>.</p>
            <p>Click the button below to accept the invitation and create your account:</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{invite_link}" class="button">Accept Invitation</a>
            </p>
            <p>This invitation will expire in {INVITATION_EXPIRY_DAYS} days.</p>
            <p>If you didn't expect this invitation, you can safely ignore this email.</p>
            <div class="footer">
                <p>— The splanly Team</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Invitation to join {firm_name} on splanly"
    msg["From"] = f"splanly <{settings.SMTP_INVITATION_FROM_EMAIL or settings.SMTP_FROM_EMAIL or settings.SMTP_USER}>"
    msg["To"] = invitation.email
    msg.attach(MIMEText(html, "html"))
    
    try:
        from app.services.email_service import get_smtp_connection

        with get_smtp_connection() as server:
            server.send_message(msg)
            logger.info(f"Invitation email sent to {invitation.email}")
    except Exception as e:
        logger.error(f"SMTP error: {e}")
        raise


def get_invitation_by_token(db: Session, token: str) -> Invitation | None:
    """Get invitation by token."""
    return db.query(Invitation).filter(
        Invitation.token == token,
        Invitation.is_used == False,
        Invitation.expires_at > datetime.now(timezone.utc),
    ).first()


def accept_invitation(db: Session, token: str, display_name: str, password: str) -> dict:
    """Accept an invitation and create user."""
    from app.services.auth_service import hash_password
    
    invitation = get_invitation_by_token(db, token)
    if not invitation:
        return {"success": False, "message": "Invalid or expired invitation"}
    
    # Check super admin limit
    if invitation.role == "super_admin":
        current_super_admins = db.query(FirmUser).filter(
            FirmUser.firm_id == invitation.firm_id,
            FirmUser.technical_role == TechnicalRole.super_admin,
            FirmUser.is_active == True,
        ).count()
        if current_super_admins >= MAX_SUPER_ADMINS:
            return {"success": False, "message": f"Maximum of {MAX_SUPER_ADMINS} super admins allowed per firm"}
    
    user = db.query(User).filter(User.email == invitation.email).first()
    
    if user:
        existing_firm_user = db.query(FirmUser).filter(
            FirmUser.user_id == user.id,
            FirmUser.firm_id == invitation.firm_id,
        ).first()
        
        if existing_firm_user:
            invitation.is_used = True
            db.commit()
            return {"success": True, "message": "You are already a member of this firm", "user": user}
        
        firm_user = FirmUser(
            user_id=user.id,
            firm_id=invitation.firm_id,
            technical_role=TechnicalRole(invitation.role),
            is_active=True,
        )
        db.add(firm_user)
    else:
        user = User(
            email=invitation.email,
            display_name=display_name,
            password_hash=hash_password(password),
            is_active=True,
        )
        db.add(user)
        db.flush()
        
        firm_user = FirmUser(
            user_id=user.id,
            firm_id=invitation.firm_id,
            technical_role=TechnicalRole(invitation.role),
            is_active=True,
        )
        db.add(firm_user)
    
    invitation.is_used = True
    db.commit()
    db.refresh(user)
    
    return {"success": True, "message": "Invitation accepted successfully", "user": user}


def list_pending_invitations(db: Session, firm_id: int) -> list[Invitation]:
    """List pending invitations for a firm."""
    return db.query(Invitation).filter(
        Invitation.firm_id == firm_id,
        Invitation.is_used == False,
        Invitation.expires_at > datetime.now(timezone.utc),
    ).order_by(Invitation.created_at.desc()).all()


def revoke_invitation(db: Session, invitation_id: int, firm_id: int) -> bool:
    """Revoke an invitation."""
    invitation = db.query(Invitation).filter(
        Invitation.id == invitation_id,
        Invitation.firm_id == firm_id,
    ).first()
    
    if not invitation:
        return False
    
    invitation.is_used = True
    db.commit()
    return True


def promote_to_role(db: Session, user_id: int, firm_id: int, new_role: str, promoted_by_user_id: int) -> dict:
    """Promote a user to admin, moderator, or super_admin."""
    firm_user = db.query(FirmUser).filter(
        FirmUser.user_id == user_id,
        FirmUser.firm_id == firm_id,
    ).first()
    
    if not firm_user:
        return {"success": False, "message": "User not found in firm"}
    
    # Check super admin limit
    if new_role == "super_admin":
        current_super_admins = db.query(FirmUser).filter(
            FirmUser.firm_id == firm_id,
            FirmUser.technical_role == TechnicalRole.super_admin,
            FirmUser.is_active == True,
            FirmUser.user_id != user_id,
        ).count()
        if current_super_admins >= MAX_SUPER_ADMINS:
            return {"success": False, "message": f"Maximum of {MAX_SUPER_ADMINS} super admins allowed per firm"}
    
    old_role = firm_user.technical_role.value
    firm_user.technical_role = TechnicalRole(new_role)
    db.commit()
    
    # Send promotion email
    try:
        user = db.query(User).filter(User.id == user_id).first()
        firm = db.query(Firm).filter(Firm.id == firm_id).first()
        if user and firm:
            send_role_change_email(user, firm, old_role, new_role)
    except Exception as e:
        logger.error(f"Failed to send promotion email: {e}")
    
    logger.info(f"Role change: User {user_id} in firm {firm_id}: {old_role} -> {new_role} by user {promoted_by_user_id}")
    
    return {"success": True, "message": f"User promoted to {new_role}"}


def transfer_super_admin(db: Session, current_super_admin_id: int, new_super_admin_id: int, firm_id: int) -> dict:
    """Transfer super admin role. Current becomes admin, new becomes super admin."""
    current_firm_user = db.query(FirmUser).filter(
        FirmUser.user_id == current_super_admin_id,
        FirmUser.firm_id == firm_id,
        FirmUser.technical_role == TechnicalRole.super_admin,
    ).first()
    
    if not current_firm_user:
        return {"success": False, "message": "You are not a super admin"}
    
    new_firm_user = db.query(FirmUser).filter(
        FirmUser.user_id == new_super_admin_id,
        FirmUser.firm_id == firm_id,
        FirmUser.is_active == True,
    ).first()
    
    if not new_firm_user:
        return {"success": False, "message": "User not found in firm"}
    
    # Transfer: current becomes admin, new becomes super admin
    current_firm_user.technical_role = TechnicalRole.admin
    new_firm_user.technical_role = TechnicalRole.super_admin
    db.commit()
    
    # Send emails
    try:
        current_user = db.query(User).filter(User.id == current_super_admin_id).first()
        new_user = db.query(User).filter(User.id == new_super_admin_id).first()
        firm = db.query(Firm).filter(Firm.id == firm_id).first()
        
        if current_user and firm:
            send_role_change_email(current_user, firm, "super_admin", "admin")
        if new_user and firm:
            send_role_change_email(new_user, firm, "admin", "super_admin")
    except Exception as e:
        logger.error(f"Failed to send transfer emails: {e}")
    
    logger.info(f"Super admin transfer: User {current_super_admin_id} -> User {new_super_admin_id} in firm {firm_id}")
    
    return {"success": True, "message": "Super admin transferred successfully"}


def send_role_change_email(user: User, firm: Firm, old_role: str, new_role: str) -> None:
    """Send role change notification email."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        return
    
    role_names = {
        "super_admin": "Super Admin",
        "admin": "Admin",
        "moderator": "Moderator",
        "viewer": "Viewer",
    }
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 500px; margin: 0 auto; padding: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>Role Update</h2>
            <p>Hi {user.display_name},</p>
            <p>Your role in <strong>{firm.name}</strong> has been updated:</p>
            <p><strong>{role_names.get(old_role, old_role)}</strong> → <strong>{role_names.get(new_role, new_role)}</strong></p>
            <p>Please log in to see your new permissions.</p>
            <p>— The splanly Team</p>
        </div>
    </body>
    </html>
    """
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Role Update in {firm.name}"
    msg["From"] = f"splanly <{settings.SMTP_INVITATION_FROM_EMAIL or settings.SMTP_FROM_EMAIL or settings.SMTP_USER}>"
    msg["To"] = user.email
    msg.attach(MIMEText(html, "html"))
    
    try:
        from app.services.email_service import get_smtp_connection

        with get_smtp_connection() as server:
            server.send_message(msg)
    except Exception as e:
        logger.error(f"Failed to send role change email: {e}")


def get_super_admin_count(db: Session, firm_id: int) -> int:
    """Get current number of super admins for a firm."""
    return db.query(FirmUser).filter(
        FirmUser.firm_id == firm_id,
        FirmUser.technical_role == TechnicalRole.super_admin,
        FirmUser.is_active == True,
    ).count()


def list_firm_users_with_roles(db: Session, firm_id: int) -> list[dict]:
    """List all users in a firm with their roles."""
    firm_users = db.query(FirmUser).filter(
        FirmUser.firm_id == firm_id,
        FirmUser.is_active == True,
    ).all()
    
    result = []
    for fu in firm_users:
        user = db.query(User).filter(User.id == fu.user_id).first()
        if user:
            result.append({
                "user": user,
                "role": fu.technical_role.value,
                "firm_user_id": fu.id,
            })
    
    return result
