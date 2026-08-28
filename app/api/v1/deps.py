"""Shared dependencies for API v1 endpoints."""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.database import get_db
from app.models.models import User, FirmUser, TechnicalRole
from app.services.firm_service import get_user_firms, get_user_role_in_firm


async def get_firm_id(request: Request) -> int | None:
    """Extract firm_id from JWT token or session."""
    # Check JWT token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from app.auth.jwt import decode_access_token
        token = auth_header[7:]
        payload = decode_access_token(token)
        if payload and payload.get("firm_id"):
            return int(payload["firm_id"])

    # Check session
    firm_id = request.session.get("firm_id")
    if firm_id:
        return int(firm_id)

    return None


async def get_current_firm_user(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FirmUser:
    """Get the FirmUser for the current user's active firm.

    Firm ID comes from JWT token or session.
    """
    firm_id = await get_firm_id(request)

    if firm_id:
        from app.services.firm_service import get_firm_user
        fu = get_firm_user(db, user.id, firm_id)
        if fu and fu.is_active:
            return fu

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="No valid active firm selected",
    )


def require_api_role(*roles: TechnicalRole):
    """Dependency factory: require current user to have one of the given roles in their firm."""

    async def _check(firm_user: FirmUser = Depends(get_current_firm_user)) -> FirmUser:
        if firm_user.technical_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return firm_user

    return _check
