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
from app.schemas.schemas import UserCreate, UserRead, UserUpdate
from app.services import user_service as service
from app.templates_setup import templates

router = APIRouter(prefix="/users", tags=["users"])


def _actor_role(db: Session, request: Request) -> TechnicalRole | None:
    from app.services.firm_service import get_user_role_in_firm

    return get_user_role_in_firm(db, request.session.get("user_id"), request.session.get("firm_id"))


def _grantable_roles(db: Session, request: Request) -> list[str]:
    """Roles the current user may assign. Only super admins can grant super_admin."""
    if _actor_role(db, request) == TechnicalRole.super_admin:
        return [r.value for r in TechnicalRole]
    return [r.value for r in TechnicalRole if r != TechnicalRole.super_admin]


def _require_can_manage(db: Session, request: Request, target_user_id: int) -> None:
    """Admins cannot modify super admins; only super admins can."""
    from app.services.firm_service import get_user_role_in_firm

    target_role = get_user_role_in_firm(db, target_user_id, request.session.get("firm_id"))
    if target_role == TechnicalRole.super_admin and _actor_role(db, request) != TechnicalRole.super_admin:
        raise HTTPException(status_code=403, detail="Only a super admin can modify another super admin")


@router.get("", response_class=HTMLResponse)
def list_users(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_users(
        db, firm_id=firm_id, limit=limit, offset=offset, q=q, is_active=is_active
    )
    # Attach firm_role to each user for template display
    from app.services.firm_service import get_user_role_in_firm
    for u in items:
        u.firm_role = get_user_role_in_firm(db, u.id, firm_id).value if firm_id and get_user_role_in_firm(db, u.id, firm_id) else "viewer"

    # Get pending approval requests with resolved info
    from app.services.approval_service import list_pending_requests
    from app.models.models import ApprovalRequest as ApprovalRequestModel
    from app.models.models import TeamMember, Client, EngagementInstance, User as UserModel
    pending_approvals = list_pending_requests(db, firm_id) if firm_id else []

    # Resolve payload IDs to names for each pending approval
    for req in pending_approvals:
        payload = req.payload or {}
        info_parts = []
        if req.resource_type.value == "assignment":
            if "team_member_id" in payload:
                m = db.query(TeamMember).get(payload["team_member_id"])
                info_parts.append(f"Member: {m.name}" if m else f"Member #{payload['team_member_id']}")
            if "engagement_instance_id" in payload:
                inst = db.query(EngagementInstance).get(payload["engagement_instance_id"])
                label = inst.period_label if inst else f"#{payload['engagement_instance_id']}"
                eng_name = inst.engagement.name if inst and inst.engagement else ""
                info_parts.append(f"Instance: {label}" + (f" ({eng_name})" if eng_name else ""))
            if "allocation_percent" in payload:
                info_parts.append(f"Allocation: {payload['allocation_percent']}%")
            if "start_date" in payload and "end_date" in payload:
                info_parts.append(f"{payload['start_date']} → {payload['end_date']}")
        elif req.resource_type.value == "client":
            if "name" in payload:
                info_parts.append(payload["name"])
        elif req.resource_type.value == "engagement":
            if "name" in payload:
                info_parts.append(payload["name"])
            if "client_id" in payload:
                c = db.query(Client).get(payload["client_id"])
                info_parts.append(f"Client: {c.name}" if c else "")
        elif req.resource_type.value == "team_member":
            if "name" in payload:
                info_parts.append(payload["name"])
            if "business_role" in payload:
                info_parts.append(str(payload["business_role"]))
        elif req.resource_type.value == "leave":
            if "start_date" in payload and "end_date" in payload:
                info_parts.append(f"{payload['start_date']} → {payload['end_date']}")
            if "leave_type" in payload:
                info_parts.append(str(payload["leave_type"]))
        req.info_text = " · ".join([p for p in info_parts if p])

    # Resolve requestor names for pending approvals
    req_user_ids = set(r.requested_by_user_id for r in pending_approvals)
    req_users_map = {}
    if req_user_ids:
        for u in db.query(UserModel).filter(UserModel.id.in_(req_user_ids)).all():
            req_users_map[u.id] = u
    for req in pending_approvals:
        req.requestor_name = req_users_map[req.requested_by_user_id].display_name if req.requested_by_user_id in req_users_map else "Unknown"

    # Get pending extension requests
    from app.services.extension_service import list_extension_requests
    pending_extensions = list_extension_requests(db, firm_id, status="pending") if firm_id else []

    # Get approval logs (last 25, paginated) with user names
    from app.models.models import ApprovalRequest as ApprovalRequestModel
    log_offset_val = int(request.query_params.get("log_offset", 0))
    log_limit = 25
    if firm_id:
        log_total = db.query(ApprovalRequestModel).filter(
            ApprovalRequestModel.firm_id == firm_id
        ).count()
        logs_raw = db.query(ApprovalRequestModel).filter(
            ApprovalRequestModel.firm_id == firm_id
        ).order_by(ApprovalRequestModel.created_at.desc()).offset(log_offset_val).limit(log_limit).all()

        user_ids = set()
        for log in logs_raw:
            user_ids.add(log.requested_by_user_id)
            if log.reviewed_by_user_id:
                user_ids.add(log.reviewed_by_user_id)
        users_map = {}
        if user_ids:
            from app.models.models import User as UserModel
            for u in db.query(UserModel).filter(UserModel.id.in_(user_ids)).all():
                users_map[u.id] = u

        approval_logs = []
        for log in logs_raw:
            log.requested_by_name = users_map[log.requested_by_user_id].display_name if log.requested_by_user_id in users_map else str(log.requested_by_user_id)
            log.requested_by_id = log.requested_by_user_id
            if log.reviewed_by_user_id and log.reviewed_by_user_id in users_map:
                log.reviewed_by_name = users_map[log.reviewed_by_user_id].display_name
                log.reviewed_by_id = log.reviewed_by_user_id
            else:
                log.reviewed_by_name = "—"
                log.reviewed_by_id = None
            approval_logs.append(log)
    else:
        log_total = 0
        approval_logs = []

    # Get deleted users
    deleted_users, deleted_total = service.list_deleted_users(db, firm_id) if firm_id else ([], 0)

    return templates.TemplateResponse(request, "users/list.html", {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "q": q or "",
        "is_active": is_active,
        "user": user,
        "pending_approvals": pending_approvals,
        "pending_extensions": pending_extensions,
        "approval_logs": approval_logs,
        "log_total": log_total,
        "log_limit": log_limit,
        "log_offset": log_offset_val,
        "deleted_users": deleted_users,
        "deleted_total": deleted_total,
    })


@router.get("/json", response_model=dict)
def list_users_json(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    items, total = service.list_users(
        db, firm_id=request.session.get("firm_id"), limit=limit, offset=offset, q=q, is_active=is_active
    )
    return {
        "items": [UserRead.model_validate(u) for u in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/new", response_class=HTMLResponse)
def new_user_form(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    return templates.TemplateResponse(request, "users/form.html", {
        "user_obj": None,
        "action": "/users/new",
        "errors": [],
        "csrf_token": get_csrf_token(request),
        "roles": _grantable_roles(db, request),
    })


@router.post("/new")
async def create_user_form(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    data["email"] = form_data.get("email", "").strip()
    data["display_name"] = form_data.get("display_name", "").strip()
    firm_role = form_data.get("technical_role", "viewer")
    password = form_data.get("password", "").strip()

    if not data["email"]:
        errors.append("Email is required")
    if not data["display_name"]:
        errors.append("Display name is required")
    if password and len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if firm_role not in _grantable_roles(db, request):
        errors.append("You are not allowed to grant this role")

    if not errors:
        try:
            firm_id = request.session.get("firm_id")
            new_user = service.create_user(
                db, data, password=password or None, firm_id=firm_id,
                firm_role=TechnicalRole(firm_role),
            )
            set_flash(request, f"User '{data['display_name']}' created.")
            return RedirectResponse(url="/users", status_code=303)
        except ValidationError as e:
            errors.append(str(e))

    return templates.TemplateResponse(request, "users/form.html", {
        "user_obj": None,
        "action": "/users/new",
        "errors": errors,
        "csrf_token": get_csrf_token(request),
        "roles": _grantable_roles(db, request),
    })


@router.get("/{user_id}/edit", response_class=HTMLResponse)
def edit_user_form(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    user_obj = service.get_user(db, user_id, firm_id=request.session.get("firm_id"))
    _require_can_manage(db, request, user_id)
    return templates.TemplateResponse(request, "users/form.html", {
        "user_obj": user_obj,
        "action": f"/users/{user_id}/edit",
        "errors": [],
        "csrf_token": get_csrf_token(request),
        "roles": _grantable_roles(db, request),
    })


@router.post("/{user_id}/edit")
async def update_user_form(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    user_obj = service.get_user(db, user_id, firm_id=firm_id)
    _require_can_manage(db, request, user_id)

    errors = []
    data = {}
    data["email"] = form_data.get("email", "").strip()
    data["display_name"] = form_data.get("display_name", "").strip()
    firm_role = form_data.get("technical_role")
    password = form_data.get("password", "").strip()

    if not data["email"]:
        errors.append("Email is required")
    if not data["display_name"]:
        errors.append("Display name is required")
    if password and len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if password and service.belongs_to_other_firms(db, user_id, firm_id):
        errors.append("This user also belongs to another firm; they must reset their own password")
    if firm_role and firm_role not in _grantable_roles(db, request):
        errors.append("You are not allowed to grant this role")

    if not errors:
        try:
            service.update_user(db, user_id, data, firm_id=firm_id)
            if password:
                from app.services.auth_service import set_user_password

                set_user_password(db, user_obj, password)
            if firm_role:
                from app.services.firm_service import update_firm_user_role

                update_firm_user_role(db, user_id, firm_id, TechnicalRole(firm_role))
            set_flash(request, f"User '{data['display_name']}' updated.")
            return RedirectResponse(url="/users", status_code=303)
        except ValidationError as e:
            errors.append(str(e))

    return templates.TemplateResponse(request, "users/form.html", {
        "user_obj": service.get_user(db, user_id, firm_id=firm_id),
        "action": f"/users/{user_id}/edit",
        "errors": errors,
        "csrf_token": get_csrf_token(request),
        "roles": _grantable_roles(db, request),
    })


@router.post("/{user_id}/deactivate")
def deactivate_user_form(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(TechnicalRole.admin)),
):
    if user_id == current_user.id:
        set_flash(request, "You cannot deactivate your own account.", "danger")
        return RedirectResponse(url="/users", status_code=303)
    _require_can_manage(db, request, user_id)
    service.soft_delete_user(db, user_id, current_user.id, firm_id=request.session.get("firm_id"))
    set_flash(request, "User deactivated.", "warning")
    return RedirectResponse(url="/users", status_code=303)


@router.post("/{user_id}/restore")
def restore_user_form(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    service.restore_user(db, user_id, firm_id=request.session.get("firm_id"))
    set_flash(request, "User restored.", "success")
    return RedirectResponse(url="/users", status_code=303)


@router.post("", response_model=UserRead, status_code=201)
def create_user_api(
    request: Request,
    data: UserCreate,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    if data.technical_role.value not in _grantable_roles(db, request):
        raise HTTPException(status_code=403, detail="You are not allowed to grant this role")
    firm_id = request.session.get("firm_id")
    result = service.create_user(
        db, data.model_dump(exclude={"technical_role"}), firm_id=firm_id,
        firm_role=data.technical_role,
    )
    return UserRead.model_validate(result)


@router.patch("/{user_id}", response_model=UserRead)
def update_user_api(
    user_id: int,
    data: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    firm_id = request.session.get("firm_id")
    service.get_user(db, user_id, firm_id=firm_id)
    _require_can_manage(db, request, user_id)
    changes = data.model_dump(exclude_unset=True)
    new_role = changes.pop("technical_role", None)
    if new_role is not None and new_role.value not in _grantable_roles(db, request):
        raise HTTPException(status_code=403, detail="You are not allowed to grant this role")
    result = service.update_user(db, user_id, changes, firm_id=firm_id)
    if new_role is not None:
        from app.services.firm_service import update_firm_user_role

        update_firm_user_role(db, user_id, firm_id, new_role)
    return UserRead.model_validate(result)
