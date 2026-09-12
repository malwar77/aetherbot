"""SQLAlchemy persistence models — trades and bot state."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, Integer,
                        String, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Trade(Base):
    """One trade, open or closed. mode records whether it executed as
    dry-run or live — dry-run trades are always marked 'dry_run'."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pair: Mapped[str] = mapped_column(String(40))
    side: Mapped[str] = mapped_column(String(10))          # long | short
    stake: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)            # base units
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_paid: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    is_win: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    strategy: Mapped[str] = mapped_column(String(60))
    exchange: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str] = mapped_column(String(10))          # dry_run | live
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    open_date: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                default=utcnow)
    close_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # AI annotation namespace — advisory only, never used for execution
    llm_annotation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    @property
    def duration_min(self) -> float | None:
        if self.close_date is None:
            return None
        return (self.close_date - self.open_date).total_seconds() / 60


class LossEvent(Base):
    """Closed losing trades, for cooldown + daily loss bookkeeping."""
    __tablename__ = "loss_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer)
    pnl: Mapped[float] = mapped_column(Float)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                           default=utcnow)


def make_session(db_url: str):
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()
