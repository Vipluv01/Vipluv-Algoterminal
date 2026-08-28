"""Model package: re-exports every mapped class.

This module exists to guarantee that importing `app.models` populates
`Base.metadata` with the FULL schema. Previously the model modules were only
imported transitively, as a side effect of whichever routers happened to be
loaded -- fine for `create_all` at app startup, but not something Alembic can
rely on: autogenerate diffs the live database against `Base.metadata`, so any
table that had not happened to be imported would look like a table the user
had added by hand, and Alembic would emit a DROP for it.

Anything mapped must be re-exported here.
"""

from __future__ import annotations

from app.models.backtest import StrategyBacktest
from app.models.execution import ParentOrder, ParentOrderStatus, SlicerAlgo
from app.models.risk import RiskSettings
from app.models.trading import (
    STARTING_PAPER_CASH,
    Bracket,
    BracketStatus,
    InstrumentType,
    JournalNote,
    LiveBrokerCredential,
    Mode,
    Order,
    OrderStatus,
    OrderType,
    PaperAccount,
    Side,
    StrategyAllocation,
    SubAccount,
)
from app.models.user import User

__all__ = [
    "STARTING_PAPER_CASH",
    "Bracket",
    "BracketStatus",
    "InstrumentType",
    "JournalNote",
    "LiveBrokerCredential",
    "Mode",
    "Order",
    "OrderStatus",
    "OrderType",
    "PaperAccount",
    "ParentOrder",
    "ParentOrderStatus",
    "RiskSettings",
    "Side",
    "SlicerAlgo",
    "StrategyAllocation",
    "StrategyBacktest",
    "SubAccount",
    "User",
]
