import secrets
from typing import Optional

from fastapi import Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.models import User, FirmUser, TechnicalRole


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Extract current user from session, JWT, or dev header.

    Priority: Session → JWT Bearer token → X-User-Id header (test suite only).
    """
    # 1. Session-based auth (Jinja app)
    user_id = request.session.get("user_id")
    if user_id:
        user = db.query(User).filter(User.id == user_id, User.is_active).first()
        if user:
            return user

    # 2. JWT Bearer token (API frontend)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from app.auth.jwt import decode_access_token
        token = auth_header[7:]
        payload = decode_access_token(token)
        if payload:
            try:
                uid = int(payload["sub"])
                user = db.query(User).filter(User.id == uid, User.is_active).first()
                if user:
                    return user
            except (ValueError, TypeError, KeyError):
                pass

    # 3. Test-suite fallback: X-User-Id header. Never honoured outside TESTING,
    #    otherwise anyone could impersonate any user by sending the header.
    header_id = request.headers.get("X-User-Id") if settings.TESTING else None
    if header_id:
        try:
            user = db.query(User).filter(
                User.id == int(header_id), User.is_active
            ).first()
            if user:
                return user
        except (ValueError, TypeError):
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


def require_role(*roles: TechnicalRole):
    """Dependency factory: require current user to have one of the given roles in their firm."""

    async def _check(
        request: Request,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        firm_id = request.session.get("firm_id")
        if not firm_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No active firm selected",
            )

        firm_user = db.query(FirmUser).filter(
            FirmUser.user_id == user.id,
            FirmUser.firm_id == firm_id,
            FirmUser.is_active == True,
        ).first()

        if not firm_user or firm_user.technical_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check
