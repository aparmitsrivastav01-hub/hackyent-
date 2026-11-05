from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now_utc():
    return datetime.now(timezone.utc)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    normalized_title: Mapped[str] = mapped_column(String(300), index=True)
    opportunity_type: Mapped[str] = mapped_column(String(20), index=True)
    organization: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    normalized_organization: Mapped[str] = mapped_column(String(300), default="", index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20), default="unknown")
    application_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stipend: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prize_pool: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fees: Mapped[str | None] = mapped_column(String(200), nullable=True)
    eligibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_skills: Mapped[list] = mapped_column(JSON, default=list)
    themes: Mapped[list] = mapped_column(JSON, default=list)
    team_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    experience_requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    notification_version: Mapped[int] = mapped_column(Integer, default=1)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    match: Mapped["OpportunityMatch | None"] = relationship(back_populates="opportunity", cascade="all, delete-orphan", uselist=False)
    __table_args__ = (UniqueConstraint("source_url", name="uq_opportunity_source_url"),)


class OpportunityMatch(Base):
    __tablename__ = "opportunity_matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), unique=True)
    overall_score: Mapped[int] = mapped_column(Integer, default=0)
    skill_score: Mapped[int] = mapped_column(Integer, default=0)
    eligibility_score: Mapped[int] = mapped_column(Integer, default=0)
    location_score: Mapped[int] = mapped_column(Integer, default=0)
    experience_score: Mapped[int] = mapped_column(Integer, default=0)
    explanation: Mapped[str] = mapped_column(Text, default="")
    opportunity: Mapped[Opportunity] = relationship(back_populates="match")


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), default="TinyFish")
    opportunity_type: Mapped[str] = mapped_column(String(20), default="both")
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_found: Mapped[int] = mapped_column(Integer, default=0)
    records_saved: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_detected: Mapped[int] = mapped_column(Integer, default=0)
    sources_searched: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    raw_output: Mapped[list] = mapped_column(JSON, default=list)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class AutomationSetting(Base):
    __tablename__ = "automation_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class EmailDelivery(Base):
    __tablename__ = "email_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    provider: Mapped[str] = mapped_column(String(30), default="resend")
    provider_message_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0)
    is_test: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items: Mapped[list["EmailDeliveryItem"]] = relationship(back_populates="delivery", cascade="all, delete-orphan")


class EmailDeliveryItem(Base):
    __tablename__ = "email_delivery_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("email_deliveries.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    opportunity_version: Mapped[int] = mapped_column(Integer, default=1)
    delivery: Mapped[EmailDelivery] = relationship(back_populates="items")
    opportunity: Mapped[Opportunity] = relationship()
    __table_args__ = (UniqueConstraint("delivery_id", "opportunity_id", name="uq_email_delivery_opportunity"),)

