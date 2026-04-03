import os
import uvicorn
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.database import init_db, get_db, async_session
from app.routes import auth_routes, recording_routes, report_routes, export_routes, settings_routes
from app.routes.usage_routes import router as usage_router
from app.routes.admin_routes import router as admin_router
from app.routes.auth_routes import get_current_user
from app.services.auth_service import seed_test_users
from config import settings

app = FastAPI(title=settings.APP_NAME)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "app", "static")), name="static")
app.mount("/screenshots", StaticFiles(directory=settings.SCREENSHOTS_DIR), name="screenshots")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))

# Include routers
app.include_router(auth_routes.router)
app.include_router(recording_routes.router)
app.include_router(report_routes.router)
app.include_router(export_routes.router)
app.include_router(settings_routes.router)
app.include_router(usage_router)
app.include_router(admin_router)


@app.on_event("startup")
async def startup():
    for d in [settings.DATA_DIR, settings.SCREENSHOTS_DIR, settings.RECORDINGS_DIR, settings.REPORTS_DIR]:
        os.makedirs(d, exist_ok=True)
    await init_db()
    await _migrate_existing_schema()
    async with async_session() as db:
        await seed_test_users(db)
        await _seed_platform_config(db)


async def _migrate_existing_schema():
    """Add new columns to existing tables if they don't exist (safe, idempotent)."""
    from app.models.database import engine
    migrations = [
        "ALTER TABLE users ADD COLUMN is_super_admin BOOLEAN DEFAULT 0",
        "ALTER TABLE ai_model_configs ADD COLUMN is_encrypted BOOLEAN DEFAULT 0",
    ]
    async with engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(__import__('sqlalchemy').text(sql))
            except Exception:
                pass  # Column already exists — safe to ignore


async def _seed_platform_config(db):
    """Seed default platform config rows if they don't exist yet."""
    from sqlalchemy import select
    from app.models.database import PlatformConfig

    defaults = [
        ("default_model_provider", settings.CUSTOM_AI_BASE_URL and "custom" or "anthropic",
         "AI provider for the platform default model"),
        ("default_model_id", settings.CUSTOM_AI_MODEL or "claude-sonnet-4-6",
         "Model ID for the platform default model"),
        ("default_model_base_url", settings.CUSTOM_AI_BASE_URL or "",
         "Base URL for the platform default model (blank = provider default)"),
        ("default_model_api_key", "",
         "Encrypted API key for the platform default model"),
        ("default_max_tokens", "8000",
         "Default max tokens for platform model"),
        ("trial_quota_sessions", "3",
         "Number of free trial sessions given to new users"),
        ("trial_quota_tokens", "50000",
         "Rough token budget for trial users (informational)"),
    ]

    for key, value, description in defaults:
        existing = (await db.execute(
            select(PlatformConfig).where(PlatformConfig.key == key)
        )).scalar_one_or_none()
        if not existing:
            db.add(PlatformConfig(key=key, value=str(value), description=description))

    await db.commit()


# ── Helper for protected page routes ────────────────────────────────────────

async def _get_user_from_cookie(request: Request):
    from app.services.auth_service import decode_access_token
    from sqlalchemy import select
    from app.models.database import User
    db = async_session()
    try:
        token = request.cookies.get("access_token")
        if not token:
            return None, db
        payload = decode_access_token(token)
        if not payload:
            return None, db
        username = payload.get("sub", "")
        if not username:
            return None, db
        # Explicit column selection to ensure is_super_admin is always loaded fresh
        result = await db.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()
        if user:
            # Force-refresh the is_super_admin attribute from DB
            await db.refresh(user)
        return user, db
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Auth cookie error: %s", e)
        return None, db


# ── Debug (temporary) ────────────────────────────────────────────────────────

@app.get("/debug/me")
async def debug_me(request: Request):
    """Temporary: shows current user's auth state. Remove after debugging."""
    user, db = await _get_user_from_cookie(request)
    try:
        if not user:
            return {"error": "not authenticated"}
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "is_super_admin": user.is_super_admin,
            "is_super_admin_type": type(user.is_super_admin).__name__,
            "is_super_admin_bool": bool(user.is_super_admin),
        }
    finally:
        await db.close()


# ── Page routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if user:
            return RedirectResponse(url="/dashboard", status_code=303)
    finally:
        await db.close()
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/report/{recording_id}", response_class=HTMLResponse)
async def report_page(recording_id: int, request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("report.html", {
            "request": request, "user": user, "recording_id": recording_id,
        })
    finally:
        await db.close()


@app.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("feedback.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("settings.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/upgrade", response_class=HTMLResponse)
async def upgrade_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("upgrade.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user:
            return RedirectResponse(url="/", status_code=303)
        if not getattr(user, "is_super_admin", False):
            return RedirectResponse(url="/dashboard", status_code=303)
        return templates.TemplateResponse("admin/dashboard.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user or not getattr(user, "is_super_admin", False):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("admin/users.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/admin/usage", response_class=HTMLResponse)
async def admin_usage_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user or not getattr(user, "is_super_admin", False):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("admin/usage.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/admin/revenue", response_class=HTMLResponse)
async def admin_revenue_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user or not getattr(user, "is_super_admin", False):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("admin/revenue.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user or not getattr(user, "is_super_admin", False):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("admin/logs.html", {"request": request, "user": user})
    finally:
        await db.close()


@app.get("/admin/config", response_class=HTMLResponse)
async def admin_config_page(request: Request):
    user, db = await _get_user_from_cookie(request)
    try:
        if not user or not getattr(user, "is_super_admin", False):
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("admin/config.html", {"request": request, "user": user})
    finally:
        await db.close()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
