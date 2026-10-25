"""Aggregation + scoring + deliverables (JSON, Markdown, self-contained HTML)."""
from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

# component weights -- renormalised over whatever is available per project
W_WEB, W_CONTRACT, W_SCENARIO = 0.40, 0.30, 0.30

SEV_COLOR = {"high": "#ef4444", "med": "#f59e0b", "low": "#38bdf8", "good": "#22c55e"}
SEV_LABEL = {"high": "HIGH", "med": "MED", "low": "INFO", "good": "GOOD"}


def score_project(web: dict | None, contract: dict | None, scenario: dict | None):
    """Returns (score|None, notes). None means 'we could not test this' -- which is
    deliberately NOT the same as zero, so recon gaps never masquerade as findings."""
    parts, notes = [], []
    if web is not None:
        ws = web.get("score")
        if ws is None:
            notes.append("frontend not tested (no verifiable public URL)")
        else:
            parts.append((W_WEB, ws))
            if web["verdict"] != "live-app":
                notes.append(f"frontend: {web['verdict']}")
    if contract is not None:
        parts.append((W_CONTRACT, contract.get("score", 0.0)))
        if contract["verdict"] == "address unknown":
            notes.append("no published contract address")
        elif contract["verdict"] == "no code at address":
            notes.append("configured address has no code")
    if scenario is not None:
        parts.append((W_SCENARIO, scenario.get("score", 0.0)))
    if not parts:
        return None, notes
    tw = sum(w for w, _ in parts)
    return round(sum(w * s for w, s in parts) / tw, 1), notes


def build(data: dict) -> dict:
    projects = data.get("projects", [])
    web_by = {w["name"]: w for w in data.get("web", [])}
    con_by: dict[str, list] = {}
    for c in data.get("contracts", []):
        con_by.setdefault(c["project"], []).append(c)
    scn_by = {s["project"]: s for s in data.get("scenarios", [])}

    rows = []
    for p in projects:
        w = web_by.get(p["name"])
        cs = con_by.get(p["name"]) or [None]
        best_c = max(cs, key=lambda c: (c or {}).get("score", 0)) if cs else None
        scn = scn_by.get(p["name"])
        score, notes = score_project(w, best_c, scn)
        basis = [
            n for n, ok in (
                ("web", w and w.get("score") is not None),
                ("contract", best_c and best_c.get("verdict") not in
                 (None, "address unknown", "not configured", "no code at address")),
                ("scenario", bool(scn)),
            ) if ok
        ]
        rows.append({
            "name": p["name"],
            "handle": p.get("handle", ""),
            "category": p.get("category", ""),
            "priority": p.get("priority", ""),
            "tested": score is not None,
            "basis": "+".join(basis) or "none",
            "score": score,
            "verdict": (w or {}).get("verdict", "n/a"),
            "web_score": (w or {}).get("score", None),
            "url": (w or {}).get("url", ""),
            "url_confidence": (w or {}).get("url_confidence", "none"),
            "contract": (best_c or {}).get("verdict", "not configured"),
            "address": (best_c or {}).get("address"),
            "notes": notes,
        })
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["name"]))

    chain = data.get("chain") or {}
    # ecosystem score is computed ONLY over projects we could actually test, and
    # coverage is reported alongside it so the number can't be misread.
    scored = [r["score"] for r in rows if r["score"] is not None]
    ecosystem = round(sum(scored) / len(scored), 1) if scored else None
    untestable = [r["name"] for r in rows if r["score"] is None]

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "network": data.get("network", {}),
        "chain": chain,
        "ecosystem_score": ecosystem,
        "projects_total": len(rows),
        "projects_probed": len(scored),
        "coverage_pct": round(100 * len(scored) / max(1, len(rows)), 1),
        "projects_testable": sum(1 for r in rows if r["score"] is not None and r["score"] >= 50),
        "projects_untestable": len(untestable),
        "untestable_names": untestable,
        "projects": rows,
        "scenarios": data.get("scenarios", []),
        "findings": derive_findings(chain, rows),
    }


