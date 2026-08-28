from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user, require_role
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.exceptions import ValidationError
from app.flash import set_flash
from app.models.models import TechnicalRole
from app.schemas.schemas import ClientCreate, ClientRead, ClientUpdate
from app.services import client_service as service
from app.templates_setup import templates

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_class=HTMLResponse)
def list_clients(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_clients(db, firm_id=firm_id, limit=limit, offset=offset, q=q, is_active=is_active)
    return templates.TemplateResponse(request, "clients/list.html", {
        "items": items, "total": total, "limit": limit, "offset": offset, "q": q or "", "is_active": is_active,
        "user": user,
    })


@router.get("/json", response_model=dict)
def list_clients_json(
    request: Request,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    q: Optional[str] = None, is_active: Optional[bool] = None,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_clients(db, firm_id=firm_id, limit=limit, offset=offset, q=q, is_active=is_active)
    return {"items": [ClientRead.model_validate(c) for c in items], "total": total, "limit": limit, "offset": offset}


@router.get("/new", response_class=HTMLResponse)
def new_client_form(request: Request, _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator))):
    return templates.TemplateResponse(request, "clients/form.html", {
        "client": None, "action": "/clients/new", "errors": [], "csrf_token": get_csrf_token(request),
    })


@router.post("/new")
async def create_client_form(
    request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    for key in ["name", "code", "industry"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val

    try:
        # Check if approval is required
        from app.approval_check import check_approval
        from app.models.models import ResourceType, OperationType
        firm_id = request.session.get("firm_id")
        result = check_approval(db, firm_id, _.id, ResourceType.client, OperationType.create, data)
        if result:
            set_flash(request, "Client creation submitted for admin approval.")
            referer = request.headers.get("referer", "/clients")
            return RedirectResponse(url=referer, status_code=303)

        service.create_client(db, data, firm_id=firm_id)
        return RedirectResponse(url="/clients", status_code=303)
    except ValidationError as e:
        errors.append(str(e))
    except Exception as e:
        errors.append(str(e))

    return templates.TemplateResponse(request, "clients/form.html", {
        "client": None, "action": "/clients/new", "errors": errors, "csrf_token": get_csrf_token(request),
    })


@router.get("/{client_id}/edit", response_class=HTMLResponse)
def edit_client_form(
    request: Request, client_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    client = service.get_client(db, client_id)
    return templates.TemplateResponse(request, "clients/form.html", {
        "client": client, "action": f"/clients/{client_id}/edit", "errors": [], "csrf_token": get_csrf_token(request),
    })


@router.post("/{client_id}/edit")
async def update_client_form(
    request: Request, client_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    for key in ["name", "code", "industry"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val

    try:
        service.update_client(db, client_id, data, firm_id=request.session.get("firm_id"))
        set_flash(request, f"Client '{data.get('name', '')}' updated.")
        return RedirectResponse(url="/clients", status_code=303)
    except (ValidationError, Exception) as e:
        errors.append(str(e))

    client = service.get_client(db, client_id)
    return templates.TemplateResponse(request, "clients/form.html", {
        "client": client, "action": f"/clients/{client_id}/edit", "errors": errors, "csrf_token": get_csrf_token(request),
    })


@router.post("", response_model=ClientRead, status_code=201)
def create_client_api(
    data: ClientCreate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.create_client(db, data.model_dump(), firm_id=request.session.get("firm_id"))
    return ClientRead.model_validate(result)


@router.post("/{client_id}/deactivate")
def deactivate_client_form(
    request: Request, client_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin)),
):
    service.soft_delete_client(db, client_id, firm_id=request.session.get("firm_id"))
    set_flash(request, "Client deactivated.", "warning")
    return RedirectResponse(url="/clients", status_code=303)


@router.patch("/{client_id}", response_model=ClientRead)
def update_client_api(
    client_id: int, data: ClientUpdate, request: Request, db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.update_client(db, client_id, data.model_dump(exclude_unset=True), firm_id=request.session.get("firm_id"))
    return ClientRead.model_validate(result)
