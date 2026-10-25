"""Elysium ecosystem QA harness -- CLI.

    python3 -m elysium_qa all                 # chain + web + contracts + report
    python3 -m elysium_qa chain               # chain health only
    python3 -m elysium_qa web                 # frontend probes only
    python3 -m elysium_qa contracts           # contract surface only
    python3 -m elysium_qa scenario --execute  # replay lifecycle steps (needs key)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import contracts as C
from . import chain as CH
from . import report as R
from . import scenario as S
from . import web as W
from .rpc import Rpc

ROOT = Path(__file__).resolve().parent.parent


def load_cfg(path: str | None) -> dict:
    p = Path(path) if path else ROOT / "config" / "targets.yaml"
    if not p.exists():
        sys.exit(f"config not found: {p}")
    return yaml.safe_load(p.read_text())


def cmd_run(args) -> int:
    if args.what == "abi":
        from .abi import selftest
        errs = selftest()
        if errs:
            print("ABI SELFTEST FAILED")
            for e in errs:
                print("  !", e)
            return 1
        print("ABI selftest: PASS (keccak vectors, selectors, calldata encoding)")
        return 0

    cfg = load_cfg(args.config)
    net = cfg["network"]
    rpc = Rpc(net["rpc"], timeout=args.timeout)

    data: dict = {"network": net, "projects": cfg.get("projects", []),
                  "web": [], "contracts": [], "scenarios": []}

    print(f"→ {net['name']}  {net['rpc']}")

    if args.what in ("all", "chain"):
        print("  [chain] probing …")
        ch = CH.probe_chain(rpc, expected_chain_id=net.get("chain_id"),
                            sample_blocks=cfg.get("sample_blocks", 60),
                            blocktime_window_s=cfg.get("blocktime_window_s", 8))
        data["chain"] = ch.to_dict()
        if ch.fatal:
            print(f"  ! {ch.fatal}")
        else:
            print(f"  [chain] score {ch.score:.0f}/100 · "
                  f"{ch.metrics.get('blocks_per_sec_measured')} blocks/s · "
                  f"{ch.metrics.get('txs_user')} user txs")

    if args.what in ("all", "contracts"):
        print("  [contracts] probing …")
        for c in cfg.get("contracts", []):
            data["contracts"].append(
                C.probe_contract(rpc, c.get("project", "_infra"), c.get("address"), c.get("label", "")).to_dict())
        for p in cfg.get("projects", []):
            for c in (p.get("contracts") or []):
                data["contracts"].append(
                    C.probe_contract(rpc, p["name"], c.get("address"), c.get("label", "")).to_dict())
        print(f"  [contracts] {len(data['contracts'])} address(es) probed")

    if args.what in ("all", "web"):
        print("  [web] probing frontends (parallel) …")
        reports = W.probe_many(cfg.get("projects", []), timeout=args.timeout)
        for wr in reports:
            if args.browser and wr.url:
                wr.browser = W.probe_browser(wr.url)
            data["web"].append(wr.to_dict())
            print(f"    {wr.name:<18} {wr.verdict:<13} {wr.status or '-'}")

    if args.what in ("all", "scenario"):
        for scn in cfg.get("scenarios", []):
            data["scenarios"].append(
                S.run_scenario(rpc, scn.get("project", "?"), scn, net.get("chain_id", 0),
                               execute=args.execute,
                               max_spend_hype=args.max_spend).to_dict())

    if not data.get("chain"):
        data["chain"] = {"endpoint": net["rpc"], "score": 0.0, "metrics": {}, "capabilities": {},
                         "checks": [], "ok": False, "fatal": "not probed"}

    rep = R.build(data)
    paths = R.write_all(rep, args.out, data)
    print("\noffline report:")
    for k, v in paths.items():
        print(f"  {k:<5} {v}")
    print(f"\nTESTED READINESS {rep['ecosystem_score']}/100 · coverage "
          f"{rep['projects_probed']}/{rep['projects_total']} ({rep['coverage_pct']}%) · "
          f"{rep['projects_testable']} testable")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="elysium_qa", description="Elysium ecosystem QA harness")
    ap.add_argument("what", choices=["all", "chain", "web", "contracts", "scenario", "abi"],
                    default="all", nargs="?")
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--browser", action="store_true", help="also run the optional Playwright layer")
    ap.add_argument("--execute", action="store_true", help="actually send transactions (needs ELYSIUM_QA_PK)")
    ap.add_argument("--max-spend", type=float, default=0.05, help="HYPE spend cap per run")
    args = ap.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
