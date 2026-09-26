"""Admin email outbox viewer — list, filter, retry failed emails."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import require_role
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.flash import set_flash
from app.models.models import TechnicalRole
from app.services import email_service as service
from app.templates_setup import templates

router = APIRouter(prefix="/admin/outbox", tags=["outbox"])


@router.get("", response_class=HTMLResponse)
def list_outbox(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    items, total = service.list_outbox(
        db, firm_id=request.session.get("firm_id"), limit=limit, offset=offset, status=status, q=q,
    )
    return templates.TemplateResponse(request, "admin/outbox.html", {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "q": q or "",
        "status": status or "",
        "user": user,
    })


@router.post("/{outbox_id}/retry")
async def retry_email(
    request: Request,
    outbox_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    if service.retry_failed(db, outbox_id, firm_id=request.session.get("firm_id")):
        set_flash(request, "Email reset to pending for retry.", "info")
    else:
        set_flash(request, "Email not found or not in failed state.", "danger")
    return RedirectResponse(url="/admin/outbox", status_code=303)


@router.post("/process")
async def process_now(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    """Manually trigger outbox processing (useful when worker is not running)."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    # Sends whatever is due platform-wide (same as the background worker); only report
    # that it ran, so one firm doesn't learn another firm's email volume.
    service.process_outbox(db)
    set_flash(request, "Outbox processed.", "info")
    return RedirectResponse(url="/admin/outbox", status_code=303)
