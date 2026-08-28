"""Auth router — password login, OTP, 2FA, MS365 OAuth, password change."""

import logging
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user
from app.config import settings
from app.csrf_utils import get_csrf_token, validate_csrf
from app.database import get_db
from app.flash import set_flash
from app.models.models import User, Firm
from app.services.auth_service import (
    authenticate_user,
    disable_totp,
    enable_totp,
    generate_totp_secret,
    get_ms365_oauth_client,
    get_totp_uri,
    set_user_password,
    verify_totp,
)
from app.templates_setup import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session(request: Request, user: User, firm_id: int | None = None, remember_me: bool = False, db: Session = None) -> None:
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_name"] = user.display_name
    
    # Set session expiry based on remember me
    if remember_me:
        request.session["remember_me"] = True
    
    if firm_id:
        request.session["firm_id"] = firm_id
        # Get role and firm name from FirmUser
        try:
            if db is None:
                from app.services.firm_service import get_user_role_in_firm, get_firm
                from app.database import SessionLocal
                db = SessionLocal()
                should_close = True
            else:
                from app.services.firm_service import get_user_role_in_firm, get_firm
                should_close = False
            
            role = get_user_role_in_firm(db, user.id, firm_id)
            request.session["user_role"] = role.value if role else "viewer"
            firm = get_firm(db, firm_id)
            request.session["firm_name"] = firm.name
            
            if should_close:
                db.close()
        except Exception:
            request.session["user_role"] = "admin"  # Default for new firms
            request.session["firm_name"] = "My Firm"
    else:
        request.session["user_role"] = "viewer"
        request.session.pop("firm_name", None)


# ── Registration ──

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: str = "", step: str = "1"):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "auth/register.html", {
        "csrf_token": get_csrf_token(request),
        "error": error,
        "step": step,
    })


@router.post("/register/check-domain")
async def register_check_domain(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    email = form_data.get("email", "").strip().lower()
    if not email or "@" not in email:
        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Please enter a valid email address",
            "step": "1",
        })

    # Check if user already exists
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": "An account with this email already exists. Please sign in.",
            "step": "1",
        })

    # Find firm by domain
    from app.services.otp_service import find_firm_by_domain
    matched_firm, reason = find_firm_by_domain(db, email)

    # Store email in session
    request.session["pending_email"] = email

    if matched_firm:
        # Domain matches existing firm - show join or create option
        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": "",
            "step": "1b",
            "email": email,
            "matched_firm": matched_firm,
        })
    else:
        # No firm found - will create new firm
        # Generate OTP for email verification
        from app.services.otp_service import generate_otp
        otp = generate_otp(db, email)
        logger.info(f"OTP for {email}: {otp}")

        request.session["pending_registration"] = {
            "email": email,
            "action": "create_firm",
            "totp_secret": None,
        }

        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": "",
            "step": "2",
            "email": email,
            "otp_sent": True,
        })


