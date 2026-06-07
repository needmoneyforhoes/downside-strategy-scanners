#!/usr/bin/env python3
"""
SR Divergence Indicator Scanner
================================
Tests ALL indicators as filters for the SR divergence signal at ANY cd.
Compares each filter against the cd 58-62 baseline (current t4_sr).

Usage:
    python3 sr_divergence_scanner.py market_history.jsonl

Reads replay data from market_history.jsonl (full market_history with ticks).
Outputs ranked indicator filters sorted by PnL.
"""

import json
import math
import sys
from collections import defaultdict

# ── Tick column indices ──────────────────────────────────────────────────────
CD, UP_BID, UP_ASK, DN_BID, DN_ASK = 0, 1, 2, 3, 4
UP_DEPTH, DN_DEPTH, UP_ZC, DN_ZC, UP_SR, DN_SR = 5, 6, 7, 8, 9, 10
BN_PRICE, BN_DELTA = 11, 12
UP_EMA, DN_EMA = 13, 14
UP_D3, DN_D3, UP_D10, DN_D10 = 15, 16, 17, 18
CROWD_SIDE, CROWD_CV = 19, 20
ASK_SUM = 21
DV_UP, DV_DN = 22, 23
CL_DELTA, CL_AGE, BN_SPREAD = 24, 25, 26

# ── SR divergence thresholds (same as t4_sr) ─────────────────────────────────
MAX_THRESH = 5.0     # max(usr, dsr) must exceed this
DIFF_THRESH = 3.0    # |usr - dsr| must exceed this
MAX_OWN_SR = 1.4     # own_sr must be <= this
MIN_ASK = 0.05       # minimum entry price


def load_replays(filepath):
    """Load market replay data from JSONL file."""
    markets = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = json.loads(line)
            if m.get('ticks') and m.get('winner') in ('UP', 'DN'):
                markets.append(m)
    return markets


def lookback_value(ticks, ti, col_idx, cd_now, secs_back):
    """Find tick value at approximately `secs_back` seconds earlier."""
    target_cd = cd_now + secs_back
    for j in range(ti - 1, max(ti - 800, -1), -1):
        if j < 0:
            return None
        tick_cd = ticks[j][CD]
        if abs(tick_cd - target_cd) < 3:
            return ticks[j][col_idx] or 0
        if tick_cd > target_cd + 5:
            return None
    return None