def derive_findings(chain: dict, rows: list[dict]) -> list[dict]:
    f: list[dict] = []
    caps = chain.get("capabilities") or {}
    m = chain.get("metrics") or {}

    missing_req = [k for k, v in caps.items() if not v.get("available") and v.get("required", True)]
    missing_opt = [k for k, v in caps.items() if not v.get("available") and not v.get("required", True)]
    if missing_req:
        f.append({"sev": "high", "text": f"Required RPC methods unavailable: {', '.join(missing_req)}"})
    if missing_opt:
        f.append({"sev": "low", "text": "Optional RPC methods unavailable (limits app debugging, not users): "
                                        + ", ".join(missing_opt)})

    if m.get("system_overhead_pct") is not None:
        f.append({"sev": "med", "text":
                  f"{m['system_overhead_pct']}% of transactions are sequencer/batch overhead, not user "
                  f"activity -- public 'tx count' claims should be read net of this."})
    if m.get("unique_senders_user") is not None:
        f.append({"sev": "med", "text":
                  f"Only {m['unique_senders_user']} distinct real senders and "
                  f"{m.get('contract_creations_user', 0)} contract deployments in the sampled window."})

    unknown = [r["name"] for r in rows if r["verdict"] == "url unknown"]
    if unknown:
        f.append({"sev": "high", "text":
                  f"{len(unknown)}/{len(rows)} projects have no public frontend that could be verified: "
                  f"{', '.join(unknown[:10])}. This is a testability gap -- an outside tester cannot reach "
                  f"them either."})
    gated = [r["name"] for r in rows if r["verdict"] == "gated"]
    if gated:
        f.append({"sev": "med", "text":
                  f"{len(gated)} project(s) reject anonymous visitors (gated / bot-protected): "
                  f"{', '.join(gated)}"})
    soon = [r["name"] for r in rows if r["verdict"] == "coming-soon"]
    if soon:
        f.append({"sev": "low", "text":
                  f"{len(soon)} still serve pre-launch pages: {', '.join(soon)}"})
    noaddr = [r["name"] for r in rows if r["contract"] == "address unknown"]
    if noaddr:
        f.append({"sev": "high", "text":
                  f"{len(noaddr)}/{len(rows)} projects publish no contract address -- nobody can integrate, "
                  f"audit or regression-test them before mainnet."})
    live = [r["name"] for r in rows if r["verdict"] == "live-app"]
    if live:
        f.append({"sev": "good", "text":
                  f"{len(live)} project(s) served a working app surface to an anonymous visitor: "
                  f"{', '.join(live)}"})
    return f


