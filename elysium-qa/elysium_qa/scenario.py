"""Deterministic lifecycle scenario runner.

A scenario is declarative in targets.yaml and written with real function
signatures -- no hand-recorded hex:

    scenarios:
      - project: Ascend
        name: "launch -> curve -> graduate -> trade"
        steps:
          - note: create token
            to: "0x…router"
            function: "createToken(string,string,uint256)"
            args: ["QA Test", "QAT", 1000000000000000000]
          - note: buy on curve
            to: "0x…router"
            function: "buy(address,uint256)"
            args: ["0x…token", 1000000000000000]
            value_hype: 0.001
            expect: success        # or revert, if you want to assert a guard works

Safety model (non-negotiable for a public artifact):
  * dry-run unless --execute
  * requires ELYSIUM_QA_PK (dedicated test key, never a main key)
  * hard spend cap per run (--max-spend, default 0.05 HYPE)
  * eth_call simulation before send, so a bad calldata never costs gas
  * sequential, nonce-checked, receipt-verified. This is a regression suite,
    not an activity farmer -- if it looks like a farmer, its reports are worthless.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from .abi import encode_call
from .rpc import Rpc, RpcError


@dataclass
class StepResult:
    name: str
    to: str | None
    status: str  # pass | fail | skipped | warn
    detail: str = ""
    tx_hash: str | None = None
    gas_used: int | None = None
    cost_hype: float | None = None
    latency_ms: float | None = None
    simulated: str | None = None


@dataclass
class ScenarioResult:
    project: str
    name: str
    mode: str
    steps: list[StepResult] = field(default_factory=list)
    score: float = 0.0
    total_cost_hype: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "name": self.name,
            "mode": self.mode,
            "score": round(self.score, 1),
            "total_cost_hype": round(self.total_cost_hype, 8),
            "error": self.error,
            "steps": [s.__dict__ for s in self.steps],
        }


def _load_account():
    try:
        from eth_account import Account
    except ImportError:
        return None, "eth-account not installed -- run: pip install eth-account"
    pk = os.environ.get("ELYSIUM_QA_PK")
    if not pk:
        return None, "ELYSIUM_QA_PK not set (refusing to execute without an explicit test key)"
    if not pk.startswith("0x"):
        pk = "0x" + pk
    return Account.from_key(pk), None


def build_calldata(step: dict) -> tuple[str, str | None]:
    """Resolve a step into calldata hex. Returns (data, error)."""
    if step.get("data"):
        return step["data"], None
    sig = step.get("function")
    if not sig:
        return "0x", "step has neither `function` nor `data`"
    try:
        return encode_call(sig, step.get("args") or []), None
    except Exception as e:
        return "0x", f"ABI encode failed: {e}"


def run_scenario(
    rpc: Rpc,
    project: str,
    scenario: dict,
    chain_id: int,
    execute: bool = False,
    max_spend_hype: float = 0.05,
) -> ScenarioResult:
    res = ScenarioResult(project=project, name=scenario.get("name", "unnamed"),
                         mode="execute" if execute else "dry-run")
    steps = scenario.get("steps") or []
    if not steps:
        res.error = "no steps defined in config"
        return res

    acct = None
    budget = None
    if execute:
        acct, err = _load_account()
        if acct is None:
            res.error = err
            return res
        try:
            bal = int(rpc.call("eth_getBalance", [acct.address, "latest"]), 16)
        except RpcError as e:
            res.error = f"balance read failed: {e}"
            return res
        budget = bal / 1e18
        if budget < max_spend_hype:
            # cap to what's actually there, never overdraw
            max_spend_hype = budget * 0.9

    from_addr = acct.address if acct else "0x0000000000000000000000000000000000000001"

    for i, st in enumerate(steps, 1):
        label = st.get("note") or st.get("function") or f"step{i}"
        to = st.get("to")
        data, derr = build_calldata(st)
        value_wei = int(float(st.get("value_hype", 0)) * 1e18)
        expect = str(st.get("expect", "success")).lower()

        if derr:
            res.steps.append(StepResult(label, to, "fail", derr))
            continue
        if not to:
            res.steps.append(StepResult(label, None, "skipped", "no target address configured"))
            continue

        # --- simulate (free, catches bad calldata / reverts) -----------
        sim = rpc.safe("eth_call", [{"from": from_addr, "to": to, "data": data,
                                     "value": hex(value_wei)}, "latest"])
        sim_note = "ok" if sim not in (None, "0x") else "no return data / would revert"
        est = rpc.safe("eth_estimateGas", [{"from": from_addr, "to": to, "data": data,
                                            "value": hex(value_wei)}])
        gas = int(est, 16) if isinstance(est, str) else None
        try:
            gp = rpc.gas_price() or 100_000_000
        except RpcError:
            gp = 100_000_000
        gp = max(gp, 100_000_000)  # never assume 0 gas price when budgeting
        cost = ((gas or 500_000) * gp) / 1e18

        # assert-style steps: expect a revert (e.g. access control must block randoms)
        if expect == "revert":
            reverted = est is None
            res.steps.append(StepResult(
                label, to, "pass" if reverted else "fail",
                "correctly rejected the call" if reverted else "expected a revert, call succeeded",
                simulated=sim_note))
            continue

        if gas is None:
            res.steps.append(StepResult(
                label, to, "fail",
                f"estimateGas failed -- calldata/args likely wrong ({derr or 'no reason returned'})",
                simulated=sim_note))
            continue

        if not execute:
            res.steps.append(StepResult(
                label, to, "pass",
                f"dry-run: gas {gas}, cost ~{cost:.6f} HYPE, value {value_wei / 1e18:.6f} HYPE",
                cost_hype=cost, simulated=sim_note))
            continue

        # --- spend guard ---------------------------------------------
        if res.total_cost_hype + cost + value_wei / 1e18 > max_spend_hype:
            res.steps.append(StepResult(
                label, to, "skipped",
                f"would exceed spend cap {max_spend_hype:.6f} HYPE "
                f"(spent {res.total_cost_hype:.6f})", cost_hype=cost))
            continue

        # --- send -----------------------------------------------------
        try:
            nonce = int(rpc.call("eth_getTransactionCount", [acct.address, "pending"]), 16)
            tx = {
                "to": to,
                "value": value_wei,
                "data": data,
                "nonce": nonce,
                "chainId": chain_id,
                "gas": int(gas * 1.3),
                "maxFeePerGas": gp * 2,
                "maxPriorityFeePerGas": gp,
            }
            signed = acct.sign_transaction(tx)
            raw = signed.raw_transaction.hex()
            raw = raw if raw.startswith("0x") else "0x" + raw
            t0 = time.perf_counter()
            h = rpc.call("eth_sendRawTransaction", [raw])
            receipt = None
            for _ in range(80):
                time.sleep(0.5)
                receipt = rpc.safe("eth_getTransactionReceipt", [h])
                if receipt:
                    break
            ms = (time.perf_counter() - t0) * 1000

            if receipt and int(receipt["status"], 16) == 1:
                gu = int(receipt["gasUsed"], 16)
                spent = (gu * gp) / 1e18 + value_wei / 1e18
                res.total_cost_hype += spent
                res.steps.append(StepResult(label, to, "pass", "mined + succeeded", h, gu,
                                            spent, ms, sim_note))
            elif receipt:
                gu = int(receipt["gasUsed"], 16)
                res.total_cost_hype += (gu * gp) / 1e18
                res.steps.append(StepResult(label, to, "fail",
                                            "reverted on-chain -- simulation passed, execution failed "
                                            "(a real finding: state-dependent bug or race)", h, gu,
                                            (gu * gp) / 1e18, ms, sim_note))
            else:
                res.steps.append(StepResult(label, to, "fail",
                                            f"not mined within 40s: {h}", h, None, None, ms, sim_note))
        except Exception as e:
            res.steps.append(StepResult(label, to, "fail", f"{type(e).__name__}: {e}"[:200]))

    passed = sum(1 for s in res.steps if s.status == "pass")
    res.score = 100.0 * passed / max(1, len(res.steps))
    return res