def compute_fires(markets):
    """Simulate SR divergence fires at ANY cd with full indicator suite."""
    all_fires = []

    for r in markets:
        slug = r['slug']
        winner = r['winner']
        ticks = r.get('ticks', [])
        trade_tape = r.get('trade_tape', [])

        fired = set()
        for ti, t in enumerate(ticks):
            usr = t[UP_SR] or 0
            dsr = t[DN_SR] or 0

            # SR divergence check
            if max(usr, dsr) < MAX_THRESH:
                continue
            if abs(usr - dsr) < DIFF_THRESH:
                continue

            # Determine buy side (opposite of pressure = low-SR side)
            if usr > dsr:
                bs = "DN"
                ba = t[DN_ASK] or 0
                os_ = dsr
                xs = usr
                od = t[DN_DEPTH] or 0
                xd = t[UP_DEPTH] or 0
                odv = t[DV_DN] or 0
                xdv = t[DV_UP] or 0
                oe = t[DN_EMA] or 0
                od3 = t[DN_D3] or 0
                xd3 = t[UP_D3] or 0
                od10 = t[DN_D10] or 0
                xd10 = t[UP_D10] or 0
                ob = t[DN_BID] or 0
            else:
                bs = "UP"
                ba = t[UP_ASK] or 0
                os_ = usr
                xs = dsr
                od = t[UP_DEPTH] or 0
                xd = t[DN_DEPTH] or 0
                odv = t[DV_UP] or 0
                xdv = t[DV_DN] or 0
                oe = t[UP_EMA] or 0
                od3 = t[UP_D3] or 0
                xd3 = t[DN_D3] or 0
                od10 = t[UP_D10] or 0
                xd10 = t[DN_D10] or 0
                ob = t[UP_BID] or 0

            if not ba or ba < MIN_ASK or os_ > MAX_OWN_SR:
                continue
            if bs in fired:
                continue
            fired.add(bs)

            is_w = bs == winner
            cd = t[CD]
            shares = round(1.0 / ba, 2)
            pnl = shares * ((1.0 - ba - 0.01) if is_w else -(ba + 0.01))

            # ── Raw indicators ──
            bn = t[BN_DELTA] or 0
            bn_wb = bn * (1 if bs == 'UP' else -1)
            cl = t[CL_DELTA] if len(t) > CL_DELTA else 0
            cv = t[CROWD_CV] or 0
            cs = t[CROWD_SIDE] or 0
            ask_sum = t[ASK_SUM] or 0
            dr = od / max(xd, 1)
            sr_diff = xs - os_
            ema_va = oe - ba if oe else 0
            spread = ba - ob
            d3_wb = od3 - xd3
            d10_wb = od10 - xd10

            # ── BN lookback trajectories (with bet) ──
            bn_10 = bn_30 = bn_60 = None
            for secs, attr in [(10, 'bn_10'), (30, 'bn_30'), (60, 'bn_60')]:
                old_bn_val = lookback_value(ticks, ti, BN_DELTA, cd, secs)
                if old_bn_val is not None:
                    val = (bn - old_bn_val) * (1 if bs == 'UP' else -1)
                    if attr == 'bn_10': bn_10 = val
                    elif attr == 'bn_30': bn_30 = val
                    else: bn_60 = val

            # ── Depth trajectories ──
            dep_col = DN_DEPTH if bs == "DN" else UP_DEPTH
            dep_opp_col = UP_DEPTH if bs == "DN" else DN_DEPTH
            depth_chg_10 = depth_chg_30 = depth_chg_60 = None
            opp_depth_chg_10 = opp_depth_chg_30 = None
            for secs, attr in [(10, 10), (30, 30), (60, 60)]:
                old_d = lookback_value(ticks, ti, dep_col, cd, secs)
                if old_d is not None:
                    if secs == 10: depth_chg_10 = od - old_d
                    elif secs == 30: depth_chg_30 = od - old_d
                    else: depth_chg_60 = od - old_d
                old_xd = lookback_value(ticks, ti, dep_opp_col, cd, secs)
                if old_xd is not None:
                    if secs == 10: opp_depth_chg_10 = xd - old_xd
                    elif secs == 30: opp_depth_chg_30 = xd - old_xd

            # ── EMA trajectories ──
            ema_col = DN_EMA if bs == "DN" else UP_EMA
            ema_chg_10 = ema_chg_30 = None
            for secs in [10, 30]:
                old_e = lookback_value(ticks, ti, ema_col, cd, secs)
                if old_e is not None:
                    if secs == 10: ema_chg_10 = oe - old_e
                    else: ema_chg_30 = oe - old_e

            # ── Ask trajectory ──
            ask_col = DN_ASK if bs == "DN" else UP_ASK
            ask_chg_10 = ask_chg_30 = None
            for secs in [10, 30]:
                old_a = lookback_value(ticks, ti, ask_col, cd, secs)
                if old_a is not None:
                    if secs == 10: ask_chg_10 = ba - old_a
                    else: ask_chg_30 = ba - old_a

            # ── DV / ZC trajectories ──
            dv_col = DV_DN if bs == "DN" else DV_UP
            zc_col = DN_ZC if bs == "DN" else UP_ZC
            dv_chg_10 = lookback_value(ticks, ti, dv_col, cd, 10)
            if dv_chg_10 is not None: dv_chg_10 = odv - dv_chg_10
            zc_chg_10 = lookback_value(ticks, ti, zc_col, cd, 10)
            if zc_chg_10 is not None:
                zc_chg_10 = (t[DN_ZC if bs == "DN" else UP_ZC] or 0) - zc_chg_10

            # ── SR lookback (divergence growing?) ──
            sr_5s = sr_10s = None
            for secs in [5, 10]:
                old_usr = lookback_value(ticks, ti, UP_SR, cd, secs)
                old_dsr = lookback_value(ticks, ti, DN_SR, cd, secs)
                if old_usr is not None and old_dsr is not None:
                    old_diff = abs(old_usr - old_dsr)
                    sr_change = abs(usr - dsr) - old_diff
                    if secs == 5: sr_5s = sr_change
                    else: sr_10s = sr_change

            # ── BN range / std / slope (from 101 tracker) ──
            bn_vals_60 = []
            for j in range(ti, max(ti - 800, -1), -1):
                if ticks[j][CD] - cd > 60:
                    break
                bn_vals_60.append(ticks[j][BN_DELTA] or 0)

            bn_range_60 = (max(bn_vals_60) - min(bn_vals_60)) if len(bn_vals_60) > 2 else 0
            bn_std_60 = 0
            if len(bn_vals_60) > 5:
                mean_bn = sum(bn_vals_60) / len(bn_vals_60)
                bn_std_60 = math.sqrt(sum((x - mean_bn) ** 2 for x in bn_vals_60) / len(bn_vals_60))

            bn_vals_30 = [v for j, v in enumerate(bn_vals_60)
                          if j < len(bn_vals_60) and
                          (ticks[min(ti, ti - j)][CD] if ti - j >= 0 else 999) - cd <= 30]
            # Simpler: just take first 30s worth
            bn_vals_30 = []
            bn_vals_10 = []
            for j in range(ti, max(ti - 800, -1), -1):
                dt = ticks[j][CD] - cd
                if dt > 60: break
                bv = ticks[j][BN_DELTA] or 0
                if dt <= 30: bn_vals_30.append(bv)
                if dt <= 10: bn_vals_10.append(bv)

            bn_slope_30_wb = 0
            if len(bn_vals_30) >= 2:
                bn_slope_30_wb = (bn_vals_30[0] - bn_vals_30[-1]) * (1 if bs == 'UP' else -1)
            bn_slope_10_wb = 0
            if len(bn_vals_10) >= 2:
                bn_slope_10_wb = (bn_vals_10[0] - bn_vals_10[-1]) * (1 if bs == 'UP' else -1)

            # BN sign flips in 30s (fake train detector)
            bn_sign_flips = 0
            if len(bn_vals_30) > 2:
                for k in range(1, len(bn_vals_30)):
                    if bn_vals_30[k] * bn_vals_30[k - 1] < 0:
                        bn_sign_flips += 1

            # ── Cancel rate (depth swings w/o price change) ──
            cancel_rate = 0
            for j in range(max(0, ti - 50), ti):
                if ticks[j][CD] - cd > 30:
                    continue
                d_now = ticks[j][dep_col] or 0
                d_prev = ticks[max(0, j - 1)][dep_col] or 0
                p_now = ticks[j][ask_col] or 0
                p_prev = ticks[max(0, j - 1)][ask_col] or 0
                if abs(d_now - d_prev) > 100 and p_now == p_prev:
                    cancel_rate += 1

            # ── Conviction velocity / acceleration ──
            cv_5s = lookback_value(ticks, ti, CROWD_CV, cd, 5)
            cv_10s = lookback_value(ticks, ti, CROWD_CV, cd, 10)
            conv_vel = (cv - (cv_5s or cv)) / 5 if cv_5s else 0
            conv_accel = 0
            if cv_5s is not None and cv_10s is not None:
                conv_accel = ((cv - cv_5s) - (cv_5s - cv_10s)) / 5

            # ── Depth oscillations ──
            depth_osc = 0
            prev_d = None
            for j in range(ti, max(ti - 500, -1), -1):
                if ticks[j][CD] - cd > 30:
                    break
                curr_d = ticks[j][dep_col] or 0
                if prev_d is not None and prev_d > 0 and curr_d > 0:
                    ratio = max(curr_d / prev_d, prev_d / curr_d)
                    if ratio > 3:
                        depth_osc += 1
                prev_d = curr_d

            # ── Lead changes up to this tick ──
            lead_changes = 0
            prev_lead = None
            for j in range(0, min(ti, len(ticks))):
                ub = ticks[j][UP_BID] or 0
                db = ticks[j][DN_BID] or 0
                lead = 'UP' if ub > db else ('DN' if db > ub else prev_lead)
                if lead and prev_lead and lead != prev_lead:
                    lead_changes += 1
                prev_lead = lead

            # ── Crowd alignment ──
            crowd_wb = 1 if (cs == 1 and bs == 'UP') or (cs == -1 and bs == 'DN') else (-1 if cs != 0 else 0)
            bn_confirms_crowd = 1 if (bn > 0 and cs == 1) or (bn < 0 and cs == -1) else (-1 if cs != 0 else 0)
            both_collapse = 1 if (usr >= 5 and dsr >= 5) else 0

            # ── Depth asymmetry ──
            depth_asym = (od - xd) / (od + xd) if (od + xd) > 0 else 0

            # ── Cross ratios ──
            bn_sr_ratio = abs(bn_wb) / max(sr_diff, 0.1)

            # ── Trade tape indicators (30s window) ──
            tape_buy_own = tape_sell_own = tape_buy_opp = tape_sell_opp = 0
            tape_whale_own = tape_whale_opp = 0
            tape_vol_own = tape_vol_opp = 0
            if trade_tape:
                for tt in trade_tape:
                    if not isinstance(tt, (list, tuple)) or len(tt) < 4:
                        continue
                    tt_cd = tt[0] or 0
                    if abs(tt_cd - cd) > 30:
                        continue
                    tt_side = tt[1]
                    tt_size = tt[2] if len(tt) > 2 else 0
                    tt_asset = tt[3] if len(tt) > 3 else None
                    if not isinstance(tt_size, (int, float)):
                        continue
                    is_own = (tt_asset == 'UP' and bs == 'UP') or (tt_asset == 'DN' and bs == 'DN')
                    is_opp = (tt_asset == 'UP' and bs == 'DN') or (tt_asset == 'DN' and bs == 'UP')
                    if is_own:
                        tape_vol_own += abs(tt_size)
                        if tt_side == 'BUY': tape_buy_own += abs(tt_size)
                        else: tape_sell_own += abs(tt_size)
                        if abs(tt_size) > 100: tape_whale_own += 1
                    elif is_opp:
                        tape_vol_opp += abs(tt_size)
                        if tt_side == 'BUY': tape_buy_opp += abs(tt_size)
                        else: tape_sell_opp += abs(tt_size)
                        if abs(tt_size) > 100: tape_whale_opp += 1

            tape_imbalance = (tape_buy_own - tape_sell_own) - (tape_buy_opp - tape_sell_opp)
            tape_ratio_val = tape_vol_own / max(tape_vol_opp, 1)

            # ── Build fire record ──
            e = {
                'is_w': is_w, 'pnl': pnl, 'slug': slug,
                # Timing
                'cd': cd,
                # Price
                'ask': ba, 'spread': spread, 'ema_va': ema_va,
                # SR
                'own_sr': os_, 'opp_sr': xs, 'sr_diff': sr_diff, 'sr_max': max(usr, dsr),
                # Depth
                'own_zc': t[DN_ZC if bs == "DN" else UP_ZC] or 0,
                'opp_zc': t[UP_ZC if bs == "DN" else DN_ZC] or 0,
                'own_depth': od, 'opp_depth': xd,
                'depth_ratio': dr, 'depth_asym': depth_asym,
                'own_dv': odv, 'opp_dv': xdv,
                # BN
                'bn_wb': bn_wb, 'bn_abs': abs(bn),
                # Oracle
                'cl': cl or 0, 'cl_age': t[CL_AGE] if len(t) > CL_AGE else 0,
                # Crowd
                'cv': cv, 'ask_sum': ask_sum, 'crowd_wb': crowd_wb,
                'bn_confirms_crowd': bn_confirms_crowd,
                # Deltas
                'd3_wb': d3_wb, 'd10_wb': d10_wb,
                # BN lookbacks (with bet)
                'bn_10': bn_10, 'bn_30': bn_30, 'bn_60': bn_60,
                # BN trajectory (from 101 tracker)
                'bn_range_60': bn_range_60, 'bn_std_60': bn_std_60,
                'bn_slope_30_wb': bn_slope_30_wb, 'bn_slope_10_wb': bn_slope_10_wb,
                'bn_sign_flips': bn_sign_flips,
                # Depth trajectories
                'depth_chg_10': depth_chg_10, 'depth_chg_30': depth_chg_30,
                'depth_chg_60': depth_chg_60,
                'opp_depth_chg_10': opp_depth_chg_10, 'opp_depth_chg_30': opp_depth_chg_30,
                # EMA trajectories
                'ema_chg_10': ema_chg_10, 'ema_chg_30': ema_chg_30,
                # Ask trajectories
                'ask_chg_10': ask_chg_10, 'ask_chg_30': ask_chg_30,
                # DV / ZC trajectories
                'dv_chg_10': dv_chg_10, 'zc_chg_10': zc_chg_10,
                # SR lookback
                'sr_5s': sr_5s, 'sr_10s': sr_10s,
                # 101 tracker fields
                'cancel_rate': cancel_rate, 'depth_osc': depth_osc,
                'lead_changes': lead_changes,
                'conv_vel': conv_vel, 'conv_accel': conv_accel,
                'both_collapse': both_collapse,
                'bn_sr_ratio': bn_sr_ratio,
                # Trade tape
                'tape_imbalance': tape_imbalance, 'tape_ratio': tape_ratio_val,
                'tape_whale_own': tape_whale_own, 'tape_whale_opp': tape_whale_opp,
                'tape_vol_own': tape_vol_own, 'tape_vol_opp': tape_vol_opp,
            }
            all_fires.append(e)

    return all_fires


