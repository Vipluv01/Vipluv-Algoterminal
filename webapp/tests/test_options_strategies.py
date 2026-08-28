"""Unit tests for the 4 multi-leg options strategies' evaluate_options()
logic, against synthetic OptionsSnapshot inputs -- no DB, no registry, the
same "strategy is a pure function of a snapshot" discipline
app/strategies/base.py's own docstring establishes for the single-
instrument strategies."""

from __future__ import annotations

import numpy as np
import pytest

from app.strategies.calendar_spread import CalendarSpreadStrategy
from app.strategies.delta_neutral import DeltaNeutralStrategy
from app.strategies.iron_condor import IronCondorStrategy
from app.strategies.options_base import OptionLegSignal, OptionsSnapshot, close_open_legs
from app.strategies.short_strangle import ShortStrangleStrategy

SPOT_HISTORY = np.array([100.0] * 10)


# ---------------------------------------------------------------------------
# close_open_legs (shared helper)
# ---------------------------------------------------------------------------

def test_close_open_legs_flips_every_leg_side():
    legs = (
        OptionLegSignal(option_type="PE", side="sell", strike=90.0, qty=1, reason=""),
        OptionLegSignal(option_type="CE", side="buy", strike=110.0, qty=2, reason=""),
    )
    closed = close_open_legs(legs, reason="test close")
    assert closed[0].side == "buy" and closed[0].strike == 90.0 and closed[0].qty == 1
    assert closed[1].side == "sell" and closed[1].strike == 110.0 and closed[1].qty == 2
    assert all(leg.reason == "test close" for leg in closed)


# ---------------------------------------------------------------------------
# Short Strangle
# ---------------------------------------------------------------------------

def test_short_strangle_enters_two_short_otm_legs_when_flat():
    strat = ShortStrangleStrategy(underlying="BANKNIFTY", otm_steps=4, qty=2)
    snap = OptionsSnapshot(underlying="BANKNIFTY", spot=1200.0, spot_history=SPOT_HISTORY, position="none")
    result = strat.evaluate_options(snap)
    assert result.new_position == "open"
    assert len(result.option_legs) == 2
    assert {leg.option_type for leg in result.option_legs} == {"CE", "PE"}
    assert all(leg.side == "sell" for leg in result.option_legs)
    assert all(leg.qty == 2 for leg in result.option_legs)
    put_leg = next(leg for leg in result.option_legs if leg.option_type == "PE")
    call_leg = next(leg for leg in result.option_legs if leg.option_type == "CE")
    assert put_leg.strike < 1200.0 < call_leg.strike


def test_short_strangle_holds_while_open_and_not_yet_due():
    strat = ShortStrangleStrategy(underlying="BANKNIFTY")
    open_legs = (
        OptionLegSignal(option_type="PE", side="sell", strike=1100.0, qty=1, reason=""),
        OptionLegSignal(option_type="CE", side="sell", strike=1300.0, qty=1, reason=""),
    )
    snap = OptionsSnapshot(underlying="BANKNIFTY", spot=1200.0, spot_history=SPOT_HISTORY,
                            position="open", open_legs=open_legs, should_exit=False)
    assert strat.evaluate_options(snap) is None


def test_short_strangle_closes_exactly_what_was_opened_when_due():
    strat = ShortStrangleStrategy(underlying="BANKNIFTY")
    open_legs = (
        OptionLegSignal(option_type="PE", side="sell", strike=1100.0, qty=3, reason=""),
        OptionLegSignal(option_type="CE", side="sell", strike=1300.0, qty=3, reason=""),
    )
    snap = OptionsSnapshot(underlying="BANKNIFTY", spot=1250.0, spot_history=SPOT_HISTORY,
                            position="open", open_legs=open_legs, should_exit=True)
    result = strat.evaluate_options(snap)
    assert result.new_position == "none"
    assert {(leg.strike, leg.option_type, leg.side, leg.qty) for leg in result.option_legs} == {
        (1100.0, "PE", "buy", 3), (1300.0, "CE", "buy", 3),
    }


def test_short_strangle_rejects_a_snapshot_for_the_wrong_underlying():
    strat = ShortStrangleStrategy(underlying="BANKNIFTY")
    snap = OptionsSnapshot(underlying="NIFTY50", spot=20000.0, spot_history=SPOT_HISTORY)
    with pytest.raises(ValueError):
        strat.evaluate_options(snap)


# ---------------------------------------------------------------------------
# Iron Condor
# ---------------------------------------------------------------------------

def test_iron_condor_enters_four_legs_with_correct_strike_ordering():
    strat = IronCondorStrategy(short_otm_steps=3, long_otm_steps=6)
    snap = OptionsSnapshot(underlying="NIFTY50", spot=20000.0, spot_history=SPOT_HISTORY, position="none")
    result = strat.evaluate_options(snap)
    assert len(result.option_legs) == 4

    puts = sorted((leg for leg in result.option_legs if leg.option_type == "PE"), key=lambda l: l.strike)
    calls = sorted((leg for leg in result.option_legs if leg.option_type == "CE"), key=lambda l: l.strike)
    long_put, short_put = puts
    short_call, long_call = calls

    assert long_put.side == "buy" and short_put.side == "sell"
    assert short_call.side == "sell" and long_call.side == "buy"
    # Defined-risk shape: long wings strictly outside the short strikes.
    assert long_put.strike < short_put.strike < 20000.0 < short_call.strike < long_call.strike


