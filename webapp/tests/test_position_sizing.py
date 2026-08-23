import pytest

from app.position_sizing import kelly_fraction, size_position


def test_classic_textbook_case_fair_coin_two_to_one_payout():
    """The standard Kelly example: p=0.5, b=2 (win 2x what you risk) ->
    f*=0.25. If this doesn't hold, nothing else built on top of it can be
    trusted."""
    f = kelly_fraction(win_rate=0.5, avg_win=2.0, avg_loss=1.0)
    assert f == pytest.approx(0.25)


def test_negative_edge_clips_to_zero_not_negative():
    # p=0.3, b=1 (breakeven payout) -> raw f* = 0.3 - 0.7/1 = -0.4
    f = kelly_fraction(win_rate=0.3, avg_win=1.0, avg_loss=1.0)
    assert f == 0.0


def test_higher_win_rate_gives_a_larger_kelly_fraction():
    low = kelly_fraction(win_rate=0.4, avg_win=1.5, avg_loss=1.0)
    high = kelly_fraction(win_rate=0.7, avg_win=1.5, avg_loss=1.0)
    assert high > low


def test_rejects_out_of_range_win_rate():
    with pytest.raises(ValueError):
        kelly_fraction(win_rate=1.5, avg_win=1.0, avg_loss=1.0)


def test_rejects_non_positive_avg_win_or_loss():
    with pytest.raises(ValueError):
        kelly_fraction(win_rate=0.5, avg_win=0.0, avg_loss=1.0)
    with pytest.raises(ValueError):
        kelly_fraction(win_rate=0.5, avg_win=1.0, avg_loss=-1.0)


def test_size_position_applies_the_fractional_multiplier():
    result = size_position(
        win_rate=0.5, avg_win=2.0, avg_loss=1.0,
        account_value=100_000.0, price=1000.0,
        kelly_multiplier=0.25, max_position_fraction=1.0,
    )
    assert result.kelly_fraction == pytest.approx(0.25)
    assert result.applied_fraction == pytest.approx(0.0625)  # 0.25 * 0.25
    assert result.position_value == pytest.approx(6250.0)
    assert result.qty == 6  # floor(6250 / 1000)


def test_max_position_fraction_caps_an_aggressive_kelly_estimate():
    # A strong apparent edge (p=0.9, b=3) would otherwise size up huge --
    # the cap must win regardless of how confident the Kelly math looks.
    result = size_position(
        win_rate=0.9, avg_win=3.0, avg_loss=1.0,
        account_value=100_000.0, price=100.0,
        kelly_multiplier=1.0, max_position_fraction=0.2,
    )
    assert result.applied_fraction == pytest.approx(0.2)
    assert result.position_value == pytest.approx(20_000.0)


def test_zero_price_returns_zero_qty_instead_of_dividing_by_zero():
    result = size_position(
        win_rate=0.5, avg_win=2.0, avg_loss=1.0,
        account_value=100_000.0, price=0.0,
    )
    assert result.qty == 0
