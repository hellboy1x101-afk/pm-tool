import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.auth import require_role
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.exceptions import NotFoundError, user_message
from app.flash import set_flash
from app.models.models import TechnicalRole
from app.schemas.schemas import SystemSettingRead, SystemSettingUpdate
from app.services import settings_service as service
from app.templates_setup import templates

router = APIRouter(prefix="/admin/settings", tags=["settings"])


def _render(request, db, saved=False, error="", approval_saved=False, domains_saved=False, auth_saved=False, roles_saved=False, security_saved=False):
    """Render the settings page with common context."""
    firm_id = request.session.get("firm_id")
    all_settings = service.list_all_settings(db, firm_id)

    # Get approval rules
    from app.services.approval_service import list_approval_rules
    rules = list_approval_rules(db, firm_id) if firm_id else []
    approval_rules = {}
    for rule in rules:
        key = f"{rule.resource_type.value}_{rule.operation.value}"
        approval_rules[key] = rule.is_enabled

    # Get firm domains and license info
    firm_domains = ""
    firm = None
    if firm_id:
        from app.services.firm_service import get_firm
        firm = get_firm(db, firm_id)
        firm_domains = firm.allowed_domains or ""
    
    # License info
    from app.services.license_service import check_license
    from app.services.license_tiers import get_tier_limits
    license_status = check_license(firm) if firm else "no_license"
    tier_limits = get_tier_limits(firm.license_tier) if firm and firm.license_tier else None
    
    # Count current users and team members
    from app.models.models import FirmUser, TeamMember
    current_users = 0
    current_team_members = 0
    if firm_id:
        current_users = db.query(FirmUser).filter(FirmUser.firm_id == firm_id, FirmUser.is_active == True).count()
        current_team_members = db.query(TeamMember).filter(TeamMember.firm_id == firm_id, TeamMember.is_active == True).count()
    
    # Auth method
    auth_method = service.get_auth_method(db, firm_id)

    # Password expiry days
    password_expiry_days = service.get_password_expiry_days(db, firm_id)
    
    # Business roles
    from app.models.firm_business_role import FirmBusinessRole
    business_roles = []
    if firm_id:
        business_roles = db.query(FirmBusinessRole).filter(
            FirmBusinessRole.firm_id == firm_id
        ).order_by(FirmBusinessRole.role_code).all()
    
    # Default currency
    default_currency = service.get_setting_value(db, "default_currency", firm_id, "INR")
    
    csrf_token = get_csrf_token(request)
    return templates.TemplateResponse(
        request,
        "admin/settings.html",
        {
            "settings": all_settings,
            "saved": saved,
            "error": error,
            "approval_rules": approval_rules,
            "approval_saved": approval_saved,
            "firm_domains": firm_domains,
            "domains_saved": domains_saved,
            "auth_method": auth_method,
            "auth_saved": auth_saved,
            "roles_saved": roles_saved,
            "csrf_token": csrf_token,
            "firm": firm,
            "license_status": license_status,
            "tier_limits": tier_limits,
            "current_users": current_users,
            "current_team_members": current_team_members,
            "business_roles": business_roles,
            "default_currency": default_currency,
            "password_expiry_days": password_expiry_days,
            "security_saved": security_saved,
        },
    )


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    return _render(request, db)


@router.get("/list", response_model=list[SystemSettingRead])
def list_settings_api(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role(TechnicalRole.admin)),
):
    return [SystemSettingRead.model_validate(s) for s in service.list_all_settings(db, request.session.get("firm_id"))]


