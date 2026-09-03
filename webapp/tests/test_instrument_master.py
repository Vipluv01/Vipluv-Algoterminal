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
