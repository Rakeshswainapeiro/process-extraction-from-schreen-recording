"""Usage quota and purchase routes."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import UsageEvent, Purchase, get_db
from app.routes.auth_routes import require_user
from app.services.usage_service import get_quota_summary, apply_purchase

router = APIRouter(prefix="/api/usage")


@router.get("/me")
async def my_quota(db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    """Return the current user's quota and trial status."""
    summary = await get_quota_summary(db, user.id)
    return JSONResponse(summary)


@router.get("/history")
async def usage_history(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Paginated list of usage events for the current user."""
    result = await db.execute(
        select(UsageEvent)
        .where(UsageEvent.user_id == user.id)
        .order_by(UsageEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    events = result.scalars().all()
    return JSONResponse([{
        "id": e.id,
        "event_type": e.event_type,
        "sessions_delta": e.sessions_delta,
        "tokens_delta": e.tokens_delta,
        "model_provider": e.model_provider,
        "model_id": e.model_id,
        "recording_id": e.recording_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in events])


@router.get("/purchases")
async def my_purchases(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """Purchase history for the current user."""
    result = await db.execute(
        select(Purchase)
        .where(Purchase.user_id == user.id)
        .order_by(Purchase.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    purchases = result.scalars().all()
    return JSONResponse([{
        "id": p.id,
        "purchase_type": p.purchase_type,
        "quantity": p.quantity,
        "amount_usd_cents": p.amount_usd_cents,
        "amount_usd": p.amount_usd_cents / 100,
        "status": p.status,
        "payment_reference": p.payment_reference,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    } for p in purchases])


@router.post("/purchases")
async def create_purchase(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """
    Record a completed purchase and credit the user's quota.

    In production this endpoint is called by your payment processor webhook
    (e.g. Stripe) after verifying the payment signature — not directly by clients.
    """
    data = await request.json()
    purchase_type = data.get("purchase_type", "")
    if purchase_type not in ("sessions", "tokens"):
        raise HTTPException(400, "purchase_type must be 'sessions' or 'tokens'")

    quantity = int(data.get("quantity", 0))
    if quantity <= 0:
        raise HTTPException(400, "quantity must be a positive integer")

    purchase = await apply_purchase(
        db=db,
        user_id=user.id,
        purchase_type=purchase_type,
        quantity=quantity,
        amount_usd_cents=int(data.get("amount_usd_cents", 0)),
        payment_reference=data.get("payment_reference"),
        notes=data.get("notes"),
    )

    return JSONResponse({
        "id": purchase.id,
        "user_id": purchase.user_id,
        "purchase_type": purchase.purchase_type,
        "quantity": purchase.quantity,
        "amount_usd": purchase.amount_usd_cents / 100,
        "status": purchase.status,
        "created_at": purchase.created_at.isoformat() if purchase.created_at else None,
    })
