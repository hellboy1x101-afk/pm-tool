"""License activation and status routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user, require_role
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.flash import set_flash
from app.models.models import Firm, TechnicalRole
from app.services.license_service import activate_license, check_license, get_days_remaining
from app.templates_setup import templates

router = APIRouter(prefix="/license", tags=["license"])


@router.get("/activate", response_class=HTMLResponse)
def activate_page(request: Request, user=Depends(require_role(TechnicalRole.admin))):
    """Show license activation form."""
    return templates.TemplateResponse(
        request, "license/activate.html", {"user": user, "csrf_token": get_csrf_token(request)}
    )


@router.post("/activate")
async def activate(
    request: Request,
    user=Depends(require_role(TechnicalRole.admin)),
    db: Session = Depends(get_db),
):
    """Activate a license key for the user's firm."""
    form = await request.form()
    
    # CSRF validation
    if not validate_csrf(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    license_key = form.get("license_key", "").strip()

    if not license_key:
        return templates.TemplateResponse(
            request, "license/activate.html", {
                "user": user,
                "error": "Please enter a license key",
                "csrf_token": get_csrf_token(request),
            },
            status_code=400,
        )

    firm_id = request.session.get("firm_id")
    if not firm_id:
        return RedirectResponse("/auth/firm-select", status_code=302)

    result = activate_license(db, firm_id, license_key, user.id)

    if result["success"]:
        set_flash(request, result["message"], "success")
        return RedirectResponse("/license/status", status_code=302)

    return templates.TemplateResponse(
        request, "license/activate.html", {
            "user": user,
            "error": result["message"],
            "csrf_token": get_csrf_token(request),
        },
        status_code=400,
    )


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Show current license status."""
    firm_id = request.session.get("firm_id")
    if not firm_id:
        return RedirectResponse("/auth/firm-select", status_code=302)

    firm = db.query(Firm).filter(Firm.id == firm_id).first()
    if not firm:
        return RedirectResponse("/auth/firm-select", status_code=302)

    license_status = check_license(firm)
    days_remaining = get_days_remaining(firm)

    return templates.TemplateResponse(
        request, "license/status.html", {
            "user": user,
            "firm": firm,
            "license_status": license_status,
            "days_remaining": days_remaining,
            "csrf_token": get_csrf_token(request),
        }
    )


@router.get("/expired", response_class=HTMLResponse)
def expired_page(request: Request, user=Depends(get_current_user)):
    """Show license expired page with inline activation form."""
    return templates.TemplateResponse(
        request, "license/expired.html", {
            "user": user,
            "csrf_token": get_csrf_token(request),
        }
    )
