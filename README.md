# downside-strategy-scanners

DN-side edge-discovery scanners for Polymarket: combo/pattern/phase/temporal scans, BN-reversal variants, SR-divergence.

## Why it exists

Polymarket 5-minute crypto up/down markets (BTC/XRP) have a structurally hard **DN (down) side** — standard temporal/momentum features that work for UP routinely fail out-of-sample for DN. This repo is the offline research lab that hunts for DN-specific alpha: it replays recorded market tick histories, derives DN-oriented features (BN/BTC drop dynamics, crowd-conviction reversals, depth asymmetry, SR divergence, choppy dwell), and scores candidate entry rules by in-sample + OOS + walk-forward PnL. Survivors graduate into the live trading bot as named DN strategies.

## What's inside

Each scanner is a self-contained backtester. They share a fixed tick-column layout (`CD, UP_BID, UP_ASK, DN_BID, DN_ASK, …, BN_DELTA, CROWD_CV, DV_UP, DV_DN, …`) and a leak-free convention: a fire decided at `cd=K` uses only ticks at `cd>=K`; the winner is read **only** to score PnL at settle.

| Script | What it scans |
| --- | --- |
| `dn_combo_scanner.py` | Multi-factor combos of DN temporal families (BN-drop persistence/depth/accel, CV reversal, DN-depth build) across 8 price zones. |
| `dn_pattern_scanner.py` | 8 named DN hypotheses (choppy dwell, BN reversal, depth divergence, late compression, oscillation, CV-stuck, price memory, BN-price divergence). |
| `dn_phase_scanner.py` | 7 drift-phase features (dwell time, $0.50→$0.30 drop velocity, BN-negative drift, depth-ratio growth, CV drop, no-reversal, SR dominance) as singles + 2/3-way combos. |
| `dn_temporal_scanner.py` | DN-specific temporal patterns: BTC drop dynamics, crowd panic, UP-peak reversal, depth asymmetry, microstructure desync. |
| `bn_reversal_variants.py` | ~120 variants of the H2b BN-reversal signal across entry timing × reversal threshold × price zone × partial-signal combos. |
| `sr_divergence_scanner.py` | Tests every indicator as a filter on the SR-divergence signal at any `cd`, benchmarked against the live `t4_sr` cd 58–62 baseline. |
| `newstrat_dip_reversal_persist.py` | Cheap-entry dip-reversal family: a side crashes ≤ `e_cap`, the crash *persists*, then a reversal tick fires; exhaustive sweep over e_cap × persistence × cd-window × reversal-signal × band. Emits a `BEST_JSON=` summary line for the orchestrator. |

## Requirements

- **Python 3.8+**, standard library only — no third-party packages (`json`, `math`, `itertools`, `collections`). No virtualenv strictly required.
- No wallet, API key, or network access. These are pure offline backtesters; they neither trade nor handle funds.

## Usage

The six `dn_*` / `bn_*` / `sr_*` scanners take a market-history JSONL as the first argument and print a ranked report to stdout:

```bash
python3 dn_combo_scanner.py        market_history.jsonl > dn_combo.txt     2>&1
python3 dn_pattern_scanner.py      market_history.jsonl > dn_patterns.txt  2>&1
python3 dn_phase_scanner.py        market_history.jsonl > dn_phase.txt     2>&1
python3 dn_temporal_scanner.py     market_history.jsonl > dn_temporal.txt  2>&1
python3 bn_reversal_variants.py    market_history.jsonl > bn_variants.txt  2>&1
python3 sr_divergence_scanner.py   market_history.jsonl
```

`newstrat_dip_reversal_persist.py` takes no argument — it reads a fixed `market_panel.json` and writes the best variant's per-market PnL column to `data/edge_pnl/dip_reversal_persist.json`:

```bash
python3 newstrat_dip_reversal_persist.py
```

## Data

These scanners read recorded tick replays — `market_history.jsonl` (a JSONL of markets, each with a `ticks` array and a resolved `winner`) and, for `newstrat_dip_reversal_persist.py`, `market_panel.json`. These data files are **not** included; fetch them from the private **polymarket-data** repo. `newstrat_dip_reversal_persist.py` currently hardcodes paths under `/home/polybot/polymarket-bot/data/` (`market_panel.json` in, `edge_pnl/dip_reversal_persist.json` out) — repoint these to your local data directory before running.

> Private research software. No warranty; trades/handles real funds at your own risk.
