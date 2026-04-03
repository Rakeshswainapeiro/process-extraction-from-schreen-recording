"""Super Admin API routes — all require is_super_admin=True."""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import (
    User, UsageQuota, UsageEvent, Purchase, ApiAuditLog, PlatformConfig, get_db,
)
from app.routes.auth_routes import require_user
from app.services.usage_service import apply_purchase, get_or_create_quota

router = APIRouter(prefix="/api/admin", tags=["admin"])

# ── Auth guard ────────────────────────────────────────────────────────────────

async def require_super_admin(user=Depends(require_user)):
    if not getattr(user, "is_super_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )
    return user


# ── Dashboard stats ───────────────────────────────────────────────────────────

@router.get("/dashboard")
async def admin_dashboard(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    today = datetime.utcnow().date()
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_users = await db.scalar(select(func.count(User.id))) or 0
    active_users = await db.scalar(
        select(func.count(User.id)).where(User.is_active == True)
    ) or 0
    trial_users = await db.scalar(
        select(func.count(UsageQuota.id)).where(UsageQuota.is_trial == True)
    ) or 0
    sessions_today = await db.scalar(
        select(func.sum(UsageEvent.sessions_delta))
        .where(func.date(UsageEvent.created_at) == today)
    ) or 0
    tokens_today = await db.scalar(
        select(func.sum(UsageEvent.tokens_delta))
        .where(func.date(UsageEvent.created_at) == today)
    ) or 0
    revenue_month = await db.scalar(
        select(func.sum(Purchase.amount_usd_cents))
        .where(
            Purchase.created_at >= month_start,
            Purchase.status == "completed",
        )
    ) or 0
    error_count = await db.scalar(
        select(func.count(ApiAuditLog.id))
        .where(
            ApiAuditLog.status_code >= 400,
            func.date(ApiAuditLog.created_at) == today,
        )
    ) or 0

    # Last 7-day session counts
    daily_sessions = []
    for i in range(6, -1, -1):
        day = (datetime.utcnow() - timedelta(days=i)).date()
        count = await db.scalar(
            select(func.sum(UsageEvent.sessions_delta))
            .where(func.date(UsageEvent.created_at) == day)
        ) or 0
        daily_sessions.append({"date": str(day), "sessions": count})

    return JSONResponse({
        "total_users": total_users,
        "active_users": active_users,
        "trial_users": trial_users,
        "paid_users": total_users - trial_users,
        "sessions_today": sessions_today,
        "tokens_today": tokens_today,
        "revenue_this_month_cents": revenue_month,
        "revenue_this_month_usd": revenue_month / 100,
        "error_count_today": error_count,
        "daily_sessions": daily_sessions,
    })


# ── User management ───────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    page: int = 1,
    limit: int = 50,
    search: str = "",
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    offset = (page - 1) * limit
    q = select(User)
    if search:
        q = q.where(
            User.username.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
        )
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    users = (await db.execute(q.order_by(User.created_at.desc()).offset(offset).limit(limit))).scalars().all()

    result = []
    for u in users:
        quota = (await db.execute(
            select(UsageQuota).where(UsageQuota.user_id == u.id)
        )).scalar_one_or_none()

        total_spend = await db.scalar(
            select(func.sum(Purchase.amount_usd_cents))
            .where(Purchase.user_id == u.id, Purchase.status == "completed")
        ) or 0

        last_event = (await db.execute(
            select(UsageEvent.created_at)
            .where(UsageEvent.user_id == u.id)
            .order_by(desc(UsageEvent.created_at))
            .limit(1)
        )).scalar_one_or_none()

        result.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "is_active": u.is_active,
            "is_super_admin": u.is_super_admin,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_activity": last_event.isoformat() if last_event else None,
            "quota": {
                "is_trial": quota.is_trial if quota else True,
                "trial_sessions_used": quota.trial_sessions_used if quota else 0,
                "trial_sessions_max": quota.trial_sessions_max if quota else 3,
                "remaining_sessions": quota.remaining_sessions if quota else 0,
                "remaining_tokens": quota.remaining_tokens if quota else 0,
                "used_sessions": quota.used_sessions if quota else 0,
                "used_tokens": quota.used_tokens if quota else 0,
            },
            "total_spend_cents": total_spend,
            "total_spend_usd": total_spend / 100,
        })

    return JSONResponse({"users": result, "total": total, "pages": max(1, -(-total // limit))})


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Activate/deactivate a user or toggle super_admin flag."""
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == admin.id:
        raise HTTPException(400, "Cannot modify your own account via admin panel")

    data = await request.json()
    allowed = {"is_active", "is_super_admin"}
    for k, v in data.items():
        if k in allowed:
            setattr(u, k, v)

    await db.commit()
    return JSONResponse({"status": "updated", "user_id": u.id})


# ── Usage overview ────────────────────────────────────────────────────────────

@router.get("/usage/summary")
async def usage_summary(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    """Per-user usage summary table."""
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    result = []
    for u in users:
        quota = (await db.execute(
            select(UsageQuota).where(UsageQuota.user_id == u.id)
        )).scalar_one_or_none()

        total_spend = await db.scalar(
            select(func.sum(Purchase.amount_usd_cents))
            .where(Purchase.user_id == u.id, Purchase.status == "completed")
        ) or 0

        result.append({
            "user_id": u.id,
            "username": u.username,
            "email": u.email,
            "is_trial": quota.is_trial if quota else True,
            "trial_sessions_used": quota.trial_sessions_used if quota else 0,
            "used_sessions": quota.used_sessions if quota else 0,
            "used_tokens": quota.used_tokens if quota else 0,
            "remaining_sessions": quota.remaining_sessions if quota else 0,
            "remaining_tokens": quota.remaining_tokens if quota else 0,
            "total_spend_usd": total_spend / 100,
        })
    return JSONResponse(result)


@router.get("/usage/events")
async def usage_events(
    user_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    """Paginated usage events, optionally filtered by user."""
    q = select(UsageEvent).order_by(desc(UsageEvent.created_at))
    if user_id:
        q = q.where(UsageEvent.user_id == user_id)
    events = (await db.execute(q.offset(offset).limit(limit))).scalars().all()

    # Batch fetch usernames
    user_ids = {e.user_id for e in events}
    users = {
        u.id: u.username
        for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars()
    }

    return JSONResponse([{
        "id": e.id,
        "user_id": e.user_id,
        "username": users.get(e.user_id, "unknown"),
        "event_type": e.event_type,
        "sessions_delta": e.sessions_delta,
        "tokens_delta": e.tokens_delta,
        "model_provider": e.model_provider,
        "model_id": e.model_id,
        "recording_id": e.recording_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in events])


# ── Revenue ───────────────────────────────────────────────────────────────────

@router.get("/purchases")
async def list_purchases(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    purchases = (await db.execute(
        select(Purchase)
        .order_by(desc(Purchase.created_at))
        .offset(offset).limit(limit)
    )).scalars().all()

    user_ids = {p.user_id for p in purchases}
    users = {
        u.id: u.email
        for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars()
    }

    return JSONResponse([{
        "id": p.id,
        "user_id": p.user_id,
        "user_email": users.get(p.user_id, "unknown"),
        "purchase_type": p.purchase_type,
        "quantity": p.quantity,
        "amount_usd": p.amount_usd_cents / 100,
        "status": p.status,
        "payment_reference": p.payment_reference,
        "notes": p.notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    } for p in purchases])


@router.get("/purchases/stats")
async def purchase_stats(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    total_revenue = await db.scalar(
        select(func.sum(Purchase.amount_usd_cents))
        .where(Purchase.status == "completed")
    ) or 0
    sessions_sold = await db.scalar(
        select(func.sum(Purchase.quantity))
        .where(Purchase.purchase_type == "sessions", Purchase.status == "completed")
    ) or 0
    tokens_sold = await db.scalar(
        select(func.sum(Purchase.quantity))
        .where(Purchase.purchase_type == "tokens", Purchase.status == "completed")
    ) or 0
    purchase_count = await db.scalar(select(func.count(Purchase.id))) or 0

    return JSONResponse({
        "total_revenue_cents": total_revenue,
        "total_revenue_usd": total_revenue / 100,
        "sessions_sold": sessions_sold,
        "tokens_sold": tokens_sold,
        "purchase_count": purchase_count,
    })


@router.post("/purchases/grant")
async def grant_quota(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Manually grant sessions or tokens to a user (no charge)."""
    data = await request.json()
    user_id = data.get("user_id")
    purchase_type = data.get("purchase_type", "")
    quantity = int(data.get("quantity", 0))

    if not user_id:
        raise HTTPException(400, "user_id is required")
    if purchase_type not in ("sessions", "tokens"):
        raise HTTPException(400, "purchase_type must be 'sessions' or 'tokens'")
    if quantity <= 0:
        raise HTTPException(400, "quantity must be positive")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    purchase = await apply_purchase(
        db=db,
        user_id=user_id,
        purchase_type=purchase_type,
        quantity=quantity,
        amount_usd_cents=0,
        notes=data.get("notes", f"Admin grant by {admin.username}"),
        granted_by=admin.id,
    )
    return JSONResponse({
        "status": "granted",
        "purchase_id": purchase.id,
        "user_id": user_id,
        "purchase_type": purchase_type,
        "quantity": quantity,
    })


# ── Platform config ───────────────────────────────────────────────────────────

@router.get("/platform/config")
async def get_platform_config(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    rows = (await db.execute(select(PlatformConfig))).scalars().all()
    result = {}
    for r in rows:
        # Never return encrypted API key values
        if "api_key" in r.key:
            result[r.key] = "***" if r.value else ""
        else:
            result[r.key] = r.value
    return JSONResponse(result)


@router.patch("/platform/config")
async def update_platform_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    from app.services.encryption_service import get_encryption_service
    enc = get_encryption_service()
    data = await request.json()

    for key, value in data.items():
        # Encrypt API key values before storing
        if "api_key" in key and value and value != "***":
            stored_value = enc.encrypt(str(value))
        else:
            stored_value = str(value)

        existing = (await db.execute(
            select(PlatformConfig).where(PlatformConfig.key == key)
        )).scalar_one_or_none()

        if existing:
            existing.value = stored_value
            existing.updated_by = admin.id
        else:
            db.add(PlatformConfig(
                key=key,
                value=stored_value,
                updated_by=admin.id,
            ))

    await db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/platform/config/test")
async def test_platform_model(
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Test the platform default model connection."""
    from app.services.model_resolver import resolve_model
    from app.services.encryption_service import get_encryption_service
    import httpx as _httpx
    import anthropic as _anthropic

    # Use user_id=0 trick — resolve_model will skip user configs and hit platform config
    rows = (await db.execute(select(PlatformConfig))).scalars().all()
    platform = {r.key: r.value for r in rows}

    if not platform.get("default_model_id"):
        return JSONResponse({"status": "error", "message": "No platform default model configured"})

    enc = get_encryption_service()
    raw_key = platform.get("default_model_api_key", "")
    try:
        api_key = enc.decrypt(raw_key) if raw_key and raw_key != "***" else ""
    except Exception:
        api_key = raw_key

    provider = platform.get("default_model_provider", "anthropic")
    model_id = platform.get("default_model_id", "")
    base_url = platform.get("default_model_base_url") or None

    try:
        if provider == "anthropic":
            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = _anthropic.Anthropic(**kwargs)
            msg = client.messages.create(
                model=model_id,
                max_tokens=20,
                messages=[{"role": "user", "content": "Reply: OK"}],
            )
            return JSONResponse({"status": "success", "response": msg.content[0].text[:100]})
        else:
            endpoint = (base_url or "https://api.openai.com/v1").rstrip("/")
            async with _httpx.AsyncClient(timeout=30) as c:
                resp = await c.post(
                    f"{endpoint}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model_id, "messages": [{"role": "user", "content": "Reply: OK"}], "max_tokens": 20},
                )
                if resp.status_code == 200:
                    text = resp.json()["choices"][0]["message"]["content"]
                    return JSONResponse({"status": "success", "response": text[:100]})
                return JSONResponse({"status": "error", "message": f"Status {resp.status_code}: {resp.text[:200]}"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


# ── Audit logs ────────────────────────────────────────────────────────────────

@router.get("/logs")
async def audit_logs(
    user_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    errors_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    q = select(ApiAuditLog).order_by(desc(ApiAuditLog.created_at))
    if user_id:
        q = q.where(ApiAuditLog.user_id == user_id)
    if errors_only:
        q = q.where(
            (ApiAuditLog.status_code >= 400) | (ApiAuditLog.error_message != None)
        )
    logs = (await db.execute(q.offset(offset).limit(limit))).scalars().all()

    user_ids = {log.user_id for log in logs if log.user_id}
    users = {
        u.id: u.username
        for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars()
    }

    return JSONResponse([{
        "id": l.id,
        "user_id": l.user_id,
        "username": users.get(l.user_id, "unknown"),
        "endpoint": l.endpoint,
        "method": l.method,
        "status_code": l.status_code,
        "model_provider": l.model_provider,
        "model_id": l.model_id,
        "tokens_used": l.tokens_used,
        "latency_ms": l.latency_ms,
        "error_message": l.error_message,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    } for l in logs])
