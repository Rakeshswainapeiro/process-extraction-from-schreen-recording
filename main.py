import os
import uvicorn
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.database import init_db, get_db, async_session
from app.routes import auth_routes, recording_routes, report_routes, export_routes, settings_routes
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


@app.on_event("startup")
async def startup():
    # Ensure data directories exist
    for d in [settings.DATA_DIR, settings.SCREENSHOTS_DIR, settings.RECORDINGS_DIR, settings.REPORTS_DIR]:
        os.makedirs(d, exist_ok=True)
    await init_db()
    # Seed test users
    async with async_session() as db:
        await seed_test_users(db)


@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    from sqlalchemy.ext.asyncio import AsyncSession
    db = async_session()
    try:
        user = None
        token = request.cookies.get("access_token")
        if token:
            from app.services.auth_service import decode_access_token, get_user_by_username
            payload = decode_access_token(token)
            if payload and payload.get("sub"):
                user = await get_user_by_username(db, payload["sub"])
        if user:
            return RedirectResponse(url="/dashboard", status_code=303)
    finally:
        await db.close()
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    from app.services.auth_service import decode_access_token, get_user_by_username
    db = async_session()
    try:
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/", status_code=303)
        payload = decode_access_token(token)
        if not payload:
            return RedirectResponse(url="/", status_code=303)
        user = await get_user_by_username(db, payload.get("sub", ""))
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "user": user,
        })
    finally:
        await db.close()


@app.get("/report/{recording_id}", response_class=HTMLResponse)
async def report_page(recording_id: int, request: Request):
    from app.services.auth_service import decode_access_token, get_user_by_username
    db = async_session()
    try:
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/", status_code=303)
        payload = decode_access_token(token)
        if not payload:
            return RedirectResponse(url="/", status_code=303)
        user = await get_user_by_username(db, payload.get("sub", ""))
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("report.html", {
            "request": request,
            "user": user,
            "recording_id": recording_id,
        })
    finally:
        await db.close()


@app.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request):
    from app.services.auth_service import decode_access_token, get_user_by_username
    db = async_session()
    try:
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/", status_code=303)
        payload = decode_access_token(token)
        if not payload:
            return RedirectResponse(url="/", status_code=303)
        user = await get_user_by_username(db, payload.get("sub", ""))
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("feedback.html", {
            "request": request,
            "user": user,
        })
    finally:
        await db.close()


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    from app.services.auth_service import decode_access_token, get_user_by_username
    db = async_session()
    try:
        token = request.cookies.get("access_token")
        if not token:
            return RedirectResponse(url="/", status_code=303)
        payload = decode_access_token(token)
        if not payload:
            return RedirectResponse(url="/", status_code=303)
        user = await get_user_by_username(db, payload.get("sub", ""))
        if not user:
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "user": user,
        })
    finally:
        await db.close()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
