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
from app.schemas.schemas import (
    EngagementCreate,
    EngagementInstanceCreate,
    EngagementInstanceRead,
    EngagementInstanceUpdate,
    EngagementRead,
    EngagementUpdate,
)
from app.services import engagement_service as service
from app.services import client_service
from app.templates_setup import templates

router = APIRouter(prefix="/engagements", tags=["engagements"])


@router.get("", response_class=HTMLResponse)
def list_engagements(
    request: Request,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    q: Optional[str] = None, is_active: Optional[bool] = None, status: Optional[str] = None,
    db: Session = Depends(get_db), user=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_engagements(db, firm_id=firm_id, limit=limit, offset=offset, q=q, is_active=is_active, status=status)
    return templates.TemplateResponse(request, "engagements/list.html", {
        "items": items, "total": total, "limit": limit, "offset": offset, "q": q or "", "is_active": is_active,
        "user": user,
    })


@router.get("/json", response_model=dict)
def list_engagements_json(
    request: Request,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    q: Optional[str] = None, is_active: Optional[bool] = None, status: Optional[str] = None,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_engagements(db, firm_id=firm_id, limit=limit, offset=offset, q=q, is_active=is_active, status=status)
    return {"items": [EngagementRead.model_validate(e) for e in items], "total": total, "limit": limit, "offset": offset}


@router.get("/new", response_class=HTMLResponse)
def new_engagement_form(request: Request, db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator))):
    firm_id = request.session.get("firm_id")
    clients, _ = client_service.list_clients(db, firm_id=firm_id, limit=200, is_active=True)
    return templates.TemplateResponse(request, "engagements/form.html", {
        "engagement": None, "action": "/engagements/new", "errors": [],
        "clients": clients, "csrf_token": get_csrf_token(request),
    })


