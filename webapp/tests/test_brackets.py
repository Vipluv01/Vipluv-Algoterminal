"""check_trigger is the part most worth getting exactly right: a stop-loss
that fires in the wrong direction for a short position would silently
lock in losses and let winners run completely unprotected -- exactly the
opposite of what the feature is for."""

import pytest

from app.brackets import check_trigger


# --- Long positions (entry_side="buy") ---

def test_long_stop_loss_fires_at_or_below_the_threshold():
    assert check_trigger(entry_side="buy", price=95.0, stop_loss_px=95.0, take_profit_px=None) == "stop_loss"
    assert check_trigger(entry_side="buy", price=90.0, stop_loss_px=95.0, take_profit_px=None) == "stop_loss"


def test_long_take_profit_fires_at_or_above_the_threshold():
    assert check_trigger(entry_side="buy", price=110.0, stop_loss_px=None, take_profit_px=110.0) == "take_profit"
    assert check_trigger(entry_side="buy", price=115.0, stop_loss_px=None, take_profit_px=110.0) == "take_profit"


def test_long_does_not_fire_between_the_thresholds():
    assert check_trigger(entry_side="buy", price=100.0, stop_loss_px=95.0, take_profit_px=110.0) is None


def test_long_stop_loss_takes_priority_if_both_would_fire():
    # A single large move could jump past both thresholds in one tick --
    # protecting against further loss must win over locking in a gain.
    assert check_trigger(entry_side="buy", price=50.0, stop_loss_px=95.0, take_profit_px=60.0) == "stop_loss"


# --- Short positions (entry_side="sell") -- the mirror image ---

def test_short_stop_loss_fires_at_or_above_the_threshold():
    """A short LOSES money as price rises -- getting this backwards (e.g.
    reusing the long logic) would make a short's 'stop-loss' fire as price
    FALLS, which is actually the winning direction for a short."""
    assert check_trigger(entry_side="sell", price=105.0, stop_loss_px=105.0, take_profit_px=None) == "stop_loss"
    assert check_trigger(entry_side="sell", price=110.0, stop_loss_px=105.0, take_profit_px=None) == "stop_loss"


def test_short_take_profit_fires_at_or_below_the_threshold():
    assert check_trigger(entry_side="sell", price=90.0, stop_loss_px=None, take_profit_px=90.0) == "take_profit"
    assert check_trigger(entry_side="sell", price=85.0, stop_loss_px=None, take_profit_px=90.0) == "take_profit"


def test_short_does_not_fire_between_the_thresholds():
    assert check_trigger(entry_side="sell", price=100.0, stop_loss_px=105.0, take_profit_px=90.0) is None


def test_short_stop_loss_takes_priority_if_both_would_fire():
    assert check_trigger(entry_side="sell", price=150.0, stop_loss_px=105.0, take_profit_px=140.0) == "stop_loss"


# --- Missing thresholds ---

def test_no_thresholds_never_fires():
    assert check_trigger(entry_side="buy", price=1_000_000.0, stop_loss_px=None, take_profit_px=None) is None


def test_only_stop_loss_set_ignores_take_profit_direction():
    assert check_trigger(entry_side="buy", price=200.0, stop_loss_px=95.0, take_profit_px=None) is None