@router.post("/register/select-firm")
async def register_select_firm(request: Request, db: Session = Depends(get_db)):
    """User chooses to join existing firm or create new one."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    action = form_data.get("action")  # "join" or "create"
    email = request.session.get("pending_email")

    if not email:
        return RedirectResponse(url="/auth/register", status_code=303)

    # Generate OTP
    from app.services.otp_service import generate_otp
    otp = generate_otp(db, email)
    logger.info(f"OTP for {email}: {otp}")

    if action == "join":
        # Join existing firm
        firm_id = form_data.get("firm_id")
        from app.models.models import Firm
        firm = db.query(Firm).filter(Firm.id == firm_id).first()
        
        request.session["pending_registration"] = {
            "email": email,
            "action": "join_firm",
            "firm_id": firm_id,
            "firm_name": firm.name if firm else "Unknown",
            "totp_secret": None,
        }
    else:
        # Create new firm
        request.session["pending_registration"] = {
            "email": email,
            "action": "create_firm",
            "totp_secret": None,
        }

    return templates.TemplateResponse(request, "auth/register.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "step": "2",
        "email": email,
        "otp_sent": True,
    })


@router.post("/register/verify-otp")
async def register_verify_otp(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    otp = form_data.get("otp", "").strip()
    pending = request.session.get("pending_registration")

    if not pending:
        return RedirectResponse(url="/auth/register", status_code=303)

    # Verify email OTP
    from app.services.otp_service import verify_otp
    if not verify_otp(db, pending["email"], otp):
        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid or expired OTP. Please try again.",
            "step": "2",
            "email": pending["email"],
            "firm_name": pending.get("firm_name", ""),
            "otp_sent": True,
        })

    # OTP verified — show password form
    request.session["otp_verified"] = True
    return templates.TemplateResponse(request, "auth/register.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "step": "3",
        "email": pending["email"],
        "firm_name": pending.get("firm_name", ""),
    })


@router.post("/register/resend-otp")
async def register_resend_otp(request: Request, db: Session = Depends(get_db)):
    """Resend OTP with rate limiting."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    pending = request.session.get("pending_registration")
    if not pending:
        return RedirectResponse(url="/auth/register", status_code=303)

    email = pending["email"]

    # Check rate limit
    from app.services.otp_service import can_resend_otp, generate_otp
    can_send, seconds_remaining = can_resend_otp(db, email)

    if not can_send:
        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": f"Please wait {seconds_remaining} seconds before requesting a new code.",
            "step": "2",
            "email": email,
            "firm_name": pending.get("firm_name", ""),
            "otp_sent": True,
            "resend_cooldown": seconds_remaining,
        })

    # Generate new OTP
    try:
        generate_otp(db, email)
    except ValueError as e:
        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": str(e),
            "step": "2",
            "email": email,
            "firm_name": pending.get("firm_name", ""),
            "otp_sent": True,
        })

    return templates.TemplateResponse(request, "auth/register.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "step": "2",
        "email": email,
        "firm_name": pending.get("firm_name", ""),
        "otp_sent": True,
        "otp_resent": True,
        "resend_cooldown": 60,
    })


@router.post("/register/complete")
async def register_complete(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    pending = request.session.get("pending_registration")
    if not pending or not request.session.get("otp_verified"):
        return RedirectResponse(url="/auth/register", status_code=303)

    display_name = form_data.get("display_name", "").strip()
    password = form_data.get("password", "")
    confirm = form_data.get("confirm_password", "")
    terms = form_data.get("terms")

    errors = []
    if not display_name:
        errors.append("Name is required")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if password != confirm:
        errors.append("Passwords do not match")
    if not terms:
        errors.append("You must agree to the Terms of Service and Privacy Policy")

    if errors:
        return templates.TemplateResponse(request, "auth/register.html", {
            "csrf_token": get_csrf_token(request),
            "error": " / ".join(errors),
            "step": "3",
            "email": pending["email"],
            "firm_name": pending.get("firm_name", ""),
        })

    # Create user (without 2FA initially)
    from app.services.auth_service import hash_password
    from app.services.firm_service import add_user_to_firm, create_firm_with_trial
    from app.models.models import TechnicalRole, Firm
    from datetime import datetime, timezone

    user = User(
        email=pending["email"],
        display_name=display_name,
        password_hash=hash_password(password),
        totp_secret=None,
        totp_enabled=False,
        password_changed_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    action = pending.get("action", "join_firm")
    firm_id = None
    firm_name = None

    if action == "create_firm":
        # Create new firm with trial
        firm = create_firm_with_trial(db, user, pending["email"])
        firm_id = firm.id
        firm_name = firm.name
    else:
        # Join existing firm
        firm_id = pending.get("firm_id")
        firm_name = pending.get("firm_name", "Unknown")
        if firm_id:
            add_user_to_firm(db, user.id, firm_id, TechnicalRole.viewer)

    # Clean up session
    request.session.pop("pending_registration", None)
    request.session.pop("otp_verified", None)
    request.session.pop("pending_email", None)

    # Check if firm has 2FA enabled in settings
    from app.services.settings_service import get_auth_method
    auth_method = get_auth_method(db, firm_id) if firm_id else "otp"

    if auth_method == "2fa":
        # Prompt user to setup 2FA (with skip option)
        request.session["pending_2fa_setup_user_id"] = user.id
        request.session["pending_2fa_firm_id"] = firm_id
        return RedirectResponse(url="/auth/2fa-setup-optional", status_code=303)

    # Auto-login without 2FA
    _set_session(request, user, firm_id)
    
    if action == "create_firm":
        set_flash(request, f"Welcome to splanly! Your firm '{firm_name}' has been created with a 14-day trial.")
    else:
        set_flash(request, f"Welcome to {firm_name}, {display_name}!")
    
    return RedirectResponse(url="/dashboard", status_code=303)


# ── Login ──

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    # Redirect to home if already logged in
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    ms365 = get_ms365_oauth_client()
    return templates.TemplateResponse(request, "auth/login.html", {
        "csrf_token": get_csrf_token(request),
        "error": error,
        "ms365_enabled": ms365 is not None,
    })


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    email = form_data.get("email", "").strip().lower()
    password = form_data.get("password", "")
    remember_me = form_data.get("remember_me") == "on"

    if not email or not password:
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Email and password are required",
            "ms365_enabled": get_ms365_oauth_client() is not None,
        })

    user = authenticate_user(db, email, password)
    if not user:
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid email or password",
            "ms365_enabled": get_ms365_oauth_client() is not None,
        })

    # Get user's firms and auth method
    from app.services.firm_service import get_user_firms, get_firm_user
    from app.services.settings_service import get_auth_method
    firms = get_user_firms(db, user.id)
    firm_id = firms[0].id if firms else None
    auth_method = get_auth_method(db, firm_id)

    # If user has 2FA enabled, redirect to 2FA verification
    if user.totp_enabled:
        request.session["pending_2fa_user_id"] = user.id
        request.session["remember_me"] = remember_me
        return RedirectResponse(url="/auth/2fa", status_code=303)

    # If 2FA required by firm and user doesn't have it, prompt setup
    if auth_method == "2fa" and not user.totp_enabled:
        request.session["pending_2fa_setup_user_id"] = user.id
        request.session["pending_2fa_firm_id"] = firm_id
        request.session["remember_me"] = remember_me
        return RedirectResponse(url="/auth/2fa-setup-optional", status_code=303)

    # For OTP method or if 2FA not required - send OTP for verification
    from app.services.otp_service import generate_otp
    otp = generate_otp(db, email)
    logger.info(f"Login OTP for {email}: {otp}")

    request.session["pending_login_email"] = email
    request.session["remember_me"] = remember_me
    return RedirectResponse(url="/auth/login-verify", status_code=303)