@router.post("", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()

    # CSRF validation
    token = form_data.get("csrf_token")
    if not validate_csrf(request, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    all_settings = service.list_all_settings(db, firm_id)
    saved = False
    error = ""

    try:
        for setting in all_settings:
            if setting.key in form_data:
                service.update_setting(
                    db, setting.key, form_data[setting.key], updated_by_user_id=user.id, firm_id=firm_id
                )
        saved = True
    except NotFoundError as e:
        error = str(e)
    except Exception as e:
        error = f"Failed to save settings: {user_message(e, db)}"

    return _render(request, db, saved=saved, error=error)


@router.post("/api", response_model=SystemSettingRead)
def update_setting_api(
    key: str,
    data: SystemSettingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    if service.get_setting(db, key) is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting '{key}'")
    result = service.update_setting(
        db, key, data.value, updated_by_user_id=user.id, firm_id=request.session.get("firm_id"),
    )
    return SystemSettingRead.model_validate(result)


@router.post("/approval", response_class=HTMLResponse)
async def update_approval_rules(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    if not firm_id:
        return _render(request, db, error="No firm selected")

    from app.services.approval_service import upsert_approval_rule
    from app.models.models import ResourceType, OperationType

    resources = ["assignment", "engagement", "client", "team_member", "leave"]
    operations = ["create", "update", "delete"]

    for resource in resources:
        for operation in operations:
            key = f"rule_{resource}_{operation}"
            is_enabled = form_data.get(key) == "on"
            upsert_approval_rule(
                db, firm_id=firm_id,
                resource_type=ResourceType(resource),
                operation=OperationType(operation),
                is_enabled=is_enabled,
            )

    return _render(request, db, approval_saved=True)


@router.post("/domains", response_class=HTMLResponse)
async def update_domains(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    if not firm_id:
        return _render(request, db, error="No firm selected")

    from app.services.firm_service import get_firm
    firm = get_firm(db, firm_id)
    firm.allowed_domains = form_data.get("allowed_domains", "").strip()
    db.commit()

    return _render(request, db, domains_saved=True)


@router.post("/auth-method", response_class=HTMLResponse)
async def update_auth_method(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    if not firm_id:
        return _render(request, db, error="No firm selected")

    auth_method = form_data.get("auth_method", "2fa")
    if auth_method not in ("2fa", "otp"):
        return _render(request, db, error="Invalid authentication method")

    service.set_auth_method(db, auth_method, firm_id, user.id)

    return _render(request, db, auth_saved=True)


@router.post("/business-roles", response_class=HTMLResponse)
async def update_business_roles(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    if not firm_id:
        return _render(request, db, error="No firm selected")

    # Update currency
    currency = form_data.get("currency", "INR")
    service.update_setting(db, "default_currency", currency, user.id, firm_id)

    # Update business roles
    from app.models.firm_business_role import FirmBusinessRole
    
    roles = db.query(FirmBusinessRole).filter(FirmBusinessRole.firm_id == firm_id).all()
    
    for role in roles:
        # Check if enabled
        enabled_key = f"role_{role.role_code}_enabled"
        is_enabled = form_data.get(enabled_key) == "on"
        
        # Get rate type and value
        rate_type_key = f"role_{role.role_code}_rate_type"
        rate_value_key = f"role_{role.role_code}_rate_value"
        
        rate_type = form_data.get(rate_type_key, "daily")
        rate_value_str = form_data.get(rate_value_key, "0")
        
        try:
            rate_value = float(rate_value_str) if rate_value_str else 0
        except ValueError:
            rate_value = 0
        
        # Update role
        role.is_enabled = is_enabled
        if is_enabled:
            role.rate_type = rate_type
            role.rate_value = rate_value
            role.currency = currency
        else:
            role.rate_type = None
            role.rate_value = None
    
    db.commit()

    return _render(request, db, roles_saved=True)


@router.post("/security", response_class=HTMLResponse)
async def update_security(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role(TechnicalRole.admin)),
):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = request.session.get("firm_id")
    if not firm_id:
        return _render(request, db, error="No firm selected")

    days_str = form_data.get("password_expiry_days", "90")
    try:
        days = int(days_str)
    except (ValueError, TypeError):
        days = 90
    days = min(max(days, 0), 90)

    service.set_password_expiry_days(db, days, firm_id, user.id)

    return _render(request, db, security_saved=True)
