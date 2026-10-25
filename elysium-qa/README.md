# Elysium QA — testnet readiness harness

A bot that tests every project in the Elysium ecosystem on chain **99801** and produces a
pre-mainnet readiness report. Built to run with **zero capital** and no dependencies beyond
`requests` + `pyyaml`.

The point is not to generate activity. It is to produce **evidence** — a repeatable, dated
report that says what works, what doesn't, and what nobody can even reach two weeks before
launch. That artifact is what gets you taken seriously by the teams in the list.

---

## Quick start

```bash
cd elysium-qa
pip install requests pyyaml

python3 -m elysium_qa all          # chain + contracts + frontends + report
python3 -m elysium_qa chain        # network health only (~25s)
python3 -m elysium_qa web          # frontend probes only
```

Outputs land in `out/`:

| file | what it is |
|---|---|
| `dashboard.html` | standalone, inline-styled dashboard (no CDN, opens anywhere) |
| `report.md` | the version you paste into Discord/Notion/a tweet |
| `report.json` | machine-readable, for trend tracking across runs |
| `raw.json` | every raw probe result |

Optional layers:

```bash
python3 -m elysium_qa abi               # self-test the encoder (keccak + selector vectors)

pip install playwright && playwright install chromium
python3 -m elysium_qa all --browser     # real console errors + visible wallet button

pip install eth-account
export ELYSIUM_QA_PK=0x...              # a dedicated throwaway test key, never a main key
python3 -m elysium_qa scenario --execute --max-spend 0.05
```

## Writing an execution scenario

No hand-recorded hex — write the function signature and args:

```yaml
scenarios:
  - project: Ascend
    name: "launch -> curve -> graduate -> trade"
    steps:
      - note: create token
        to: "0xROUTER"
        function: "createToken(string,string,uint256)"
        args: ["QA Test", "QAT", 1000000000000000000]
      - note: buy on bonding curve
        to: "0xROUTER"
        function: "buy(address,uint256)"
        args: ["0xTOKEN", 1000000000000000]
        value_hype: 0.001
      - note: unapproved caller must be rejected
        to: "0xROUTER"
        function: "pause()"
        expect: revert        # assert a guard actually holds
```

`elysium_qa/abi.py` is a dependency-free ABI encoder (keccak256, selectors, dynamic
types, arrays), verified against known-answer vectors on every `abi` run.

Execution safety, in order: `eth_call` simulation → `estimateGas` → spend-cap check →
send → receipt verification. A revert that passed simulation is reported as its own
finding ("state-dependent bug"), because that is exactly the class of bug a launchpad
should hear about before mainnet.

---

## What it actually measures

**1. Chain health** (`chain.py`)
- liveness, chain-id match, gas price
- measured blocks/sec over a real time window (not a claimed number)
- block-time from timestamps, with a note that sub-second blocks collapse to 1s resolution
- **real usage vs infra overhead** — automatically detects and subtracts the Orbit batch
  poster (`0x…a4b05`) so "N transactions" is reported *net* of sequencer noise
- capability matrix: `eth_getLogs`, `eth_feeHistory`, `eth_estimateGas`, `eth_getProof`,
  `debug_traceTransaction` … i.e. *will app developers be blocked?*
- RPC latency p50/p95 and error rate

**2. Frontend readiness** (`web.py`)
- anonymous HTTP probe → classified as `live-app` / `landing-only` / `coming-soon` /
  `gated` / `broken` / `url unknown`
- detects a wallet-connect surface (the difference between an app and a landing page)
- detects access gates ("private access", 401/403/429 bot walls)

**3. Contract surface** (`contracts.py`)
- code presence, EIP-1967 proxy/implementation detection
- standard view-call smoke test (name/symbol/decimals/totalSupply/owner/paused)
- "no published address" is reported as a finding, because it is one

