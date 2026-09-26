import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user, require_role
from app.config import settings
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.exceptions import NotFoundError, ValidationError, user_message
from app.flash import set_flash
from app.models.models import TechnicalRole, TeamMember
from app.schemas.schemas import (
    BulkUploadError,
    BulkUploadResult,
    BulkUploadRow,
    TeamMemberCreate,
    TeamMemberRead,
    TeamMemberUpdate,
)
from app.services import team_member_service as service
from app.templates_setup import templates

router = APIRouter(prefix="/team-members", tags=["team-members"])


@router.get("", response_class=HTMLResponse)
def list_team_members(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    business_role: Optional[str] = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    items, total = service.list_team_members(
        db, firm_id=firm_id, limit=limit, offset=offset, q=q, is_active=is_active, business_role=business_role
    )
    return templates.TemplateResponse(request, "team_members/list.html", {
        "items": items, "total": total, "limit": limit, "offset": offset, "q": q or "", "is_active": is_active,
        "user": user,
    })


@router.get("/json", response_model=dict)
def list_team_members_json(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    is_active: Optional[bool] = None,
    business_role: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    items, total = service.list_team_members(
        db, firm_id=request.session.get("firm_id"), limit=limit, offset=offset, q=q,
        is_active=is_active, business_role=business_role,
    )
    return {"items": [TeamMemberRead.model_validate(t) for t in items], "total": total, "limit": limit, "offset": offset}


@router.get("/new", response_class=HTMLResponse)
def new_team_member_form(request: Request, _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator))):
    return templates.TemplateResponse(request, "team_members/form.html", {
        "member": None, "action": "/team-members/new", "errors": [], "csrf_token": get_csrf_token(request),
    })


