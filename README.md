# downside-strategy-scanners

Offline backtesters that hunt for DN-side entry rules on Polymarket 5-minute crypto up/down markets (BTC/XRP).

DN-oriented features (BN/BTC drop dynamics, crowd-conviction reversals, depth asymmetry, SR divergence, choppy dwell) work differently from the UP side, so these scanners derive and score DN-specific signals separately. Each scanner replays recorded tick histories and ranks candidate entry rules by in-sample, OOS, and walk-forward PnL. Survivors graduate into the live bot as named DN strategies.

## Conventions

Scanners share a fixed tick-column layout (`CD, UP_BID, UP_ASK, DN_BID, DN_ASK, ..., BN_DELTA, CROWD_CV, DV_UP, DV_DN, ...`). Leak-free: a fire decided at `cd=K` uses only ticks at `cd>=K`. The winner is read only to score PnL at settle. PnL is per $1 stake: win pays `1/entry - 1`, loss pays `-1`, unfired markets count as $0.

## Scripts

- `dn_combo_scanner.py`: multi-factor combos of DN temporal families (BN-drop persistence/depth/accel, CV reversal, DN-depth build) across 8 price zones.
- `dn_pattern_scanner.py`: 8 named DN hypotheses (choppy dwell, BN reversal, depth divergence, late compression, oscillation, CV-stuck, price memory, BN-price divergence).
- `dn_phase_scanner.py`: 7 drift-phase features (dwell time, $0.50 to $0.30 drop velocity, BN-negative drift, depth-ratio growth, CV drop, no-reversal, SR dominance) as singles plus 2/3-way combos.
- `dn_temporal_scanner.py`: DN-specific temporal patterns (BTC drop dynamics, crowd panic, UP-peak reversal, depth asymmetry, microstructure desync).
- `bn_reversal_variants.py`: ~120 variants of the H2b BN-reversal signal across entry timing, reversal threshold, price zone, and partial-signal combos.
- `sr_divergence_scanner.py`: tests each indicator as a filter on the SR-divergence signal at any `cd`, benchmarked against the live `t4_sr` cd 58-62 baseline.
- `newstrat_dip_reversal_persist.py`: cheap-entry dip-reversal sweep over e_cap, persistence, cd-window, reversal signal, and band. A side crashes to ask <= e_cap, stays cheap, then a reversal tick fires. Emits a `BEST_JSON=` line for the orchestrator.

## Usage

The six `dn_*` / `bn_*` / `sr_*` scanners take a market-history JSONL as the first argument and print a ranked report to stdout:

```bash
python3 dn_combo_scanner.py      market_history.jsonl > dn_combo.txt    2>&1
python3 dn_pattern_scanner.py    market_history.jsonl > dn_patterns.txt 2>&1
python3 dn_phase_scanner.py      market_history.jsonl > dn_phase.txt    2>&1
python3 dn_temporal_scanner.py   market_history.jsonl > dn_temporal.txt 2>&1
python3 bn_reversal_variants.py  market_history.jsonl > bn_variants.txt 2>&1
python3 sr_divergence_scanner.py market_history.jsonl
```

`newstrat_dip_reversal_persist.py` takes no argument. It reads `market_panel.json` and writes the best variant's per-market PnL column to `edge_pnl/dip_reversal_persist.json`:

```bash
python3 newstrat_dip_reversal_persist.py
```

## Data

Reads recorded tick replays from the private polymarket-data repo: `market_history.jsonl` (markets with a `ticks` array and resolved `winner`) and `market_panel.json`. Not included here. `newstrat_dip_reversal_persist.py` hardcodes `$DATA_DIR/market_panel.json` in and `$DATA_DIR/edge_pnl/dip_reversal_persist.json` out; repoint these to your local data directory before running.

Python 3.8+, standard library only. No wallet, API key, or network access; these neither trade nor handle funds.
