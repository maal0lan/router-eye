from datetime import datetime, date, timezone
from sqlalchemy import String, Integer, Float, Date, DateTime, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

def utcnow(): return datetime.now(timezone.utc)

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    snapshots: Mapped[list["DailySnapshot"]] = relationship(back_populates="account", cascade="all, delete-orphan")

class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "day", name="uq_account_day"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    spend_usd: Mapped[float] = mapped_column(Float, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_credits: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    account: Mapped[Account] = relationship(back_populates="snapshots")
