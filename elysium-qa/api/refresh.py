"""Vercel serverless refresh endpoint.

    GET /api/refresh                -> JSON report (fast sample, ~5s)
    GET /api/refresh?full=1         -> larger sample (needs maxDuration headroom)
    GET /api/refresh?format=html    -> the dashboard HTML, regenerated live

This is optional. The static index.html + the daily GitHub Action already keep the
site fresh without any serverless function. Keep this for "click to re-run".
"""
from __future__ import annotations

import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def refresh(full: bool = False, want: str = "json") -> tuple[int, str, str]:
    """Returns (status, content_type, body)."""
    try:
        from elysium_qa import chain as CH
        from elysium_qa import contracts as C
        from elysium_qa import report as R
        from elysium_qa import web as W
        from elysium_qa.rpc import Rpc
        import yaml

        cfg = yaml.safe_load((ROOT / "config" / "targets.yaml").read_text())
        net = cfg["network"]
        rpc = Rpc(net["rpc"], timeout=12.0, retries=1)

        data: dict = {"network": net, "projects": cfg.get("projects", []),
                      "web": [], "contracts": [], "scenarios": []}
        data["chain"] = CH.probe_chain(
            rpc,
            expected_chain_id=net.get("chain_id"),
            sample_blocks=40 if full else 10,
            blocktime_window_s=6 if full else 2,
        ).to_dict()

        for c in cfg.get("contracts", []):
            data["contracts"].append(
                C.probe_contract(rpc, c.get("project", "_infra"),
                                 c.get("address"), c.get("label", "")).to_dict())
        data["web"] = [w.to_dict() for w in W.probe_many(cfg.get("projects", []),
                                                         timeout=8.0, workers=10)]

        rep = R.build(data)
        if want == "html":
            return 200, "text/html; charset=utf-8", R.to_html(rep)
        return 200, "application/json", json.dumps(rep)
    except Exception as e:
        return 500, "application/json", json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "hint": "If this is an import error, the function bundle is missing the package. "
                    "Check vercel.json includeFiles, or rely on the GitHub Action instead.",
            "trace": traceback.format_exc()[-900:],
        })


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        full = q.get("full", ["0"])[0] not in ("0", "", "false")
        fmt = q.get("format", ["json"])[0]
        status, ctype, body = refresh(full=full, want="html" if fmt == "html" else "json")
        raw = body.encode()
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "s-maxage=60, stale-while-revalidate=300")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
