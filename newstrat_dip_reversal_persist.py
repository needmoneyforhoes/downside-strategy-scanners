#!/usr/bin/env python3
"""
newstrat_dip_reversal_persist.py — DIP-REVERSAL cheap-buy strategy family.

IDEA (math: at $1 stake, mean/fire = p/e - 1, so +$1/mkt needs WR p >= 2*e;
the only place this lives is CHEAP entries e<=~0.25):

  A side crashed to ask <= e_cap. Require the crash to PERSIST (the side stayed
  cheap, ask <= e_cap+band, for at least X seconds of cd-time). THEN require a
  REVERSAL tick on that same cheap side (ask ticks back up off its low, or
  d10 of that side turns positive, or vel flips toward it). BUY that cheap side
  (only when its ask is still <= e_cap, i.e. actually fillable cheap).

We scan cd HIGH->LOW (first-trigger, leak-free). The decision at cd=K uses only
ticks at cd>=K. The winner is used ONLY to score PnL at settle.

PnL per $1 stake:  win -> (1/entry - 1),  loss -> -1.  Mean over ALL markets
(unfired market = $0).

Persistence is measured in cd-SECONDS (ticks are event-driven, multiple/sec and
irregular): "held cheap for X sec" = (cd when this cheap episode began) - (cur cd)
>= X, AND the side never left the cheap band (ask <= e_cap+band) in between.

EXHAUSTIVE sweep (fast, vectorized per market):
  e_cap        in {0.10, 0.15, 0.20, 0.25}
  persist X    in {1, 2, 3, 5, 8, 12, 20} seconds
  cd-window    in {full, cd>=120, cd>=60, 60..240, 30..200, cd>=200}
  reversal sig in {ask_up, d10_pos, vel_flip, ask_up_OR_d10, ask_up_AND_d10,
                   any_of_three}
  band width   in {0.00, 0.02, 0.05}

We pick (1) BEST per-market mean among variants that BEAT breakeven (WR>2*avg_e),
and (2) the HIGHEST-COVERAGE variant that still beats breakeven. The best
variant's per-market pnl column {slug: pnl_per_dollar} (0.0 for unfired) is
written to edge_pnl/dip_reversal_persist.json.

SPEED: per market, we precompute for each (e_cap,band) the per-side cheap-episode
"held" seconds and the fillable flag, plus per-tick reversal flags (independent
of e_cap). Then each (persist,window,rev_sig) variant is a cheap O(ticks) scan.
"""

import json
import os
from collections import Counter

PANEL = "./data/market_panel.json"
OUT_COL = "./data/edge_pnl/dip_reversal_persist.json"
CD_FILL_FLOOR = 15

E_CAPS = [0.10, 0.15, 0.20, 0.25]
PERSISTS = [1, 2, 3, 5, 8, 12, 20]
CD_WINDOWS = {
    "full":     (CD_FILL_FLOOR, 10_000),
    "cd>=120":  (120, 10_000),
    "cd>=60":   (60, 10_000),
    "60..240":  (60, 240),
    "30..200":  (30, 200),
    "cd>=200":  (200, 10_000),
}
REV_SIGS = ["ask_up", "d10_pos", "vel_flip", "ask_up_OR_d10",
            "ask_up_AND_d10", "any_of_three"]
BANDS = [0.00, 0.02, 0.05]
MIN_FIRES = 10


def pnl(entry, won):
    return (1.0 / entry - 1.0) if won else -1.0