def scan_indicators(fires):
    """Test every indicator at multiple thresholds."""
    skip = {'is_w', 'pnl', 'slug'}
    keys = [k for k in fires[0].keys() if k not in skip]

    results = []
    for key in keys:
        vals = [f[key] for f in fires
                if f[key] is not None and isinstance(f[key], (int, float))]
        if len(vals) < 10:
            continue
        sv = sorted(set(vals))

        for pct in range(5, 96, 5):
            idx = min(int(len(sv) * pct / 100), len(sv) - 1)
            thresh = sv[idx]

            for direction in ['>', '<']:
                if direction == '>':
                    fn = lambda f, th=thresh, k=key: f[k] is not None and f[k] > th
                    label = f"{key} > {thresh:.4f}"
                else:
                    fn = lambda f, th=thresh, k=key: f[k] is not None and f[k] < th
                    label = f"{key} < {thresh:.4f}"

                p = [f for f in fires if fn(f)]
                if len(p) < 8 or len(p) > len(fires) - 5:
                    continue
                w = sum(1 for f in p if f['is_w'])
                pnl = sum(f['pnl'] for f in p)
                wr = w / len(p) * 100

                # Compute breakeven and edge
                wins_pnl = [f['pnl'] for f in p if f['is_w']]
                loss_pnl = [f['pnl'] for f in p if not f['is_w']]
                avg_w = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
                avg_l = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
                if avg_w > 0 and avg_l < 0 and (avg_w - avg_l) != 0:
                    be = -avg_l / (avg_w - avg_l) * 100
                elif not loss_pnl:
                    be = 0
                else:
                    be = 100
                edge = wr - be

                # Keep if positive edge AND positive PnL
                if pnl > 0 and edge > 0:
                    results.append((edge, pnl, label, len(p), w, wr, be, avg_w, avg_l, key))

    results.sort(reverse=True)  # sorted by edge (first element)
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 sr_divergence_scanner.py <market_history.jsonl>")
        print("  Can also pass multiple files or a combined replay JSONL.")
        sys.exit(1)

    # Load all files
    markets = []
    for fp in sys.argv[1:]:
        markets.extend(load_replays(fp))
    print(f"Loaded {len(markets)} markets with replay data")

    # Compute fires
    print("Computing SR divergence fires with full indicator suite...")
    fires = compute_fires(markets)
    w_all = sum(1 for f in fires if f['is_w'])
    pnl_all = sum(f['pnl'] for f in fires)
    n_indicators = len(fires[0]) - 3  # minus is_w, pnl, slug
    print(f"Total fires: {len(fires)}, {w_all}W, WR={w_all/len(fires)*100:.0f}%, "
          f"PnL=${pnl_all:+.2f}")
    print(f"Indicators per fire: {n_indicators}")

    # Baseline: cd 58-62
    cd_fires = [f for f in fires if 58 <= f['cd'] <= 62]
    if cd_fires:
        cd_w = sum(1 for f in cd_fires if f['is_w'])
        cd_pnl = sum(f['pnl'] for f in cd_fires)
        print(f"\nBASELINE cd 58-62: {len(cd_fires)}F {cd_w}W "
              f"WR={cd_w/len(cd_fires)*100:.0f}% ${cd_pnl:+.2f}")
    else:
        print(f"\nBASELINE cd 58-62: 0 fires (first-fire dedup fires earlier)")
        print(f"  Note: this scanner fires at FIRST SR divergence per side.")
        print(f"  The real t4_sr waits until cd 58-62 specifically.")

    # Scan
    print(f"\nScanning {n_indicators} indicators × ~19 thresholds × 2 directions...")
    results = scan_indicators(fires)

    # Print results — RANKED BY EDGE (best per indicator key)
    print(f"\n{'=' * 100}")
    print(f"TOP INDICATORS BY EDGE (best threshold per indicator)")
    print(f"  Edge = actual WR - breakeven WR. Higher = more profitable.")
    print(f"  Low BE = asymmetric payoff (can afford many losses).")
    print(f"{'=' * 100}")
    print(f"\n{'#':>2} {'filter':<40} {'F':>4} {'W':>3} {'WR':>5} {'BE':>5} "
          f"{'edge':>7} {'avg_W':>7} {'avg_L':>7} {'PnL':>9} {'$/f':>7}")
    print("-" * 100)

    seen = set()
    for edge, pnl, label, n, w, wr, be, avg_w, avg_l, key in results:
        if key in seen:
            continue
        seen.add(key)
        print(f"{len(seen):>2} {label:<40} {n:>4} {w:>3} {wr:>4.0f}% {be:>4.0f}% "
              f"{edge:>+6.1f}pp ${avg_w:>+6.2f} ${avg_l:>+6.2f} "
              f"${pnl:>+8.2f} ${pnl/n:>+6.2f}")
        if len(seen) >= 30:
            break

    # Print LOWEST BREAKEVEN filters (asymmetric payoff strategies)
    print(f"\n{'=' * 100}")
    print(f"LOWEST BREAKEVEN FILTERS (most forgiving — can afford many losses)")
    print(f"{'=' * 100}")
    print(f"\n{'#':>2} {'filter':<40} {'F':>4} {'W':>3} {'WR':>5} {'BE':>5} "
          f"{'edge':>7} {'W/L':>6} {'PnL':>9}")
    print("-" * 90)

    by_be = sorted(results, key=lambda r: r[6])  # sort by BE ascending
    seen_be = set()
    for edge, pnl, label, n, w, wr, be, avg_w, avg_l, key in by_be:
        if key in seen_be or be <= 0:
            continue
        seen_be.add(key)
        wl_ratio = avg_w / abs(avg_l) if avg_l != 0 else float('inf')
        wlr = f"{wl_ratio:.1f}x" if wl_ratio < 100 else "∞"
        print(f"{len(seen_be):>2} {label:<40} {n:>4} {w:>3} {wr:>4.0f}% {be:>4.0f}% "
              f"{edge:>+6.1f}pp {wlr:>6} ${pnl:>+8.2f}")
        if len(seen_be) >= 20:
            break

    # Print ALL results (all thresholds, all indicators) — top 60 by edge
    print(f"\n{'=' * 100}")
    print(f"ALL PASSING FILTERS — sorted by EDGE (top 60)")
    print(f"{'=' * 100}")
    print(f"\n{'filter':<40} {'F':>4} {'W':>3} {'WR':>5} {'BE':>5} "
          f"{'edge':>7} {'PnL':>9} {'$/f':>7}")
    print("-" * 90)
    for edge, pnl, label, n, w, wr, be, avg_w, avg_l, key in results[:60]:
        print(f"{label:<40} {n:>4} {w:>3} {wr:>4.0f}% {be:>4.0f}% "
              f"{edge:>+6.1f}pp ${pnl:>+8.2f} ${pnl/n:>+6.2f}")

    # Test key combos
    print(f"\n{'=' * 100}")
    print(f"KEY COMBOS (no cd restriction) — ranked by edge")
    print(f"{'=' * 100}")
    print(f"\n{'filter':<50} {'F':>4} {'W':>3} {'WR':>5} {'BE':>5} "
          f"{'edge':>7} {'PnL':>9}")
    print("-" * 95)

    combos = [
        ('BASELINE: cd 58-62', lambda f: 58 <= f['cd'] <= 62),
        ('bn_range_60 > 0.008', lambda f: f['bn_range_60'] > 0.008),
        ('bn_range_60 > 0.008 AND dr > 5', lambda f: f['bn_range_60'] > 0.008 and f['depth_ratio'] > 5),
        ('bn_range_60 > 0.008 AND dr > 10', lambda f: f['bn_range_60'] > 0.008 and f['depth_ratio'] > 10),
        ('bn_wb > 0', lambda f: f['bn_wb'] > 0),
        ('bn_wb > 0 AND dr > 5', lambda f: f['bn_wb'] > 0 and f['depth_ratio'] > 5),
        ('bn_wb > 0 AND dr > 10', lambda f: f['bn_wb'] > 0 and f['depth_ratio'] > 10),
        ('cancel_rate > 5', lambda f: f['cancel_rate'] > 5),
        ('cancel_rate > 5 AND bn_wb > 0', lambda f: f['cancel_rate'] > 5 and f['bn_wb'] > 0),
        ('dr > 20', lambda f: f['depth_ratio'] > 20),
        ('bn_30 > 0 (30s trajectory)', lambda f: f['bn_30'] is not None and f['bn_30'] > 0),
        ('cl > 0 AND dr > 5', lambda f: f['cl'] > 0 and f['depth_ratio'] > 5),
        ('bn_range_60 > 0.005 AND bn_wb > 0 AND dr > 5',
         lambda f: f['bn_range_60'] > 0.005 and f['bn_wb'] > 0 and f['depth_ratio'] > 5),
    ]

    combo_results = []
    for fname, fn in combos:
        p = [f for f in fires if fn(f)]
        if len(p) < 5:
            continue
        w = sum(1 for f in p if f['is_w'])
        pnl = sum(f['pnl'] for f in p)
        wr = w / len(p) * 100
        wins_pnl = [f['pnl'] for f in p if f['is_w']]
        loss_pnl = [f['pnl'] for f in p if not f['is_w']]
        avg_w = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
        avg_l = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0
        if avg_w > 0 and avg_l < 0 and (avg_w - avg_l) != 0:
            be = -avg_l / (avg_w - avg_l) * 100
        elif not loss_pnl:
            be = 0
        else:
            be = 100
        edge = wr - be
        combo_results.append((edge, fname, len(p), w, wr, be, pnl))

    combo_results.sort(reverse=True)
    for edge, fname, n, w, wr, be, pnl in combo_results:
        m = ' ⭐' if edge > 10 and pnl > 5 else ' ✅' if pnl > 0 else ''
        print(f"{fname:<50} {n:>4} {w:>3} {wr:>4.0f}% {be:>4.0f}% "
              f"{edge:>+6.1f}pp ${pnl:>+8.2f}{m}")


if __name__ == '__main__':
    main()
