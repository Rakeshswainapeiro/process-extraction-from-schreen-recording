# SaaS AI Model Management — Complete Implementation Plan

> **Stack context**: FastAPI + SQLAlchemy + SQLite + Jinja2 + Vanilla JS.
> The existing codebase already has `AIModelConfig`, settings CRUD, multi-provider `ProcessAnalyzer`, and JWT auth.
> This document covers exactly what is missing and how to build it.

---

## Table of Contents

1. [System Architecture Diagram](#1-system-architecture-diagram)
2. [Database Schema](#2-database-schema)
3. [API Endpoints](#3-api-endpoints)
4. [Frontend Flow](#4-frontend-flow)
5. [Key Code Snippets](#5-key-code-snippets)
6. [Security Best Practices](#6-security-best-practices)
7. [Edge Cases](#7-edge-cases)
8. [Implementation Order](#8-implementation-order-recommended)

---

## 1. System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLIENT LAYER                               │
│                                                                     │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────────────┐ │
│  │  Dashboard  │  │  Settings Page   │  │  Super Admin Panel     │ │
│  │ (recorder + │  │ (model config +  │  │ (users, usage,         │ │
│  │  switcher)  │  │  usage meters)   │  │  revenue, logs)        │ │
│  └──────┬──────┘  └────────┬─────────┘  └──────────┬─────────────┘ │
└─────────┼──────────────────┼────────────────────────┼───────────────┘
          │  HTTPS + JWT Cookie                        │
┌─────────▼──────────────────▼────────────────────────▼───────────────┐
│                       FASTAPI APPLICATION                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     MIDDLEWARE CHAIN                         │   │
│  │  [Auth] → [UsageGuard] → [ModelResolver] → [AuditLogger]    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────┐ ┌─────────────┐  │
│  │ auth_routes │ │recording_    │ │settings_   │ │admin_routes │  │
│  │             │ │routes        │ │routes      │ │             │  │
│  └─────────────┘ └──────────────┘ └────────────┘ └─────────────┘  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                      SERVICE LAYER                          │   │
│  │  ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐ │   │
│  │  │ProcessAnalyzer│  │UsageService   │  │EncryptionService│ │   │
│  │  │(AI dispatch)  │  │(quota/billing)│  │(API key vault)  │ │   │
│  │  └───────────────┘  └───────────────┘  └─────────────────┘ │   │
│  │  ┌───────────────┐  ┌───────────────┐                       │   │
│  │  │ModelResolver  │  │AdminService   │                       │   │
│  │  │(user→model)   │  │(reporting)    │                       │   │
│  │  └───────────────┘  └───────────────┘                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
          │                       │
┌─────────▼──────────┐   ┌────────▼───────────────────────────────┐
│  SQLite (dev)      │   │  EXTERNAL AI PROVIDERS                 │
│  PostgreSQL (prod) │   │  ┌────────────┐ ┌──────┐ ┌──────────┐ │
│                    │   │  │ Anthropic  │ │OpenAI│ │ Custom   │ │
│  Tables:           │   │  │ Claude API │ │ API  │ │ Endpoint │ │
│  - users           │   │  └────────────┘ └──────┘ └──────────┘ │
│  - ai_model_config │   └────────────────────────────────────────┘
│  - platform_config │
│  - usage_quotas    │
│  - usage_events    │
│  - purchases       │
│  - api_audit_logs  │
└────────────────────┘

MODEL RESOLUTION ORDER (per request):
  1. preferred_config_id (user switched in UI this session)
  2. User's DB default model (AIModelConfig.is_default=True)
  3. User's any active model (AIModelConfig.is_active=True)
  4. Platform default (PlatformConfig table, admin-controlled)
  5. Env vars (backward compat: CUSTOM_AI_BASE_URL / ANTHROPIC_API_KEY)
  6. Demo/mock mode
```

---

## 2. Database Schema

### New Tables

```sql
-- ─────────────────────────────────────────────
-- PLATFORM CONFIG  (admin-controlled defaults)
-- ─────────────────────────────────────────────
CREATE TABLE platform_config (
    id              INTEGER PRIMARY KEY,
    key             TEXT UNIQUE NOT NULL,
    value           TEXT NOT NULL,
    description     TEXT,
    updated_by      INTEGER REFERENCES users(id),
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Seed rows:
-- key='default_model_provider'   value='anthropic'
-- key='default_model_id'         value='claude-sonnet-4-6'
-- key='default_model_base_url'   value=''
-- key='default_model_api_key'    value='<encrypted>'
-- key='default_max_tokens'       value='8000'
-- key='trial_quota_sessions'     value='3'
-- key='trial_quota_tokens'       value='50000'

-- ─────────────────────────────────────────────
-- USAGE QUOTAS  (one row per user)
-- ─────────────────────────────────────────────
CREATE TABLE usage_quotas (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER UNIQUE NOT NULL REFERENCES users(id),

    -- Trial state
    is_trial            BOOLEAN DEFAULT TRUE,
    trial_sessions_used INTEGER DEFAULT 0,
    trial_sessions_max  INTEGER DEFAULT 3,     -- copied from platform_config at signup

    -- Purchased balances (additive; never go negative)
    purchased_sessions  INTEGER DEFAULT 0,
    purchased_tokens    INTEGER DEFAULT 0,

    -- Consumed from purchased balance
    used_sessions       INTEGER DEFAULT 0,
    used_tokens         INTEGER DEFAULT 0,

    -- Lifecycle
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Derived values (computed in Python, not SQL):
--   remaining_sessions = purchased_sessions - used_sessions
--   remaining_tokens   = purchased_tokens   - used_tokens
--   can_use_trial      = is_trial AND trial_sessions_used < trial_sessions_max
--   can_use_ai         = can_use_trial OR remaining_sessions > 0 OR remaining_tokens > 0

-- ─────────────────────────────────────────────
-- USAGE EVENTS  (immutable ledger)
-- ─────────────────────────────────────────────
CREATE TABLE usage_events (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    recording_id    INTEGER REFERENCES recordings(id),
    event_type      TEXT NOT NULL,   -- 'trial' | 'session' | 'token'
    sessions_delta  INTEGER DEFAULT 0,
    tokens_delta    INTEGER DEFAULT 0,
    model_config_id INTEGER REFERENCES ai_model_config(id),
    model_provider  TEXT,
    model_id        TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- PURCHASES
-- ─────────────────────────────────────────────
CREATE TABLE purchases (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    purchase_type       TEXT NOT NULL,          -- 'sessions' | 'tokens'
    quantity            INTEGER NOT NULL,
    amount_usd_cents    INTEGER NOT NULL,
    payment_reference   TEXT,                   -- Stripe payment intent ID, etc.
    status              TEXT DEFAULT 'completed', -- 'pending'|'completed'|'refunded'
    notes               TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by          INTEGER REFERENCES users(id)  -- admin manual grants
);

-- ─────────────────────────────────────────────
-- API AUDIT LOGS
-- ─────────────────────────────────────────────
CREATE TABLE api_audit_logs (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    endpoint        TEXT NOT NULL,
    method          TEXT NOT NULL,
    status_code     INTEGER,
    model_provider  TEXT,
    model_id        TEXT,
    tokens_used     INTEGER DEFAULT 0,
    latency_ms      INTEGER,
    error_message   TEXT,
    ip_address      TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for query performance
CREATE INDEX idx_usage_events_user  ON usage_events(user_id, created_at);
CREATE INDEX idx_audit_logs_user    ON api_audit_logs(user_id, created_at);
CREATE INDEX idx_audit_logs_created ON api_audit_logs(created_at);
CREATE INDEX idx_purchases_user     ON purchases(user_id, created_at);
```

### Alterations to Existing Tables

```sql
-- Encrypt API keys stored in ai_model_config
ALTER TABLE ai_model_config ADD COLUMN is_encrypted BOOLEAN DEFAULT FALSE;

-- Promote a user to super admin
ALTER TABLE users ADD COLUMN is_super_admin BOOLEAN DEFAULT FALSE;
```

### SQLAlchemy Model Additions (`app/models/database.py`)

```python
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, ForeignKey
)
from sqlalchemy.orm import relationship

class PlatformConfig(Base):
    __tablename__ = "platform_config"
    id          = Column(Integer, primary_key=True)
    key         = Column(String, unique=True, nullable=False)
    value       = Column(Text, nullable=False)
    description = Column(Text)
    updated_by  = Column(Integer, ForeignKey("users.id"))
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UsageQuota(Base):
    __tablename__ = "usage_quotas"
    id                  = Column(Integer, primary_key=True)
    user_id             = Column(Integer, ForeignKey("users.id"), unique=True)
    is_trial            = Column(Boolean, default=True)
    trial_sessions_used = Column(Integer, default=0)
    trial_sessions_max  = Column(Integer, default=3)
    purchased_sessions  = Column(Integer, default=0)
    purchased_tokens    = Column(Integer, default=0)
    used_sessions       = Column(Integer, default=0)
    used_tokens         = Column(Integer, default=0)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user                = relationship("User", back_populates="quota")

    @property
    def remaining_sessions(self) -> int:
        return max(0, self.purchased_sessions - self.used_sessions)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.purchased_tokens - self.used_tokens)

    @property
    def can_use_trial(self) -> bool:
        return self.is_trial and self.trial_sessions_used < self.trial_sessions_max

    @property
    def can_use_ai(self) -> bool:
        return self.can_use_trial or self.remaining_sessions > 0 or self.remaining_tokens > 0


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id              = Column(Integer, primary_key=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False)
    recording_id    = Column(Integer, ForeignKey("recordings.id"))
    event_type      = Column(String, nullable=False)  # trial | session | token
    sessions_delta  = Column(Integer, default=0)
    tokens_delta    = Column(Integer, default=0)
    model_config_id = Column(Integer, ForeignKey("ai_model_config.id"))
    model_provider  = Column(String)
    model_id        = Column(String)
    created_at      = Column(DateTime, default=datetime.utcnow)


class Purchase(Base):
    __tablename__ = "purchases"
    id                  = Column(Integer, primary_key=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False)
    purchase_type       = Column(String, nullable=False)  # sessions | tokens
    quantity            = Column(Integer, nullable=False)
    amount_usd_cents    = Column(Integer, nullable=False)
    payment_reference   = Column(String)
    status              = Column(String, default="completed")
    notes               = Column(Text)
    created_at          = Column(DateTime, default=datetime.utcnow)
    created_by          = Column(Integer, ForeignKey("users.id"))
    user                = relationship("User", foreign_keys=[user_id])


class ApiAuditLog(Base):
    __tablename__ = "api_audit_logs"
    id             = Column(Integer, primary_key=True)
    user_id        = Column(Integer, ForeignKey("users.id"))
    endpoint       = Column(String, nullable=False)
    method         = Column(String, nullable=False)
    status_code    = Column(Integer)
    model_provider = Column(String)
    model_id       = Column(String)
    tokens_used    = Column(Integer, default=0)
    latency_ms     = Column(Integer)
    error_message  = Column(Text)
    ip_address     = Column(String)
    created_at     = Column(DateTime, default=datetime.utcnow)
```

### Entity Relationship Overview

```
users ─────────────┬──── usage_quotas    (1:1)
                   ├──── ai_model_config (1:N)
                   ├──── usage_events    (1:N)
                   ├──── purchases       (1:N)
                   ├──── api_audit_logs  (1:N)
                   └──── recordings      (1:N)
                              └──── usage_events (1:N, nullable)

platform_config (key-value store, admin-controlled)
```

---

## 3. API Endpoints

### A. Settings / Model Management _(existing — keep as-is)_

```
GET    /api/settings/models              → list user's models
POST   /api/settings/models              → add model
PUT    /api/settings/models/{id}         → update model
DELETE /api/settings/models/{id}         → delete model
POST   /api/settings/models/{id}/default → set as default
POST   /api/settings/models/test         → test connection
```

### B. Model Switcher _(new)_

```
GET  /api/settings/models/active
     → Currently active model for this session
       { id, provider, name, model_id, max_tokens, is_default }

POST /api/settings/models/active
     Body: { config_id: int }
     → Sets session-active model (stored in DB flag or session cookie)
```

### C. Usage & Quota _(new)_

```
GET  /api/usage/me
     → { is_trial, trial_sessions_used, trial_sessions_max,
         purchased_sessions, purchased_tokens,
         used_sessions, used_tokens,
         remaining_sessions, remaining_tokens,
         can_use_ai }

GET  /api/usage/history?limit=50&offset=0
     → [ { event_type, sessions_delta, tokens_delta,
           model_id, created_at, recording_id } ]
```

### D. Purchase / Top-Up _(new)_

```
POST /api/purchases
     Body: { purchase_type: "sessions"|"tokens",
             quantity: int,
             amount_usd_cents: int,
             payment_reference: str }
     → { id, user_id, purchase_type, quantity, status, created_at }
     NOTE: In production, call this from your payment processor webhook
           (e.g. Stripe), not directly from the client.

GET  /api/purchases/me
     → [ { id, purchase_type, quantity, amount_usd_cents,
           status, created_at } ]
```

### E. Admin Routes _(new — require `is_super_admin=True`)_

```
-- Dashboard Stats --
GET  /api/admin/dashboard
     → { total_users, active_users, trial_users, paid_users,
         sessions_today, tokens_today, revenue_this_month_cents }

-- User Management --
GET  /api/admin/users?page=1&limit=50&search=&status=
     → { users: [...], total, pages }

PATCH /api/admin/users/{user_id}
     Body: { is_active: bool } | { is_super_admin: bool }
     → updated user object

-- Usage Overview --
GET  /api/admin/usage/summary
     → [ { user_id, username, email, trial_sessions_used,
           used_sessions, used_tokens, remaining_sessions,
           remaining_tokens, total_purchases_usd_cents, last_activity } ]

GET  /api/admin/usage/events?user_id=&from=&to=&limit=100
     → Paginated usage events with user context

-- Revenue --
GET  /api/admin/purchases?from=&to=&status=
     → [ { id, user.email, purchase_type, quantity,
           amount_usd_cents, status, payment_reference, created_at } ]

GET  /api/admin/purchases/stats
     → { total_revenue_cents, sessions_sold, tokens_sold,
         purchase_count, breakdown_by_type }

POST /api/admin/purchases/grant
     Body: { user_id, purchase_type, quantity, notes }
     → Manual quota grant (amount_usd_cents=0)

-- Platform Model Config --
GET  /api/admin/platform/config
     → { default_model_provider, default_model_id,
         default_model_base_url, default_max_tokens,
         trial_quota_sessions, trial_quota_tokens }

PATCH /api/admin/platform/config
     Body: { key: value, ... }   (partial update, keys match PlatformConfig.key)
     → { status: "ok" }

POST /api/admin/platform/config/test
     → Tests connectivity of the current platform default model

-- Audit Logs --
GET  /api/admin/logs?user_id=&endpoint=&from=&to=&limit=100
     → [ { id, user.email, endpoint, method, status_code,
           model_provider, model_id, tokens_used,
           latency_ms, error_message, created_at } ]

GET  /api/admin/logs/errors?from=&to=
     → Logs where status_code >= 400 or error_message IS NOT NULL
```

### F. Page Routes _(new HTML pages)_

```
GET  /admin              → Super Admin dashboard
GET  /admin/users        → User management
GET  /admin/usage        → Usage analytics
GET  /admin/purchases    → Revenue
GET  /admin/logs         → Audit log viewer
GET  /upgrade            → Upgrade/purchase page (end-user facing)
```

---

## 4. Frontend Flow

### 4A. Model Switcher (Dashboard Header)

```
┌──────────────────────────────────────────────────────────────────┐
│  DASHBOARD HEADER                                                │
│  ┌──────────────────────────────┐  ┌─────────────────────────┐  │
│  │  Process Extractor Pro       │  │  [Model: DeepSeek V3 ▾] │  │
│  └──────────────────────────────┘  └─────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

On dropdown click:
┌─────────────────────────────┐
│  Your Models                │
│  ● DeepSeek V3.1  ✓ active  │  ← currently selected
│  ○ GPT-4o                   │
│  ○ Claude Sonnet 4.6        │
│  ─────────────────────────  │
│  + Add New Model            │  ← links to /settings
│  ─────────────────────────  │
│  Platform Default (fallback)│
└─────────────────────────────┘

State stored in: localStorage['active_model_id']
                 + POST /api/settings/models/active on change
```

### 4B. Usage Meter (Dashboard Sidebar / Header)

```
Normal (trial active):
┌─────────────────────────────────────────────┐
│  Usage                                      │
│  ┌─────────────────────────────────────┐    │
│  │  Trial  ████░░  2 / 3 sessions      │    │
│  └─────────────────────────────────────┘    │
│  Sessions remaining: 0                      │
│  Tokens remaining:   48,210                 │
│  [Upgrade →]                                │
└─────────────────────────────────────────────┘

When trial + purchased credits exhausted:
┌─────────────────────────────────────────────┐
│  ⚠ You've used all free sessions            │
│  Purchase sessions or tokens to continue.  │
│  [Buy 10 Sessions — $9]  [Buy Tokens — $5] │
└─────────────────────────────────────────────┘
Start Recording button → disabled
```

### 4C. Settings Page — Model Configuration (extending existing `settings.html`)

```
FLOW:
  1. User opens /settings
  2. GET /api/settings/models  → renders model cards
  3. User clicks "Add Model"
     ├─ Modal opens with form:
     │    Provider (select): OpenAI | Anthropic | Custom
     │    Name: [________________]
     │    Model ID: [____________]
     │    API Key: [●●●●●●●●●●●]  (write-only; never shown again)
     │    Base URL: [___________]  (shown only for Custom provider)
     │    Max Tokens: [8000     ]
     │    [Test Connection]  [Save]
     └─ On save: POST /api/settings/models
  4. "Set as Default" on a card  → POST /api/settings/models/{id}/set-default
  5. Usage meter loaded from GET /api/usage/me
```

### 4D. Super Admin Panel Layout

```
┌─────────┬────────────────────────────────────────────────────────┐
│  ADMIN  │                     CONTENT AREA                      │
│  SIDEBAR│                                                        │
│         │  KPI CARDS:                                           │
│ Dashboard│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│ Users   │  │Total     │ │Active    │ │Sessions  │ │Revenue  │ │
│ Usage   │  │Users: 42 │ │Today: 12 │ │Today: 38 │ │$142/mo  │ │
│ Revenue │  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
│ Model   │                                                        │
│ Config  │  MODEL USAGE CHART (by provider, last 7 days)        │
│ Logs    │  [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]      │
│         │                                                        │
│ [Logout]│  RECENT ERRORS TABLE                                  │
└─────────┴────────────────────────────────────────────────────────┘
```

---

## 5. Key Code Snippets

### A. `app/services/encryption_service.py` — API Key Vault

```python
import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionService:
    """
    AES-256-GCM encryption for API keys.
    ENCRYPTION_KEY must be a 32-byte secret stored in environment,
    NOT in the database or source code.

    Generate key:
        python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
    """

    def __init__(self):
        raw = os.environ.get("ENCRYPTION_KEY", "")
        if not raw:
            raise RuntimeError("ENCRYPTION_KEY env var not set")
        # Accept hex (64 chars) or base64 encoded 32-byte key
        key_bytes = bytes.fromhex(raw) if len(raw) == 64 else base64.b64decode(raw)
        if len(key_bytes) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to exactly 32 bytes")
        self._aesgcm = AESGCM(key_bytes)

    def encrypt(self, plaintext: str) -> str:
        """Returns base64(nonce + ciphertext). Each call uses a fresh nonce."""
        nonce = os.urandom(12)  # 96-bit nonce — never reuse with the same key
        ct = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token)
        nonce, ct = raw[:12], raw[12:]
        return self._aesgcm.decrypt(nonce, ct, None).decode()


_svc: EncryptionService | None = None


def get_encryption_service() -> EncryptionService:
    global _svc
    if _svc is None:
        _svc = EncryptionService()
    return _svc
```

> Add `cryptography` to `requirements.txt`.

---

### B. `app/services/usage_service.py` — Trial & Quota Guard

```python
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from app.models.database import UsageQuota, UsageEvent, PlatformConfig
import logging

logger = logging.getLogger(__name__)


async def get_or_create_quota(db: AsyncSession, user_id: int) -> UsageQuota:
    result = await db.execute(
        select(UsageQuota).where(UsageQuota.user_id == user_id)
    )
    quota = result.scalar_one_or_none()
    if quota is None:
        # New user: read trial limit from admin-configured platform config
        trial_max = await _get_platform_int(db, "trial_quota_sessions", 3)
        quota = UsageQuota(user_id=user_id, is_trial=True, trial_sessions_max=trial_max)
        db.add(quota)
        await db.flush()
    return quota


async def check_and_deduct(
    db: AsyncSession,
    user_id: int,
    recording_id: int,
    model_config_id: int | None,
    model_provider: str,
    model_id: str,
    tokens_used: int = 0,
) -> str:
    """
    Validates quota, deducts one session (and tokens), writes an immutable
    UsageEvent record. Call AFTER a successful AI response — failures do not
    cost the user credits.

    Returns the event_type used: 'trial' | 'session' | 'token'.
    Raises HTTP 402 if quota is exhausted.
    """
    quota = await get_or_create_quota(db, user_id)

    if not quota.can_use_ai:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "QUOTA_EXHAUSTED",
                "message": "You have used all available sessions and tokens.",
                "remaining_sessions": quota.remaining_sessions,
                "remaining_tokens": quota.remaining_tokens,
            }
        )

    # Determine deduction bucket (trial first, then sessions, then tokens)
    if quota.can_use_trial:
        event_type = "trial"
        quota.trial_sessions_used += 1
        if quota.trial_sessions_used >= quota.trial_sessions_max:
            quota.is_trial = False  # trial fully consumed
    elif quota.remaining_sessions > 0:
        event_type = "session"
        quota.used_sessions += 1
    else:
        event_type = "token"
        if quota.remaining_tokens < tokens_used:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "TOKENS_EXHAUSTED",
                    "message": (
                        f"Need ~{tokens_used} tokens but only "
                        f"{quota.remaining_tokens} remain."
                    ),
                }
            )
        quota.used_tokens += tokens_used

    # Always accumulate token usage for reporting (even on trial/session events)
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
    logger.info(f"Usage deducted: user={user_id} type={event_type} tokens={tokens_used}")
    return event_type


async def apply_purchase(
    db: AsyncSession,
    user_id: int,
    purchase_type: str,       # 'sessions' | 'tokens'
    quantity: int,
    amount_usd_cents: int,
    payment_reference: str | None = None,
    granted_by: int | None = None,
) -> "Purchase":
    from app.models.database import Purchase
    quota = await get_or_create_quota(db, user_id)

    if purchase_type == "sessions":
        quota.purchased_sessions += quantity
    else:
        quota.purchased_tokens += quantity

    purchase = Purchase(
        user_id=user_id,
        purchase_type=purchase_type,
        quantity=quantity,
        amount_usd_cents=amount_usd_cents,
        payment_reference=payment_reference,
        status="completed",
        created_by=granted_by,
    )
    db.add(purchase)
    await db.commit()
    await db.refresh(purchase)
    return purchase


async def _get_platform_int(db: AsyncSession, key: str, default: int) -> int:
    row = await db.scalar(
        select(PlatformConfig).where(PlatformConfig.key == key)
    )
    return int(row.value) if row else default
```

---

### C. `app/services/model_resolver.py` — Central Model Selection

```python
import os
import logging
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.database import AIModelConfig, PlatformConfig
from app.services.encryption_service import get_encryption_service

logger = logging.getLogger(__name__)


@dataclass
class ResolvedModel:
    provider:   str
    model_id:   str
    api_key:    str
    base_url:   str | None
    max_tokens: int
    config_id:  int | None  # None = platform default or env fallback


async def resolve_model(
    db: AsyncSession,
    user_id: int,
    preferred_config_id: int | None = None,
) -> ResolvedModel:
    """
    Resolves the AI model to use for a given user.

    Priority order:
      1. preferred_config_id  (user selected a specific model in the UI)
      2. User's DB default model  (AIModelConfig.is_default=True)
      3. User's any active model  (AIModelConfig.is_active=True, most recent)
      4. Platform defaults  (PlatformConfig table, admin-managed)
      5. Env vars  (backward compatibility)
      6. Demo mode  (no AI, returns mock report)
    """
    enc = get_encryption_service()

    # --- Priorities 1, 2, 3: user-level config ---
    query = select(AIModelConfig).where(
        AIModelConfig.user_id == user_id,
        AIModelConfig.is_active == True,
    )
    if preferred_config_id:
        query = query.where(AIModelConfig.id == preferred_config_id)
    else:
        query = query.order_by(
            AIModelConfig.is_default.desc(),
            AIModelConfig.created_at.desc()
        )

    cfg = await db.scalar(query.limit(1))

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

    # --- Priority 4: platform config table ---
    rows = (await db.execute(select(PlatformConfig))).scalars().all()
    platform = {r.key: r.value for r in rows}

    if platform.get("default_model_id"):
        raw_key = platform.get("default_model_api_key", "")
        api_key = enc.decrypt(raw_key) if raw_key.startswith("enc:") else raw_key
        return ResolvedModel(
            provider=platform.get("default_model_provider", "anthropic"),
            model_id=platform["default_model_id"],
            api_key=api_key,
            base_url=platform.get("default_model_base_url") or None,
            max_tokens=int(platform.get("default_max_tokens", 8000)),
            config_id=None,
        )

    # --- Priority 5: env vars (existing behaviour) ---
    base_url = os.getenv("CUSTOM_AI_BASE_URL")
    if base_url:
        return ResolvedModel(
            provider="custom",
            model_id=os.getenv("CUSTOM_AI_MODEL", "gpt-4o"),
            api_key=os.getenv("CUSTOM_AI_API_KEY", ""),
            base_url=base_url,
            max_tokens=8000,
            config_id=None,
        )

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        return ResolvedModel(
            provider="anthropic",
            model_id="claude-sonnet-4-6",
            api_key=anthropic_key,
            base_url=None,
            max_tokens=8000,
            config_id=None,
        )

    # --- Priority 6: demo mode ---
    logger.warning(f"No AI model configured for user {user_id}. Using demo mode.")
    return ResolvedModel(
        provider="demo", model_id="demo",
        api_key="", base_url=None, max_tokens=0, config_id=None,
    )
```

---

### D. `app/services/process_analyzer.py` — Integration Patch

Replace the existing `_build_client()` and hardcoded model logic with:

```python
# In analyze_recording(), replace existing client-building logic with:

import time
from app.services.model_resolver import resolve_model
from app.services.usage_service import check_and_deduct
from app.models.database import ApiAuditLog


async def analyze_recording(
    self,
    db,
    recording_id: int,
    user_id: int,
    preferred_config_id: int | None = None,
):
    # 1. Resolve model — zero hardcoded values
    resolved = await resolve_model(db, user_id, preferred_config_id)

    # 2. Fetch activities (existing code, unchanged)
    activities = await self._get_activities(db, recording_id)
    recording  = await db.get(Recording, recording_id)

    # 3. Demo fallback — no deduction, no audit log
    if resolved.provider == "demo":
        return self._generate_demo_report(activities, recording)

    # 4. Call AI and measure latency
    t0 = time.monotonic()
    status_code = 200
    error_msg   = None
    tokens_used = 0

    try:
        response_text, tokens_used = await self._call_provider(resolved, activities, recording)
        result = self._parse_json_response(response_text)
    except Exception as e:
        status_code = 500
        error_msg   = str(e)
        raise
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        db.add(ApiAuditLog(
            user_id=user_id,
            endpoint=f"/api/recordings/{recording_id}/stop",
            method="POST",
            status_code=status_code,
            model_provider=resolved.provider,
            model_id=resolved.model_id,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            error_message=error_msg,
        ))

    # 5. Deduct usage AFTER successful AI call
    await check_and_deduct(
        db=db,
        user_id=user_id,
        recording_id=recording_id,
        model_config_id=resolved.config_id,
        model_provider=resolved.provider,
        model_id=resolved.model_id,
        tokens_used=tokens_used,
    )

    return result


async def _call_provider(self, resolved, activities, recording):
    """Returns (response_text: str, tokens_used: int)."""
    if resolved.provider == "anthropic":
        return await self._call_anthropic(resolved, activities, recording)
    return await self._call_openai_compat(resolved, activities, recording)


async def _call_anthropic(self, resolved, activities, recording):
    import anthropic
    client = anthropic.AsyncAnthropic(
        api_key=resolved.api_key,
        base_url=resolved.base_url,   # None → uses Anthropic's default endpoint
    )
    msg = await client.messages.create(
        model=resolved.model_id,
        max_tokens=resolved.max_tokens,
        messages=[{"role": "user", "content": self._build_prompt(activities, recording)}]
    )
    tokens = msg.usage.input_tokens + msg.usage.output_tokens
    return msg.content[0].text, tokens


async def _call_openai_compat(self, resolved, activities, recording):
    import httpx
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{resolved.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {resolved.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model":      resolved.model_id,
                "max_tokens": resolved.max_tokens,
                "messages":   [{"role": "user",
                                "content": self._build_prompt(activities, recording)}],
            }
        )
        resp.raise_for_status()
        data    = resp.json()
        text    = data["choices"][0]["message"]["content"]
        tokens  = data.get("usage", {}).get("total_tokens", 0)
        return text, tokens
```

---

### E. `app/routes/admin_routes.py` — Super Admin API

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from app.models.database import (
    User, UsageQuota, UsageEvent, Purchase, ApiAuditLog, PlatformConfig
)
from app.routes.auth_routes import get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_super_admin(current_user=Depends(get_current_user)):
    if not getattr(current_user, "is_super_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    return current_user


@router.get("/dashboard")
async def admin_dashboard(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    today = datetime.utcnow().date()
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)

    total_users    = await db.scalar(select(func.count(User.id)))
    active_users   = await db.scalar(select(func.count(User.id)).where(User.is_active == True))
    trial_users    = await db.scalar(select(func.count(UsageQuota.id)).where(UsageQuota.is_trial == True))
    sessions_today = await db.scalar(
        select(func.sum(UsageEvent.sessions_delta))
        .where(func.date(UsageEvent.created_at) == today)
    ) or 0
    tokens_today   = await db.scalar(
        select(func.sum(UsageEvent.tokens_delta))
        .where(func.date(UsageEvent.created_at) == today)
    ) or 0
    revenue        = await db.scalar(
        select(func.sum(Purchase.amount_usd_cents))
        .where(Purchase.created_at >= month_start, Purchase.status == "completed")
    ) or 0

    return {
        "total_users": total_users,
        "active_users": active_users,
        "trial_users": trial_users,
        "paid_users": (total_users or 0) - (trial_users or 0),
        "sessions_today": sessions_today,
        "tokens_today": tokens_today,
        "revenue_this_month_cents": revenue,
    }


@router.get("/users")
async def list_users(
    page: int = 1, limit: int = 50, search: str = "",
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    offset = (page - 1) * limit
    q = select(User)
    if search:
        q = q.where(
            User.username.ilike(f"%{search}%") | User.email.ilike(f"%{search}%")
        )
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    users = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return {"users": users, "total": total, "pages": -(-total // limit)}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int, body: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    for k in ("is_active", "is_super_admin"):
        if k in body:
            setattr(user, k, body[k])
    await db.commit()
    return user


@router.post("/purchases/grant")
async def grant_quota(
    body: dict,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    from app.services.usage_service import apply_purchase
    purchase = await apply_purchase(
        db=db,
        user_id=body["user_id"],
        purchase_type=body["purchase_type"],
        quantity=body["quantity"],
        amount_usd_cents=0,
        granted_by=admin.id,
    )
    return {"status": "granted", "purchase_id": purchase.id}


@router.get("/platform/config")
async def get_platform_config(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    rows = (await db.execute(select(PlatformConfig))).scalars().all()
    return {r.key: r.value for r in rows}


@router.patch("/platform/config")
async def update_platform_config(
    body: dict,
    db: AsyncSession = Depends(get_db),
    admin=Depends(require_super_admin),
):
    from app.services.encryption_service import get_encryption_service
    enc = get_encryption_service()
    for key, value in body.items():
        row = await db.scalar(select(PlatformConfig).where(PlatformConfig.key == key))
        if "api_key" in key and value:
            value = "enc:" + enc.encrypt(value)  # mark as encrypted
        if row:
            row.value = str(value)
            row.updated_by = admin.id
        else:
            db.add(PlatformConfig(key=key, value=str(value), updated_by=admin.id))
    await db.commit()
    return {"status": "ok"}
```

---

### F. Usage Meter & Model Switcher — Frontend JS

Add to `dashboard.js` or a shared `usage.js`:

```javascript
// ─── Usage Meter ────────────────────────────────────────────────────
async function loadUsageMeter() {
  const res = await fetch('/api/usage/me', { credentials: 'include' });
  if (!res.ok) return;
  const q = await res.json();

  const meter = document.getElementById('usage-meter');
  if (!meter) return;

  if (q.is_trial && q.trial_sessions_used < q.trial_sessions_max) {
    const pct = (q.trial_sessions_used / q.trial_sessions_max) * 100;
    meter.innerHTML = `
      <div class="usage-label">Free Trial</div>
      <div class="usage-bar">
        <div class="usage-fill" style="width:${pct}%"></div>
      </div>
      <div class="usage-text">
        ${q.trial_sessions_used} / ${q.trial_sessions_max} sessions used
      </div>`;
  } else if (!q.can_use_ai) {
    meter.innerHTML = `
      <div class="usage-exhausted">
        <span>⚠ No sessions remaining</span>
        <a href="/upgrade" class="btn-upgrade">Upgrade →</a>
      </div>`;
    document.getElementById('start-btn')?.setAttribute('disabled', 'true');
  } else {
    meter.innerHTML = `
      <div class="usage-label">Sessions: ${q.remaining_sessions} left</div>
      <div class="usage-label">
        Tokens: ${(q.remaining_tokens / 1000).toFixed(1)}k left
      </div>`;
  }
}

// ─── Model Switcher ─────────────────────────────────────────────────
async function loadModelSwitcher() {
  const res = await fetch('/api/settings/models', { credentials: 'include' });
  if (!res.ok) return;
  const models = await res.json();
  const active = models.find(m => m.is_default) || models[0];

  const switcher = document.getElementById('model-switcher');
  if (!switcher) return;

  switcher.innerHTML = `
    <button class="model-btn" onclick="toggleModelMenu()">
      ${active?.name || 'Platform Default'} ▾
    </button>
    <ul class="model-menu" id="model-menu" hidden>
      ${models.map(m => `
        <li onclick="switchModel(${m.id})">
          ${m.is_default ? '●' : '○'} ${m.name}
          <small>${m.model_id}</small>
        </li>
      `).join('')}
      <li class="divider"></li>
      <li><a href="/settings">+ Add Model</a></li>
    </ul>`;
}

function toggleModelMenu() {
  const menu = document.getElementById('model-menu');
  menu.hidden = !menu.hidden;
}

async function switchModel(configId) {
  await fetch('/api/settings/models/active', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ config_id: configId }),
  });
  localStorage.setItem('active_model_id', configId);
  location.reload();
}

// ─── Init ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadUsageMeter();
  loadModelSwitcher();
});
```

---

## 6. Security Best Practices

| Concern | Implementation |
|---|---|
| **API key storage** | AES-256-GCM via `EncryptionService`. `ENCRYPTION_KEY` lives only in the environment / secret manager, never in the DB, git, or logs. |
| **API key display** | Write-only UI: show `●●●●●●●●` after initial save. `GET /api/settings/models` never returns plaintext. Decrypt only inside service layer during AI calls. |
| **Admin escalation** | `is_super_admin` flag on `User`. `require_super_admin` dependency enforced on every admin route. |
| **Trial abuse** | Quota tied to `user_id` (DB row with `UNIQUE` constraint). Not cookie/IP — cannot be bypassed by clearing the browser. |
| **Double-spend** | Use `SELECT ... FOR UPDATE` (`with_for_update()` in SQLAlchemy) on the `usage_quotas` row under PostgreSQL. SQLite serialises writes natively. |
| **Payment webhook** | The `POST /api/purchases` endpoint must be called by your payment processor only (e.g. Stripe), verified via HMAC signature header — not by browser clients. |
| **SQL injection** | SQLAlchemy ORM with parameterised queries throughout. Never interpolate user input into raw SQL. |
| **SSRF via base_url** | Validate `base_url` in `settings_routes.py` before saving: block `localhost`, `127.x`, `169.254.x.x`, `10.x`, `172.16-31.x`, `192.168.x`. |
| **Rate limiting** | Add `slowapi` middleware: 10 req/min on `POST /api/recordings/*/stop` (the expensive AI endpoint). |
| **Secret rotation** | Add `key_version` column to `ai_model_config`. On decrypt failure, re-encrypt with the new key and update `key_version`. |
| **Logging** | Never log raw API keys or tokens. Log only the first 6 / last 4 chars for debugging (e.g. `sk-abc...xyz0`). |

---

## 7. Edge Cases

| Scenario | Handling |
|---|---|
| User deletes their only model mid-recording | `resolve_model` falls through to platform default; analysis completes normally. |
| Admin sets `trial_quota_sessions=0` | `can_use_trial` is False immediately; new users see the upgrade prompt on first visit. |
| AI call succeeds but usage DB write fails | Wrap in a single transaction; rollback undoes both the quota update and the `usage_event`. Recording is marked `failed`; user is not charged. |
| Admin deactivates a user with an in-flight recording | Middleware checks `user.is_active` on every request; recording marked `failed` at next activity post. |
| User has session balance but switches to a high-token model | `check_and_deduct` prefers session deduction if `remaining_sessions > 0`. Tokens are still tracked for reporting even when sessions are deducted. |
| Platform default model key is invalid | Admin's `POST /api/admin/platform/config/test` warns before save. If invalid in production, analyzer falls back to demo mode and surfaces a banner to users. |
| Concurrent requests exhaust the last session | `SELECT FOR UPDATE` (PostgreSQL) or SQLite serialised writes prevent two threads from each believing one session remains. |
| User purchases tokens then configures a huge `max_tokens` value | Pre-flight: estimate prompt token count (`len(text) / 4`) before calling AI; reject if `estimated > remaining_tokens × 0.9` with a clear error. |
| Admin grants quota to a user still in trial | `purchased_sessions` is incremented; `is_trial` flag remains True. Trial sessions drain first (per the priority in `check_and_deduct`), then purchased sessions. |
| AI provider returns no `usage` object | `tokens_used = 0` default; still deduct 1 session to prevent unmetered AI use. |
| Key encryption service unavailable at startup | `EncryptionService.__init__` raises `RuntimeError` — application refuses to start, preventing unencrypted key storage. |

---

## 8. Implementation Order (Recommended)

```
Week 1 — Foundation
  ├─ Write migration script adding new tables
  ├─ EncryptionService + backfill-encrypt existing api_key rows
  ├─ UsageService (get_or_create_quota, check_and_deduct, apply_purchase)
  └─ ModelResolver (replace hardcoded logic in process_analyzer.py)

Week 2 — Core User-Facing Features
  ├─ Wire UsageService into recording stop endpoint
  ├─ GET /api/usage/me  +  GET /api/usage/history
  ├─ Usage meter component in dashboard.html
  └─ /upgrade page with purchase UI

Week 3 — Super Admin Panel
  ├─ admin_routes.py (all endpoints above)
  ├─ /admin/* Jinja2 templates (dashboard, users, usage, revenue, logs)
  └─ Platform config UI (default model, trial limits)

Week 4 — Model Switcher + Polish
  ├─ POST /api/settings/models/active
  ├─ Model switcher dropdown in dashboard header
  ├─ Encrypt API keys on settings save (patch settings_routes.py)
  └─ ApiAuditLog writes in process_analyzer.py
```

> **Critical pre-production step**: The `api_key` column in existing `ai_model_config`
> rows is stored as plaintext. Before any public deployment, run a one-time migration
> that calls `enc.encrypt(row.api_key)` for each row, stores the result back, and
> sets `is_encrypted=True`.