def prep_market(m):
    """Flatten a market into parallel arrays for fast scanning.
    Returns dict with per-tick:
      cd, up_ask, dn_ask, up_ask_up, dn_ask_up (ask ticked up off prev),
      up_d10_pos, dn_d10_pos, up_vel_flip, dn_vel_flip
    Only ticks with cd > CD_FILL_FLOOR are kept (window: entry->cd15)."""
    cds = []
    up_ask = []
    dn_ask = []
    up_ask_up = []
    dn_ask_up = []
    up_d10p = []
    dn_d10p = []
    up_velf = []
    dn_velf = []
    prev_ua = None
    prev_da = None
    for t in m["ticks"]:
        cd = t["cd"]
        if cd <= CD_FILL_FLOOR:
            continue
        ua = t.get("up_ask")
        da = t.get("dn_ask")
        d10u = t.get("d10_up")
        d10d = t.get("d10_dn")
        vel = t.get("vel")
        crowd = t.get("crowd")
        cds.append(cd)
        up_ask.append(ua)
        dn_ask.append(da)
        up_ask_up.append(ua is not None and prev_ua is not None and ua > prev_ua)
        dn_ask_up.append(da is not None and prev_da is not None and da > prev_da)
        up_d10p.append(d10u is not None and d10u > 0)
        dn_d10p.append(d10d is not None and d10d > 0)
        up_velf.append(vel is not None and crowd == 1 and vel > 0)
        dn_velf.append(vel is not None and crowd == 0 and vel > 0)
        if ua is not None:
            prev_ua = ua
        if da is not None:
            prev_da = da
    return {
        "slug": m["slug"], "winner": m["winner"], "cd": cds,
        "up_ask": up_ask, "dn_ask": dn_ask,
        "up_ask_up": up_ask_up, "dn_ask_up": dn_ask_up,
        "up_d10p": up_d10p, "dn_d10p": dn_d10p,
        "up_velf": up_velf, "dn_velf": dn_velf,
    }


def held_arrays(cd, ask, e_cap, band):
    """For one side, return list `held[i]` = seconds this cheap episode has been
    sustained at tick i (or -1 if not currently in the cheap band ask<=e_cap+band).
    Episode resets whenever ask rises above e_cap+band or ask is None."""
    upper = e_cap + band
    held = [-1] * len(cd)
    since = None
    for i in range(len(cd)):
        a = ask[i]
        if a is not None and a <= upper:
            if since is None:
                since = cd[i]
            held[i] = since - cd[i]
        else:
            since = None
            held[i] = -1
    return held


def fire_for_variant(pm, held_up, held_dn, e_cap, persist_s, cd_lo, cd_hi, rev_sig):
    """First-trigger scan (cd high->low = array order) for one full variant.
    Returns (cd, side, entry_ask, won) or None."""
    cd = pm["cd"]
    up_ask = pm["up_ask"]
    dn_ask = pm["dn_ask"]
    winner = pm["winner"]
    if rev_sig == "ask_up":
        up_rev = pm["up_ask_up"]; dn_rev = pm["dn_ask_up"]
    elif rev_sig == "d10_pos":
        up_rev = pm["up_d10p"]; dn_rev = pm["dn_d10p"]
    elif rev_sig == "vel_flip":
        up_rev = pm["up_velf"]; dn_rev = pm["dn_velf"]
    elif rev_sig == "ask_up_OR_d10":
        up_rev = [a or b for a, b in zip(pm["up_ask_up"], pm["up_d10p"])]
        dn_rev = [a or b for a, b in zip(pm["dn_ask_up"], pm["dn_d10p"])]
    elif rev_sig == "ask_up_AND_d10":
        up_rev = [a and b for a, b in zip(pm["up_ask_up"], pm["up_d10p"])]
        dn_rev = [a and b for a, b in zip(pm["dn_ask_up"], pm["dn_d10p"])]
    else:  # any_of_three
        up_rev = [a or b or c for a, b, c in
                  zip(pm["up_ask_up"], pm["up_d10p"], pm["up_velf"])]
        dn_rev = [a or b or c for a, b, c in
                  zip(pm["dn_ask_up"], pm["dn_d10p"], pm["dn_velf"])]

    for i in range(len(cd)):
        c = cd[i]
        if not (cd_lo < c <= cd_hi):
            continue
        # UP side
        ua = up_ask[i]
        if (held_up[i] >= persist_s and up_rev[i]
                and ua is not None and ua <= e_cap):
            return c, "UP", ua, (winner == "UP")
        # DN side
        da = dn_ask[i]
        if (held_dn[i] >= persist_s and dn_rev[i]
                and da is not None and da <= e_cap):
            return c, "DN", da, (winner == "DN")
    return None


