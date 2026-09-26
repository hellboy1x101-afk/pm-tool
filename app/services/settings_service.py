from typing import Optional

from sqlalchemy.orm import Session

from app.exceptions import NotFoundError
from app.models.models import SystemSetting


# Settings are stored as platform defaults (firm_id NULL) plus optional per-firm overrides.
# Reads fall back from the firm's override to the default; writes made on behalf of a firm
# only ever create/update that firm's own row, so one firm can never change another's value.


def _row(db: Session, key: str, firm_id: int | None) -> Optional[SystemSetting]:
    query = db.query(SystemSetting).filter(SystemSetting.key == key)
    if firm_id is None:
        return query.filter(SystemSetting.firm_id.is_(None)).first()
    return query.filter(SystemSetting.firm_id == firm_id).first()


def get_setting(db: Session, key: str, firm_id: int | None = None) -> Optional[SystemSetting]:
    """Effective setting: the firm's override if present, else the platform default."""
    if firm_id is not None:
        setting = _row(db, key, firm_id)
        if setting:
            return setting
    return _row(db, key, None)


def get_setting_value(db: Session, key: str, firm_id: int | None = None, default: str = "") -> str:
    setting = get_setting(db, key, firm_id)
    return setting.value if setting else default


def update_setting(db: Session, key: str, value: str, updated_by_user_id: Optional[int] = None,
                   firm_id: int | None = None):
    """Write the firm's own override, or the platform default when firm_id is None."""
    setting = _row(db, key, firm_id)
    if not setting:
        default = _row(db, key, None) if firm_id is not None else None
        setting = SystemSetting(
            key=key, value=value, firm_id=firm_id, updated_by_user_id=updated_by_user_id,
            description=default.description if default else None,
        )
        db.add(setting)
    else:
        setting.value = value
        setting.updated_by_user_id = updated_by_user_id
    db.commit()
    db.refresh(setting)
    return setting


def list_all_settings(db: Session, firm_id: int | None = None):
    """Effective settings for a firm (one row per key), or the platform defaults when firm_id is None."""
    defaults = db.query(SystemSetting).filter(SystemSetting.firm_id.is_(None)).all()
    effective = {s.key: s for s in defaults}
    if firm_id is not None:
        for s in db.query(SystemSetting).filter(SystemSetting.firm_id == firm_id).all():
            effective[s.key] = s
    return [effective[key] for key in sorted(effective)]


def get_auth_method(db: Session, firm_id: int | None = None) -> str:
    """Get authentication method for firm. Returns '2fa' or 'otp'."""
    return get_setting_value(db, "auth_method", firm_id, default="2fa")


def set_auth_method(db: Session, method: str, firm_id: int | None = None, updated_by_user_id: int | None = None):
    """Set authentication method for firm."""
    if method not in ("2fa", "otp"):
        raise ValueError("Invalid auth method. Must be '2fa' or 'otp'")
    return update_setting(db, "auth_method", method, updated_by_user_id, firm_id)


def get_password_expiry_days(db: Session, firm_id: int | None = None) -> int:
    """Get password expiry days for firm. Returns 0 if disabled."""
    val = get_setting_value(db, "password_expiry_days", firm_id, default="90")
    try:
        days = int(val)
        return min(max(days, 0), 90)
    except (ValueError, TypeError):
        return 90


def set_password_expiry_days(db: Session, days: int, firm_id: int | None = None,
                             updated_by_user_id: int | None = None):
    """Set password expiry days for firm. 0=disabled, max=90."""
    days = min(max(days, 0), 90)
    return update_setting(db, "password_expiry_days", str(days), updated_by_user_id, firm_id)