@router.get("/login-verify", response_class=HTMLResponse)
def login_verify_page(request: Request, error: str = ""):
    email = request.session.get("pending_login_email")
    if not email:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    return templates.TemplateResponse(request, "auth/login_verify.html", {
        "csrf_token": get_csrf_token(request),
        "error": error,
        "email": email,
    })


@router.post("/login-verify")
async def login_verify(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    otp = form_data.get("otp", "").strip()
    email = request.session.get("pending_login_email")
    remember_me = request.session.get("remember_me", False)

    if not email:
        return RedirectResponse(url="/auth/login", status_code=303)

    # Verify OTP
    from app.services.otp_service import verify_otp
    if not verify_otp(db, email, otp):
        return templates.TemplateResponse(request, "auth/login_verify.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid or expired OTP. Please try again.",
            "email": email,
        })

    # OTP verified - login user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)

    # Clean up session
    request.session.pop("pending_login_email", None)
    request.session.pop("remember_me", None)

    # Get user's firms
    from app.services.firm_service import get_user_firms, get_firm_user
    firms = get_user_firms(db, user.id)

    if len(firms) == 0:
        # User has no firm - show error
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Your account is not associated with any firm. Please contact your administrator.",
            "ms365_enabled": get_ms365_oauth_client() is not None,
        })

    if len(firms) == 1:
        firm_id = firms[0].id
        _set_session(request, user, firm_id, remember_me)
        set_flash(request, f"Welcome back, {user.display_name}!")
        return RedirectResponse(url="/dashboard", status_code=303)

    # Multiple firms — show firm selector
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_name"] = user.display_name
    # Store firm_users data for the selector page (plain dicts only, no ORM objects)
    firm_users = []
    for f in firms:
        fu = get_firm_user(db, user.id, f.id)
        if fu:
            firm_users.append({"firm_id": f.id, "role": fu.technical_role.value, "firm_name": f.name})
    request.session["pending_firm_users"] = firm_users
    return RedirectResponse(url="/auth/firm-select", status_code=303)