def main():
    with open(PANEL) as f:
        markets = json.load(f)
    n = len(markets)
    wdist = Counter(m["winner"] for m in markets)
    print(f"Markets: {n}  UP={wdist.get('UP',0)} DN={wdist.get('DN',0)}")
    print(f"Window: entry -> cd{CD_FILL_FLOOR}; first-trigger, leak-free.\n")

    pms = [prep_market(m) for m in markets]

    # accumulate per variant; key = (e_cap,band,persist,win_name,rev_sig)
    variants = {}
    for e_cap in E_CAPS:
        for band in BANDS:
            # precompute held arrays for both sides at this (e_cap,band)
            held_up_all = [held_arrays(pm["cd"], pm["up_ask"], e_cap, band)
                           for pm in pms]
            held_dn_all = [held_arrays(pm["cd"], pm["dn_ask"], e_cap, band)
                           for pm in pms]
            for persist_s in PERSISTS:
                for win_name, (lo, hi) in CD_WINDOWS.items():
                    for rev_sig in REV_SIGS:
                        fired = wins = 0
                        sum_pnl = sum_e = 0.0
                        col = {}
                        for k, pm in enumerate(pms):
                            col[pm["slug"]] = 0.0
                            f = fire_for_variant(pm, held_up_all[k],
                                                 held_dn_all[k], e_cap,
                                                 persist_s, lo, hi, rev_sig)
                            if f is None:
                                continue
                            c, side, ask, won = f
                            fired += 1
                            sum_e += ask
                            if won:
                                wins += 1
                            p = pnl(ask, won)
                            sum_pnl += p
                            col[pm["slug"]] = p
                        cov = 100.0 * fired / n
                        wr = (100.0 * wins / fired) if fired else 0.0
                        avg_e = (sum_e / fired) if fired else 0.0
                        be_wr = 100.0 * 2.0 * avg_e
                        mean_fire = (sum_pnl / fired) if fired else 0.0
                        mean_mkt = sum_pnl / n
                        variants[(e_cap, band, persist_s, win_name, rev_sig)] = {
                            "e_cap": e_cap, "band": band, "persist": persist_s,
                            "win_name": win_name, "rev_sig": rev_sig,
                            "cov": cov, "fires": fired, "wins": wins, "wr": wr,
                            "avg_e": avg_e, "be_wr": be_wr,
                            "wr_minus_be": wr - be_wr,
                            "mean_fire": mean_fire, "mean_mkt": mean_mkt,
                            "col": col,
                        }

    all_variants = list(variants.values())
    print(f"Swept {len(all_variants)} variants.\n")

    def label(v):
        return (f"e_cap<={v['e_cap']:.2f} band={v['band']:.2f} "
                f"persist>={v['persist']}s win={v['win_name']} sig={v['rev_sig']}")

    beats = [v for v in all_variants
             if v["wr"] > v["be_wr"] and v["fires"] >= MIN_FIRES]

    print("=== TOP 25 by mean/mkt (min_fires=%d) ===" % MIN_FIRES)
    print(f"{'cov%':>6} {'fires':>5} {'WR%':>5} {'beWR%':>6} {'WR-be':>6} "
          f"{'m/fire':>8} {'m/mkt':>8}  rule")
    for v in sorted((x for x in all_variants if x["fires"] >= MIN_FIRES),
                    key=lambda x: -x["mean_mkt"])[:25]:
        print(f"{v['cov']:>6.1f} {v['fires']:>5} {v['wr']:>5.1f} {v['be_wr']:>6.1f} "
              f"{v['wr_minus_be']:>+6.1f} {v['mean_fire']:>+8.3f} "
              f"{v['mean_mkt']:>+8.4f}  {label(v)}")

    print(f"\n=== BEAT breakeven (WR>2e, fires>={MIN_FIRES}): {len(beats)} ===")
    if beats:
        for v in sorted(beats, key=lambda x: -x["mean_mkt"])[:25]:
            print(f"{v['cov']:>6.1f} {v['fires']:>5} {v['wr']:>5.1f} "
                  f"{v['be_wr']:>6.1f} {v['wr_minus_be']:>+6.1f} "
                  f"{v['mean_fire']:>+8.3f} {v['mean_mkt']:>+8.4f}  {label(v)}")
    else:
        print("NONE beat breakeven. Closest (largest WR-be, i.e. least negative):")
        for v in sorted((x for x in all_variants if x["fires"] >= MIN_FIRES),
                        key=lambda x: -x["wr_minus_be"])[:15]:
            print(f"{v['cov']:>6.1f} {v['fires']:>5} {v['wr']:>5.1f} "
                  f"{v['be_wr']:>6.1f} {v['wr_minus_be']:>+6.1f} "
                  f"{v['mean_fire']:>+8.3f} {v['mean_mkt']:>+8.4f}  {label(v)}")

    if beats:
        best = max(beats, key=lambda x: x["mean_mkt"])
        best_cov = max(beats, key=lambda x: x["cov"])
        pool = "beats_breakeven"
    else:
        best = max((v for v in all_variants if v["fires"] >= MIN_FIRES),
                   key=lambda x: x["mean_mkt"])
        best_cov = None
        pool = "none_beat_breakeven_best_meanmkt"

    print("\n=== CHOSEN BEST (pool=%s) ===" % pool)
    print(label(best))
    print(f"cov={best['cov']:.1f}% fires={best['fires']} WR={best['wr']:.2f}% "
          f"beWR={best['be_wr']:.2f}% WR-be={best['wr_minus_be']:+.2f} "
          f"avg_e={best['avg_e']:.4f} mean/fire={best['mean_fire']:+.4f} "
          f"mean/mkt={best['mean_mkt']:+.4f}")

    print("\n=== HIGHEST-COVERAGE that beats breakeven ===")
    if best_cov is not None:
        print(label(best_cov))
        print(f"cov={best_cov['cov']:.1f}% fires={best_cov['fires']} "
              f"WR={best_cov['wr']:.2f}% beWR={best_cov['be_wr']:.2f}% "
              f"WR-be={best_cov['wr_minus_be']:+.2f} "
              f"mean/fire={best_cov['mean_fire']:+.4f} "
              f"mean/mkt={best_cov['mean_mkt']:+.4f}")
    else:
        print("(none beat breakeven)")

    os.makedirs(os.path.dirname(OUT_COL), exist_ok=True)
    with open(OUT_COL, "w") as f:
        json.dump(best["col"], f)
    print(f"\nWrote best-variant per-market pnl column -> {OUT_COL} "
          f"({len(best['col'])} markets)")
    # machine-readable summary line for the orchestrator
    print("\nBEST_JSON=" + json.dumps({
        "family": "dip_reversal_persist",
        "best_variant": label(best),
        "coverage_pct": best["cov"], "wr": best["wr"]/100.0,
        "mean_entry": best["avg_e"], "breakeven_wr": 2*best["avg_e"],
        "beats_breakeven": bool(best["wr"] > best["be_wr"]),
        "per_fire_mean": best["mean_fire"], "per_market_mean": best["mean_mkt"],
        "best_persistence_sec": best["persist"], "n_fires": best["fires"],
        "best_cov_variant": (label(best_cov) if best_cov else None),
        "best_cov_cov": (best_cov["cov"] if best_cov else None),
        "best_cov_meanmkt": (best_cov["mean_mkt"] if best_cov else None),
    }))


if __name__ == "__main__":
    main()
