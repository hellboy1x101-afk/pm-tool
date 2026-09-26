"""Invitation routes for user invitations."""

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.auth.auth import require_role
from app.config import settings
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.exceptions import user_message
from app.flash import set_flash
from app.models.models import TechnicalRole
from app.services.invitation_service import (
    create_invitation,
    list_pending_invitations,
    revoke_invitation,
    promote_to_role,
    transfer_super_admin,
    get_super_admin_count,
    list_firm_users_with_roles,
)
from app.templates_setup import templates

router = APIRouter(prefix="/invitations", tags=["invitations"])


def _invitable_roles(db: Session, request: Request) -> list[str]:
    """Roles the current user may grant. Only super admins can create super admins."""
    from app.services.firm_service import get_user_role_in_firm

    role = get_user_role_in_firm(db, request.session.get("user_id"), request.session.get("firm_id"))
    if role == TechnicalRole.super_admin:
        return ["super_admin", "admin", "moderator"]
    return ["admin", "moderator"]


@router.get("", response_class=HTMLResponse)
def invitations_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """List pending invitations."""
    firm_id = request.session.get("firm_id")
    invitations = list_pending_invitations(db, firm_id) if firm_id else []
    super_admin_count = get_super_admin_count(db, firm_id) if firm_id else 0
    
    return templates.TemplateResponse(request, "invitations/list.html", {
        "csrf_token": get_csrf_token(request),
        "invitations": invitations,
        "super_admin_count": super_admin_count,
        "max_super_admins": 2,
    })


