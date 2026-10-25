# Elysium Testnet Readiness Report
_Generated 2026-10-25T12:40:06+00:00 · automated run · chain 99801_

**Tested readiness score: 60.8/100** across 12/28 projects (42.9% coverage) · 5 testable · 16 not verifiable

## Chain health

- Chain score: **98.1/100**
- `blocks_per_sec_measured`: 6.39
- `block_time_s`: 0.1356
- `gas_price_gwei`: 0.0
- `txs_user`: 110
- `unique_senders_user`: 79
- `contract_creations_user`: 13
- `system_overhead_pct`: 35.3
- RPC: 81 calls, 8 errors, p50 153.7ms / p95 471.6ms

## Findings

- **[INFO]** Optional RPC methods unavailable (limits app debugging, not users): debug_traceTransaction
- **[MED]** 35.3% of transactions are sequencer/batch overhead, not user activity -- public 'tx count' claims should be read net of this.
- **[MED]** Only 79 distinct real senders and 13 contract deployments in the sampled window.
- **[HIGH]** 16/28 projects have no public frontend that could be verified: Aegis Hedge, Dexda Costa, Eloscape, Elypact, Elyra, Elysa, Elysium Names, Montra, Nest, Neurii. This is a testability gap -- an outside tester cannot reach them either.
- **[MED]** 1 project(s) reject anonymous visitors (gated / bot-protected): Ascend
- **[INFO]** 3 still serve pre-launch pages: Chainzy, CorePad, Kalos
- **[GOOD]** 5 project(s) served a working app surface to an anonymous visitor: Atlas, FrogFlip, HyperFlip, HyperFlow, Oxley

## Scenarios

### _infra — WHYPE interface smoke test (view calls only) (dry-run, score 100.0)
- `pass` **balanceOf(qa wallet)** — dry-run: gas 31353, cost ~0.000003 HYPE, value 0.000000 HYPE
- `pass` **totalSupply** — dry-run: gas 30908, cost ~0.000003 HYPE, value 0.000000 HYPE
- `pass` **decimals** — dry-run: gas 30871, cost ~0.000003 HYPE, value 0.000000 HYPE

## Projects

| # | Project | Category | Score | Basis | Frontend | Contract | URL |
|---|---|---|---|---|---|---|---|
| 1 | Atlas | NFT | 100.0 | web | live-app | not configured | https://atlashl.xyz |
| 2 | FrogFlip | Games | 100.0 | web | live-app | not configured | https://frogflip.xyz |
| 3 | HyperFlip | Perps | 100.0 | web | live-app | not configured | https://hyperflip.xyz |
| 4 | HyperFlow | DEX | 100.0 | web | live-app | not configured | https://hyperflow.fun |
| 5 | Oxley | Launchpad | 100.0 | web | live-app | not configured | https://oxley.fun |
| 6 | Hybra Finance | Liquidity | 45.0 | web | landing-only | not configured | https://hybra.finance |
| 7 | Liquid Terminal | Tools | 45.0 | web | landing-only | not configured | https://liquidterminal.xyz |
| 8 | Pindar | Derivatives | 45.0 | web | landing-only | not configured | https://pindar.finance |
| 9 | Chainzy | Launchpad | 25.0 | web | coming-soon | not configured | https://chainzy.xyz |
| 10 | CorePad | Launchpad | 25.0 | web | coming-soon | not configured | https://corepad.xyz |
| 11 | Kalos | NFT/DEX | 25.0 | web | coming-soon | not configured | https://kalos.fun |
| 12 | Ascend | Launchpad | 20.0 | web | gated | not configured | https://ascendlaunch.xyz |
| 13 | Aegis Hedge | DeFi | — | none | url unknown | not configured | — |
| 14 | Dexda Costa | DEX | — | none | url unknown | not configured | — |
| 15 | Eloscape | GameFi | — | none | url unknown | not configured | — |
| 16 | Elypact | DeFi | — | none | url unknown | not configured | — |
| 17 | Elyra | Lending | — | none | url unknown | not configured | — |
| 18 | Elysa | Games | — | none | url unknown | not configured | — |
| 19 | Elysium Names | Tools | — | none | url unknown | not configured | — |
| 20 | Montra | Launchpad | — | none | url unknown | not configured | — |
| 21 | Nest | DEX/ve(3,3) | — | none | url unknown | not configured | — |
| 22 | Neurii | Launchpad | — | none | url unknown | not configured | — |
| 23 | Pantheon Index | Index | — | none | url unknown | not configured | — |
| 24 | PerpMe | Launchpad | — | none | url unknown | not configured | — |
| 25 | Signal | Launchpad | — | none | url unknown | not configured | — |
| 26 | Speculate | Prediction | — | none | url unknown | not configured | — |
| 27 | StockFlow | RWA | — | none | url unknown | not configured | — |
| 28 | Temporal Finance | Options | — | none | url unknown | not configured | — |

_Basis 'web' only means the frontend was assessed and nothing else was configured or published -- read a 100 as 'nothing wrong found in what we could reach', not 'ready'. Add contract addresses and scenarios to config/targets.yaml to deepen coverage._
