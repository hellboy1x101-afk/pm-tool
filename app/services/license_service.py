"""License service — generation, validation, activation."""

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import Firm, FirmUser, TechnicalRole
from app.models.super_admin import SuperAdmin

GRACE_PERIOD_DAYS = 7

# Duration presets in days
DURATION_DAYS = {
    "3m": 90,
    "6m": 180,
    "12m": 365,
}


def generate_license_key(tier: str, duration_months: int | None = None) -> tuple[str, int | None]:
    """Generate a signed license key.

    Args:
        tier: "standard" or "enterprise"
        duration_months: 3, 6, 12, or None for perpetual

    Returns:
        Tuple of (key_string, duration_days or None for perpetual)
    """
    # Calculate duration in days
    duration_days = None
    if duration_months:
        duration_days = DURATION_DAYS.get(f"{duration_months}m", duration_months * 30)

    # Create payload with tier and duration
    payload = f"{tier}:{duration_months or 'perpetual'}"
    sig = hmac.new(
        settings.LICENSE_SIGNING_KEY.encode(), payload.encode(), hashlib.sha256
    ).digest()
    key_bytes = base64.b32encode(sig[:12]).decode().rstrip("=")
    key = f"SPLY-{key_bytes[:4]}-{key_bytes[4:8]}-{key_bytes[8:12]}-{key_bytes[12:16]}"

    return key, duration_days


def hash_license_key(key: str) -> str:
    """SHA-256 hash of the license key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def validate_license_key(key: str) -> dict | None:
    """Validate a license key by checking signature.

    Returns:
        {"tier": str, "duration_months": int|None} or None if invalid
    """
    if not key or not key.startswith("SPLY-"):
        return None

    # Try all valid combinations to find matching signature
    for tier in ("standard", "enterprise"):
        for duration in (None, 3, 6, 12):
            payload = f"{tier}:{duration or 'perpetual'}"
            sig = hmac.new(
                settings.LICENSE_SIGNING_KEY.encode(), payload.encode(), hashlib.sha256
            ).digest()
            key_bytes = base64.b32encode(sig[:12]).decode().rstrip("=")
            expected = f"SPLY-{key_bytes[:4]}-{key_bytes[4:8]}-{key_bytes[8:12]}-{key_bytes[12:16]}"

            if hmac.compare_digest(key.upper(), expected):
                return {"tier": tier, "duration_months": duration}

    return None


def check_license(firm: Firm) -> str:
    """Check license status for a firm.

    Returns:
        "no_license", "trial", "active", "grace", "expired"
    """
    if not firm.license_key_hash:
        return "no_license"

    # Check if it's a trial
    if firm.license_key_hash.startswith(hashlib.sha256(b"TRIAL").hexdigest()[:16]):
        if firm.license_expires_at:
            now = datetime.now(timezone.utc)
            expires_at = firm.license_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                grace_end = expires_at + timedelta(days=GRACE_PERIOD_DAYS)
                if now > grace_end:
                    return "expired"
                return "grace"
        return "trial"

    # Regular license
    if firm.license_expires_at:
        now = datetime.now(timezone.utc)
        expires_at = firm.license_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            grace_end = expires_at + timedelta(days=GRACE_PERIOD_DAYS)
            if now > grace_end:
                return "expired"
            return "grace"

    return "active"


def get_days_remaining(firm: Firm) -> int | None:
    """Get days remaining on license/trial. None if perpetual."""
    if not firm.license_expires_at:
        return None
    now = datetime.now(timezone.utc)
    expires_at = firm.license_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining = (expires_at - now).days
    return max(0, remaining)


def activate_license(db: Session, firm_id: int, license_key: str, user_id: int) -> dict:
    """Activate a license key for a firm.

    Returns:
        {"success": bool, "message": str}
    """
    # Validate key signature
    key_info = validate_license_key(license_key)
    if not key_info:
        return {"success": False, "message": "Invalid license key"}

    key_hash = hash_license_key(license_key)

    # Check if key already activated by another firm
    existing = db.query(Firm).filter(Firm.license_key_hash == key_hash).first()
    if existing and existing.id != firm_id:
        return {"success": False, "message": "License key already activated by another firm"}

    # Get firm
    firm = db.query(Firm).filter(Firm.id == firm_id).first()
    if not firm:
        return {"success": False, "message": "Firm not found"}

    # Calculate expiry
    now = datetime.utcnow()
    duration_days = DURATION_DAYS.get(f"{key_info['duration_months']}m") if key_info["duration_months"] else None

    # Upgrade path: extend from old expiry if upgrading
    if duration_days:
        if firm.license_expires_at and firm.license_expires_at > now:
            # Extending from existing expiry
            new_expires = firm.license_expires_at + timedelta(days=duration_days)
        else:
            # New license starts from now
            new_expires = now + timedelta(days=duration_days)
    else:
        # Perpetual license
        new_expires = None

    # Activate
    firm.license_key = license_key
    firm.license_key_hash = key_hash
    firm.license_tier = key_info["tier"]
    firm.license_expires_at = new_expires
    firm.license_activated_at = now

    # Bootstrap: the admin who activates the first license becomes the firm's super admin.
    # Never promote once the firm already has one, or activation becomes a way to escalate.
    has_super_admin = db.query(FirmUser).filter(
        FirmUser.firm_id == firm_id,
        FirmUser.technical_role == TechnicalRole.super_admin,
        FirmUser.is_active == True,
    ).count() > 0
    firm_user = (
        db.query(FirmUser)
        .filter(FirmUser.firm_id == firm_id, FirmUser.user_id == user_id)
        .first()
    )
    if firm_user and not has_super_admin:
        firm_user.technical_role = TechnicalRole.super_admin

    db.commit()

    tier_name = key_info["tier"].title()
    if duration_days:
        months = key_info["duration_months"]
        return {"success": True, "message": f"License activated ({tier_name}, {months} months)"}
    return {"success": True, "message": f"License activated ({tier_name}, perpetual)"}


def create_super_admin(db: Session, email: str, password: str, display_name: str) -> SuperAdmin:
    """Create a super admin user."""
    from app.services.auth_service import hash_password

    admin = SuperAdmin(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def authenticate_super_admin(db: Session, email: str, password: str) -> SuperAdmin | None:
    """Authenticate a super admin."""
    from app.services.auth_service import verify_password

    admin = db.query(SuperAdmin).filter(SuperAdmin.email == email, SuperAdmin.is_active == True).first()
    if not admin:
        return None
    if not verify_password(password, admin.password_hash):
        return None
    return admin