@router.post("/new")
async def create_engagement_form(
    request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    for key in ["name", "client_id", "engagement_type", "recurrence_pattern", "start_date", "end_date", "status"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val

    try:
        if "client_id" in data:
            data["client_id"] = int(data["client_id"])

        # Check if approval is required
        from app.approval_check import check_approval
        from app.models.models import ResourceType, OperationType
        firm_id = request.session.get("firm_id")
        result = check_approval(db, firm_id, _.id, ResourceType.engagement, OperationType.create, data)
        if result:
            set_flash(request, "Engagement creation submitted for admin approval.")
            referer = request.headers.get("referer", "/engagements")
            return RedirectResponse(url=referer, status_code=303)

        service.create_engagement(db, data, firm_id=firm_id)
        return RedirectResponse(url="/engagements", status_code=303)
    except (ValidationError, Exception) as e:
        errors.append(user_message(e, db))

    clients, _ = client_service.list_clients(db, firm_id=firm_id, limit=200, is_active=True)
    return templates.TemplateResponse(request, "engagements/form.html", {
        "engagement": None, "action": "/engagements/new", "errors": errors,
        "clients": clients, "csrf_token": get_csrf_token(request),
    })


@router.get("/{engagement_id}", response_class=HTMLResponse)
def engagement_detail(
    request: Request, engagement_id: int,
    db: Session = Depends(get_db), user=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    engagement = service.get_engagement(db, engagement_id, firm_id=firm_id)
    instances, _ = service.list_instances(db, firm_id=firm_id, limit=200, engagement_id=engagement_id)
    return templates.TemplateResponse(request, "engagements/detail.html", {
        "engagement": engagement,
        "instances": instances,
        "csrf_token": get_csrf_token(request),
        "user": user,
    })


@router.post("/{engagement_id}/instances/new")
async def create_instance_form(
    request: Request, engagement_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    try:
        data = {
            "engagement_id": engagement_id,
            "period_label": form_data.get("period_label", "").strip(),
            "start_date": form_data.get("start_date"),
            "end_date": form_data.get("end_date"),
            "due_date": form_data.get("due_date") or None,
            "status": form_data.get("status", "planned"),
        }
        service.create_instance(db, data, firm_id=request.session.get("firm_id"))
        set_flash(request, f"Instance '{data['period_label']}' created.")
        return RedirectResponse(url=f"/engagements/{engagement_id}", status_code=303)
    except Exception as e:
        errors.append(user_message(e, db))

    firm_id = request.session.get("firm_id")
    engagement = service.get_engagement(db, engagement_id, firm_id=firm_id)
    instances, _ = service.list_instances(db, firm_id=firm_id, limit=200, engagement_id=engagement_id)
    return templates.TemplateResponse(request, "engagements/detail.html", {
        "engagement": engagement,
        "instances": instances,
        "errors": errors,
        "csrf_token": get_csrf_token(request),
        "user": user,
    })


@router.get("/{engagement_id}/edit", response_class=HTMLResponse)
def edit_engagement_form(
    request: Request, engagement_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    firm_id = request.session.get("firm_id")
    engagement = service.get_engagement(db, engagement_id, firm_id=firm_id)
    clients, _ = client_service.list_clients(db, firm_id=firm_id, limit=200, is_active=True)
    return templates.TemplateResponse(request, "engagements/form.html", {
        "engagement": engagement, "action": f"/engagements/{engagement_id}/edit", "errors": [],
        "clients": clients, "csrf_token": get_csrf_token(request),
    })


@router.post("/{engagement_id}/edit")
async def update_engagement_form(
    request: Request, engagement_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    for key in ["name", "client_id", "engagement_type", "recurrence_pattern", "start_date", "end_date", "status"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val

    try:
        if "client_id" in data:
            data["client_id"] = int(data["client_id"])
        service.update_engagement(db, engagement_id, data, firm_id=firm_id)
        set_flash(request, "Engagement updated.")
        return RedirectResponse(url="/engagements", status_code=303)
    except (ValidationError, Exception) as e:
        errors.append(user_message(e, db))

    engagement = service.get_engagement(db, engagement_id, firm_id=firm_id)
    firm_id = request.session.get("firm_id")
    clients, _ = client_service.list_clients(db, firm_id=firm_id, limit=200, is_active=True)
    return templates.TemplateResponse(request, "engagements/form.html", {
        "engagement": engagement, "action": f"/engagements/{engagement_id}/edit", "errors": errors,
        "clients": [str(c.id) for c in clients], "csrf_token": get_csrf_token(request),
    })


@router.post("", response_model=EngagementRead, status_code=201)
def create_engagement_api(
    data: EngagementCreate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.create_engagement(db, data.model_dump(), firm_id=request.session.get("firm_id"))
    return EngagementRead.model_validate(result)


@router.post("/{engagement_id}/deactivate")
def deactivate_engagement_form(
    request: Request, engagement_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin)),
):
    service.soft_delete_engagement(db, engagement_id, firm_id=request.session.get("firm_id"))
    set_flash(request, "Engagement deactivated.", "warning")
    return RedirectResponse(url="/engagements", status_code=303)


@router.patch("/{engagement_id}", response_model=EngagementRead)
def update_engagement_api(
    engagement_id: int, data: EngagementUpdate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.update_engagement(db, engagement_id, data.model_dump(exclude_unset=True), firm_id=request.session.get("firm_id"))
    return EngagementRead.model_validate(result)


@router.get("/instances", response_model=dict)
def list_instances(
    request: Request,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    engagement_id: Optional[int] = None, status: Optional[str] = None,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    items, total = service.list_instances(db, firm_id=request.session.get("firm_id"), limit=limit, offset=offset, engagement_id=engagement_id, status=status)
    return {"items": [EngagementInstanceRead.model_validate(i) for i in items], "total": total, "limit": limit, "offset": offset}


@router.post("/instances", response_model=EngagementInstanceRead, status_code=201)
def create_instance(
    data: EngagementInstanceCreate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.create_instance(db, data.model_dump(), firm_id=request.session.get("firm_id"))
    return EngagementInstanceRead.model_validate(result)


@router.patch("/instances/{instance_id}", response_model=EngagementInstanceRead)
def update_instance(
    instance_id: int, data: EngagementInstanceUpdate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.update_instance(db, instance_id, data.model_dump(exclude_unset=True), firm_id=request.session.get("firm_id"))
    return EngagementInstanceRead.model_validate(result)