@router.get("/new", response_class=HTMLResponse)
def new_invitation_form(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """Show invitation form."""
    firm_id = request.session.get("firm_id")
    super_admin_count = get_super_admin_count(db, firm_id) if firm_id else 0
    
    return templates.TemplateResponse(request, "invitations/form.html", {
        "csrf_token": get_csrf_token(request),
        "errors": [],
        "roles": _invitable_roles(db, request),
        "super_admin_count": super_admin_count,
        "max_super_admins": 2,
    })


@router.post("/new", response_class=HTMLResponse)
async def create_invitation_form(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """Create a new invitation."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    email = form_data.get("email", "").strip().lower()
    role = form_data.get("role", "moderator")
    
    allowed_roles = _invitable_roles(db, request)
    errors = []
    if not email:
        errors.append("Email is required")
    if "@" not in email:
        errors.append("Invalid email format")
    if role not in allowed_roles:
        errors.append("You are not allowed to invite users with this role")
    
    firm_id = request.session.get("firm_id")
    user_id = request.session.get("user_id")
    
    if not firm_id:
        errors.append("No firm selected")
    
    if not errors:
        try:
            invitation = create_invitation(db, firm_id, email, role, user_id)
            set_flash(request, f"Invitation sent to {email}")
            return RedirectResponse(url="/invitations", status_code=303)
        except ValueError as e:
            errors.append(str(e))
    
    super_admin_count = get_super_admin_count(db, firm_id) if firm_id else 0
    
    return templates.TemplateResponse(request, "invitations/form.html", {
        "csrf_token": get_csrf_token(request),
        "errors": errors,
        "roles": allowed_roles,
        "email": email,
        "role": role,
        "super_admin_count": super_admin_count,
        "max_super_admins": 2,
    })


@router.get("/template")
def download_template(
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """Download CSV template for bulk invitations."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "role"])
    writer.writerow(["john@example.com", "moderator"])
    writer.writerow(["jane@example.com", "admin"])
    writer.writerow(["", ""])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invitation_template.csv"},
    )


@router.get("/bulk", response_class=HTMLResponse)
def bulk_invite_form(
    request: Request,
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """Show bulk invitation form."""
    return templates.TemplateResponse(request, "invitations/bulk.html", {
        "csrf_token": get_csrf_token(request),
        "results": None,
    })


@router.post("/bulk", response_class=HTMLResponse)
async def bulk_invite(
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(None),
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """Process bulk invitations."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    method = form_data.get("method", "csv")
    firm_id = request.session.get("firm_id")
    user_id = request.session.get("user_id")
    
    if not firm_id:
        return templates.TemplateResponse(request, "invitations/bulk.html", {
            "csrf_token": get_csrf_token(request),
            "results": {"sent": 0, "failed": 0, "errors": ["No firm selected"]},
        })
    
    emails_with_roles = []
    
    if method == "csv":
        if not file:
            return templates.TemplateResponse(request, "invitations/bulk.html", {
                "csrf_token": get_csrf_token(request),
                "results": {"sent": 0, "failed": 0, "errors": ["No file uploaded"]},
            })
        
        contents = file.file.read()
        try:
            reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig")))
            for row in reader:
                email = row.get("email", "").strip().lower()
                role = row.get("role", "moderator").strip().lower()
                if email:
                    emails_with_roles.append((email, role))
        except Exception as e:
            return templates.TemplateResponse(request, "invitations/bulk.html", {
                "csrf_token": get_csrf_token(request),
                "results": {"sent": 0, "failed": 0, "errors": [f"CSV parse error: {str(e)}"]},
            })
    else:
        emails_text = form_data.get("emails", "")
        default_role = form_data.get("default_role", "moderator")
        
        for line in emails_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            email = parts[0].lower()
            role = parts[1] if len(parts) > 1 else default_role
            if email and "@" in email:
                emails_with_roles.append((email, role))
    
    sent = 0
    failed = 0
    errors = []
    allowed_roles = _invitable_roles(db, request)

    for email, role in emails_with_roles:
        if role == "super_admin" and role not in allowed_roles:
            failed += 1
            errors.append(f"{email}: You are not allowed to invite super admins")
            continue
        if role not in allowed_roles:
            role = "moderator"

        try:
            create_invitation(db, firm_id, email, role, user_id)
            sent += 1
        except Exception as e:
            failed += 1
            errors.append(f"{email}: {user_message(e, db)}")
    
    return templates.TemplateResponse(request, "invitations/bulk.html", {
        "csrf_token": get_csrf_token(request),
        "results": {"sent": sent, "failed": failed, "errors": errors},
    })


@router.post("/{invitation_id}/revoke")
async def revoke_invitation_form(
    invitation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """Revoke an invitation."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    firm_id = request.session.get("firm_id")
    if not firm_id:
        return RedirectResponse(url="/invitations", status_code=303)
    
    success = revoke_invitation(db, invitation_id, firm_id)
    
    if success:
        set_flash(request, "Invitation revoked")
    else:
        set_flash(request, "Invitation not found", "danger")
    
    return RedirectResponse(url="/invitations", status_code=303)


@router.get("/manage", response_class=HTMLResponse)
def manage_users_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.super_admin, TechnicalRole.admin)),
):
    """Manage users and roles."""
    firm_id = request.session.get("firm_id")
    if not firm_id:
        return RedirectResponse(url="/dashboard", status_code=303)
    
    users_with_roles = list_firm_users_with_roles(db, firm_id)
    super_admin_count = get_super_admin_count(db, firm_id)
    current_user_id = request.session.get("user_id")
    
    return templates.TemplateResponse(request, "invitations/manage.html", {
        "csrf_token": get_csrf_token(request),
        "users_with_roles": users_with_roles,
        "super_admin_count": super_admin_count,
        "max_super_admins": 2,
        "current_user_id": current_user_id,
    })


@router.post("/promote/{user_id}")
async def promote_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(TechnicalRole.super_admin)),
):
    """Promote a user to a new role."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    new_role = form_data.get("role", "")
    firm_id = request.session.get("firm_id")
    
    if not firm_id:
        set_flash(request, "No firm selected", "danger")
        return RedirectResponse(url="/invitations/manage", status_code=303)
    
    if new_role not in ["admin", "moderator", "super_admin"]:
        set_flash(request, "Invalid role", "danger")
        return RedirectResponse(url="/invitations/manage", status_code=303)
    
    result = promote_to_role(db, user_id, firm_id, new_role, current_user.id)
    
    if result["success"]:
        set_flash(request, result["message"])
    else:
        set_flash(request, result["message"], "danger")
    
    return RedirectResponse(url="/invitations/manage", status_code=303)


@router.post("/transfer-super-admin/{user_id}")
async def transfer_super_admin_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(TechnicalRole.super_admin)),
):
    """Transfer super admin role to another user."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    firm_id = request.session.get("firm_id")
    
    if not firm_id:
        set_flash(request, "No firm selected", "danger")
        return RedirectResponse(url="/invitations/manage", status_code=303)
    
    result = transfer_super_admin(db, current_user.id, user_id, firm_id)
    
    if result["success"]:
        set_flash(request, result["message"])
    else:
        set_flash(request, result["message"], "danger")
    
    return RedirectResponse(url="/invitations/manage", status_code=303)