def test_iron_condor_closes_all_four_legs_when_due():
    strat = IronCondorStrategy()
    open_legs = tuple(
        OptionLegSignal(option_type=ot, side=side, strike=k, qty=1, reason="")
        for ot, side, k in [("PE", "buy", 19000.0), ("PE", "sell", 19500.0), ("CE", "sell", 20500.0), ("CE", "buy", 21000.0)]
    )
    snap = OptionsSnapshot(underlying="NIFTY50", spot=20000.0, spot_history=SPOT_HISTORY,
                            position="open", open_legs=open_legs, should_exit=True)
    result = strat.evaluate_options(snap)
    assert result.new_position == "none"
    assert len(result.option_legs) == 4
    assert {leg.side for leg in result.option_legs} == {"buy", "sell"}


# ---------------------------------------------------------------------------
# Calendar Spread
# ---------------------------------------------------------------------------

def test_calendar_spread_enters_long_near_short_far_at_the_same_strike():
    strat = CalendarSpreadStrategy(near_expiry_bars=200, far_expiry_bars=800)
    snap = OptionsSnapshot(underlying="NIFTY50", spot=20000.0, spot_history=SPOT_HISTORY, position="none")
    result = strat.evaluate_options(snap)
    assert len(result.option_legs) == 2
    near, far = sorted(result.option_legs, key=lambda leg: leg.expiry_bars)
    assert near.side == "buy" and far.side == "sell"
    assert near.strike == far.strike  # same strike, per spec
    assert near.expiry_bars < far.expiry_bars  # near leg genuinely expires first


def test_calendar_spread_legs_share_the_same_option_type():
    strat = CalendarSpreadStrategy()
    snap = OptionsSnapshot(underlying="NIFTY50", spot=20000.0, spot_history=SPOT_HISTORY, position="none")
    result = strat.evaluate_options(snap)
    assert len({leg.option_type for leg in result.option_legs}) == 1


# ---------------------------------------------------------------------------
# Delta Neutral
# ---------------------------------------------------------------------------

def test_delta_neutral_enters_a_short_call_plus_a_long_equity_hedge():
    strat = DeltaNeutralStrategy(underlying="ICICIBANK", qty=1)
    snap = OptionsSnapshot(underlying="ICICIBANK", spot=1250.0, spot_history=SPOT_HISTORY, position="none")
    result = strat.evaluate_options(snap)
    assert len(result.option_legs) == 1
    leg = result.option_legs[0]
    assert leg.option_type == "CE" and leg.side == "sell"
    assert len(result.equity_legs) == 1
    symbol, sig = result.equity_legs[0]
    assert symbol == "ICICIBANK"
    assert sig.side == "buy"
    assert sig.qty > 0  # the ATM_DELTA_APPROX hedge, not zero


def test_delta_neutral_rebalances_toward_the_target_hedge():
    strat = DeltaNeutralStrategy(underlying="ICICIBANK", min_rebalance_shares=2)
    open_legs = (OptionLegSignal(option_type="CE", side="sell", strike=1250.0, qty=1, reason=""),)
    # current_option_delta is already NEGATIVE (a short call) -- the
    # target hedge is -delta, i.e. positive/long. Currently under-hedged
    # (holding fewer shares than the target), so a rebalance should BUY.
    snap = OptionsSnapshot(
        underlying="ICICIBANK", spot=1300.0, spot_history=SPOT_HISTORY, position="open",
        open_legs=open_legs, should_rebalance=True, current_hedge_qty=10, current_option_delta=-60.0,
    )
    result = strat.evaluate_options(snap)
    assert result.option_legs == []
    assert len(result.equity_legs) == 1
    symbol, sig = result.equity_legs[0]
    assert sig.side == "buy"
    assert sig.qty == 50  # target = round(60) = 60, currently holding 10 -> buy 50


def test_delta_neutral_skips_a_rebalance_smaller_than_the_minimum():
    strat = DeltaNeutralStrategy(underlying="ICICIBANK", min_rebalance_shares=5)
    open_legs = (OptionLegSignal(option_type="CE", side="sell", strike=1250.0, qty=1, reason=""),)
    snap = OptionsSnapshot(
        underlying="ICICIBANK", spot=1250.0, spot_history=SPOT_HISTORY, position="open",
        open_legs=open_legs, should_rebalance=True, current_hedge_qty=50, current_option_delta=-52.0,
    )
    assert strat.evaluate_options(snap) is None


def test_delta_neutral_exit_closes_the_option_and_unwinds_the_full_hedge():
    strat = DeltaNeutralStrategy(underlying="ICICIBANK")
    open_legs = (OptionLegSignal(option_type="CE", side="sell", strike=1250.0, qty=1, reason=""),)
    snap = OptionsSnapshot(
        underlying="ICICIBANK", spot=1300.0, spot_history=SPOT_HISTORY, position="open",
        open_legs=open_legs, should_exit=True, current_hedge_qty=42,
    )
    result = strat.evaluate_options(snap)
    assert result.new_position == "none"
    assert result.option_legs[0].side == "buy"  # closing the short call
    symbol, sig = result.equity_legs[0]
    assert sig.side == "sell" and sig.qty == 42


def test_delta_neutral_neither_exits_nor_rebalances_when_neither_flag_set():
    strat = DeltaNeutralStrategy(underlying="ICICIBANK")
    open_legs = (OptionLegSignal(option_type="CE", side="sell", strike=1250.0, qty=1, reason=""),)
    snap = OptionsSnapshot(
        underlying="ICICIBANK", spot=1250.0, spot_history=SPOT_HISTORY, position="open",
        open_legs=open_legs, should_exit=False, should_rebalance=False,
    )
    assert strat.evaluate_options(snap) is None
