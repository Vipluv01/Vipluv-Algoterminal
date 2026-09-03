"""app/broker/instrument_master.py -- Angel One's real instrument master
(strike/expiry/token discovery for options), exercised against a small,
realistic fake file rather than the real 33MB download. Field shapes
(token/symbol/name/expiry/strike/lotsize/instrumenttype/exch_seg) are
taken directly from the real file, confirmed live 2026-09-03 (see that
module's own docstring)."""

from __future__ import annotations

import json
import time

import pytest

from app.broker import instrument_master as im

FAKE_ROWS = [
    # A non-option row -- must be filtered out entirely.
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "expiry": "", "strike": "0.000000",
     "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE"},
    # Two NIFTY strikes, one weekly expiry.
    {"token": "40677", "symbol": "NIFTY06OCT2622250CE", "name": "NIFTY", "expiry": "06OCT2026",
     "strike": "2225000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    {"token": "40678", "symbol": "NIFTY06OCT2622250PE", "name": "NIFTY", "expiry": "06OCT2026",
     "strike": "2225000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    # A later expiry, same underlying -- for expiry-ordering tests (deliberately
    # inserted BEFORE the earlier one above to prove sorting isn't just insertion order).
    {"token": "40700", "symbol": "NIFTY13NOV2622250CE", "name": "NIFTY", "expiry": "13NOV2026",
     "strike": "2225000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    # A stock option, different underlying entirely.
    {"token": "106361", "symbol": "RELIANCE29SEP261210PE", "name": "RELIANCE", "expiry": "29SEP2026",
     "strike": "121000.000000", "lotsize": "500", "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
    # A malformed row (strike isn't a real number) -- must be skipped, not crash the whole load.
    {"token": "999", "symbol": "BROKEN", "name": "BROKEN", "expiry": "01JAN2027",
     "strike": "not-a-number", "lotsize": "1", "instrumenttype": "OPTSTK", "exch_seg": "NFO"},
    # A real plain-equity row shape (confirmed live) -- empty instrumenttype, NSE segment, "-EQ" suffix.
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "", "strike": "-1.000000",
     "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE"},
    # A BSE-segment row for the SAME name -- must not count as an NSE equity listing.
    {"token": "500325", "symbol": "RELIANCE", "name": "RELIANCE", "expiry": "", "strike": "-1.000000",
     "lotsize": "1", "instrumenttype": "", "exch_seg": "BSE"},
    # A real NSE connectivity-test security shape (confirmed live,
    # 2026-09-04) -- otherwise indistinguishable from a genuine equity
    # row, must still be excluded from the real tradable universe.
    {"token": "9999", "symbol": "011NSETEST-EQ", "name": "011NSETEST", "expiry": "", "strike": "-1.000000",
     "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE"},
]


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Every test gets its own cache path and a fresh in-memory state --
    without this, the module-level cache from one test would leak into
    the next (exactly the failure mode app/pairs_service.py's own
    telemetry cache reset exists to prevent, same reasoning applied
    here)."""
    cache_file = tmp_path / "instrument_master.json"
    cache_file.write_text(json.dumps(FAKE_ROWS))
    monkeypatch.setattr(im, "CACHE_PATH", cache_file)
    monkeypatch.setattr(im, "_cache", None)
    monkeypatch.setattr(im, "_cache_loaded_at", None)
    monkeypatch.setattr(im, "_underlyings_sorted", None)
    monkeypatch.setattr(im, "_equity_names", None)
    monkeypatch.setattr(im, "_equity_names_sorted", None)
    yield


def test_loads_and_filters_to_option_instruments_only():
    underlyings = im.list_option_underlyings()
    # AMXIDX (the non-option index row) and BROKEN (whose one row is
    # malformed and dropped entirely) must both be absent -- an
    # underlying with zero real usable contracts isn't a real underlying.
    assert set(underlyings) == {"NIFTY", "RELIANCE"}


def test_expiries_are_sorted_chronologically_not_alphabetically():
    expiries = im.list_expiries("NIFTY")
    assert expiries == ["06OCT2026", "13NOV2026"]  # NOT insertion order, NOT alphabetical


def test_get_option_chain_contracts_filters_to_the_exact_expiry():
    contracts = im.get_option_chain_contracts("NIFTY", "06OCT2026")
    assert len(contracts) == 2
    assert {c.option_type for c in contracts} == {"CE", "PE"}
    assert all(c.expiry == "06OCT2026" for c in contracts)


def test_strike_is_divided_by_100_from_the_raw_file_value():
    contracts = im.get_option_chain_contracts("NIFTY", "06OCT2026")
    assert contracts[0].strike == 22250.0  # raw file value is 2225000.000000


def test_resolve_option_contract_finds_the_exact_contract():
    result = im.resolve_option_contract("NIFTY", "06OCT2026", 22250.0, "CE")
    assert result is not None
    assert result.tradingsymbol == "NIFTY06OCT2622250CE"


def test_resolve_option_contract_returns_none_rather_than_guessing():
    """The options equivalent of resolve_equity_symbol's own discipline --
    a strike that isn't actually listed for that expiry is a real,
    ordinary outcome (a UI letting a user pick freely), not an error to
    raise, and never silently substituted for the nearest real one."""
    assert im.resolve_option_contract("NIFTY", "06OCT2026", 99999.0, "CE") is None
    assert im.resolve_option_contract("NIFTY", "06OCT2026", 22250.0, "PE") is not None
    assert im.resolve_option_contract("NOTREAL", "06OCT2026", 22250.0, "CE") is None


def test_list_live_equity_names_returns_every_real_nse_eq_listing_sorted():
    """The full real universe a live-mode symbol search should offer --
    not this app's own 7-symbol simulated NAMED_INSTRUMENTS, and not
    limited to underlyings that also happen to have listed options
    (NIFTY has options here but no "-EQ" row at all, so it must be
    absent). Also confirms the NSETEST row (below) is excluded."""
    assert im.list_live_equity_names() == ["RELIANCE"]


def test_list_live_equity_names_excludes_nse_connectivity_test_securities():
    """Regression test: NSE's own test securities (e.g. "011NSETEST")
    are real rows, shaped identically to a genuine equity listing --
    confirmed live, 2026-09-04, 22 of them in the real file. They must
    never surface in a live-mode symbol search."""
    assert "011NSETEST" not in im.list_live_equity_names()


def test_is_equity_live_tradable_true_for_a_real_nse_eq_listing():
    assert im.is_equity_live_tradable("RELIANCE") is True


def test_is_equity_live_tradable_false_for_a_name_with_no_nse_eq_listing():
    """The real, confirmed gap this function exists for: TATAMOTORS
    (2026-09-03) has zero matches anywhere in Angel One's own real
    instrument master -- almost certainly the real corporate demerger,
    not a bug. NIFTY has real OPTION listings in the fixture but no
    "-EQ" row (indices aren't equities), which must also read as False,
    not accidentally True from the option-side data being present."""
    assert im.is_equity_live_tradable("TATAMOTORS") is False
    assert im.is_equity_live_tradable("NIFTY") is False


def test_is_equity_live_tradable_ignores_a_non_nse_exchange_segment():
    """A BSE-only listing under the same name must not count -- this
    app's live equity orders route through NSE specifically (see
    resolve_equity_symbol's own exchange check), so availability has to
    mean available on THAT exchange, not "listed somewhere."""
    # RELIANCE has both an NSE-EQ row (True) and a BSE row (must not
    # independently make some OTHER, NSE-absent name read as tradable).
    assert im.is_equity_live_tradable("RELIANCE") is True


def test_concurrent_callers_only_parse_the_file_once(monkeypatch):
    """Regression test for a real bug found live, 2026-09-04: right after
    adding a higher-traffic endpoint onto this module (GET /live/market/
    equities), CPU pinned at 228% and a single request took 40+ seconds
    with nothing logged -- the check-then-parse in _ensure_loaded had no
    lock, so several real concurrent requests (FastAPI runs a sync def
    route in a threadpool) each independently re-parsed the same
    142,867-row file at once. This fires N real threads at a cold cache
    simultaneously and asserts the actual parse work happens exactly
    once, not N times."""
    import threading

    real_load = im._load_from_disk
    call_count = []

    def counting_load():
        call_count.append(1)
        return real_load()

    monkeypatch.setattr(im, "_load_from_disk", counting_load)

    barrier = threading.Barrier(8)
    def worker():
        barrier.wait()  # maximize real overlap, not just "roughly concurrent"
        im.list_live_equity_names()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert call_count == [1]


def test_refreshes_only_when_the_cached_file_is_stale(monkeypatch):
    fetch_calls = []
    monkeypatch.setattr(im, "_fetch_and_cache", lambda: fetch_calls.append(1))

    im.list_option_underlyings()  # cache file exists and is fresh -- must NOT trigger a fetch
    assert fetch_calls == []

    # Age the cached file past the refresh interval.
    old_time = time.time() - im.REFRESH_INTERVAL_SECONDS - 60
    import os
    os.utime(im.CACHE_PATH, (old_time, old_time))
    monkeypatch.setattr(im, "_cache", None)
    im.list_option_underlyings()
    assert fetch_calls == [1]
