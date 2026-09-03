"""Angel One's real instrument master -- the ONLY safe way to discover
real option contracts (which strikes, which expiries, which underlyings)
at the scale a full options chain feature needs.

Confirmed live, 2026-09-03: this is a real, public, downloadable JSON
file (https://margincalculator.angelbroking.com/OpenAPI_File/files/
OpenAPIScripMaster.json), ~33MB, 142,867 total instruments, 222 distinct
underlyings with listed options (56,377 individual-stock contracts +
11,050 index contracts). Using this instead of searchScrip for options
discovery is not an optimization, it's a requirement: searchScrip is
rate-limited tightly enough that even 2 CONCURRENT calls tripped Angel
One's real "Access denied because of exceeding access rate" (see
angelone.py's own _call_semaphore history) -- a single option chain has
dozens of strikes, and "all NSE stocks + NIFTY + BANKNIFTY" spans 222
underlyings. Calling searchScrip per-contract at that scale would be the
same mistake the WS connection-limit incident already was, just on the
REST side instead. The instrument master eliminates that entirely for
discovery: it's a local file lookup, zero real Angel One traffic, for
everything except the final LTP fetch (see live_options.py, which uses
getMarketData's real batch-quote endpoint for that -- confirmed live to
fetch multiple tokens across exchange segments in ONE real call).

Cached to disk (default: data/angelone_instrument_master.json, override
via ANGELONE_INSTRUMENT_MASTER_PATH) and refreshed only when the cached
copy is older than REFRESH_INTERVAL_SECONDS -- option listings don't
change intraday, so re-downloading a 33MB file on every request (or even
every process restart) would be pure waste. Held in memory as one
module-level cache after first load, filtered down to ONLY option
instruments (instrumenttype in OPTIDX/OPTSTK) at parse time -- the other
~75,000 rows (equities, futures, currency/commodity derivatives, an
index) are irrelevant here and dropped rather than held in memory
needlessly, the same "don't hold what nothing needs" discipline as
elsewhere in this codebase.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "angelone_instrument_master.json"
CACHE_PATH = Path(os.environ.get("ANGELONE_INSTRUMENT_MASTER_PATH", str(_DEFAULT_CACHE_PATH)))

# Option listings don't change intraday -- daily is comfortably safe
# without ever serving a genuinely stale (e.g. post-expiry-rollover)
# chain for more than a few hours.
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

_OPTION_INSTRUMENT_TYPES = {"OPTIDX", "OPTSTK"}


@dataclass(frozen=True)
class OptionContract:
    token: str
    tradingsymbol: str
    underlying: str
    expiry: str  # Angel One's own format, e.g. "06OCT2026" -- kept as-is, not reparsed, so a lookup round-trips exactly
    strike: float  # real rupee strike -- the raw file's value is x100 (confirmed live: 121000 -> RELIANCE...1210PE), divided here so every OTHER caller works in real rupees
    option_type: str  # "CE" | "PE"
    lot_size: int
    exchange_segment: str  # "NFO" for every option contract confirmed so far, kept rather than hardcoded in case that's ever not true


_cache: dict[str, list[OptionContract]] | None = None  # underlying -> its contracts
_cache_loaded_at: float | None = None
_underlyings_sorted: list[str] | None = None
# NSE equity names Angel One's own instrument master currently lists a
# real "{NAME}-EQ" tradingsymbol for -- see is_equity_live_tradable's own
# docstring on why this exists (a real, confirmed gap: TATAMOTORS has
# ZERO matches anywhere in this file, not just among options -- the
# ticker isn't currently listed under any instrument type at all,
# almost certainly the real corporate demerger, not a bug).
_equity_names: set[str] | None = None


def _fetch_and_cache() -> None:
    log.info("Fetching Angel One instrument master from %s", SCRIP_MASTER_URL)
    # 120s, not the more usual few-second budget elsewhere in this app --
    # confirmed live this is a real ~33MB transfer that took ~59s over a
    # normal connection; a short timeout here would fail this on every
    # single attempt, not just a slow one.
    response = httpx.get(SCRIP_MASTER_URL, timeout=120.0)
    response.raise_for_status()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_bytes(response.content)


def _load_from_disk() -> tuple[dict[str, list[OptionContract]], set[str]]:
    with CACHE_PATH.open() as f:
        raw = json.load(f)

    by_underlying: dict[str, list[OptionContract]] = {}
    equity_names: set[str] = set()
    # One pass over all 142,867 rows builds BOTH indices -- re-reading
    # the same 33MB file twice for two different lookups would be pure
    # waste when every row is already being visited once here anyway.
    for row in raw:
        instrument_type = row.get("instrumenttype")
        if instrument_type in _OPTION_INSTRUMENT_TYPES:
            underlying = row.get("name")
            if not underlying:
                continue
            try:
                contract = OptionContract(
                    token=row["token"],
                    tradingsymbol=row["symbol"],
                    underlying=underlying,
                    expiry=row["expiry"],
                    strike=float(row["strike"]) / 100.0,
                    option_type="CE" if row["symbol"].endswith("CE") else "PE",
                    lot_size=int(row["lotsize"]),
                    exchange_segment=row.get("exch_seg", "NFO"),
                )
            except (KeyError, ValueError):
                # A row shaped differently than every other one confirmed
                # live -- skip it rather than let one malformed entry crash
                # the whole chain feature; this is discovery data, not an
                # order, so silently dropping one unusable row is the right
                # failure mode here (unlike anywhere real money is involved).
                continue
            by_underlying.setdefault(underlying, []).append(contract)
        elif instrument_type == "" and row.get("exch_seg") == "NSE" and str(row.get("symbol", "")).endswith("-EQ"):
            # Confirmed live shape for a real NSE equity listing (e.g.
            # RELIANCE-EQ) -- plain equities carry an EMPTY instrumenttype
            # string, not a named one the way options do.
            name = row.get("name")
            if name:
                equity_names.add(name)

    return by_underlying, equity_names


def _ensure_loaded(force_refresh: bool = False) -> None:
    global _cache, _cache_loaded_at, _underlyings_sorted, _equity_names

    needs_download = force_refresh or not CACHE_PATH.exists()
    if not needs_download:
        age_seconds = time.time() - CACHE_PATH.stat().st_mtime
        needs_download = age_seconds > REFRESH_INTERVAL_SECONDS

    if needs_download:
        _fetch_and_cache()
        _cache = None  # force a re-parse below even if this process already had an in-memory copy

    if _cache is None:
        _cache, _equity_names = _load_from_disk()
        _cache_loaded_at = time.time()
        _underlyings_sorted = sorted(_cache.keys())
        log.info(
            "Loaded Angel One instrument master: %d underlyings, %d option contracts",
            len(_underlyings_sorted), sum(len(v) for v in _cache.values()),
        )


def list_option_underlyings() -> list[str]:
    _ensure_loaded()
    assert _underlyings_sorted is not None
    return _underlyings_sorted


def is_equity_live_tradable(name: str) -> bool:
    """Whether Angel One's OWN current instrument master lists a real
    "{name}-EQ" NSE equity for this ticker right now -- a real, confirmed
    gap this exists to catch: TATAMOTORS (2026-09-03) has ZERO matches
    anywhere in the real file, under any instrument type, almost
    certainly the real corporate demerger rather than a bug. This app's
    own NAMED_INSTRUMENTS (app/markets.py) is a fixed, simulated symbol
    list that paper/virtual mode has no reason to ever change -- but live
    mode routes real orders through the real exchange, so offering a
    symbol the real exchange doesn't currently list sets a user up to
    hit exactly this failure. Callers filtering a live-mode symbol picker
    should call this per NAMED_INSTRUMENTS symbol, not assume every
    simulated symbol is also a real one."""
    _ensure_loaded()
    assert _equity_names is not None
    return name in _equity_names


def list_expiries(underlying: str) -> list[str]:
    """Angel One's own expiry strings (e.g. "06OCT2026"), de-duplicated
    and sorted chronologically -- NOT alphabetically, which would put
    "06OCT2026" before "13NOV2026" correctly but "06OCT2026" after
    "06NOV2025" incorrectly (year isn't the leading token in this
    format). Parsed with datetime.strptime purely for sort ordering; the
    original string is what's returned and used everywhere else, since
    that's the exact value get_option_chain_contracts and every real
    Angel One call needs back unchanged."""
    from datetime import datetime

    _ensure_loaded()
    contracts = _cache.get(underlying, []) if _cache else []
    unique = {c.expiry for c in contracts}
    return sorted(unique, key=lambda e: datetime.strptime(e, "%d%b%Y"))


def get_option_chain_contracts(underlying: str, expiry: str) -> list[OptionContract]:
    _ensure_loaded()
    contracts = _cache.get(underlying, []) if _cache else []
    return [c for c in contracts if c.expiry == expiry]


def resolve_option_contract(underlying: str, expiry: str, strike: float, option_type: str) -> OptionContract | None:
    """The options equivalent of AngelOneAdapter.resolve_equity_symbol --
    an exact, unambiguous lookup, never a nearest-match guess. Unlike
    equity's "-EQ" ambiguity problem (multiple series sharing a ticker
    prefix), (underlying, expiry, strike, option_type) is a compound key
    the exchange itself guarantees is unique -- there is exactly one real
    contract for any given combination, or none. Returns None (a clear,
    checkable absence) rather than raising, since "this strike isn't
    listed for this expiry" is an ordinary, expected outcome for a UI
    letting a user pick strikes freely -- ROUTERS calling this for an
    actual order are the ones that turn a None into a real rejection,
    the same "refuse rather than guess" discipline resolve_equity_symbol
    established for equities."""
    for c in get_option_chain_contracts(underlying, expiry):
        # Float equality is safe here specifically because strike is
        # ALWAYS reconstructed the same way (raw x100 integer-ish value
        # / 100.0) on both sides of this comparison -- the caller's
        # strike came from THIS module's own chain listing to begin
        # with, not from independent user-typed input re-parsed some
        # other way.
        if c.strike == strike and c.option_type == option_type:
            return c
    return None


def refresh_now() -> None:
    """Forces a re-download regardless of cache age -- for an explicit
    admin/ops action, not called from any normal request path."""
    _ensure_loaded(force_refresh=True)
