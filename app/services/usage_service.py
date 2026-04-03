"""
Usage quota management: trial tracking, paid balance deduction, purchase grants.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.database import UsageQuota, UsageEvent, Purchase, PlatformConfig

logger = logging.getLogger(__name__)


# ── Quota helpers ─────────────────────────────────────────────────────────────

async def get_or_create_quota(db: AsyncSession, user_id: int) -> UsageQuota:
    """Return the user's UsageQuota row, creating it on first access."""
    quota = (await db.execute(
        select(UsageQuota).where(UsageQuota.user_id == user_id)
    )).scalar_one_or_none()

    if quota is None:
        trial_max = await _get_platform_int(db, "trial_quota_sessions", 3)
        quota = UsageQuota(
            user_id=user_id,
            is_trial=True,
            trial_sessions_max=trial_max,
        )
        db.add(quota)
        await db.flush()

    return quota


async def get_quota_summary(db: AsyncSession, user_id: int) -> dict:
    """Return a serialisable summary of the user's quota state."""
    quota = await get_or_create_quota(db, user_id)
    return {
        "is_trial": quota.is_trial,
        "trial_sessions_used": quota.trial_sessions_used,
        "trial_sessions_max": quota.trial_sessions_max,
        "purchased_sessions": quota.purchased_sessions,
        "purchased_tokens": quota.purchased_tokens,
        "used_sessions": quota.used_sessions,
        "used_tokens": quota.used_tokens,
        "remaining_sessions": quota.remaining_sessions,
        "remaining_tokens": quota.remaining_tokens,
        "can_use_trial": quota.can_use_trial,
        "can_use_ai": quota.can_use_ai,
    }


# ── Guard + Deduction ─────────────────────────────────────────────────────────

async def check_and_deduct(
    db: AsyncSession,
    user_id: int,
    recording_id: Optional[int],
    model_config_id: Optional[int],
    model_provider: str,
    model_id: str,
    tokens_used: int = 0,
) -> str:
    """
    Validates quota, deducts one session (and tokens), writes an immutable
    UsageEvent. Call AFTER a successful AI response so failures don't cost credits.

    Returns the event_type consumed: 'trial' | 'session' | 'token'.
    Raises HTTP 402 when quota is exhausted.
    """
    quota = await get_or_create_quota(db, user_id)

    if not quota.can_use_ai:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "QUOTA_EXHAUSTED",
                "message": "You have used all available sessions and tokens. Please upgrade to continue.",
                "remaining_sessions": quota.remaining_sessions,
                "remaining_tokens": quota.remaining_tokens,
            },
        )

    # Determine deduction bucket: trial → sessions → tokens
    if quota.can_use_trial:
        event_type = "trial"
        quota.trial_sessions_used += 1
        if quota.trial_sessions_used >= quota.trial_sessions_max:
            quota.is_trial = False

    elif quota.remaining_sessions > 0:
        event_type = "session"
        quota.used_sessions += 1

    else:
        event_type = "token"
        if tokens_used > 0 and quota.remaining_tokens < tokens_used:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "TOKENS_EXHAUSTED",
                    "message": (
                        f"Need ~{tokens_used} tokens but only "
                        f"{quota.remaining_tokens} remain."
                    ),
                },
            )
        quota.used_tokens += tokens_used

    # Always accumulate token usage for reporting regardless of bucket
    if tokens_used > 0 and event_type != "token":
        quota.used_tokens += tokens_used

    quota.updated_at = datetime.utcnow()

    event = UsageEvent(
        user_id=user_id,
        recording_id=recording_id,
        event_type=event_type,
        sessions_delta=1 if event_type in ("trial", "session") else 0,
        tokens_delta=tokens_used,
        model_config_id=model_config_id,
        model_provider=model_provider,
        model_id=model_id,
    )
    db.add(event)
    await db.commit()

    logger.info(
        "Usage deducted: user=%s type=%s tokens=%s provider=%s model=%s",
        user_id, event_type, tokens_used, model_provider, model_id,
    )
    return event_type


# ── Purchase / Grant ──────────────────────────────────────────────────────────

async def apply_purchase(
    db: AsyncSession,
    user_id: int,
    purchase_type: str,         # "sessions" | "tokens"
    quantity: int,
    amount_usd_cents: int,
    payment_reference: Optional[str] = None,
    notes: Optional[str] = None,
    granted_by: Optional[int] = None,
) -> Purchase:
    """
    Adds credits to the user's quota and records the purchase.
    Used both for real payments (webhook) and admin manual grants.
    """
    quota = await get_or_create_quota(db, user_id)

    if purchase_type == "sessions":
        quota.purchased_sessions += quantity
    elif purchase_type == "tokens":
        quota.purchased_tokens += quantity
    else:
        raise ValueError(f"Invalid purchase_type: {purchase_type}")

    quota.updated_at = datetime.utcnow()

    purchase = Purchase(
        user_id=user_id,
        purchase_type=purchase_type,
        quantity=quantity,
        amount_usd_cents=amount_usd_cents,
        payment_reference=payment_reference,
        status="completed",
        notes=notes,
        created_by=granted_by,
    )
    db.add(purchase)
    await db.commit()
    await db.refresh(purchase)
    return purchase


# ── Platform config helper ────────────────────────────────────────────────────

async def _get_platform_int(db: AsyncSession, key: str, default: int) -> int:
    row = (await db.execute(
        select(PlatformConfig).where(PlatformConfig.key == key)
    )).scalar_one_or_none()
    return int(row.value) if row else default
