"""A Python client for cmd/simserver -- the JSON subprocess protocol wrapping
the Go matching engine.

One call = one line in, one line out. This is deliberately synchronous and
un-pipelined: the simulation's bottleneck is agent decision logic (Python),
not the engine (Go, measured at ~2.4M ops/sec, see ../results/latency.json),
so there is nothing to gain from pipelining requests and real cost in
correctness risk from getting response/request pairing wrong under it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Side = Literal["buy", "sell"]
OrdType = Literal["limit", "market", "stop_limit"]
TIF = Literal["gtc", "ioc", "fok"]


@dataclass(frozen=True)
class Fill:
    seq: int
    taker_id: int
    maker_id: int
    px: int
    qty: int
    taker_side: Side


@dataclass(frozen=True)
class PriceLevel:
    px: int
    qty: int
    count: int


@dataclass(frozen=True)
class SubmitResult:
    fills: tuple[Fill, ...]
    reject: str

    @property
    def accepted(self) -> bool:
        return self.reject == "none"

    @property
    def filled_qty(self) -> int:
        return sum(f.qty for f in self.fills)


class EngineError(RuntimeError):
    """Raised when the Go process reports an error or dies unexpectedly."""


def find_simserver_binary(repo_root: Path | None = None) -> Path:
    """Locate (building if necessary) the simserver binary.

    Building on first use rather than requiring a pre-built binary keeps
    "clone and run" working without a separate manual build step -- the same
    reasoning as the Dockerfile at the repo root.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    bin_path = root / "bin" / "simserver"
    if bin_path.exists():
        return bin_path

    go = shutil.which("go") or str(Path.home() / ".local/go/bin/go")
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [go, "build", "-o", str(bin_path), "./cmd/simserver"],
        cwd=root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise EngineError(f"failed to build simserver:\n{proc.stderr}")
    return bin_path


class Engine:
    """One matching-engine instance, backed by a simserver subprocess.

    Use as a context manager so the subprocess is always cleaned up:

        with Engine(min_px=1, max_px=20_000, tick=1) as eng:
            eng.submit(...)
    """

    def __init__(
        self,
        *,
        min_px: int,
        max_px: int,
        tick: int = 1,
        capacity: int = 1 << 20,
        binary_path: Path | None = None,
        wal_path: str | None = None,
        price_collar_bps: int = 0,
        position_limit: int = 0,
    ) -> None:
        self._bin = binary_path or find_simserver_binary()
        self._proc = subprocess.Popen(
            [str(self._bin)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,  # line-buffered: matches the Go side's
                                    # flush-per-response contract
        )
        self._next_id = 0
        config: dict = {"min_px": min_px, "max_px": max_px, "tick": tick, "capacity": capacity}
        if wal_path is not None:
            config["wal_path"] = wal_path
        if price_collar_bps:
            config["price_collar_bps"] = price_collar_bps
        if position_limit:
            config["position_limit"] = position_limit
        resp = self._call("new_book", config=config)
        self.recovered: int = resp.get("recovered", 0)

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _call(self, op: str, **fields) -> dict:
        if self._proc.poll() is not None:
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise EngineError(f"simserver process has exited (code {self._proc.returncode}): {stderr}")

        self._next_id += 1
        req = {"id": self._next_id, "op": op, **fields}
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()

        line = self._proc.stdout.readline()
        if not line:
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise EngineError(f"simserver closed stdout unexpectedly: {stderr}")

        resp = json.loads(line)
        if resp.get("id") != req["id"]:
            raise EngineError(f"response id mismatch: sent {req['id']}, got {resp.get('id')}; "
                               "protocol is synchronous request/response and must never desync")
        if "error" in resp and resp["error"]:
            raise EngineError(resp["error"])
        return resp

    def submit(
        self,
        *,
        order_id: int,
        side: Side,
        qty: int,
        px: int = 0,
        stop_px: int = 0,
        owner: int = 0,
        order_type: OrdType = "limit",
        tif: TIF = "gtc",
    ) -> SubmitResult:
        resp = self._call("submit", order={
            "id": order_id, "owner": owner, "px": px, "stop_px": stop_px,
            "qty": qty, "side": side, "type": order_type, "tif": tif,
        })
        fills = tuple(
            Fill(f["seq"], f["taker_id"], f["maker_id"], f["px"], f["qty"], f["taker_side"])
            for f in resp.get("fills", [])
        )
        return SubmitResult(fills=fills, reject=resp["reject"])

    def cancel(self, order_id: int) -> str:
        resp = self._call("cancel", cancel_id=order_id)
        return resp["reject"]

    def best_bid(self) -> tuple[int, int] | None:
        resp = self._call("best_bid")
        return (resp["px"], resp["qty"]) if resp.get("present") else None

    def best_ask(self) -> tuple[int, int] | None:
        resp = self._call("best_ask")
        return (resp["px"], resp["qty"]) if resp.get("present") else None

    def mid(self) -> float | None:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid[0] + ask[0]) / 2.0

    def depth(self, n: int = 10) -> tuple[list[PriceLevel], list[PriceLevel]]:
        resp = self._call("depth", depth=n)
        bids = [PriceLevel(**l) for l in resp.get("bids", [])]
        asks = [PriceLevel(**l) for l in resp.get("asks", [])]
        return bids, asks

    def check_invariants(self) -> None:
        """Raises EngineError if the book's internal invariants are violated.

        Cheap enough (O(live orders) inside Go, one round trip here) to call
        periodically during a long simulation run as a correctness tripwire,
        the same discipline internal/book's own property tests use.
        """
        self._call("check")

    def position(self, owner: int) -> int:
        """Net FILLED position for owner: positive is net long, negative is
        net short, zero if the owner has never traded (or doesn't exist).
        Reflects fills only, never open resting-order exposure -- see
        book.Config's PositionLimit doc comment for why that's deliberate.
        """
        resp = self._call("position", owner=owner)
        return resp["position"]
