"""Dev Portal router — standalone developer control plane at /dev-portal/.

Auth flow: access code gate → username/password → OTP → TOTP 2FA → dashboard.
Credentials hardcoded (single developer). Uses DevPortalUser model for auth.
"""

import base64
import csv
import hashlib
import io
import logging
import os
import platform
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.auth.dev_portal import check_gate, require_dev_portal
from app.config import settings
from app.database import SessionLocal
from app.models.models import (
    Assignment,
    Firm,
    FirmUser,
    TeamMember,
    User,
)
from app.models.dev_portal_user import DevPortalUser
from app.services.auth_service import hash_password, verify_password, verify_totp
from app.services.otp_service import generate_otp, verify_otp as svc_verify_otp
from app.templates_setup import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dev-portal", tags=["dev-portal"])

_SERVER_START = time.time()

_MUTATION_RE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC|MERGE)\b",
    re.IGNORECASE,
)

_DEV_USER = "samarthhs"
_DEV_EMAIL = "devportal@splanly.local"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _render(request: Request, template: str, ctx: dict = None) -> HTMLResponse:
    return templates.TemplateResponse(request, f"dev-portal/{template}", ctx or {})


def _gen_qr_b64(secret: str, username: str) -> str:
    uri = pyotp.totp.TOTP(secret).provisioning_uri(username, issuer_name="splanly-dev-portal")
    qr = qrcode.make(uri)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _ensure_dev_user(db) -> DevPortalUser:
    """Ensure a DevPortalUser row exists with initial password."""
    user = db.query(DevPortalUser).filter(DevPortalUser.username == _DEV_USER).first()
    if not user:
        user = DevPortalUser(
            username=_DEV_USER,
            password_hash=hash_password("258022"),
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


# ─── Auth routes ───────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
def dev_portal_root(request: Request):
    if request.session.get("dev_portal_dev_id") and check_gate(request):
        return RedirectResponse("/dev-portal/dashboard", status_code=302)
    return RedirectResponse("/dev-portal/gate", status_code=302)


@router.get("/gate", response_class=HTMLResponse)
def gate_page(request: Request):
    if check_gate(request) and request.session.get("dev_portal_dev_id"):
        return RedirectResponse("/dev-portal/dashboard", status_code=302)
    return _render(request, "gate.html")


@router.post("/gate")
async def gate_verify(request: Request, access_code: str = Form(...)):
    if access_code != settings.DEV_PORTAL_ACCESS_CODE:
        return _render(request, "gate.html", {"error": "Invalid access code"})
    resp = RedirectResponse("/dev-portal/login", status_code=302)
    resp.set_cookie("dev_portal_gate", _hash_code(settings.DEV_PORTAL_ACCESS_CODE),
                    max_age=7200, httponly=True, samesite="lax")
    return resp


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not check_gate(request):
        raise HTTPException(status_code=404, detail="Not found")
    if request.session.get("dev_portal_dev_id"):
        return RedirectResponse("/dev-portal/dashboard", status_code=302)
    return _render(request, "login.html")


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not check_gate(request):
        raise HTTPException(status_code=404, detail="Not found")

    if username != _DEV_USER:
        return _render(request, "login.html", {"error": "Invalid credentials"})

    db = SessionLocal()
    try:
        dp_user = _ensure_dev_user(db)
        if not verify_password(password, dp_user.password_hash):
            return _render(request, "login.html", {"error": "Invalid credentials"})
        try:
            generate_otp(db, _DEV_EMAIL, purpose="dev_portal")
        except ValueError as e:
            return _render(request, "login.html", {"error": str(e)})
    finally:
        db.close()

    request.session["dev_portal_pending_email"] = _DEV_EMAIL
    return RedirectResponse("/dev-portal/verify-otp", status_code=302)


@router.get("/verify-otp", response_class=HTMLResponse)
def verify_otp_page(request: Request):
    if not check_gate(request):
        raise HTTPException(status_code=404, detail="Not found")
    if not request.session.get("dev_portal_pending_email"):
        return RedirectResponse("/dev-portal/login", status_code=302)
    return _render(request, "verify_otp.html")


@router.post("/verify-otp")
async def verify_otp_submit(request: Request, otp: str = Form(...)):
    if not check_gate(request):
        raise HTTPException(status_code=404, detail="Not found")

    email = request.session.get("dev_portal_pending_email")
    if not email:
        return RedirectResponse("/dev-portal/login", status_code=302)

    db = SessionLocal()
    try:
        valid_otp = svc_verify_otp(db, email, otp, purpose="dev_portal")
    finally:
        db.close()
    if not valid_otp:
        return _render(request, "verify_otp.html", {"error": "Invalid or expired OTP"})

    request.session.pop("dev_portal_pending_email", None)
    request.session["dev_portal_email"] = email
    request.session["dev_portal_otp_verified"] = True

    db = SessionLocal()
    try:
        dp_user = db.query(DevPortalUser).filter(DevPortalUser.username == _DEV_USER).first()
        if not dp_user or not dp_user.totp_enabled:
            secret = pyotp.random_base32()
            request.session["dev_portal_pending_totp_secret"] = secret
            return _render(request, "verify_2fa.html", {
                "setup_mode": True,
                "qr_svg": _gen_qr_b64(secret, _DEV_USER),
                "secret": secret,
            })
    finally:
        db.close()

    return RedirectResponse("/dev-portal/verify-2fa", status_code=302)


@router.get("/verify-2fa", response_class=HTMLResponse)
def verify_2fa_page(request: Request):
    if not check_gate(request):
        raise HTTPException(status_code=404, detail="Not found")
    if not request.session.get("dev_portal_email"):
        return RedirectResponse("/dev-portal/login", status_code=302)

    email = request.session["dev_portal_email"]
    db = SessionLocal()
    try:
        dp_user = db.query(DevPortalUser).filter(DevPortalUser.username == _DEV_USER).first()
        if dp_user and dp_user.totp_enabled:
            return _render(request, "verify_2fa.html", {"setup_mode": False})
    finally:
        db.close()

    secret = request.session.get("dev_portal_pending_totp_secret")
    if secret:
        return _render(request, "verify_2fa.html", {
            "setup_mode": True,
            "qr_svg": _gen_qr_b64(secret, _DEV_USER),
            "secret": secret,
        })
    return RedirectResponse("/dev-portal/login", status_code=302)


@router.post("/verify-2fa")
async def verify_2fa_submit(request: Request, code: str = Form(...)):
    if not check_gate(request):
        raise HTTPException(status_code=404, detail="Not found")

    email = request.session.get("dev_portal_email")
    if not email:
        return RedirectResponse("/dev-portal/login", status_code=302)

    db = SessionLocal()
    try:
        dp_user = db.query(DevPortalUser).filter(DevPortalUser.username == _DEV_USER).first()
        if not dp_user:
            return _render(request, "verify_2fa.html", {"error": "Account not found"})

        if dp_user.totp_enabled:
            if not verify_totp(dp_user.totp_secret, code):
                return _render(request, "verify_2fa.html", {"error": "Invalid TOTP code"})
        else:
            pending_secret = request.session.get("dev_portal_pending_totp_secret")
            if not pending_secret:
                return RedirectResponse("/dev-portal/login", status_code=302)
            if not verify_totp(pending_secret, code):
                return _render(request, "verify_2fa.html", {
                    "error": "Invalid TOTP code — try again",
                    "setup_mode": True,
                    "qr_svg": _gen_qr_b64(pending_secret, _DEV_USER),
                    "secret": pending_secret,
                })
            dp_user.totp_secret = pending_secret
            dp_user.totp_enabled = True
            db.commit()
            request.session.pop("dev_portal_pending_totp_secret", None)
    finally:
        db.close()

    request.session["dev_portal_dev_id"] = dp_user.id
    request.session["dev_portal_dev_email"] = dp_user.username
    request.session["dev_portal_dev_name"] = dp_user.username
    request.session["dev_portal_started_at"] = time.time()
    request.session.pop("dev_portal_email", None)
    request.session.pop("dev_portal_otp_verified", None)

    return RedirectResponse("/dev-portal/dashboard", status_code=302)


@router.get("/logout")
def logout(request: Request):
    keys = [k for k in request.session if k.startswith("dev_portal_")]
    for k in keys:
        request.session.pop(k, None)
    resp = RedirectResponse("/dev-portal/gate", status_code=302)
    resp.delete_cookie("dev_portal_gate")
    return resp


@router.post("/api/change-password")
def api_change_password(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    old_password = body.get("old_password", "")
    new_password = body.get("new_password", "")

    if not old_password or not new_password:
        raise HTTPException(400, "Both old and new password required")
    if len(new_password) < 6:
        raise HTTPException(400, "New password must be at least 6 characters")

    db = SessionLocal()
    try:
        dp_user = db.query(DevPortalUser).filter(DevPortalUser.id == dev_id).first()
        if not dp_user:
            raise HTTPException(404, "User not found")
        if not verify_password(old_password, dp_user.password_hash):
            raise HTTPException(400, "Current password is incorrect")
        dp_user.password_hash = hash_password(new_password)
        db.commit()
        return {"ok": True, "message": "Password changed"}
    finally:
        db.close()


# ─── Dashboard page ────────────────────────────────────────────────────────────


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, dev_id=Depends(require_dev_portal)):
    return _render(request, "dashboard.html", {
        "dev_name": request.session.get("dev_portal_dev_name", "Developer"),
    })


# ─── API endpoints ─────────────────────────────────────────────────────────────


@router.get("/api/overview")
def api_overview(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        uptime_secs = int(time.time() - _SERVER_START)
        pool = db.get_bind().pool

        users = db.query(User).count()
        firms = db.query(Firm).filter(Firm.is_active).count()
        members = db.query(TeamMember).filter(TeamMember.is_active).count()
        assignments = db.query(Assignment).count()
        from app.models.models import Engagement
        engagements_count = db.query(Engagement).count()

        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False

        from app.services.license_service import check_license
        license_summary = {}
        for firm in db.query(Firm).filter(Firm.is_active).all():
            status = check_license(firm)
            license_summary[status] = license_summary.get(status, 0) + 1

        return {
            "uptime_seconds": uptime_secs,
            "python_version": sys.version.split()[0],
            "fastapi_version": "0.110+",
            "os": f"{platform.system()} {platform.release()}",
            "db_pool": {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
            },
            "health": {
                "database": "ok" if db_ok else "error",
                "smtp": "ok" if settings.SMTP_HOST else "not_configured",
                "oauth": "ok" if settings.MS365_CLIENT_ID else "not_configured",
            },
            "counts": {
                "users": users,
                "firms": firms,
                "team_members": members,
                "assignments": assignments,
                "engagements": engagements_count,
            },
            "licenses": license_summary,
        }
    finally:
        db.close()


@router.get("/api/db")
def api_db(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        pg_version = db.execute(text("SELECT version()")).scalar()

        tables = [
            "users", "firms", "firm_users", "team_members", "clients",
            "engagements", "engagement_instances", "assignments", "leaves",
            "super_admins", "approval_requests", "license_inventory",
        ]
        row_counts = {}
        for t in tables:
            try:
                row_counts[t] = db.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            except Exception:
                row_counts[t] = "N/A"

        size_rows = db.execute(text("""
            SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) as total_size,
                   pg_size_pretty(pg_indexes_size(relid)) as index_size
            FROM pg_catalog.pg_statio_user_tables
            ORDER BY pg_total_relation_size(relid) DESC LIMIT 20
        """)).fetchall()
        table_sizes = [{"name": r[0], "total": r[1], "indexes": r[2]} for r in size_rows]

        conns = db.execute(text("""
            SELECT pid, state, query, now() - query_start AS duration
            FROM pg_stat_activity
            WHERE state != 'idle' AND query IS NOT NULL
            ORDER BY duration DESC LIMIT 20
        """)).fetchall()
        active_conns = [
            {"pid": r[0], "state": r[1], "query": r[2][:200], "duration": str(r[3])}
            for r in conns
        ]

        try:
            migrations = db.execute(text("""
                SELECT version_num, update_time
                FROM alembic_version
                ORDER BY update_time DESC LIMIT 10
            """)).fetchall()
            migration_list = [{"version": r[0], "time": str(r[1])} for r in migrations]
        except Exception:
            migration_list = []

        return {
            "pg_version": pg_version,
            "row_counts": row_counts,
            "table_sizes": table_sizes,
            "active_connections": active_conns,
            "migrations": migration_list,
        }
    finally:
        db.close()


@router.post("/api/db/kill-query")
def api_kill_query(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    pid = body.get("pid")
    if not pid:
        raise HTTPException(400, "pid required")

    db = SessionLocal()
    try:
        db.execute(text(f"SELECT pg_terminate_backend({int(pid)})"))
        db.commit()
        return {"ok": True, "message": f"Terminated PID {pid}"}
    finally:
        db.close()


@router.post("/api/sql")
def api_sql(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query required")
    if _MUTATION_RE.match(query):
        raise HTTPException(400, "Only SELECT queries are allowed")

    db = SessionLocal()
    try:
        start = time.time()
        result = db.execute(text(query))
        elapsed_ms = round((time.time() - start) * 1000, 1)
        if result.returns_rows:
            rows = result.fetchmany(100)
            columns = list(result.keys())
            data = [dict(zip(columns, row)) for row in rows]
            return {"columns": columns, "data": data, "row_count": len(data), "elapsed_ms": elapsed_ms}
        return {"columns": [], "data": [], "row_count": 0, "elapsed_ms": elapsed_ms}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        db.close()


@router.get("/api/endpoints")
def api_endpoints(dev_id=Depends(require_dev_portal)):
    from app.main import app as fastapi_app

    routes = []
    for route in fastapi_app.routes:
        if hasattr(route, "methods"):
            for method in route.methods:
                if method in ("HEAD", "OPTIONS"):
                    continue
                path = route.path
                if "/dev-portal/" in path:
                    auth_level = "dev_portal"
                elif "/auth/" in path or "/api/v1/auth/" in path:
                    auth_level = "auth"
                elif "/admin" in path or "/users" in path or "/invitations" in path:
                    auth_level = "admin"
                elif "/api/v1/" in path:
                    auth_level = "api_v1"
                elif "/health" in path:
                    auth_level = "public"
                else:
                    auth_level = "authenticated"
                routes.append({"method": method.upper(), "path": path, "auth": auth_level})
    return {"routes": routes}


@router.get("/api/logs")
def api_logs(request: Request, dev_id=Depends(require_dev_portal)):
    level = request.query_params.get("level", "")
    keyword = request.query_params.get("keyword", "")

    log_lines = []
    log_path = "server.log"
    if os.path.exists(log_path):
        with open(log_path, "r", errors="replace") as f:
            log_lines = f.readlines()[-200:]

    if level:
        log_lines = [l for l in log_lines if level.upper() in l.upper()]
    if keyword:
        log_lines = [l for l in log_lines if keyword.lower() in l.lower()]

    return {"lines": [l.rstrip() for l in log_lines[-200:]], "total": len(log_lines)}


@router.get("/api/outbox")
def api_outbox(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        from app.models.models import EmailOutbox
        total = db.query(EmailOutbox).count()
        pending = db.query(EmailOutbox).filter(EmailOutbox.status == "pending").count()
        sent = db.query(EmailOutbox).filter(EmailOutbox.status == "sent").count()
        failed = db.query(EmailOutbox).filter(EmailOutbox.status == "failed").count()
        failed_emails = db.query(EmailOutbox).filter(
            EmailOutbox.status == "failed"
        ).order_by(EmailOutbox.created_at.desc()).limit(20).all()
        return {
            "counts": {"total": total, "pending": pending, "sent": sent, "failed": failed},
            "failed": [
                {"id": e.id, "to": e.to_email, "subject": e.subject, "error": e.error_message, "created_at": str(e.created_at)}
                for e in failed_emails
            ],
        }
    except Exception:
        return {"counts": {"total": 0, "pending": 0, "sent": 0, "failed": 0}, "failed": []}
    finally:
        db.close()


@router.post("/api/outbox/retry")
def api_outbox_retry(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    email_id = body.get("id")
    if not email_id:
        raise HTTPException(400, "id required")

    db = SessionLocal()
    try:
        from app.models.models import EmailOutbox
        email = db.query(EmailOutbox).filter(EmailOutbox.id == email_id).first()
        if not email:
            raise HTTPException(404, "Email not found")
        email.status = "pending"
        email.error_message = None
        email.retry_count = 0
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/api/outbox/test")
def api_outbox_test(dev_id=Depends(require_dev_portal)):
    from app.services.otp_service import send_otp_email
    try:
        send_otp_email(settings.SMTP_FROM_EMAIL, "000000", purpose="dev_portal")
        return {"ok": True, "message": "Test email sent"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/api/users")
def api_users(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        users = db.query(User).all()
        user_list = []
        for u in users:
            fu = db.query(FirmUser).filter(FirmUser.user_id == u.id, FirmUser.is_active).first()
            firm_name = ""
            role = ""
            if fu:
                firm = db.query(Firm).filter(Firm.id == fu.firm_id).first()
                firm_name = firm.name if firm else ""
                role = fu.technical_role.value if fu.technical_role else ""
            user_list.append({
                "id": u.id, "email": u.email, "display_name": u.display_name,
                "is_active": u.is_active, "firm": firm_name, "role": role,
                "last_login": str(u.last_login) if u.last_login else None,
                "totp_enabled": u.totp_enabled,
            })

        # Dev portal users
        dp_users = db.query(DevPortalUser).all()
        dp_list = [
            {"id": u.id, "username": u.username, "totp_enabled": u.totp_enabled}
            for u in dp_users
        ]
        return {"users": user_list, "dev_portal_users": dp_list}
    finally:
        db.close()


@router.get("/api/firms")
def api_firms(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        from app.services.license_service import check_license, get_days_remaining
        firms = db.query(Firm).filter(Firm.is_active).all()
        result = []
        for f in firms:
            user_count = db.query(FirmUser).filter(FirmUser.firm_id == f.id, FirmUser.is_active).count()
            member_count = db.query(TeamMember).filter(TeamMember.firm_id == f.id, TeamMember.is_active).count()
            status = check_license(f)
            result.append({
                "id": f.id, "name": f.name, "license_tier": f.license_tier,
                "license_status": status, "license_expires_at": str(f.license_expires_at) if f.license_expires_at else None,
                "days_remaining": get_days_remaining(f),
                "user_count": user_count, "member_count": member_count,
                "allowed_domains": f.allowed_domains,
            })
        return {"firms": result}
    finally:
        db.close()


@router.post("/api/firms/extend-trial")
def api_extend_trial(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    firm_id = body.get("firm_id")
    days = body.get("days", 14)

    db = SessionLocal()
    try:
        firm = db.query(Firm).filter(Firm.id == firm_id).first()
        if not firm:
            raise HTTPException(404, "Firm not found")
        if firm.license_expires_at:
            firm.license_expires_at = firm.license_expires_at + timedelta(days=days)
        else:
            firm.license_expires_at = datetime.now(timezone.utc) + timedelta(days=days)
        db.commit()
        return {"ok": True, "new_expiry": str(firm.license_expires_at)}
    finally:
        db.close()


@router.get("/api/licenses")
def api_licenses(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        from app.models.license_inventory import LicenseInventory
        items = db.query(LicenseInventory).order_by(LicenseInventory.created_at.desc()).all()
        return {
            "licenses": [
                {
                    "id": l.id,
                    "key_masked": l.license_key[:8] + "..." + l.license_key[-4:] if len(l.license_key) > 12 else l.license_key,
                    "tier": l.tier, "duration_days": l.duration_days, "status": l.status,
                    "note": l.note, "assigned_firm_id": l.assigned_firm_id,
                    "expires_at": str(l.expires_at) if l.expires_at else None,
                    "created_at": str(l.created_at),
                }
                for l in items
            ]
        }
    finally:
        db.close()


@router.post("/api/licenses/generate")
def api_generate_licenses(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    tier = body.get("tier", "standard")
    duration = body.get("duration_days", 365)
    quantity = min(body.get("quantity", 1), 50)
    note = body.get("note", "")

    db = SessionLocal()
    try:
        from app.models.license_inventory import LicenseInventory
        generated = []
        for _ in range(quantity):
            key = "SPLN-" + "-".join(secrets.token_hex(2).upper() for _ in range(4))
            key_hash = hashlib.sha256(key.encode()).hexdigest()
            li = LicenseInventory(
                license_key=key, license_key_hash=key_hash, tier=tier,
                duration_days=duration, note=note, generated_by_id=dev_id,
            )
            db.add(li)
            generated.append(key)
        db.commit()
        return {"ok": True, "count": len(generated), "keys": generated}
    finally:
        db.close()


@router.post("/api/licenses/assign")
def api_assign_license(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    license_id = body.get("license_id")
    firm_id = body.get("firm_id")

    db = SessionLocal()
    try:
        from app.models.license_inventory import LicenseInventory
        li = db.query(LicenseInventory).filter(LicenseInventory.id == license_id).first()
        if not li or li.status != "available":
            raise HTTPException(400, "License not available")
        firm = db.query(Firm).filter(Firm.id == firm_id).first()
        if not firm:
            raise HTTPException(404, "Firm not found")

        li.status = "assigned"
        li.assigned_firm_id = firm_id
        li.assigned_at = datetime.now(timezone.utc)
        li.expires_at = datetime.now(timezone.utc) + timedelta(days=li.duration_days)
        firm.license_key = li.license_key_hash
        firm.license_tier = li.tier
        firm.license_expires_at = li.expires_at
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/api/licenses/revoke")
def api_revoke_license(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    license_id = body.get("license_id")

    db = SessionLocal()
    try:
        from app.models.license_inventory import LicenseInventory
        li = db.query(LicenseInventory).filter(LicenseInventory.id == license_id).first()
        if not li:
            raise HTTPException(404, "License not found")
        if li.assigned_firm_id:
            firm = db.query(Firm).filter(Firm.id == li.assigned_firm_id).first()
            if firm:
                firm.license_key = None
                firm.license_tier = None
        li.status = "revoked"
        li.assigned_firm_id = None
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/api/licenses/export")
def api_export_licenses(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        from app.models.license_inventory import LicenseInventory
        items = db.query(LicenseInventory).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Key", "Tier", "Duration (days)", "Status", "Note", "Created"])
        for l in items:
            writer.writerow([l.license_key, l.tier, l.duration_days, l.status, l.note or "", str(l.created_at)])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=license_inventory.csv"},
        )
    finally:
        db.close()


@router.get("/api/alerts")
def api_alerts(dev_id=Depends(require_dev_portal)):
    db = SessionLocal()
    try:
        from app.services.license_service import check_license
        alerts = []

        for firm in db.query(Firm).filter(Firm.is_active).all():
            status = check_license(firm)
            if status == "expired":
                alerts.append({"level": "error", "message": f"License expired for {firm.name}"})
            elif status == "grace":
                alerts.append({"level": "warning", "message": f"License expiring soon for {firm.name}"})

        today = datetime.now(timezone.utc).date()
        members = db.query(TeamMember).filter(TeamMember.is_active).all()
        for m in members:
            total = db.query(func.sum(Assignment.allocation_percent)).filter(
                Assignment.team_member_id == m.id,
                Assignment.start_date <= today,
                Assignment.end_date >= today,
            ).scalar() or 0
            if total > 100:
                alerts.append({"level": "warning", "message": f"{m.display_name} over-allocated at {total}%"})

        try:
            from app.models.models import EmailOutbox
            failed_count = db.query(EmailOutbox).filter(
                EmailOutbox.status == "failed",
                EmailOutbox.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            ).count()
            if failed_count > 5:
                alerts.append({"level": "error", "message": f"{failed_count} email failures in last 24h"})
        except Exception:
            pass

        return {"alerts": alerts}
    finally:
        db.close()


@router.get("/api/system")
def api_system(dev_id=Depends(require_dev_portal)):
    sensitive_keys = {"SECRET_KEY", "SMTP_PASSWORD", "MS365_CLIENT_SECRET", "LICENSE_SIGNING_KEY", "DEV_PORTAL_ACCESS_CODE"}
    env_vars = {}
    for k, v in os.environ.items():
        if k.startswith("DEV_") or k in sensitive_keys or k.startswith("DATABASE"):
            env_vars[k] = v[:4] + "****" if k in sensitive_keys and len(v) > 4 else (v if k not in sensitive_keys else "****")

    from app.main import app as fastapi_app
    return {
        "env_vars": env_vars,
        "server_start": datetime.fromtimestamp(_SERVER_START, tz=timezone.utc).isoformat(),
        "route_count": sum(1 for r in fastapi_app.routes if hasattr(r, "methods")),
    }


@router.post("/api/settings/update")
def api_update_setting(request: Request, dev_id=Depends(require_dev_portal)):
    import asyncio

    async def _body():
        return await request.json()

    body = asyncio.get_event_loop().run_until_complete(_body())
    key = body.get("key")
    value = body.get("value")
    if not key:
        raise HTTPException(400, "key required")

    db = SessionLocal()
    try:
        from app.services.settings_service import update_setting
        update_setting(db, key, value, updated_by_user_id=None, firm_id=None)
        return {"ok": True}
    finally:
        db.close()
