from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keitaro_campaign_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    geo: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    streams: Mapped[list[Stream]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="Stream.id",
    )
    sync_logs: Mapped[list[SyncLog]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="SyncLog.id.desc()",
    )


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    keitaro_stream_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(64), default="regular")
    stream_schema: Mapped[str] = mapped_column("schema", String(64), default="redirect")
    has_offers: Mapped[bool] = mapped_column(Boolean, default=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    published_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list[StreamOffer]] = relationship(
        back_populates="stream",
        cascade="all, delete-orphan",
        order_by="StreamOffer.sort_order",
    )


class StreamOffer(Base):
    __tablename__ = "stream_offers"
    __table_args__ = (
        UniqueConstraint("stream_id", "keitaro_offer_id", name="uq_stream_offer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"))
    keitaro_offer_id: Mapped[int] = mapped_column(Integer, index=True)
    offer_name: Mapped[str] = mapped_column(String(512), default="")
    share: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | removed
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    stream: Mapped[Stream] = relationship(back_populates="offers")


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    stream_id: Mapped[int | None] = mapped_column(
        ForeignKey("streams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # create_campaign | fetch | push | cancel
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | error
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="sync_logs")