@router.post("/new")
async def create_team_member_form(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    data["firm_id"] = request.session.get("firm_id")
    for key in ["name", "email", "employee_code", "business_role", "seniority_level", "date_of_joining", "date_of_relieving"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val
    data["is_oversight_only"] = form_data.get("is_oversight_only") == "true"

    try:
        # Check if approval is required
        from app.approval_check import check_approval
        from app.models.models import ResourceType, OperationType
        firm_id = request.session.get("firm_id")
        result = check_approval(db, firm_id, user.id, ResourceType.team_member, OperationType.create, data)
        if result:
            set_flash(request, "Team member creation submitted for admin approval.")
            referer = request.headers.get("referer", "/team-members")
            return RedirectResponse(url=referer, status_code=303)

        service.create_team_member(db, data, firm_id=firm_id)
        set_flash(request, f"Team member '{data['name']}' created successfully.")
        return RedirectResponse(url="/team-members", status_code=303)
    except ValidationError as e:
        errors.append(user_message(e, db))
    except Exception as e:
        errors.append(user_message(e, db))

    return templates.TemplateResponse(request, "team_members/form.html", {
        "member": None, "action": "/team-members/new", "errors": errors, "csrf_token": get_csrf_token(request),
    })


@router.get("/{member_id}", response_class=HTMLResponse)
def team_member_detail(
    request: Request, member_id: int,
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    firm_id = request.session.get("firm_id")
    member = service.get_team_member(db, member_id, firm_id=firm_id)
    from app.services import allocation_service
    assignments, _ = allocation_service.list_assignments(db, firm_id=firm_id, limit=200, team_member_id=member_id)
    from app.services import leave_service
    leaves_list, _ = leave_service.list_leaves(db, firm_id=firm_id, limit=200, team_member_id=member_id)

    return templates.TemplateResponse(request, "team_members/detail.html", {
        "member": member, "assignments": assignments, "leaves_list": leaves_list,
        "csrf_token": get_csrf_token(request),
    })


@router.get("/{member_id}/edit", response_class=HTMLResponse)
def edit_team_member_form(
    request: Request, member_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    member = service.get_team_member(db, member_id, firm_id=request.session.get("firm_id"))
    return templates.TemplateResponse(request, "team_members/form.html", {
        "member": member, "action": f"/team-members/{member_id}/edit", "errors": [], "csrf_token": get_csrf_token(request),
    })


@router.post("/{member_id}/edit")
async def update_team_member_form(
    request: Request, member_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    errors = []
    data = {}
    for key in ["name", "email", "employee_code", "business_role", "seniority_level", "date_of_joining", "date_of_relieving"]:
        val = form_data.get(key, "").strip()
        if val:
            data[key] = val
    data["is_oversight_only"] = form_data.get("is_oversight_only") == "true"
    # Only set date_of_relieving if provided (don't clear it accidentally)
    if "date_of_relieving" not in data and "date_of_relieving" in form_data:
        data["date_of_relieving"] = form_data.get("date_of_relieving") or None

    firm_id = request.session.get("firm_id")
    try:
        service.update_team_member(db, member_id, data, firm_id=firm_id)
        set_flash(request, "Team member updated successfully.")
        return RedirectResponse(url=f"/team-members/{member_id}", status_code=303)
    except Exception as e:
        errors.append(user_message(e, db))

    member = service.get_team_member(db, member_id, firm_id=firm_id)
    return templates.TemplateResponse(request, "team_members/form.html", {
        "member": member, "action": f"/team-members/{member_id}/edit", "errors": errors, "csrf_token": get_csrf_token(request),
    })


@router.post("/{member_id}/deactivate")
def deactivate_team_member(
    request: Request, member_id: int,
    db: Session = Depends(get_db), _=Depends(require_role(TechnicalRole.admin)),
):
    service.soft_delete_team_member(db, member_id, firm_id=request.session.get("firm_id"))
    set_flash(request, "Team member deactivated.", "warning")
    return RedirectResponse(url=f"/team-members/{member_id}", status_code=303)


@router.post("", response_model=TeamMemberRead, status_code=201)
def create_team_member_api(
    data: TeamMemberCreate,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.create_team_member(db, data.model_dump(), firm_id=request.session.get("firm_id"))
    return TeamMemberRead.model_validate(result)


@router.patch("/{member_id}", response_model=TeamMemberRead)
def update_team_member_api(
    member_id: int,
    data: TeamMemberUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    result = service.update_team_member(
        db, member_id, data.model_dump(exclude_unset=True), firm_id=request.session.get("firm_id"),
    )
    return TeamMemberRead.model_validate(result)


@router.post("/bulk-upload")
def bulk_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    if not file.content_type or file.content_type not in (
        "text/csv", "application/vnd.ms-excel", "application/octet-stream",
    ):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted")

    contents = file.file.read()
    max_bytes = settings.CSV_MAX_FILE_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File exceeds {settings.CSV_MAX_FILE_SIZE_MB}MB")

    from app.services.settings_service import get_setting_value
    max_rows_str = get_setting_value(
        db, "bulk_upload_max_rows", request.session.get("firm_id"), default=str(settings.BULK_UPLOAD_MAX_ROWS),
    )
    max_rows = int(max_rows_str)

    reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig")))
    rows = list(reader)
    if len(rows) > max_rows:
        raise HTTPException(status_code=400, detail=f"File has {len(rows)} rows, max is {max_rows}")

    inserted = 0
    failed = 0
    errors = []
    seen_emails = set()

    for idx, row in enumerate(rows, start=2):
        try:
            email = row.get("email", "").strip()
            if not email:
                errors.append(BulkUploadError(row=idx, field="email", reason="email is required"))
                failed += 1
                continue
            if email in seen_emails:
                errors.append(BulkUploadError(row=idx, field="email", reason="duplicate email in file"))
                failed += 1
                continue

            validated = BulkUploadRow(**{k.strip(): v.strip() for k, v in row.items()})
            seen_emails.add(validated.email)
            service.create_team_member(db, validated.model_dump(), firm_id=request.session.get("firm_id"))
            inserted += 1
        except ValidationError as e:
            errors.append(BulkUploadError(row=idx, field="", reason=user_message(e, db)))
            failed += 1
        except Exception as e:
            errors.append(BulkUploadError(row=idx, field="", reason=user_message(e, db)))
            failed += 1

    msg = f"Bulk upload: {inserted} inserted, {failed} failed."
    set_flash(request, msg, "success" if failed == 0 else "warning")
    return RedirectResponse(url="/team-members", status_code=303)


@router.post("/bulk-deactivate")
def bulk_deactivate(
    member_ids: list[int],
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    count = service.bulk_deactivate(db, member_ids, firm_id=request.session.get("firm_id"))
    return {"deactivated": count}


@router.post("/{member_id}/extend")
async def create_extension_form(
    request: Request, member_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin, TechnicalRole.moderator)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    from app.services.extension_service import create_extension_request
    from datetime import date as _date

    try:
        ext = create_extension_request(
            db=db,
            firm_id=firm_id,
            user_id=user.id,
            team_member_id=member_id,
            engagement_instance_id=int(form_data["engagement_instance_id"]),
            allocation_percent=int(form_data["allocation_percent"]),
            start_date=_date.today(),
            end_date=_date.fromisoformat(form_data["end_date"]),
            role_on_engagement=form_data.get("role_on_engagement", "").strip() or None,
            reason=form_data.get("reason", "").strip() or None,
        )
        set_flash(request, "Extension request submitted for approval")
    except Exception as e:
        set_flash(request, user_message(e, db), "danger")

    return RedirectResponse(url=f"/team-members/{member_id}", status_code=303)
