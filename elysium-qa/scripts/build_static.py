#!/usr/bin/env python3
"""Regenerate the static dashboard (index.html) from a live probe run.

This is what the GitHub Action runs daily and what you run locally before pushing:

    python3 scripts/build_static.py            # full run (~30s), writes index.html
    python3 scripts/build_static.py --fast     # short sample (~8s), for smoke tests

Writes:
    index.html            <- the page Vercel serves at /
    out/report.json|md    <- machine + human readable evidence
    out/raw.json          <- raw probes
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from elysium_qa import chain as CH  # noqa: E402
from elysium_qa import contracts as C  # noqa: E402
from elysium_qa import report as R  # noqa: E402
from elysium_qa import scenario as S  # noqa: E402
from elysium_qa import web as W  # noqa: E402
from elysium_qa.rpc import Rpc  # noqa: E402

import yaml  # noqa: E402


def run(fast: bool = False, outdir: Path | None = None) -> dict:
    outdir = outdir or ROOT / "out"
    cfg = yaml.safe_load((ROOT / "config" / "targets.yaml").read_text())
    net = cfg["network"]
    rpc = Rpc(net["rpc"], timeout=20.0)

    data: dict = {"network": net, "projects": cfg.get("projects", []),
                  "web": [], "contracts": [], "scenarios": []}

    data["chain"] = CH.probe_chain(
        rpc,
        expected_chain_id=net.get("chain_id"),
        sample_blocks=12 if fast else cfg.get("sample_blocks", 60),
        blocktime_window_s=2 if fast else cfg.get("blocktime_window_s", 8),
    ).to_dict()

    for c in cfg.get("contracts", []):
        data["contracts"].append(
            C.probe_contract(rpc, c.get("project", "_infra"), c.get("address"), c.get("label", "")).to_dict())
    for p in cfg.get("projects", []):
        for c in (p.get("contracts") or []):
            data["contracts"].append(
                C.probe_contract(rpc, p["name"], c.get("address"), c.get("label", "")).to_dict())

    data["web"] = [w.to_dict() for w in W.probe_many(cfg.get("projects", []))]

    for scn in cfg.get("scenarios", []):
        data["scenarios"].append(
            S.run_scenario(rpc, scn.get("project", "?"), scn, net.get("chain_id", 0),
                           execute=False).to_dict())

    rep = R.build(data)
    R.write_all(rep, outdir, data)
    shutil.copyfile(outdir / "dashboard.html", ROOT / "index.html")
    print(f"index.html written · readiness {rep['ecosystem_score']}/100 · "
          f"coverage {rep['projects_probed']}/{rep['projects_total']}")
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="short sample for smoke tests")
    a = ap.parse_args()
    run(a.fast)
