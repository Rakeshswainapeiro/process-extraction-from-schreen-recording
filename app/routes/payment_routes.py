"""
Stripe payment routes.

Flow:
  1. POST /api/payments/checkout  — frontend calls this, gets back a Stripe checkout URL
  2. Stripe redirects user to /upgrade?success=1  or  /upgrade?cancelled=1
  3. POST /api/payments/webhook   — Stripe calls this after payment is confirmed;
                                    we verify signature and credit the user's quota.

If STRIPE_SECRET_KEY is not set the checkout endpoint falls back to demo mode
(credits applied immediately, no real charge) so the app still works in dev.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.routes.auth_routes import require_user
from app.services.usage_service import apply_purchase
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments")

# ── Pack catalogue (mirrors upgrade.html) ────────────────────────────────────

PACKS = {
    "sessions": {
        5:        {"amount_usd_cents": 499,   "label": "5 Sessions"},
        20:       {"amount_usd_cents": 1499,  "label": "20 Sessions"},
        100:      {"amount_usd_cents": 4999,  "label": "100 Sessions"},
    },
    "tokens": {
        500_000:      {"amount_usd_cents": 499,   "label": "500K Tokens"},
        2_000_000:    {"amount_usd_cents": 1499,  "label": "2M Tokens"},
        10_000_000:   {"amount_usd_cents": 4999,  "label": "10M Tokens"},
    },
}


def _validate_pack(purchase_type: str, quantity: int) -> dict:
    if purchase_type not in PACKS:
        raise HTTPException(400, "purchase_type must be 'sessions' or 'tokens'")
    pack = PACKS[purchase_type].get(quantity)
    if not pack:
        raise HTTPException(400, f"Invalid quantity {quantity} for {purchase_type}")
    return pack


# ── Checkout ──────────────────────────────────────────────────────────────────

@router.post("/checkout")
async def create_checkout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_user),
):
    """
    Create a Stripe Checkout session and return the hosted URL.
    If Stripe is not configured, falls back to demo mode (instant credit).
    """
    data = await request.json()
    purchase_type: str = data.get("purchase_type", "")
    quantity: int = int(data.get("quantity", 0))
    pack = _validate_pack(purchase_type, quantity)

    # ── DEMO MODE — no Stripe keys ────────────────────────────────────────────
    if not settings.STRIPE_SECRET_KEY:
        purchase = await apply_purchase(
            db=db,
            user_id=user.id,
            purchase_type=purchase_type,
            quantity=quantity,
            amount_usd_cents=pack["amount_usd_cents"],
            payment_reference="demo-" + str(user.id),
            notes="Demo purchase (no payment gateway configured)",
        )
        return JSONResponse({
            "mode": "demo",
            "purchase_id": purchase.id,
            "redirect_url": f"{settings.APP_BASE_URL}/upgrade?success=1&demo=1",
        })

    # ── STRIPE MODE ───────────────────────────────────────────────────────────
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": pack["amount_usd_cents"],
                "product_data": {
                    "name": f"Process Extractor Pro — {pack['label']}",
                    "description": f"{quantity} {purchase_type} added to your account",
                },
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=(
            f"{settings.APP_BASE_URL}/upgrade"
            "?success=1&session_id={CHECKOUT_SESSION_ID}"
        ),
        cancel_url=f"{settings.APP_BASE_URL}/upgrade?cancelled=1",
        metadata={
            "user_id": str(user.id),
            "purchase_type": purchase_type,
            "quantity": str(quantity),
            "amount_usd_cents": str(pack["amount_usd_cents"]),
        },
        customer_email=user.email,
    )

    logger.info(
        "Stripe checkout created: user=%s type=%s qty=%s session=%s",
        user.id, purchase_type, quantity, session.id,
    )
    return JSONResponse({"mode": "stripe", "redirect_url": session.url})


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Stripe sends a POST here after payment is confirmed.
    IMPORTANT: register this URL in the Stripe dashboard and set STRIPE_WEBHOOK_SECRET.
    The endpoint does NOT require user auth — it is secured by the Stripe signature.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(501, "Stripe webhook secret not configured")

    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    payload = await request.body()
    sig_header: Optional[str] = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        raise HTTPException(400, "Invalid Stripe signature")
    except Exception as exc:
        logger.error("Stripe webhook parse error: %s", exc)
        raise HTTPException(400, "Webhook parse error")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta = session.get("metadata", {})

        user_id = int(meta.get("user_id", 0))
        purchase_type = meta.get("purchase_type", "")
        quantity = int(meta.get("quantity", 0))
        amount_usd_cents = int(meta.get("amount_usd_cents", 0))

        if not user_id or not purchase_type or not quantity:
            logger.error("Stripe webhook missing metadata: %s", meta)
            return JSONResponse({"status": "ignored"})

        await apply_purchase(
            db=db,
            user_id=user_id,
            purchase_type=purchase_type,
            quantity=quantity,
            amount_usd_cents=amount_usd_cents,
            payment_reference=session.get("id"),
            notes=f"Stripe payment {session.get('payment_intent')}",
        )
        logger.info(
            "Stripe webhook: credited user=%s type=%s qty=%s",
            user_id, purchase_type, quantity,
        )

    return JSONResponse({"status": "ok"})