**4. Lifecycle scenarios** (`scenario.py`)
- declarative steps in `config/targets.yaml` (record once from the app's network tab)
- replayed as a regression test: launch → bonding curve → graduation → trade
- dry-run by default; `--execute` requires a key **and** enforces a HYPE spend cap

---

## Design decisions you should keep if you fork this

**Recon gaps are never scored as project failures.** If we can't verify a project's URL,
it's marked `url unknown` and excluded from the score — because reporting "Nest scores 0"
when the truth is "I couldn't find Nest's frontend" is how you lose credibility with the
team in one message. Coverage % is printed next to every score for the same reason.

**One wallet, real sequence, no farming.** The moment this becomes an activity farmer, the
reports stop being believable — which is the only thing they're worth. Scenarios are
sequenced and human-shaped on purpose.

**Newsletter-quality caveats, not hype numbers.** Transaction counts are shown net of
batch overhead. Component scores are renormalised and the "basis" of each score is printed.

**Everything degrades gracefully.** Dead RPC, missing Playwright, missing key, missing
addresses: the run still completes and produces a report explaining what it couldn't do.

---

## Extending it (this is the real work)

`config/targets.yaml` is the product surface. Per project:

```yaml
  - name: Ascend
    url: "https://ascendlaunch.xyz"
    url_confidence: confirmed        # confirmed | likely | none
    contracts:
      - label: AscendRouter
        address: "0x…"               # from the team, or the app's network tab
scenarios:
  - project: Ascend
    name: launch → curve → graduate → trade
    steps:
      - note: create token
        to: "0x…"
        data: "0x…"
        value_hype: 0
```

Every address you add deepens coverage; every scenario you add turns a static report into a
regression suite a team can run before they ship. That's the thing worth paying for.

---

## Publishing it responsibly

If this goes public, two rules protect both the report and your relationships:

1. **Send it to the teams 24–48h before publishing.** Not as a courtesy — because they
   will hand you the addresses, URLs and ABI you're missing, which turns 43% coverage into
   coverage that can't be dismissed. Publish first and the story becomes "he missed half
   the ecosystem." Send first and the story becomes "he found this before we did."
2. **Only assert what a stranger could reproduce.** "Returned HTTP 401 to an anonymous GET
   at 12:04 UTC" is unfalsifiable. "Half-baked product" is not, and one of those in the
   report costs you the whole audience.

Also: never publish the `raw.json` unedited if it contains a wallet address you use
elsewhere, and never publish a report from an `--execute` run that includes tx hashes tied
to a key you care about.

## Known limits

- **No ABI discovery yet.** Signatures must come from the team or the app's bundle —
  guessing 27 unknown ABIs produces false failures, so the harness refuses to guess.
- **Execution needs addresses.** The engine is complete and tested end-to-end (signature →
  calldata → simulate → estimate → send → receipt); it is limited only by the missing
  address books, which is the finding, not a bug.
- **No auth-gated flows.** Ascend returns 401 to anonymous visitors, so it needs the
  Playwright layer and an invite.
- **Frontend classification is heuristic** (string markers + status codes). It reports
  signals, not judgements — `gated` is a fact, "bad product" is not.
- **Not a security audit.** View-call smoke tests ≠ reviewing contracts. Say so, always.

---

## Deploy it (GitHub → Vercel)

This repo is built to deploy with **no build step**: `index.html` is committed, so Vercel
serves it as a static site the moment you import the repo.

### 1. Push to GitHub

```bash
cd elysium-qa
./push.sh                      # prints the manual commands if gh isn't installed
# or explicitly:
GH_USER=yourhandle REPO=elysium-qa ./push.sh
```

Manual path, if you prefer the browser:

```bash
git init -b main && git add -A
git commit -m "Elysium launch readiness index"
git remote add origin git@github.com:YOURHANDLE/elysium-qa.git
git push -u origin main
```

### 2. Deploy on Vercel

1. **vercel.com/new** → import the repo
2. Framework preset: **Other** (no build command, no output directory — leave both empty)
3. **Deploy**

Result: `https://<project>.vercel.app` serves the dashboard.

Optional live re-run endpoint (serverless, no key needed):

```
https://<project>.vercel.app/api/refresh?format=html    # regenerates + returns HTML
https://<project>.vercel.app/api/refresh?full=1         # larger sample
```

> If the API returns an import error, the function bundle missed the package — the
> `includeFiles` glob in `vercel.json` handles that in most cases. The static page and the
> daily Action don't depend on it at all, so a broken function never takes the site down.

### 3. Keep it fresh (no server needed)

`.github/workflows/refresh.yml` re-probes every day at 06:00 UTC, commits the refreshed
`index.html`, and Vercel auto-redeploys. Trigger it manually from the **Actions** tab too.

That daily diff is the actual product: *"3 more projects shipped a frontend since
yesterday"* is far more interesting to a team than any single snapshot.

### Local preview

```bash
python3 scripts/build_static.py     # regenerate index.html (~15s)
python3 -m http.server 8000         # then open http://localhost:8000
```

### Deploy checklist before the URL goes public

- [ ] `grep -r "0x" index.html` — make sure no wallet you use elsewhere is in the page
- [ ] Nothing from `.env*` is committed (`git log --stat` should stay boring)
- [ ] README says what the numbers mean, including that they measure *readiness, not merit*
- [ ] Teams were contacted about the "not verifiable" list before it went live


---

## Suggested cadence

```bash
# daily during the run-up to Nov 9, so the report has a trend line
python3 -m elysium_qa all --out out/$(date +%F)
```

Day-over-day deltas ("3 more projects shipped a frontend since yesterday") are far more
interesting to a team than any single snapshot — and they're free to produce.

---

## Repo layout

```
.
├── index.html                  # the dashboard Vercel serves at /  (generated)
├── vercel.json                 # static deploy + optional serverless refresh
├── api/refresh.py              # GET /api/refresh (optional live re-run)
├── config/targets.yaml         # THE product surface: projects, addresses, scenarios
├── elysium_qa/
│   ├── rpc.py                  # JSON-RPC client, latency/error accounting
│   ├── chain.py                # health + real-usage-vs-overhead + capability matrix
│   ├── web.py                  # frontend classification (parallel)
│   ├── contracts.py            # code presence, EIP-1967 proxy, view-call smoke test
│   ├── abi.py                  # dependency-free keccak256 + ABI encoder (self-tested)
│   ├── scenario.py             # lifecycle regression runner (simulate → estimate → send)
│   ├── report.py               # scoring + JSON/Markdown/HTML deliverables
│   └── cli.py                  # python3 -m elysium_qa …
├── scripts/build_static.py     # regenerate index.html
└── .github/workflows/refresh.yml
```