@router.post("/login/resend-otp")
async def login_resend_otp(request: Request, db: Session = Depends(get_db)):
    """Resend login OTP with rate limiting."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    email = request.session.get("pending_login_email")
    if not email:
        return RedirectResponse(url="/auth/login", status_code=303)

    # Check rate limit
    from app.services.otp_service import can_resend_otp, generate_otp
    can_send, seconds_remaining = can_resend_otp(db, email)

    if not can_send:
        return templates.TemplateResponse(request, "auth/login_verify.html", {
            "csrf_token": get_csrf_token(request),
            "error": f"Please wait {seconds_remaining} seconds before requesting a new code.",
            "email": email,
            "resend_cooldown": seconds_remaining,
        })

    # Generate new OTP
    try:
        generate_otp(db, email)
        logger.info(f"Resent login OTP for {email}")
    except ValueError as e:
        return templates.TemplateResponse(request, "auth/login_verify.html", {
            "csrf_token": get_csrf_token(request),
            "error": str(e),
            "email": email,
        })

    return templates.TemplateResponse(request, "auth/login_verify.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "email": email,
        "otp_resent": True,
        "resend_cooldown": 60,
    })


# ── 2FA ──

@router.get("/2fa", response_class=HTMLResponse)
def twofa_page(request: Request):
    if not request.session.get("pending_2fa_user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    return templates.TemplateResponse(request, "auth/2fa.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
    })


@router.post("/2fa")
async def twofa_verify(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    user_id = request.session.get("pending_2fa_user_id")
    remember_me = request.session.get("remember_me", False)
    if not user_id:
        return RedirectResponse(url="/auth/login", status_code=303)

    code = form_data.get("code", "").strip()
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.totp_secret:
        return RedirectResponse(url="/auth/login", status_code=303)

    if not verify_totp(user.totp_secret, code):
        return templates.TemplateResponse(request, "auth/2fa.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid code. Try again.",
        })

    request.session.pop("pending_2fa_user_id", None)
    request.session.pop("remember_me", None)
    
    from app.services.firm_service import get_user_firms
    firms = get_user_firms(db, user.id)
    firm_id = firms[0].id if firms else None
    _set_session(request, user, firm_id, remember_me)
    set_flash(request, f"Welcome back, {user.display_name}!")
    return RedirectResponse(url="/dashboard", status_code=303)


# ── 2FA Setup (Optional - with Skip) ──

@router.get("/2fa-setup-optional", response_class=HTMLResponse)
def twofa_setup_optional_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("pending_2fa_setup_user_id")
    if not user_id:
        return RedirectResponse(url="/auth/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)

    # If already has 2FA, skip this page
    if user.totp_enabled:
        request.session.pop("pending_2fa_setup_user_id", None)
        request.session.pop("pending_2fa_firm_id", None)
        return RedirectResponse(url="/dashboard", status_code=303)

    # Generate TOTP secret
    from app.services.auth_service import generate_totp_secret, get_totp_uri
    totp_secret = generate_totp_secret()
    totp_uri = get_totp_uri(totp_secret, user.email)

    request.session["pending_2fa_secret"] = totp_secret

    return templates.TemplateResponse(request, "auth/2fa_setup_optional.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "totp_uri": totp_uri,
        "totp_secret": totp_secret,
        "email": user.email,
    })


@router.post("/2fa-setup-optional", response_class=HTMLResponse)
async def twofa_setup_optional_verify(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    user_id = request.session.get("pending_2fa_setup_user_id")
    firm_id = request.session.get("pending_2fa_firm_id")
    remember_me = request.session.get("remember_me", False)

    if not user_id:
        return RedirectResponse(url="/auth/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)

    # Check if user clicked "Skip"
    action = form_data.get("action", "setup")

    if action == "skip":
        # Skip 2FA setup, go to dashboard
        request.session.pop("pending_2fa_setup_user_id", None)
        request.session.pop("pending_2fa_firm_id", None)
        request.session.pop("pending_2fa_secret", None)
        request.session.pop("remember_me", None)

        _set_session(request, user, firm_id, remember_me, db=db)
        set_flash(request, f"Welcome, {user.display_name}! You can enable 2FA later in settings.")
        return RedirectResponse(url="/dashboard", status_code=303)

    # Setup 2FA
    code = form_data.get("code", "").strip()
    totp_secret = request.session.get("pending_2fa_secret")

    if not totp_secret:
        return RedirectResponse(url="/auth/login", status_code=303)

    from app.services.auth_service import verify_totp
    if not verify_totp(totp_secret, code):
        from app.services.auth_service import get_totp_uri
        totp_uri = get_totp_uri(totp_secret, user.email)
        return templates.TemplateResponse(request, "auth/2fa_setup_optional.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid code. Please try again.",
            "totp_uri": totp_uri,
            "totp_secret": totp_secret,
            "email": user.email,
        })

    # Enable 2FA for user
    user.totp_secret = totp_secret
    user.totp_enabled = True
    db.commit()

    # Clean up session
    request.session.pop("pending_2fa_setup_user_id", None)
    request.session.pop("pending_2fa_firm_id", None)
    request.session.pop("pending_2fa_secret", None)
    request.session.pop("remember_me", None)

    _set_session(request, user, firm_id, remember_me, db=db)
    set_flash(request, f"2FA enabled! Welcome, {user.display_name}.")
    return RedirectResponse(url="/dashboard", status_code=303)


# ── MS365 OAuth ──

@router.get("/ms365")
async def ms365_login(request: Request):
    ms365 = get_ms365_oauth_client()
    if not ms365:
        raise HTTPException(status_code=501, detail="MS365 OAuth not configured")
    redirect_uri = settings.MS365_REDIRECT_URI
    return await ms365.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def ms365_callback(request: Request, db: Session = Depends(get_db)):
    ms365 = get_ms365_oauth_client()
    if not ms365:
        logger.error("MS365 OAuth callback called but OAuth is not configured")
        raise HTTPException(status_code=501, detail="MS365 OAuth not configured")

    try:
        token = await ms365.authorize_access_token(request)
    except Exception as e:
        logger.error(f"MS365 OAuth token exchange failed: {e}")
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Authentication failed. Please try again.",
            "ms365_enabled": True,
        })

    if not token:
        logger.warning("MS365 OAuth returned empty token")
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Authentication failed. Please try again.",
            "ms365_enabled": True,
        })

    claims = token.get("userinfo") or {}
    if not claims:
        # Fetch user info manually if not in token
        try:
            resp = await ms365.get("https://graph.microsoft.com/v1.0/me", token=token)
            if resp.status_code == 200:
                profile = resp.json()
                claims = {
                    "oid": profile.get("id"),
                    "email": profile.get("mail") or profile.get("userPrincipalName"),
                    "name": profile.get("displayName"),
                }
            else:
                logger.warning(f"MS365 Graph API returned status {resp.status_code}")
                return templates.TemplateResponse(request, "auth/login.html", {
                    "csrf_token": get_csrf_token(request),
                    "error": "Could not fetch profile. Please try again.",
                    "ms365_enabled": True,
                })
        except Exception as e:
            logger.error(f"MS365 Graph API error: {e}")
            return templates.TemplateResponse(request, "auth/login.html", {
                "csrf_token": get_csrf_token(request),
                "error": "Authentication failed. Please try again.",
                "ms365_enabled": True,
            })

    email = claims.get("email")
    if not email:
        logger.warning(f"MS365 OAuth returned no email. Claims: {claims}")
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Could not get email from Microsoft. Please try again.",
            "ms365_enabled": True,
        })

    from app.services.auth_service import find_or_create_oauth_user
    user = find_or_create_oauth_user(db, claims)
    if not user:
        logger.error(f"find_or_create_oauth_user returned None for email={email}")
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Could not create account. Please contact support.",
            "ms365_enabled": True,
        })

    from app.services.firm_service import get_user_firms
    firms = get_user_firms(db, user.id)
    
    if len(firms) == 0:
        # User has no firm - show error
        return templates.TemplateResponse(request, "auth/login.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Your account is not associated with any firm. Please contact your administrator.",
            "ms365_enabled": True,
        })
    
    firm_id = firms[0].id

    # Check if 2FA is required
    from app.services.settings_service import get_auth_method
    auth_method = get_auth_method(db, firm_id)

    if auth_method == "2fa" and not user.totp_enabled:
        request.session["pending_2fa_setup_user_id"] = user.id
        request.session["pending_2fa_firm_id"] = firm_id
        return RedirectResponse(url="/auth/2fa-setup-optional", status_code=303)

    _set_session(request, user, firm_id)
    set_flash(request, f"Welcome, {user.display_name}!")
    return RedirectResponse(url="/dashboard", status_code=303)


# ── Accept Invitation ──

@router.get("/accept-invitation", response_class=HTMLResponse)
def accept_invitation_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    """Show accept invitation page."""
    if not token:
        return RedirectResponse(url="/auth/login", status_code=303)
    
    from app.services.invitation_service import get_invitation_by_token
    invitation = get_invitation_by_token(db, token)
    
    if not invitation:
        return templates.TemplateResponse(request, "auth/accept_invitation.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid or expired invitation",
            "token": token,
            "firm_name": "Unknown",
            "role": "viewer",
        })
    
    # Get firm name
    firm = db.query(Firm).filter(Firm.id == invitation.firm_id).first()
    firm_name = firm.name if firm else "Unknown Firm"
    
    return templates.TemplateResponse(request, "auth/accept_invitation.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "token": token,
        "firm_name": firm_name,
        "role": invitation.role,
    })


@router.post("/accept-invitation", response_class=HTMLResponse)
async def accept_invitation_submit(request: Request, db: Session = Depends(get_db)):
    """Accept invitation and create account."""
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    token = form_data.get("token", "")
    display_name = form_data.get("display_name", "").strip()
    password = form_data.get("password", "")
    confirm_password = form_data.get("confirm_password", "")
    
    # Get invitation
    from app.services.invitation_service import get_invitation_by_token, accept_invitation
    invitation = get_invitation_by_token(db, token)
    
    if not invitation:
        return templates.TemplateResponse(request, "auth/accept_invitation.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid or expired invitation",
            "token": token,
            "firm_name": "Unknown",
            "role": "viewer",
        })
    
    # Get firm name
    firm = db.query(Firm).filter(Firm.id == invitation.firm_id).first()
    firm_name = firm.name if firm else "Unknown Firm"
    
    # Validate
    errors = []
    if not display_name:
        errors.append("Name is required")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if password != confirm_password:
        errors.append("Passwords do not match")
    
    if errors:
        return templates.TemplateResponse(request, "auth/accept_invitation.html", {
            "csrf_token": get_csrf_token(request),
            "error": " / ".join(errors),
            "token": token,
            "firm_name": firm_name,
            "role": invitation.role,
            "display_name": display_name,
        })
    
    # Accept invitation
    result = accept_invitation(db, token, display_name, password)
    
    if not result["success"]:
        return templates.TemplateResponse(request, "auth/accept_invitation.html", {
            "csrf_token": get_csrf_token(request),
            "error": result["message"],
            "token": token,
            "firm_name": firm_name,
            "role": invitation.role,
        })
    
    return templates.TemplateResponse(request, "auth/accept_invitation.html", {
        "csrf_token": get_csrf_token(request),
        "success": True,
        "message": result["message"],
        "token": token,
        "firm_name": firm_name,
        "role": invitation.role,
    })


# ── Forgot Password (OTP-based) ──

@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request, error: str = "", step: str = "1"):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request, "auth/forgot_password.html", {
        "csrf_token": get_csrf_token(request),
        "error": error,
        "step": step,
    })


@router.post("/forgot-password")
async def forgot_password(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    email = form_data.get("email", "").strip().lower()
    if not email or "@" not in email:
        return templates.TemplateResponse(request, "auth/forgot_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Please enter a valid email address",
            "step": "1",
        })

    user = db.query(User).filter(User.email == email, User.is_active).first()
    if not user:
        return templates.TemplateResponse(request, "auth/forgot_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "No account found with this email address",
            "step": "1",
        })

    from app.services.otp_service import generate_otp
    generate_otp(db, email, purpose="password_reset")

    request.session["pending_reset_email"] = email
    return templates.TemplateResponse(request, "auth/forgot_password.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "step": "2",
        "email": email,
        "resend_cooldown": 60,
    })


@router.get("/forgot-password/verify", response_class=HTMLResponse)
def forgot_password_verify_page(request: Request, error: str = ""):
    email = request.session.get("pending_reset_email")
    if not email:
        return RedirectResponse(url="/auth/forgot-password", status_code=303)
    return templates.TemplateResponse(request, "auth/forgot_password.html", {
        "csrf_token": get_csrf_token(request),
        "error": error,
        "step": "2",
        "email": email,
    })


@router.post("/forgot-password/verify")
async def forgot_password_verify(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    email = request.session.get("pending_reset_email")
    if not email:
        return RedirectResponse(url="/auth/forgot-password", status_code=303)

    otp = form_data.get("otp", "").strip()
    new_password = form_data.get("new_password", "")
    confirm_password = form_data.get("confirm_password", "")

    from app.services.otp_service import verify_otp
    if not verify_otp(db, email, otp, purpose="password_reset"):
        return templates.TemplateResponse(request, "auth/forgot_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Invalid or expired OTP. Please try again.",
            "step": "2",
            "email": email,
        })

    if len(new_password) < 8:
        return templates.TemplateResponse(request, "auth/forgot_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Password must be at least 8 characters",
            "step": "2",
            "email": email,
            "otp_verified": True,
        })

    if new_password != confirm_password:
        return templates.TemplateResponse(request, "auth/forgot_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Passwords do not match",
            "step": "2",
            "email": email,
            "otp_verified": True,
        })

    user = db.query(User).filter(User.email == email, User.is_active).first()
    if not user:
        return RedirectResponse(url="/auth/forgot-password", status_code=303)

    from app.services.auth_service import hash_password
    from datetime import datetime, timezone
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()

    request.session.pop("pending_reset_email", None)
    request.session.pop("pending_reset_otp_verified", None)

    return templates.TemplateResponse(request, "auth/forgot_password.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "step": "3",
    })


@router.post("/forgot-password/resend-otp")
async def forgot_password_resend_otp(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    email = request.session.get("pending_reset_email")
    if not email:
        return RedirectResponse(url="/auth/forgot-password", status_code=303)

    from app.services.otp_service import can_resend_otp, generate_otp
    can_send, seconds_remaining = can_resend_otp(db, email, purpose="password_reset")

    if not can_send:
        return templates.TemplateResponse(request, "auth/forgot_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": f"Please wait {seconds_remaining} seconds before requesting a new code.",
            "step": "2",
            "email": email,
            "resend_cooldown": seconds_remaining,
        })

    try:
        generate_otp(db, email, purpose="password_reset")
    except ValueError as e:
        return templates.TemplateResponse(request, "auth/forgot_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": str(e),
            "step": "2",
            "email": email,
        })

    return templates.TemplateResponse(request, "auth/forgot_password.html", {
        "csrf_token": get_csrf_token(request),
        "error": "",
        "step": "2",
        "email": email,
        "otp_resent": True,
        "resend_cooldown": 60,
    })


# ── Password Change ──

@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, error: str = "", success: bool = False, forced: str = ""):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    return templates.TemplateResponse(request, "auth/change_password.html", {
        "csrf_token": get_csrf_token(request),
        "error": error,
        "success": success,
        "forced": forced == "1",
    })


@router.post("/change-password")
async def change_password(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)

    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    forced = form_data.get("forced") == "1"
    current_password = form_data.get("current_password", "")
    new_password = form_data.get("new_password", "")
    confirm_password = form_data.get("confirm_password", "")

    user = db.query(User).filter(User.id == request.session["user_id"]).first()
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)

    if not authenticate_user(db, user.email, current_password):
        return templates.TemplateResponse(request, "auth/change_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Current password is incorrect",
            "forced": forced,
        })

    if len(new_password) < 8:
        return templates.TemplateResponse(request, "auth/change_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "New password must be at least 8 characters",
            "forced": forced,
        })

    if new_password != confirm_password:
        return templates.TemplateResponse(request, "auth/change_password.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Passwords do not match",
            "forced": forced,
        })

    from app.services.auth_service import hash_password
    from datetime import datetime, timezone
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    db.commit()

    if forced:
        set_flash(request, "Password updated successfully. You can now continue.")
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(request, "auth/change_password.html", {
        "csrf_token": get_csrf_token(request),
        "success": True,
    })


# ── Logout ──

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)


# ── Firm Select ──

@router.get("/firm-select", response_class=HTMLResponse)
def firm_select_page(request: Request, error: str = ""):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/auth/login", status_code=303)
    firm_users = request.session.get("pending_firm_users", [])
    return templates.TemplateResponse(request, "auth/firm_select.html", {
        "csrf_token": get_csrf_token(request),
        "error": error,
        "firm_users": firm_users,
    })


@router.post("/firm-select")
async def firm_select(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    if not validate_csrf(request, form_data.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    firm_id = form_data.get("firm_id")
    if not firm_id:
        return templates.TemplateResponse(request, "auth/firm_select.html", {
            "csrf_token": get_csrf_token(request),
            "error": "Please select a firm",
            "firm_users": request.session.get("pending_firm_users", []),
        })

    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/auth/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)

    from app.services.firm_service import get_firm
    firm = get_firm(db, int(firm_id))

    _set_session(request, user, int(firm_id))
    request.session.pop("pending_firm_users", None)
    set_flash(request, f"Welcome to {firm.name}, {user.display_name}!")
    return RedirectResponse(url="/dashboard", status_code=303)
