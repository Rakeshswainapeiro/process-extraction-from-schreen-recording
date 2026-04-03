import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Float
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

from config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(120), nullable=False)
    role = Column(String(50), nullable=False)  # admin, procurement, hr, project_manager
    is_active = Column(Boolean, default=True)
    is_super_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    recordings = relationship("Recording", back_populates="user")
    feedback = relationship("Feedback", back_populates="user")
    quota = relationship("UsageQuota", back_populates="user", uselist=False)
    purchases = relationship("Purchase", back_populates="user", foreign_keys="Purchase.user_id")


class Recording(Base):
    __tablename__ = "recordings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default="recording")  # recording, processing, completed, failed
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="recordings")
    activities = relationship("Activity", back_populates="recording", cascade="all, delete-orphan")
    process_report = relationship("ProcessReport", back_populates="recording", uselist=False)


class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, index=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    activity_type = Column(String(50), nullable=False)  # click, navigation, typing, app_switch, scroll
    application = Column(String(200), nullable=True)
    window_title = Column(String(500), nullable=True)
    url = Column(String(1000), nullable=True)
    element_text = Column(String(500), nullable=True)
    element_type = Column(String(100), nullable=True)
    screenshot_path = Column(String(500), nullable=True)
    x_coord = Column(Integer, nullable=True)
    y_coord = Column(Integer, nullable=True)
    metadata_json = Column(Text, nullable=True)
    sequence_order = Column(Integer, nullable=False)

    recording = relationship("Recording", back_populates="activities")


class ProcessReport(Base):
    __tablename__ = "process_reports"

    id = Column(Integer, primary_key=True, index=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False)
    process_summary = Column(Text, nullable=True)
    l3_process_map = Column(Text, nullable=True)  # JSON
    l4_process_map = Column(Text, nullable=True)  # JSON
    sop_document = Column(Text, nullable=True)  # Markdown
    automation_recommendations = Column(Text, nullable=True)  # JSON
    ai_recommendations = Column(Text, nullable=True)  # JSON
    mermaid_diagram = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    recording = relationship("Recording", back_populates="process_report")


class AIModelConfig(Base):
    __tablename__ = "ai_model_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String(50), nullable=False, default="anthropic")  # anthropic, openai, custom
    name = Column(String(100), nullable=False, default="Default")
    api_key = Column(Text, nullable=False)
    is_encrypted = Column(Boolean, default=False)
    base_url = Column(String(500), nullable=True)  # custom endpoint URL
    model_id = Column(String(200), nullable=False, default="claude-sonnet-4-6")
    max_tokens = Column(Integer, default=8000)
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=True)
    category = Column(String(50), nullable=False)  # accuracy, usability, completeness, suggestion
    rating = Column(Integer, nullable=True)  # 1-5
    comment = Column(Text, nullable=False)
    status = Column(String(20), default="pending")  # pending, reviewed, implemented
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="feedback")


# ── Platform Config (admin key-value store) ───────────────────────────────────

class PlatformConfig(Base):
    """Admin-controlled global settings stored as key-value pairs."""
    __tablename__ = "platform_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ── Usage Quota (one row per user) ────────────────────────────────────────────

class UsageQuota(Base):
    """Tracks trial usage and purchased balance per user."""
    __tablename__ = "usage_quotas"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Trial
    is_trial = Column(Boolean, default=True)
    trial_sessions_used = Column(Integer, default=0)
    trial_sessions_max = Column(Integer, default=3)

    # Purchased credits
    purchased_sessions = Column(Integer, default=0)
    purchased_tokens = Column(Integer, default=0)

    # Consumed from purchased balance
    used_sessions = Column(Integer, default=0)
    used_tokens = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    user = relationship("User", back_populates="quota")

    @property
    def remaining_sessions(self) -> int:
        return max(0, self.purchased_sessions - self.used_sessions)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.purchased_tokens - self.used_tokens)

    @property
    def can_use_trial(self) -> bool:
        return bool(self.is_trial and self.trial_sessions_used < self.trial_sessions_max)

    @property
    def can_use_ai(self) -> bool:
        return self.can_use_trial or self.remaining_sessions > 0 or self.remaining_tokens > 0


# ── Usage Events (immutable ledger) ──────────────────────────────────────────

class UsageEvent(Base):
    """One record per AI call — immutable audit ledger."""
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=True)
    event_type = Column(String(20), nullable=False)  # trial | session | token
    sessions_delta = Column(Integer, default=0)
    tokens_delta = Column(Integer, default=0)
    model_config_id = Column(Integer, ForeignKey("ai_model_configs.id"), nullable=True)
    model_provider = Column(String(50), nullable=True)
    model_id = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ── Purchases ────────────────────────────────────────────────────────────────

class Purchase(Base):
    """Records of purchased or admin-granted quota."""
    __tablename__ = "purchases"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    purchase_type = Column(String(20), nullable=False)  # sessions | tokens
    quantity = Column(Integer, nullable=False)
    amount_usd_cents = Column(Integer, nullable=False, default=0)
    payment_reference = Column(String(200), nullable=True)
    status = Column(String(20), default="completed")   # pending | completed | refunded
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # admin grants

    user = relationship("User", back_populates="purchases", foreign_keys=[user_id])


# ── API Audit Logs ────────────────────────────────────────────────────────────

class ApiAuditLog(Base):
    """Per-request AI call log for monitoring and billing audit."""
    __tablename__ = "api_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=True)
    endpoint = Column(String(200), nullable=False)
    method = Column(String(10), nullable=False, default="POST")
    status_code = Column(Integer, nullable=True)
    model_provider = Column(String(50), nullable=True)
    model_id = Column(String(200), nullable=True)
    model_config_id = Column(Integer, ForeignKey("ai_model_configs.id"), nullable=True)
    tokens_used = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    ip_address = Column(String(60), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