# --- markdown -------------------------------------------------------------
def to_markdown(rep: dict) -> str:
    ch = rep["chain"]
    m = ch.get("metrics", {})
    L = ["# Elysium Testnet Readiness Report",
         f"_Generated {rep['generated_at']} · automated run · chain "
         f"{rep['network'].get('chain_id','?')}_\n"]
    L.append(f"**Tested readiness score: {rep['ecosystem_score']}/100** across "
             f"{rep['projects_probed']}/{rep['projects_total']} projects "
             f"({rep['coverage_pct']}% coverage) · {rep['projects_testable']} testable · "
             f"{rep['projects_untestable']} not verifiable\n")

    L.append("## Chain health\n")
    L.append(f"- Chain score: **{ch.get('score', 0)}/100**")
    for k in ("blocks_per_sec_measured", "block_time_s", "gas_price_gwei", "txs_user",
              "unique_senders_user", "contract_creations_user", "system_overhead_pct"):
        if k in m:
            L.append(f"- `{k}`: {m[k]}")
    rpc = m.get("rpc", {})
    if rpc:
        L.append(f"- RPC: {rpc.get('calls')} calls, {rpc.get('errors')} errors, "
                 f"p50 {rpc.get('latency_p50_ms')}ms / p95 {rpc.get('latency_p95_ms')}ms")
    L.append("")

    L.append("## Findings\n")
    for f_ in rep["findings"]:
        L.append(f"- **[{SEV_LABEL[f_['sev']]}]** {f_['text']}")
    L.append("")

    if rep.get("scenarios"):
        L.append("## Scenarios\n")
        for s in rep["scenarios"]:
            L.append(f"### {s['project']} — {s['name']} ({s['mode']}, score {s['score']})")
            for st in s["steps"]:
                L.append(f"- `{st['status']}` **{st['name']}** — {st['detail']}")
            L.append("")

    L.append("## Projects\n")
    L.append("| # | Project | Category | Score | Basis | Frontend | Contract | URL |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rep["projects"], 1):
        sc = "—" if r["score"] is None else r["score"]
        L.append(f"| {i} | {r['name']} | {r['category']} | {sc} | {r['basis']} | {r['verdict']} | "
                 f"{r['contract']} | {r['url'] or '—'} |")
    L.append("")
    L.append("_Basis 'web' only means the frontend was assessed and nothing else was configured or "
             "published -- read a 100 as 'nothing wrong found in what we could reach', not 'ready'. "
             "Add contract addresses and scenarios to config/targets.yaml to deepen coverage._")
    L.append("")
    return "\n".join(L)


# --- html dashboard -------------------------------------------------------
CSS = """
*{box-sizing:border-box}
body{margin:0;background:#060b16;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#e2e8f0}
.wrap{max-width:1080px;margin:0 auto;padding:32px 22px 60px}
.hdr{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;margin-bottom:8px}
.kicker{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#38bdf8;font-weight:600}
h1{margin:6px 0 0;font-size:29px;letter-spacing:-.02em}
.sub{font-size:12px;color:#64748b;margin-top:5px}
.big{font-size:38px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
.big span{font-size:14px;color:#475569}
.biglab{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.09em;margin-top:5px}
.meta{font-size:11px;color:#475569;margin-bottom:26px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:11px;margin-bottom:26px}
.card{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:14px 16px}
.card .k{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:#64748b}
.card .v{font-size:24px;font-weight:700;color:#e2e8f0;margin-top:6px;font-variant-numeric:tabular-nums}
.card .u{font-size:11px;color:#475569;font-weight:400;margin-left:5px}
h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:#64748b;margin:0 0 10px}
.panel{background:#0b1220;border:1px solid #16233a;border-radius:12px;padding:16px 18px;margin-bottom:26px}
.ul{margin:0;padding:0;list-style:none}
.ul li{margin:9px 0;color:#cbd5e1;font-size:13px;line-height:1.6;display:flex;gap:10px;align-items:flex-start}
.sev{flex:0 0 auto;font-size:9px;font-weight:700;letter-spacing:.06em;border-radius:4px;padding:3px 6px;margin-top:1px}
.high{color:#ef4444;background:#ef44441a;border:1px solid #ef444444}
.med{color:#f59e0b;background:#f59e0b1a;border:1px solid #f59e0b44}
.low{color:#38bdf8;background:#38bdf81a;border:1px solid #38bdf844}
.good{color:#22c55e;background:#22c55e1a;border:1px solid #22c55e44}
.chip{font-size:10px;padding:3px 8px;border-radius:6px;margin:3px 4px 3px 0;display:inline-block}
.on{background:#052e16;color:#4ade80;border:1px solid #166534}
.off{background:#2d0a0a;color:#f87171;border:1px solid #7f1d1d}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:10px;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.08em;background:#0f172a}
td{padding:9px 10px;border-bottom:1px solid #16233a;vertical-align:top}
.nm{color:#e2e8f0;font-weight:600;font-size:13px}
.hd{font-size:10px;color:#64748b;font-weight:400}
.cat{color:#94a3b8;font-size:11px}
.basis{color:#475569;font-size:10px}
.bar{display:flex;align-items:center;gap:8px;width:130px}
.track{flex:1;height:7px;background:#1e293b;border-radius:4px;overflow:hidden}
.fill{height:100%;border-radius:4px}
.num{font-size:11px;font-variant-numeric:tabular-nums;min-width:34px;text-align:right}
.nt{font-size:10px;color:#475569;font-style:italic}
.badge{font-size:10px;padding:2px 7px;border-radius:20px;white-space:nowrap}
a{color:#38bdf8;text-decoration:none;font-size:11px}
.dash{color:#334155;font-size:11px}
.method{margin-top:26px;padding:16px 18px;background:#0b1220;border:1px solid #16233a;border-radius:12px;font-size:11px;color:#64748b;line-height:1.7}
.method b{color:#94a3b8}
.chart{background:#0b1220;border:1px solid #16233a;border-radius:12px;padding:16px 18px 8px;margin-bottom:26px}
.chart .k{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#64748b;margin-bottom:4px}
.legend{font-size:11px;color:#475569;margin-bottom:8px}
.sw{color:#38bdf8}.sw2{color:#334155;margin-left:12px}
.scn{margin-bottom:14px}
.scnh{font-size:12.5px;color:#e2e8f0;font-weight:600}
.scnh span{color:#64748b;font-weight:400;font-size:11px;margin-left:8px}
.step{display:flex;gap:9px;font-size:11.5px;padding:5px 0;border-top:1px solid #16233a;color:#94a3b8}
.st{font-weight:700;flex:0 0 46px}
.pass{color:#4ade80}.fail{color:#f87171}
.sn{flex:1;color:#cbd5e1}
.sd{flex:1.4;color:#64748b}
"""


def to_html(rep: dict) -> str:
    ch = rep["chain"]
    m = ch.get("metrics", {})
    rows = rep["projects"]

    def bar(score) -> str:
        if score is None:
            return "<span class='nt'>not tested</span>"
        pct = max(0.0, min(100.0, score))
        color = "#22c55e" if pct >= 70 else "#f59e0b" if pct >= 40 else "#ef4444"
        return (f"<div class='bar'><div class='track'><div class='fill' "
                f"style='width:{pct}%;background:{color}'></div></div>"
                f"<span class='num' style='color:{color}'>{pct:.0f}</span></div>")

    def badge(v: str) -> str:
        c = {"live-app": "#22c55e", "landing-only": "#f59e0b", "coming-soon": "#a78bfa",
             "gated": "#8b5cf6", "broken": "#ef4444", "unreachable": "#ef4444",
             "url unknown": "#334155"}.get(v, "#64748b")
        return (f"<span class='badge' style='background:{c}22;color:{c};border:1px solid {c}55'>"
                f"{html.escape(v)}</span>")

    cards = [
        ("Chain score", f"{ch.get('score', 0):.0f}", "/100"),
        ("Block time", f"{m.get('block_time_s', '—')}", "sec"),
        ("Real user txs", f"{m.get('txs_user', '—')}", f"of {m.get('txs_sampled', '—')}"),
        ("Unique senders", f"{m.get('unique_senders_user', '—')}", "in window"),
        ("New contracts", f"{m.get('contract_creations_user', '—')}", "deployed"),
        ("Batch overhead", f"{m.get('system_overhead_pct', '—')}", "% of txs"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='k'>{k}</div><div class='v'>{v}"
        f"<span class='u'>{s}</span></div></div>" for k, v, s in cards)

    # ---- activity chart (inline SVG, no libraries) ----
    series = m.get("activity_series") or {}
    user, sys = series.get("user") or [], series.get("system") or []
    chart = ""
    if user:
        W, H, pad = 980, 110, 18
        n = len(user)
        tot = [u + s for u, s in zip(user, sys)]
        mx = max(tot) or 1
        bw = (W - pad * 2) / max(1, n)
        bars = ""
        for i in range(n):
            x = pad + i * bw
            hu = (user[i] / mx) * (H - 26)
            hs = (sys[i] / mx) * (H - 26)
            if user[i]:
                bars += (f"<rect x='{x:.1f}' y='{H - 14 - hu:.1f}' width='{max(1.4, bw - 1.2):.1f}' "
                         f"height='{hu:.1f}' fill='#38bdf8' rx='1'/>")
            if sys[i]:
                bars += (f"<rect x='{x:.1f}' y='{H - 14 - hs:.1f}' width='{max(1.4, bw - 1.2):.1f}' "
                         f"height='{hs:.1f}' fill='#334155' rx='1'/>")
        chart = (f"<div class='chart'><div class='k'>Transactions per block — last {n} blocks</div>"
                 "<div class='legend'><span class='sw'>■</span> real user transactions"
                 f"<span class='sw2'>■</span> sequencer / batch overhead</div>"
                 f"<svg viewBox='0 0 {W} {H}' width='100%' height='{H}' style='display:block'>{bars}</svg></div>")

    trs = ""
    for i, r in enumerate(rows, 1):
        url = (f"<a href='{html.escape(r['url'])}'>{html.escape(r['url'].replace('https://',''))}</a>"
               if r["url"] else "<span class='dash'>—</span>")
        trs += (f"<tr><td class='basis'>{i}</td>"
                f"<td><div class='nm'>{html.escape(r['name'])}</div>"
                f"<div class='hd'>{html.escape(r['handle'])}</div></td>"
                f"<td class='cat'>{html.escape(r['category'])}</td>"
                f"<td>{bar(r['score'])}</td>"
                f"<td class='basis'>{html.escape(r['basis'])}</td>"
                f"<td>{badge(r['verdict'])}</td><td>{url}</td></tr>")

    findings = "".join(
        f"<li><span class='sev {f_['sev']}'>{SEV_LABEL[f_['sev']]}</span>"
        f"<span>{html.escape(f_['text'])}</span></li>" for f_ in rep["findings"])

    caps = (ch.get("capabilities") or {})
    cap_chips = "".join(
        f"<span class='chip {'on' if v.get('available') else 'off'}'>"
        f"{html.escape(k)}{'' if v.get('required', True) else ' · optional'}</span>"
        for k, v in caps.items())

    scn_html = ""
    if rep.get("scenarios"):
        blocks = ""
        for s in rep["scenarios"]:
            passed = sum(1 for st in s["steps"] if st["status"] == "pass")
            steps = "".join(
                f"<div class='step'><span class='st {"pass" if st["status"] == "pass" else "fail"}'>"
                f"{st['status']}</span><span class='sn'>{html.escape(st['name'])}</span>"
                f"<span class='sd'>{html.escape(st['detail'][:110])}</span></div>"
                for st in s["steps"])
            blocks += (f"<div class='scn'><div class='scnh'>{html.escape(s['name'])}"
                       f"<span>{s['mode']} · {passed}/{len(s['steps'])} passed</span></div>{steps}</div>")
        scn_html = (f"<h2>Lifecycle scenarios</h2><div class='panel'>{blocks}</div>")

    eco = rep["ecosystem_score"]
    eco_txt = "n/a" if eco is None else f"{eco:.0f}"
    eco_col = "#64748b" if eco is None else ("#22c55e" if eco >= 70 else "#f59e0b" if eco >= 40 else "#ef4444")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Elysium Launch Readiness Index</title><style>{CSS}</style></head>
<body><div class="wrap">
<div class="hdr">
  <div><div class="kicker">Independent · automated · reproducible</div>
    <h1>Elysium Launch Readiness Index</h1>
    <div class="sub">{(rep['network'] or {}).get('name','Elysium Testnet')} · pre-mainnet QA run</div></div>
  <div style="text-align:right"><div class="big" style="color:{eco_col}">{eco_txt}<span>/100</span></div>
    <div class="biglab">readiness of tested projects</div></div>
</div>
<div class="meta">{html.escape(rep['generated_at'])} · chain {rep['network'].get('chain_id','?')} ·
  {rep['projects_total']} projects · {rep['projects_probed']} probed ({rep['coverage_pct']}% coverage) ·
  {rep['projects_testable']} testable · <span style="color:#94a3b8">{rep['projects_untestable']} not verifiable</span></div>
<div class="cards">{cards_html}</div>
{chart}
<h2>Findings</h2><div class="panel"><ul class="ul">{findings}</ul></div>
<h2>RPC capability matrix</h2><div style="margin-bottom:26px">{cap_chips}</div>
{scn_html}
<h2>Projects</h2>
<div style="border:1px solid #16233a;border-radius:12px;overflow:hidden;background:#0b1220">
<table><thead><tr><th>#</th><th>Project</th><th>Category</th><th>Score</th><th>Basis</th>
<th>Frontend</th><th>URL</th></tr></thead><tbody>{trs}</tbody></table></div>
<div class="method"><b>Method.</b> Frontend score from an anonymous HTTP probe classified into
live-app / landing-only / coming-soon / gated / broken. Contract score from code presence, EIP-1967
proxy detection and standard view-call responses. Scenario score from replayed lifecycle steps
(simulated with eth_call before any transaction is sent). Components are renormalised when a data
source is unavailable, and a project we could not reach is marked "not tested" rather than zero —
recon gaps are never reported as project failures. Transaction counts are net of sequencer/batch
overhead. Scores measure <i>testability and readiness</i>, not investment merit.<br><br>
<b>Reproducible.</b> One command, no API keys, no wallet for this run. Raw probe output is included
alongside this page.</div>
</div></body></html>"""


def write_all(rep: dict, outdir: str | Path, data: dict | None = None) -> dict:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = {}
    if data is not None:
        p = outdir / "raw.json"
        p.write_text(json.dumps(data, indent=2))
        paths["raw"] = str(p)
    for name, content in (("report.json", json.dumps(rep, indent=2)),
                          ("report.md", to_markdown(rep)),
                          ("dashboard.html", to_html(rep))):
        p = outdir / name
        p.write_text(content)
        paths[name.split(".")[1]] = str(p)
    return paths
