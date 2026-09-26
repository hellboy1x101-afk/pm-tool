import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.email_worker import email_worker_lifespan

logger = logging.getLogger(__name__)
from app.exceptions import (
    ConflictWithLeaveError,
    NotFoundError,
    OverAllocationError,
    ValidationError,
)
from app.routers import (
    admin_settings,
    assignments,
    auth,
    clients,
    contact,
    dashboard,
    engagements,
    health,
    invitations,
    leaves,
    license,
    outbox,
    reports,
    team_members,
    users,
)
from app.routers import dev_portal
from app.api.v1 import router as api_v1_router
from app.middleware.license import LicenseMiddleware


@asynccontextmanager
async def lifespan(app):
    async with email_worker_lifespan():
        yield


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

class FlashMiddleware(BaseHTTPMiddleware):
    """Pop flash message before rendering so it shows only once."""
    async def dispatch(self, request: Request, call_next):
        request.state.flash = request.session.pop("_flash", None)
        response = await call_next(request)
        return response


# Middleware order: outermost → innermost
app.add_middleware(LicenseMiddleware)
from app.middleware.password_expiry import PasswordExpiryMiddleware
app.add_middleware(PasswordExpiryMiddleware)
app.add_middleware(FlashMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie=settings.SESSION_COOKIE_NAME,
    max_age=settings.SESSION_MAX_AGE_SECONDS,
    same_site="lax",
    https_only=False,  # Allow HTTP in development
)

# CORS middleware for cross-origin frontend (Next.js on Vercel)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://*.vercel.app",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
from app.templates_setup import templates


def csrf_token(request: Request) -> str:
    """Get or generate a CSRF token stored in the session."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return token





# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("app/static/icons/icon-192.png", media_type="image/png")

# Include routers
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(contact.router)
app.include_router(invitations.router)
app.include_router(team_members.router)
app.include_router(clients.router)
app.include_router(engagements.router)
app.include_router(assignments.router)
app.include_router(leaves.router)
app.include_router(reports.router)
app.include_router(admin_settings.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(outbox.router)
app.include_router(license.router)
app.include_router(dev_portal.router)
app.include_router(api_v1_router)


@app.get("/test")
def test_page(request: Request):
    return templates.TemplateResponse(request, "test.html")


@app.get("/faq")
def faq_page(request: Request):
    return templates.TemplateResponse(request, "faq.html")


@app.get("/book-demo")
def book_demo_page(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://calendar.zoho.in/zc/view/slot-booking/zz080212300acc13033d01ac0cdd47751184164ddc793c900fba9f96207145c64ddf5695a014546d2030fdcbb2a669de45cf4f4c5f", status_code=302)


@app.get("/docs-app")
def docs_page(request: Request):
    return templates.TemplateResponse(request, "docs.html")


@app.get("/privacy")
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "legal/privacy.html")


@app.get("/terms")
def terms_page(request: Request):
    return templates.TemplateResponse(request, "legal/terms.html")


@app.get("/dpdp")
def dpdp_page(request: Request):
    return templates.TemplateResponse(request, "legal/dpdp.html")


@app.get("/cookies")
def cookies_page(request: Request):
    return templates.TemplateResponse(request, "legal/cookies.html")


@app.get("/refund")
def refund_page(request: Request):
    return templates.TemplateResponse(request, "legal/refund.html")


@app.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "landing.html")


# Global exception handlers
@app.exception_handler(NotFoundError)
def not_found_handler(request: Request, exc: NotFoundError):
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "errors/error.html", {
            "status_code": 404, "detail": str(exc),
        }, status_code=404)
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(OverAllocationError)
def over_allocation_handler(request: Request, exc: OverAllocationError):
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "errors/error.html", {
            "status_code": 409, "detail": str(exc),
        }, status_code=409)
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ConflictWithLeaveError)
def conflict_leave_handler(request: Request, exc: ConflictWithLeaveError):
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "errors/error.html", {
            "status_code": 409, "detail": str(exc),
        }, status_code=409)
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
def validation_handler(request: Request, exc: ValidationError):
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "errors/error.html", {
            "status_code": 422, "detail": str(exc),
        }, status_code=422)
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/auth/login", status_code=303)
    if "text/html" in request.headers.get("accept", ""):
        if exc.status_code == 403:
            return templates.TemplateResponse(request, "errors/403.html", status_code=403)
        if exc.status_code == 404:
            return templates.TemplateResponse(request, "errors/404.html", status_code=404)
        return templates.TemplateResponse(request, "errors/error.html", {
            "status_code": exc.status_code, "detail": exc.detail,
        }, status_code=exc.status_code)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(404)
async def not_found_page_handler(request: Request, exc):
    """Handle 404 for missing routes (Starlette-level, before HTTPException handler)."""
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "errors/404.html", status_code=404)
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """Invalid input: friendly page for browsers, FastAPI's usual 422 body for API clients."""
    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(request, "errors/error.html", {
            "status_code": 422, "detail": "Some of the submitted information was invalid. Please check and try again.",
        }, status_code=422)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last resort: log the full error server-side, never show internals to the user."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    if "text/html" in request.headers.get("accept", ""):
        try:
            return templates.TemplateResponse(request, "errors/500.html", status_code=500)
        except Exception:
            logger.exception("Failed to render errors/500.html")
            return HTMLResponse(
                "<h1>Something went wrong</h1><p>Please try again. If it keeps happening, contact support.</p>",
                status_code=500,
            )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
