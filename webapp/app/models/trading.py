"""Core trading schema: paper accounts, strategy allocations, orders, and
encrypted-at-rest broker credentials.

Deliberately NO separate "positions" table. Positions and cash are derived
by summing FILLED orders, the same discipline bourse's own matching engine
uses internally (Position() is computed from fills, never stored and
mutated independently -- see internal/book/book.go). Two sources of truth
for the same number is exactly the class of bug that kept surfacing while
building the live demo's own order tracking earlier; not repeating it here
at the database layer, where a drift bug would be much harder to notice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

STARTING_PAPER_CASH = 100_000.0  # matches Bull's own convention (see opensoft-2026/trading-bots)


class Mode(str, PyEnum):
    paper = "paper"
    live = "live"


class OrderStatus(str, PyEnum):
    # A live order sits here until a human explicitly approves it (see
    # OrderStatus.pending_confirmation's role in routers/orders.py) --
    # paper orders skip straight to submitted, since there's no real money
    # and no safety reason to gate them.
    pending_confirmation = "pending_confirmation"
    submitted = "submitted"
    filled = "filled"
    partially_filled = "partially_filled"
    rejected = "rejected"
    cancelled = "cancelled"


class Side(str, PyEnum):
    buy = "buy"
    sell = "sell"


class OrderType(str, PyEnum):
    limit = "limit"
    market = "market"


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    cash: Mapped[float] = mapped_column(Float, default=STARTING_PAPER_CASH)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class StrategyAllocation(Base):
    """A user's chosen weight/enabled-state for one of the 5 strategies, in
    one mode. Two rows per strategy per user (paper + live) is intentional:
    a strategy can be live-tested in paper mode while a DIFFERENT weight
    (or nothing at all) runs in live mode -- collapsing these into one row
    would force paper and live to always move in lockstep, which is exactly
    the coupling a "prove it in paper first" workflow needs to avoid."""

    __tablename__ = "strategy_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    strategy_key: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[Mode] = mapped_column(Enum(Mode))
    enabled: Mapped[bool] = mapped_column(default=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1, from the portfolio optimizer or set manually
    # Which named instrument (app/markets.py) this allocation trades.
    # Ignored for pairs_cointegration, which always trades the one pair
    # it was validated against (ICICIBANK/HDFCBANK) -- not user-configurable,
    # since a different pair has no cointegration evidence behind it.
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[Mode] = mapped_column(Enum(Mode), index=True)
    strategy_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # null = manual order, not strategy-generated
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[Side] = mapped_column(Enum(Side))
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType))
    qty: Mapped[int] = mapped_column(Integer)
    px: Mapped[float | None] = mapped_column(Float, nullable=True)  # null for market orders
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), index=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The id this order was actually submitted to the SymbolMarket's bourse
    # Engine under -- needed to cancel a still-resting GTC limit order
    # against the real book later. Distinct from this row's own `id`
    # (database primary key) and from broker_order_id (a live/Angel One
    # concept) -- three different identity spaces for three different
    # systems this order can exist in.
    engine_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set only for mode=live orders that actually reached the broker --
    # paper orders never have one, and a live order with status
    # pending_confirmation never has one yet either (see routers/orders.py:
    # this field being non-null is the actual proof a real trade happened).
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Only ever set for pairs_cointegration entry orders -- the spread
    # z-score at the moment this order was submitted, threaded through from
    # PairSignal.zscore (app/strategies/pairs_cointegration.py) so the Pair
    # Overview page can show "entered at z=X" instead of that number being
    # computed once for the trade decision and then thrown away.
    entry_zscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class BracketStatus(str, PyEnum):
    active = "active"
    triggered = "triggered"
    cancelled = "cancelled"


class Bracket(Base):
    """An attached stop-loss and/or take-profit watching one filled entry
    order -- the "Risk Management" fields on the reviewed video's Manual
    Trade screen. OCO (one-cancels-other) by construction: there is exactly
    one row per entry, so whichever threshold the tick loop's monitor hits
    first sets status=triggered and closes the position, which removes it
    from consideration for the other threshold on the very next tick --
    no separate cancellation step needed, unlike a system that models SL
    and TP as two independent linked orders.

    entry_side is the position's OWN side (buy = long, sell = short) -- the
    monitor needs it to know which DIRECTION each threshold triggers in:
    a long's stop-loss fires on price falling to/through it, a short's
    fires on price rising to/through it. Storing this explicitly, rather
    than re-deriving "am I long or short" from Order history at check time,
    keeps the tick-loop hot path (runs every second, over every active
    bracket) a single indexed row read instead of a full order-history walk
    per bracket per tick.
    """

    __tablename__ = "brackets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[Mode] = mapped_column(Enum(Mode))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    entry_side: Mapped[Side] = mapped_column(Enum(Side))
    qty: Mapped[int] = mapped_column(Integer)
    stop_loss_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_px: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[BracketStatus] = mapped_column(Enum(BracketStatus), default=BracketStatus.active, index=True)
    entry_order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    closing_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class JournalNote(Base):
    """Free-text trade journal entries, the "Notes" tab in the Dashboard
    view (see the plan / reviewed screenshots). Deliberately just a
    timestamped blob of text -- not linked to a specific Order, matching
    what the reviewed screenshots showed (notes like "Closed all SOL
    before the weekend" that summarize a session, not a single trade)."""

    __tablename__ = "journal_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class LiveBrokerCredential(Base):
    """API key/secret/access-token, encrypted at rest with Fernet
    (symmetric encryption, app.crypto.encrypt/decrypt) -- never stored or
    logged in plaintext. The encryption key itself lives in an environment
    variable (BROKER_CREDENTIAL_KEY), never in this database or in source
    control, so a stolen database file alone is not enough to recover a
    user's real broker credentials."""

    __tablename__ = "live_broker_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    broker: Mapped[str] = mapped_column(String(32))  # "zerodha" | "groww"
    encrypted_api_key: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_api_secret: Mapped[bytes] = mapped_column(LargeBinary)
    encrypted_access_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
