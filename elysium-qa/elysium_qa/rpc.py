"""Minimal dependency-free EVM JSON-RPC client with latency + error accounting."""
from __future__ import annotations

import itertools
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

UA = "Mozilla/5.0 (compatible; elysium-qa/0.1; +local)"


class RpcError(Exception):
    pass


@dataclass
class CallStat:
    method: str
    ms: float
    ok: bool
    error: str = ""


@dataclass
class Rpc:
    url: str
    timeout: float = 20.0
    retries: int = 2
    stale_after_ms: float = 4000.0
    stats: list[CallStat] = field(default_factory=list)
    _id: int = 0
    _counter: "itertools.count" = field(default_factory=lambda: itertools.count(1))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- plumbing -----------------------------------------------------
    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "content-type": "application/json",
                "accept": "*/*",
                "user-agent": UA,
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Single RPC call. Raises RpcError on transport or JSON-RPC error."""
        params = params or []
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            rid = next(self._counter)
            t0 = time.perf_counter()
            try:
                out = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
                ms = (time.perf_counter() - t0) * 1000
                if "error" in out:
                    msg = str(out["error"].get("message", out["error"]))[:140]
                    with self._lock:
                        self.stats.append(CallStat(method, ms, False, msg))
                    raise RpcError(f"{method}: {msg}")
                with self._lock:
                    self.stats.append(CallStat(method, ms, True))
                return out.get("result")
            except RpcError:
                raise
            except Exception as e:  # transport, HTTP, decode
                ms = (time.perf_counter() - t0) * 1000
                last = e
                with self._lock:
                    self.stats.append(CallStat(method, ms, False, f"{type(e).__name__}: {e}"[:140]))
                if attempt < self.retries:
                    time.sleep(0.6 * (attempt + 1))
        raise RpcError(f"{method} failed after {self.retries + 1} attempts: {last}")

    def safe(self, method: str, params: list[Any] | None = None, default: Any = None) -> Any:
        """Never raises. Use for capability probes."""
        try:
            return self.call(method, params)
        except RpcError:
            return default

    # ---- typed helpers ------------------------------------------------
    def block_number(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def chain_id(self) -> int:
        return int(self.call("eth_chainId"), 16)

    def gas_price(self) -> int:
        return int(self.call("eth_gasPrice"), 16)

    def block(self, n: int | str, full: bool = True) -> dict:
        tag = n if isinstance(n, str) else hex(n)
        return self.call("eth_getBlockByNumber", [tag, full])

    def code(self, addr: str, tag: str = "latest") -> str:
        return self.call("eth_getCode", [addr, tag]) or "0x"

    def storage(self, addr: str, slot: str) -> str:
        return self.call("eth_getStorageAt", [addr, slot, "latest"]) or "0x0"

    def eth_call(self, to: str, data: str, tag: str = "latest") -> str | None:
        return self.safe("eth_call", [{"to": to, "data": data}, tag])

    # ---- accounting ---------------------------------------------------
    @property
    def latency_p50(self) -> float:
        xs = sorted(s.ms for s in self.stats if s.ok)
        return xs[len(xs) // 2] if xs else 0.0

    @property
    def latency_p95(self) -> float:
        xs = sorted(s.ms for s in self.stats if s.ok)
        return xs[min(len(xs) - 1, int(len(xs) * 0.95))] if xs else 0.0

    @property
    def error_rate(self) -> float:
        if not self.stats:
            return 0.0
        return sum(1 for s in self.stats if not s.ok) / len(self.stats)

    def summary(self) -> dict:
        return {
            "endpoint": self.url,
            "calls": len(self.stats),
            "errors": sum(1 for s in self.stats if not s.ok),
            "error_rate": round(self.error_rate, 4),
            "latency_p50_ms": round(self.latency_p50, 1),
            "latency_p95_ms": round(self.latency_p95, 1),
        }


# --- tiny ABI helpers (enough for view-call smoke tests) ---------------
SEL = {
    "name": "0x06fdde03",
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
    "totalSupply": "0x18160ddd",
    "owner": "0x8da5cb5b",
    "paused": "0x5c975abb",
    "implementation": "0x5c60da1b",
}


def dec_uint(hexval: str | None) -> int | None:
    if not hexval or hexval in ("0x", "0x0"):
        return None
    try:
        return int(hexval, 16)
    except ValueError:
        return None


def dec_address(hexval: str | None) -> str | None:
    if not hexval or len(hexval) < 42:
        return None
    a = "0x" + hexval[-40:]
    return None if int(a, 16) == 0 else a


def dec_string(hexval: str | None) -> str | None:
    """Decode ABI-encoded string return. Handles dynamic and bytes32 styles."""
    if not hexval or len(hexval) < 130:
        return None
    raw = bytes.fromhex(hexval[2:])
    try:
        # bytes32 style (older NFTs)
        if len(raw) == 32:
            return raw.rstrip(b"\x00").decode("utf-8", "replace")
        off = int.from_bytes(raw[0:32], "big")
        if off + 32 > len(raw):
            return None
        ln = int.from_bytes(raw[off : off + 32], "big")
        if ln > 256 or off + 32 + ln > len(raw):
            return None
        return raw[off + 32 : off + 32 + ln].decode("utf-8", "replace")
    except Exception:
        return None
