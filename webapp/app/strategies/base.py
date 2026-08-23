"""The common interface all 5 strategies implement.

A strategy's job is narrow and stops at the signal: given recent market
data, decide what trade (if any) it wants, and say why. It does not touch
an Engine, does not know about paper vs live, does not know about a user's
existing position or cash -- all of that is the execution layer's job (see
routers/orders.py once it exists), kept separate so a strategy can be unit
tested against a synthetic price series in milliseconds, the same
discipline sim/bourse_sim/stylized_facts.py uses for validating the
simulation's price series against known references.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np


@dataclass(frozen=True)
class MarketSnapshot:
    """Recent price history for one instrument, oldest first. `prices`
    should be the SAME mid-price series bourse's simulation/demo already
    produces -- these strategies are meant to run against paper-mode data
    that came from the real engine, not a separately-simulated series."""

    symbol: str
    prices: np.ndarray


@dataclass(frozen=True)
class Signal:
    side: Literal["buy", "sell"]
    qty: int
    order_type: Literal["limit", "market"]
    px: float | None  # required for limit, None for market
    reason: str  # shown in the UI / audit trail -- "why did the strategy do this"


class Strategy(Protocol):
    key: str
    name: str

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        """Return None for "no action this evaluation" -- a strategy doing
        nothing most of the time is the normal case, not a special one."""
        ...
