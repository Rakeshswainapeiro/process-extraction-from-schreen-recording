"""
Resolves which AI model to use for a given user.

Priority order:
  1. preferred_config_id  — user explicitly selected a model in the switcher
  2. User's DB default model  (AIModelConfig.is_default=True)
  3. User's any active model  (most recently created)
  4. Platform defaults  (PlatformConfig table, admin-managed)
  5. Env vars  (backward compatibility with existing .env setup)
  6. Demo/mock mode  (no AI keys anywhere)
"""
from __future__ import annotations
import os
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.database import AIModelConfig, PlatformConfig

logger = logging.getLogger(__name__)


@dataclass
class ResolvedModel:
    provider: str        # anthropic | openai | custom | demo
    model_id: str
    api_key: str
    base_url: Optional[str]
    max_tokens: int
    config_id: Optional[int]   # None when resolved from platform/env/demo


async def resolve_model(
    db: AsyncSession,
    user_id: int,
    preferred_config_id: Optional[int] = None,
) -> ResolvedModel:
    from app.services.encryption_service import get_encryption_service
    enc = get_encryption_service()

    # ── 1 & 2 & 3: user-level config ─────────────────────────────────────────
    query = select(AIModelConfig).where(
        AIModelConfig.user_id == user_id,
        AIModelConfig.is_active == True,
    )
    if preferred_config_id:
        query = query.where(AIModelConfig.id == preferred_config_id)
    else:
        # default first, then newest
        query = query.order_by(
            AIModelConfig.is_default.desc(),
            AIModelConfig.created_at.desc(),
        )

    cfg = (await db.execute(query.limit(1))).scalar_one_or_none()

    if cfg:
        api_key = enc.decrypt(cfg.api_key) if cfg.is_encrypted else cfg.api_key
        return ResolvedModel(
            provider=cfg.provider,
            model_id=cfg.model_id,
            api_key=api_key,
            base_url=cfg.base_url,
            max_tokens=cfg.max_tokens or 8000,
            config_id=cfg.id,
        )

    # ── 4: platform config table ──────────────────────────────────────────────
    rows = (await db.execute(select(PlatformConfig))).scalars().all()
    platform = {r.key: r.value for r in rows}

    if platform.get("default_model_id"):
        raw_key = platform.get("default_model_api_key", "")
        if raw_key:
            try:
                api_key = enc.decrypt(raw_key)
            except Exception:
                api_key = raw_key   # stored unencrypted (legacy / admin typed it in)
        else:
            api_key = ""

        # Skip platform config if no API key is set — fall through to env vars
        if not api_key.strip():
            logger.warning("Platform default model has no API key — falling back to env vars")
        else:
            return ResolvedModel(
                provider=platform.get("default_model_provider", "anthropic"),
                model_id=platform["default_model_id"],
                api_key=api_key,
                base_url=platform.get("default_model_base_url") or None,
                max_tokens=int(platform.get("default_max_tokens", 8000)),
                config_id=None,
            )

    # ── 5: env vars (existing behaviour, kept for zero-config dev) ────────────
    from config import settings

    if settings.CUSTOM_AI_BASE_URL:
        return ResolvedModel(
            provider="custom",
            model_id=settings.CUSTOM_AI_MODEL or "gpt-4o",
            api_key=settings.CUSTOM_AI_API_KEY or settings.ANTHROPIC_API_KEY,
            base_url=settings.CUSTOM_AI_BASE_URL,
            max_tokens=8000,
            config_id=None,
        )

    if settings.ANTHROPIC_API_KEY:
        return ResolvedModel(
            provider="anthropic",
            model_id="claude-sonnet-4-6",
            api_key=settings.ANTHROPIC_API_KEY,
            base_url=None,
            max_tokens=8000,
            config_id=None,
        )

    # ── 6: demo / mock ────────────────────────────────────────────────────────
    logger.warning("No AI model configured for user %s — using demo mode", user_id)
    return ResolvedModel(
        provider="demo",
        model_id="demo",
        api_key="",
        base_url=None,
        max_tokens=0,
        config_id=None,
    )
