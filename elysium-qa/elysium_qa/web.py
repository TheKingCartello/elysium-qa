"""Frontend readiness probes.

A project's testnet presence is not "does the domain resolve" -- it's:
  1. does it serve content for an anonymous visitor?
  2. is there a wallet-connect surface (i.e. is it an app, not a landing page)?
  3. does it announce itself as "coming soon" / "private access" (i.e. not testable)?
  4. does it break under a real browser (optional Playwright layer)?
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

GATED_MARKERS = ("private access", "private beta", "request access", "allowlist", "waitlist", "invite code")
SOON_MARKERS = ("coming soon", "launching soon", "pre-launch", "under construction", "stay tuned")
APP_MARKERS = ("connect wallet", "connect a wallet", "launch app", "connect", "walletconnect",
               "wagmi", "rainbowkit", "appkit", "reown", "eth_requestAccounts")
TESTNET_HINTS = ("testnet", "test net", "sepolia", "99801", "998")
BROKEN_MARKERS = ("application error", "404", "not found", "internal server error", "site not found",
                  "deployment not found", "this page could not be found")


@dataclass
class WebReport:
    name: str
    url: str
    reachable: bool = False
    status: int | None = None
    final_url: str | None = None
    bytes: int = 0
    title: str = ""
    latency_ms: float = 0.0
    verdict: str = "unknown"  # live-app | landing-only | gated | coming-soon | broken | unreachable
    signals: list[str] = field(default_factory=list)
    error: str | None = None
    browser: dict | None = None  # filled by optional playwright layer
    url_confidence: str = "guessed"  # confirmed | likely | guessed | none
    score: float | None = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "reachable": self.reachable,
            "status": self.status,
            "final_url": self.final_url,
            "bytes": self.bytes,
            "title": self.title,
            "latency_ms": round(self.latency_ms, 1),
            "verdict": self.verdict,
            "signals": self.signals,
            "error": self.error,
            "browser": self.browser,
            "url_confidence": self.url_confidence,
            "score": None if self.score is None else round(self.score, 1),
        }


def probe_web(name: str, url: str, timeout: float = 20.0,
              url_confidence: str = "guessed") -> WebReport:
    r = WebReport(name=name, url=url, url_confidence=url_confidence)
    if not url:
        r.verdict = "url unknown"
        r.error = "no public url configured"
        r.score = None
        return r

    try:
        resp = requests.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"user-agent": UA, "accept": "text/html,application/xhtml+xml,*/*"},
        )
        r.reachable = True
        r.status = resp.status_code
        r.final_url = str(resp.url)
        r.bytes = len(resp.content)
        r.latency_ms = resp.elapsed.total_seconds() * 1000
        body = resp.text[:400_000]
        low = body.lower()

        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        r.title = re.sub(r"\s+", " ", m.group(1)).strip()[:90] if m else ""

        # --- classify -------------------------------------------------
        if resp.status_code == 401 or resp.status_code == 403:
            r.verdict = "gated"
            r.signals.append(f"HTTP {resp.status_code} (access restricted / bot protection)")
        elif resp.status_code == 429:
            r.verdict = "gated"
            r.signals.append("HTTP 429 (bot protection -- cannot verify as an anonymous user)")
        elif resp.status_code >= 500:
            r.verdict = "broken"
            r.signals.append(f"HTTP {resp.status_code}")
        elif resp.status_code >= 400:
            r.verdict = "broken"
            r.signals.append(f"HTTP {resp.status_code}")
        else:
            for mk in GATED_MARKERS:
                if mk in low:
                    r.verdict = "gated"
                    r.signals.append(f"marker: '{mk}'")
                    break
            if r.verdict == "unknown":
                for mk in SOON_MARKERS:
                    if mk in low:
                        r.verdict = "coming-soon"
                        r.signals.append(f"marker: '{mk}'")
                        break
            if r.verdict == "unknown":
                app_hits = [mk for mk in APP_MARKERS if mk in low]
                if app_hits:
                    r.verdict = "live-app"
                    r.signals.append("wallet-connect surface: " + ", ".join(app_hits[:3]))
                elif r.bytes < 4000:
                    r.verdict = "landing-only"
                    r.signals.append(f"thin response ({r.bytes}B), no app surface")
                else:
                    r.verdict = "landing-only"
                    r.signals.append("no wallet-connect surface found")
            if r.verdict == "unknown":
                r.verdict = "broken"

            if any(mk in low for mk in TESTNET_HINTS):
                r.signals.append("mentions testnet")
            for mk in BROKEN_MARKERS:
                if mk in low and len(body) < 2000:
                    r.signals.append(f"error marker: '{mk}'")
                    break

    except requests.exceptions.Timeout:
        r.error = "timeout"
        r.verdict = "unreachable"
    except requests.exceptions.SSLError as e:
        r.error = f"TLS error: {e}"[:140]
        r.verdict = "unreachable"
    except requests.exceptions.ConnectionError:
        r.error = "DNS/connection failure"
        r.verdict = "unreachable"
    except Exception as e:  # pragma: no cover
        r.error = f"{type(e).__name__}: {e}"[:140]
        r.verdict = "unreachable"

    # INTEGRITY: an unresolved domain is only a project finding if we are
    # certain it is their domain. Otherwise it is a gap in OUR recon.
    if r.verdict == "unreachable" and url_confidence != "confirmed":
        r.verdict = "url unknown"
        r.score = None
        r.signals.append("no verifiable public frontend found -- not scored")
        return r

    r.score = {
        "live-app": 100.0,
        "landing-only": 45.0,
        "coming-soon": 25.0,
        "gated": 20.0,
        "broken": 5.0,
        "unreachable": 0.0,
        "url unknown": None,
        "unknown": 0.0,
    }[r.verdict]
    return r


def probe_many(projects: list[dict], timeout: float = 20.0, workers: int = 8) -> list[WebReport]:
    """Probe all frontends concurrently. 28 sequential 2s requests is a 60s run;
    this keeps the whole pass to a few seconds, which matters for serverless."""
    from concurrent.futures import ThreadPoolExecutor

    def one(p: dict) -> WebReport:
        return probe_web(p["name"], p.get("url"), timeout=timeout,
                         url_confidence=p.get("url_confidence", "guessed"))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, projects))


# --- optional browser layer ------------------------------------------------
def probe_browser(url: str, timeout_ms: int = 25_000) -> dict | None:
    """Real-browser smoke: console errors, failed requests, wallet button visibility.

    Requires: pip install playwright && playwright install chromium
    Returns None when Playwright is unavailable so the harness still runs.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"available": False, "note": "playwright not installed (pip install playwright)"}

    out = {"available": True, "console_errors": [], "failed_requests": [], "wallet_button": False,
           "title": "", "body_bytes": 0}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: out["console_errors"].append(m.text[:200])
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: out["failed_requests"].append(
            f"{r.method} {r.url[:120]} -- {r.failure}"))
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            out["title"] = page.title()
            out["body_bytes"] = len(page.content())
            for sel in ("text=/connect wallet/i", "button:has-text('Connect')",
                        "text=/launch app/i", "text=/connect/i"):
                try:
                    if page.locator(sel).first.is_visible(timeout=1500):
                        out["wallet_button"] = True
                        out["wallet_selector"] = sel
                        break
                except Exception:
                    continue
        except Exception as e:
            out["nav_error"] = f"{type(e).__name__}: {e}"[:200]
        b.close()
    return out
