from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user, require_role
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.exceptions import ValidationError, user_message
from app.flash import set_flash
from app.models.models import TechnicalRole
from app.schemas.schemas import LeaveCreate, LeaveRead, LeaveUpdate
from app.services import leave_service as service
from app.services import team_member_service
from app.templates_setup import templates

router = APIRouter(prefix="/leaves", tags=["leaves"])


@router.get("", response_class=HTMLResponse)
def list_leaves(
    request: Request,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    team_member_id: Optional[int] = None, status: Optional[str] = None,
    db: Session = Depends(get_db), user=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_leaves(db, firm_id=firm_id, limit=limit, offset=offset, team_member_id=team_member_id, status=status)
    return templates.TemplateResponse(request, "leaves/list.html", {
        "items": items, "total": total, "limit": limit, "offset": offset,
        "user": user,
    })


@router.get("/json", response_model=dict)
def list_leaves_json(
    request: Request,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    team_member_id: Optional[int] = None, status: Optional[str] = None,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_leaves(db, firm_id=firm_id, limit=limit, offset=offset, team_member_id=team_member_id, status=status)
    return {"items": [LeaveRead.model_validate(l) for l in items], "total": total, "limit": limit, "offset": offset}


def _firm_members(db: Session, request: Request):
    members, _total = team_member_service.list_team_members(
        db, firm_id=request.session.get("firm_id"), limit=200, is_active=True,
    )
    return members


@router.get("/new", response_class=HTMLResponse)
def new_leave_form(
    request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    members = _firm_members(db, request)
    return templates.TemplateResponse(request, "leaves/form.html", {
        "leave": None, "action": "/leaves/new", "errors": [],
        "members": members, "csrf_token": get_csrf_token(request),
    })


@router.post("/new")
async def create_leave_form(
    request: Request, db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    for key in ["team_member_id", "leave_type", "start_date", "end_date", "reason"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val

    try:
        data["team_member_id"] = int(data["team_member_id"])

        # Check if approval is required
        from app.approval_check import check_approval
        from app.models.models import ResourceType, OperationType
        firm_id = request.session.get("firm_id")
        result = check_approval(db, firm_id, user.id, ResourceType.leave, OperationType.create, data)
        if result:
            set_flash(request, "Leave request submitted for admin approval.")
            referer = request.headers.get("referer", "/leaves")
            return RedirectResponse(url=referer, status_code=303)

        service.create_leave(db, data, firm_id=firm_id)
        return RedirectResponse(url="/leaves", status_code=303)
    except (ValidationError, Exception) as e:
        errors.append(user_message(e, db))

    return templates.TemplateResponse(request, "leaves/form.html", {
        "leave": None, "action": "/leaves/new", "errors": errors,
        "members": _firm_members(db, request),
        "csrf_token": get_csrf_token(request),
    })


@router.get("/{leave_id}/edit", response_class=HTMLResponse)
def edit_leave_form(
    request: Request, leave_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    leave = service.get_leave(db, leave_id, firm_id=request.session.get("firm_id"))
    return templates.TemplateResponse(request, "leaves/form.html", {
        "leave": leave, "action": f"/leaves/{leave_id}/edit", "errors": [],
        "members": _firm_members(db, request),
        "csrf_token": get_csrf_token(request),
    })


@router.post("/{leave_id}/edit")
async def update_leave_form(
    request: Request, leave_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    for key in ["team_member_id", "leave_type", "start_date", "end_date", "reason", "status"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val

    firm_id = request.session.get("firm_id")
    try:
        if "team_member_id" in data:
            data["team_member_id"] = int(data["team_member_id"])
        service.update_leave(db, leave_id, data, firm_id=firm_id)
        set_flash(request, "Leave updated.")
        return RedirectResponse(url="/leaves", status_code=303)
    except (ValidationError, Exception) as e:
        errors.append(user_message(e, db))

    leave = service.get_leave(db, leave_id, firm_id=firm_id)
    return templates.TemplateResponse(request, "leaves/form.html", {
        "leave": leave, "action": f"/leaves/{leave_id}/edit", "errors": errors,
        "members": _firm_members(db, request),
        "csrf_token": get_csrf_token(request),
    })


@router.post("", response_model=LeaveRead, status_code=201)
def create_leave_api(
    data: LeaveCreate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.create_leave(db, data.model_dump(), firm_id=request.session.get("firm_id"))
    return LeaveRead.model_validate(result)


@router.patch("/{leave_id}", response_model=LeaveRead)
def update_leave_api(
    leave_id: int, data: LeaveUpdate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.update_leave(
        db, leave_id, data.model_dump(exclude_unset=True), firm_id=request.session.get("firm_id"),
    )
    return LeaveRead.model_validate(result)
