import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
    create_user,
    decode_access_token,
    get_user_by_email,
    get_user_by_username,
)

router = APIRouter()

# ── Validation helpers ───────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")


def _validate_password(password: str) -> Optional[str]:
    """Return an error message if the password is too weak, else None."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not re.search(r"[A-Z]", password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r"[0-9]", password):
        return "Password must contain at least one digit"
    if not re.search(r"[^a-zA-Z0-9]", password):
        return "Password must contain at least one special character"
    return None


# ── Login ────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=401, detail="Username and password are required")

    user = await authenticate_user(db, username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    token = create_access_token(data={"sub": user.username, "role": user.role})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="access_token", value=token, httponly=True, max_age=28800)
    return response


# ── Registration ─────────────────────────────────────────────────────────────

@router.post("/register")
async def register(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    full_name = form.get("full_name", "").strip()
    email = form.get("email", "").strip().lower()
    username = form.get("username", "").strip().lower()
    password = form.get("password", "")
    confirm_password = form.get("confirm_password", "")

    # --- Validation ---
    errors = []

    if not full_name or len(full_name) < 2:
        errors.append("Full name is required (at least 2 characters)")

    if not _EMAIL_RE.match(email):
        errors.append("Please enter a valid email address")

    if not _USERNAME_RE.match(username):
        errors.append("Username must be 3-30 characters (letters, digits, underscores only)")

    pwd_error = _validate_password(password)
    if pwd_error:
        errors.append(pwd_error)

    if password != confirm_password:
        errors.append("Passwords do not match")

    if errors:
        return JSONResponse({"detail": errors}, status_code=422)

    # --- Uniqueness checks ---
    if await get_user_by_username(db, username):
        return JSONResponse({"detail": ["Username is already taken"]}, status_code=409)

    if await get_user_by_email(db, email):
        return JSONResponse({"detail": ["An account with this email already exists"]}, status_code=409)

    # --- Create user ---
    await create_user(db, username, email, password, full_name, role="user")

    return JSONResponse({"message": "Account created successfully. Please sign in.", "redirect": "/"}, status_code=201)


# ── Logout ───────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("access_token")
    return response


# ── Auth helpers (dependency injection) ──────────────────────────────────────

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    username = payload.get("sub")
    if not username:
        return None
    return await get_user_by_username(db, username)


async def require_user(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return user
