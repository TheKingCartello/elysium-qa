"""Chain-level readiness probes: is the network healthy enough to build on?."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import dataclass, field

from .rpc import Rpc, RpcError

# Infra/system addresses that post batch data or run the sequencer. Their txs are
# NOT user activity -- subtracting them is the difference between a real usage
# number and a vanity metric.
KNOWN_SYSTEM = {
    "0x00000000000000000000000000000000000a4b05",  # Orbit batch poster / SeqInbox
    "0x0000000000000000000000000000000000000000",  # burn / zero
}


@dataclass
class ChainReport:
    endpoint: str
    expected_chain_id: int | None = None
    ok: bool = False
    fatal: str | None = None
    checks: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    score: float = 0.0

    def check(self, name: str, status: str, detail: str = "", weight: float = 0.0):
        """status: pass | warn | fail | info"""
        self.checks.append({"name": name, "status": status, "detail": detail, "weight": weight})

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "ok": self.ok,
            "fatal": self.fatal,
            "score": round(self.score, 1),
            "checks": self.checks,
            "metrics": self.metrics,
            "capabilities": self.capabilities,
        }


def probe_chain(
    rpc: Rpc,
    expected_chain_id: int | None = None,
    sample_blocks: int = 60,
    blocktime_window_s: float = 8.0,
) -> ChainReport:
    rep = ChainReport(endpoint=rpc.url, expected_chain_id=expected_chain_id)

    # --- 1. liveness + identity ---------------------------------------
    try:
        head = rpc.block_number()
        rep.ok = True
    except RpcError as e:
        rep.fatal = f"RPC unreachable: {e}"
        rep.check("rpc reachable", "fail", str(e), weight=40)
        return rep

    rep.check("rpc reachable", "pass", f"head={head}", weight=10)

    try:
        cid = rpc.chain_id()
        rep.metrics["chain_id"] = cid
        if expected_chain_id and cid != expected_chain_id:
            rep.check("chain id", "fail", f"got {cid}, expected {expected_chain_id}", weight=15)
        else:
            rep.check("chain id", "pass", f"{cid}", weight=15)
    except RpcError as e:
        rep.check("chain id", "fail", str(e), weight=15)

    # --- 2. throughput: blocks per second over a real window ----------
    try:
        n1 = rpc.block_number()
        t0 = time.time()
        time.sleep(blocktime_window_s)
        n2 = rpc.block_number()
        elapsed = time.time() - t0
        bps = (n2 - n1) / elapsed
        rep.metrics["blocks_per_sec_measured"] = round(bps, 2)
        rep.metrics["blocks_sampled"] = n2 - n1
        if bps >= 3:
            rep.check("throughput", "pass", f"{bps:.1f} blocks/s", weight=10)
        elif bps >= 1:
            rep.check("throughput", "warn", f"{bps:.1f} blocks/s", weight=10)
        else:
            rep.check("throughput", "fail", f"{bps:.1f} blocks/s", weight=10)
    except RpcError as e:
        rep.check("throughput", "warn", str(e), weight=10)

    # --- 3. gas price --------------------------------------------------
    try:
        gp = rpc.gas_price()
        gwei = gp / 1e18
        rep.metrics["gas_price_gwei"] = round(gwei, 6)
        rep.check("gas price readable", "pass", f"{gwei:.4f} gwei", weight=5)
    except RpcError as e:
        rep.check("gas price readable", "warn", str(e), weight=5)

    # --- 4. block sample: block time + real usage vs infra overhead ----
    try:
        # block fetches are independent -> fan out (60 sequential 150ms calls = 9s)
        with ThreadPoolExecutor(max_workers=min(10, sample_blocks)) as ex:
            blocks = list(ex.map(lambda i: rpc.block(head - i, full=True),
                                 range(sample_blocks)))
        ts = [int(b["timestamp"], 16) for b in blocks if b]
        span = ts[0] - ts[-1] if len(ts) > 1 else 0
        rep.metrics["sample_span_s"] = span
        if span > 0:
            rep.metrics["block_time_s"] = round(span / (len(ts) - 1), 4)
            rep.metrics["timestamp_resolution"] = "1s (sub-second block time not observable via timestamps)"
        else:
            rep.metrics["timestamp_resolution"] = "all sampled blocks share one second -> sub-second blocks"

        txs = [t for b in blocks for t in (b.get("transactions") or [])]
        senders = Counter(t["from"].lower() for t in txs)
        targets = Counter((t.get("to") or "create").lower() for t in txs)

        # auto-detect infra overhead: a sender dominating the window
        system = set(KNOWN_SYSTEM)
        if senders:
            top_addr, top_n = senders.most_common(1)[0]
            if top_n / max(1, len(txs)) > 0.20:
                system.add(top_addr)
                rep.metrics["auto_detected_system_sender"] = top_addr

        user_txs = [t for t in txs if t["from"].lower() not in system]

        # per-block series (oldest -> newest) for the dashboard activity chart
        series_user, series_sys = [], []
        for b in reversed(blocks):
            btxs = b.get("transactions") or []
            u = sum(1 for t in btxs if t["from"].lower() not in system)
            series_user.append(u)
            series_sys.append(len(btxs) - u)
        rep.metrics["activity_series"] = {"user": series_user, "system": series_sys}
        rep.metrics.update(
            {
                "txs_sampled": len(txs),
                "txs_user": len(user_txs),
                "system_overhead_pct": round(100 * (1 - len(user_txs) / max(1, len(txs))), 1),
                "unique_senders_user": len({t["from"].lower() for t in user_txs}),
                "unique_targets_user": len(
                    {(t.get("to") or "create").lower() for t in user_txs}
                ),
                "contract_creations_user": sum(1 for t in user_txs if not t.get("to")),
                "txs_per_s_user": round(
                    len(user_txs) / max(span, 1), 2
                ),
            }
        )
        rep.metrics["top_user_targets"] = [
            {"address": a, "txs": c}
            for a, c in targets.most_common(8)
            if a not in system
        ][:8]

        ov = rep.metrics["system_overhead_pct"]
        rep.check(
            "usage is real (infra overhead subtracted)",
            "info",
            f"{len(user_txs)}/{len(txs)} txs are user txs; {ov}% is sequencer/batch overhead",
            weight=0,
        )
    except RpcError as e:
        rep.check("block sample", "warn", str(e), weight=5)

    # --- 5. capability matrix: will app developers be blocked? ---------
    caps = {}

    def cap(name: str, fn, expect_present: bool = True):
        """expect_present=True  -> core capability, absence is a hard fail
           expect_present=False -> nice-to-have, absence is a warn (costs a little)"""
        try:
            v = fn()
            present = v is not None
            caps[name] = {"available": present, "required": expect_present, "sample": _short(v)}
            if expect_present:
                rep.check(f"rpc capability: {name}", "pass" if present else "fail", _short(v), weight=5)
            else:
                rep.check(f"rpc capability: {name} (optional)", "pass" if present else "warn",
                          _short(v), weight=3)
        except RpcError as e:
            caps[name] = {"available": False, "required": expect_present, "error": str(e)[:140]}
            rep.check(
                f"rpc capability: {name}" + ("" if expect_present else " (optional)"),
                "fail" if expect_present else "warn",
                str(e)[:140],
                weight=5 if expect_present else 3,
            )

    probes = [
        ("eth_getLogs", lambda: rpc.safe("eth_getLogs", [{"fromBlock": hex(max(0, head - 20)), "toBlock": hex(head)}]), True),
        ("eth_feeHistory", lambda: rpc.safe("eth_feeHistory", ["0x4", hex(head), [25, 75]]), True),
        ("eth_maxPriorityFeePerGas", lambda: rpc.safe("eth_maxPriorityFeePerGas"), True),
        ("eth_estimateGas", lambda: rpc.safe("eth_estimateGas", [{"to": "0x0000000000000000000000000000000000000001", "data": "0x"}]), True),
        ("web3_clientVersion", lambda: rpc.safe("web3_clientVersion"), True),
        ("net_version", lambda: rpc.safe("net_version"), True),
        ("eth_syncing", lambda: rpc.safe("eth_syncing"), True),
        # debug/trace are genuinely optional -- absence limits app tooling, not users
        ("debug_traceTransaction", lambda: rpc.safe("debug_traceTransaction", ["0x0"]), False),
        ("eth_getProof", lambda: rpc.safe("eth_getProof", ["0x0000000000000000000000000000000000000001", [], "latest"]), False),
    ]
    results = {}
    with ThreadPoolExecutor(max_workers=len(probes)) as ex:
        futs = {ex.submit(fn): (name, req) for name, fn, req in probes}
        for f, (name, req) in list(futs.items()):
            pass
        for f in list(futs):
            name, req = futs[f]
            try:
                results[name] = (f.result(), req, None)
            except Exception as e:  # noqa: BLE001
                results[name] = (None, req, e)
    for name, (val, req, err) in results.items():
        if err is not None:
            cap(name, lambda: (_ for _ in ()).throw(err), expect_present=req)
        else:
            cap(name, lambda v=val: v, expect_present=req)

    rep.capabilities = caps

    # --- 6. score ------------------------------------------------------
    total_w = sum(c["weight"] for c in rep.checks)
    earned = sum(
        c["weight"] * {"pass": 1.0, "warn": 0.5, "fail": 0.0, "info": 0.0}[c["status"]]
        for c in rep.checks
    )
    rep.score = (earned / total_w) * 100 if total_w else 0.0
    rep.metrics["rpc"] = rpc.summary()
    return rep


def _short(v, n: int = 90) -> str:
    s = repr(v)
    return s[:n] + ("..." if len(s) > n else "")
