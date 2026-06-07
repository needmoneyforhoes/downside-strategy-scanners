#!/usr/bin/env python3
"""
DN-Specific Temporal Scanner
==============================
The standard temporal features (stability, bn_persistence, etc.) FAIL 
for DN OOS. This scanner tests DN-SPECIFIC temporal patterns:

  1. BTC DROP DYNAMICS — how BTC falls matters
     - Drop acceleration (falling faster over time?)
     - Failed recovery (bounced but dropped again?)
     - Drop persistence without bounce

  2. CROWD PANIC — DN wins often follow crowd over-reaction
     - Conviction velocity (how fast is crowd deciding?)
     - Conviction reversal (was UP-leaning, now shifting?)
     - Spread blowout (spreads widening = uncertainty)

  3. REVERSAL PATTERNS — DN wins from UP trend reversal
     - UP price peak in window vs current (peak decay)
     - Time since UP peaked
     - Opponent (UP) momentum dying

  4. DEPTH ASYMMETRY — DN-specific order book patterns
     - DN depth building while UP flat
     - Depth velocity divergence
     - Zero-count trajectory (manipulation fading?)

  5. MICROSTRUCTURE — market structure signals
     - Ask sum deviation (gap from 1.02)
     - Desync patterns (data staleness)
     - Trade tape imbalance over time

Run on VPS:
  python3 dn_temporal_scanner.py market_history.jsonl > dn_temporal.txt 2>&1
"""
import json, math, sys
from collections import defaultdict

CD,UP_BID,UP_ASK,DN_BID,DN_ASK=0,1,2,3,4
UP_DEPTH,DN_DEPTH,UP_ZC,DN_ZC,UP_SR,DN_SR=5,6,7,8,9,10
BN_PRICE,BN_DELTA=11,12; UP_EMA,DN_EMA=13,14
UP_D3,DN_D3,UP_D10,DN_D10=15,16,17,18
CROWD_SIDE,CROWD_CV=19,20; ASK_SUM=21
DV_UP,DV_DN=22,23; CL_DELTA,CL_AGE,BN_SPREAD=24,25,26

ZONES = [
    (0.03, 0.10, "lottery"),
    (0.10, 0.20, "deep"),
    (0.20, 0.30, "mid-low"),
    (0.30, 0.40, "mid"),
    (0.40, 0.50, "mid-high"),
    (0.03, 0.20, "all-deep"),
    (0.20, 0.40, "all-mid"),
    (0.03, 0.50, "all"),
]

def load(fp):
    mkts = []
    with open(fp) as f:
        for line in f:
            if not line.strip(): continue
            m = json.loads(line)
            if m.get('ticks') and m.get('winner') in ('UP','DN'):
                mkts.append(m)
    mkts.sort(key=lambda m: m.get('ts',''))
    return mkts

def get_window(ticks, ti, cd, secs):
    r = []
    for j in range(ti, max(ti-2000,-1), -1):
        if j < 0: break
        if ticks[j][CD] > cd + secs + 2: break
        if cd - 2 <= ticks[j][CD] <= cd + secs + 2:
            r.append(ticks[j])
    return r

def lb(ticks, ti, col, cd, secs):
    for j in range(ti-1, max(ti-800,-1), -1):
        if j < 0: return None
        if abs(ticks[j][CD]-(cd+secs)) < 3: return ticks[j][col] or 0
        if ticks[j][CD] > cd+secs+5: return None
    return None

def compute_dn_features(ticks, ti, cd):
    """Compute DN-specific temporal features."""
    t = ticks[ti]
    f = {}
    
    dn_ask = t[DN_ASK] or 0
    up_ask = t[UP_ASK] or 0
    bn = t[BN_DELTA] or 0
    cv = t[CROWD_CV] or 0
    cl = t[CL_DELTA] if len(t) > CL_DELTA else 0
    dn_depth = t[DN_DEPTH] or 0
    up_depth = t[UP_DEPTH] or 0
    dn_sr = t[DN_SR] or 0
    up_sr = t[UP_SR] or 0
    ask_sum = t[ASK_SUM] or 0
    
    f['cd'] = cd
    f['ask'] = dn_ask
    f['bn_wb'] = -bn  # for DN, negative BN = with-trend
    f['cl'] = cl or 0
    f['cv'] = cv
    
    for window in [30, 60, 120]:
        w = get_window(ticks, ti, cd, window)
        if len(w) < 5:
            for k in [f'bn_drop_accel_{window}', f'bn_failed_recovery_{window}',
                      f'bn_drop_persist_{window}', f'bn_min_in_window_{window}',
                      f'cv_velocity_{window}', f'cv_reversal_{window}',
                      f'spread_blowout_{window}', f'up_peak_decay_{window}',
                      f'up_momentum_dying_{window}', f'dn_depth_build_{window}',
                      f'depth_vel_diverge_{window}', f'zc_trajectory_{window}',
                      f'ask_sum_deviation_{window}', f'desync_trend_{window}',
                      f'dn_depth_stability_{window}', f'up_ask_falling_{window}',
                      f'dn_sr_growing_{window}', f'bn_consec_neg_{window}',
                      f'cv_accel_{window}', f'depth_ratio_trend_{window}']:
                f[k] = None
            continue
        
        # ═══ 1. BTC DROP DYNAMICS ═══
        bn_vals = [tk[BN_DELTA] or 0 for tk in w]
        
        # Drop acceleration: is BN falling FASTER over time?
        # Compare first-half avg vs second-half avg
        half = len(bn_vals) // 2
        if half > 0:
            first_half = sum(bn_vals[half:]) / max(len(bn_vals) - half, 1)
            second_half = sum(bn_vals[:half]) / max(half, 1)
            # Negative acceleration = dropping faster (good for DN)
            f[f'bn_drop_accel_{window}'] = second_half - first_half
        else:
            f[f'bn_drop_accel_{window}'] = 0
        
        # Failed recovery: BN dropped, bounced, then dropped again
        # Count: how many times did BN go from negative to less-negative back to more-negative
        recoveries = 0
        for i in range(2, len(bn_vals)):
            if bn_vals[i] < bn_vals[i-1] < bn_vals[i-2] < 0:
                # Getting more negative → continued drop
                pass
            elif bn_vals[i-1] > bn_vals[i-2] and bn_vals[i] < bn_vals[i-1] and bn_vals[i] < 0:
                # Bounced then dropped → failed recovery
                recoveries += 1
        f[f'bn_failed_recovery_{window}'] = recoveries
        
        # Drop persistence: longest consecutive BN < 0 run
        max_neg = 0; cur_neg = 0
        for b in bn_vals:
            if b < 0:
                cur_neg += 1
                max_neg = max(max_neg, cur_neg)
            else:
                cur_neg = 0
        f[f'bn_consec_neg_{window}'] = max_neg / max(len(bn_vals), 1)
        
        # BN minimum in window (how deep did BTC drop?)
        f[f'bn_min_in_window_{window}'] = min(bn_vals) if bn_vals else 0
        
        # BN drop persistence without bounce
        f[f'bn_drop_persist_{window}'] = max_neg
        
        # ═══ 2. CROWD PANIC ═══
        cvs = [tk[CROWD_CV] or 0 for tk in w if tk[CROWD_CV]]
        
        # Conviction velocity: how fast is cv changing?
        if len(cvs) >= 3:
            cv_vel = (cvs[0] - cvs[-1]) / max(window, 1)
            f[f'cv_velocity_{window}'] = cv_vel
            
            # CV acceleration
            third = len(cvs) // 3
            if third > 0:
                cv_early = sum(cvs[-third:]) / third
                cv_late = sum(cvs[:third]) / third
                cv_mid = sum(cvs[third:2*third]) / max(third, 1)
                f[f'cv_accel_{window}'] = (cv_late - cv_mid) - (cv_mid - cv_early)
            else:
                f[f'cv_accel_{window}'] = 0
        else:
            f[f'cv_velocity_{window}'] = None
            f[f'cv_accel_{window}'] = None
        
        # Conviction reversal: was crowd leaning UP, now shifting to DN?
        if len(cvs) >= 4:
            early_cv = sum(cvs[-len(cvs)//2:]) / max(len(cvs)//2, 1)
            late_cv = sum(cvs[:len(cvs)//2]) / max(len(cvs)//2, 1)
            # For DN: early high cv (crowd confident UP) → late lower cv (doubt) = reversal
            f[f'cv_reversal_{window}'] = early_cv - late_cv
        else:
            f[f'cv_reversal_{window}'] = None
        
        # Spread blowout: are spreads widening?
        dn_spreads = [(tk[DN_ASK] or 0) - (tk[DN_BID] or 0) for tk in w if tk[DN_ASK] and tk[DN_BID]]
        if len(dn_spreads) >= 3:
            f[f'spread_blowout_{window}'] = dn_spreads[0] - dn_spreads[-1]  # positive = widening
        else:
            f[f'spread_blowout_{window}'] = None
        
        # ═══ 3. REVERSAL PATTERNS ═══
        up_asks = [tk[UP_ASK] or 0 for tk in w if tk[UP_ASK]]
        
        # UP price peak vs current (how much has UP fallen from its peak?)
        if up_asks:
            up_peak = max(up_asks)
            up_current = up_asks[0] if up_asks else 0
            f[f'up_peak_decay_{window}'] = up_peak - up_current  # positive = UP falling from peak
        else:
            f[f'up_peak_decay_{window}'] = None
        
        # UP momentum dying: is UP ask velocity slowing?
        if len(up_asks) >= 3:
            up_vel_recent = up_asks[0] - up_asks[min(len(up_asks)//3, len(up_asks)-1)]
            up_vel_early = up_asks[len(up_asks)//3] - up_asks[-1] if len(up_asks) > 3 else 0
            f[f'up_momentum_dying_{window}'] = up_vel_early - up_vel_recent  # positive = UP slowing
            f[f'up_ask_falling_{window}'] = up_asks[-1] - up_asks[0]  # negative = UP falling
        else:
            f[f'up_momentum_dying_{window}'] = None
            f[f'up_ask_falling_{window}'] = None
        
        # ═══ 4. DEPTH ASYMMETRY ═══
        dn_depths = [tk[DN_DEPTH] or 0 for tk in w]
        up_depths = [tk[UP_DEPTH] or 0 for tk in w]
        
        # DN depth building while UP flat
        if len(dn_depths) >= 3 and len(up_depths) >= 3:
            dn_change = dn_depths[0] - dn_depths[-1]
            up_change = up_depths[0] - up_depths[-1]
            f[f'dn_depth_build_{window}'] = dn_change  # positive = DN growing
            f[f'depth_vel_diverge_{window}'] = dn_change - up_change  # positive = DN growing faster
        else:
            f[f'dn_depth_build_{window}'] = None
            f[f'depth_vel_diverge_{window}'] = None
        
        # DN depth stability
        if len(dn_depths) >= 3:
            dm = sum(dn_depths)/len(dn_depths)
            dstd = math.sqrt(sum((d-dm)**2 for d in dn_depths)/max(len(dn_depths)-1,1))
            f[f'dn_depth_stability_{window}'] = 1.0 / (dstd + 1)
        else:
            f[f'dn_depth_stability_{window}'] = None
        
        # Zero-count trajectory: are zero-counts INCREASING (bad) or DECREASING (good)?
        zcs = [tk[DN_ZC] or 0 for tk in w]
        if len(zcs) >= 3:
            zc_early = sum(zcs[-len(zcs)//2:]) / max(len(zcs)//2, 1)
            zc_late = sum(zcs[:len(zcs)//2]) / max(len(zcs)//2, 1)
            f[f'zc_trajectory_{window}'] = zc_early - zc_late  # positive = ZC decreasing (good)
        else:
            f[f'zc_trajectory_{window}'] = None
        
        # DN SR growing over time
        dn_srs = [tk[DN_SR] or 0 for tk in w]
        if len(dn_srs) >= 3:
            f[f'dn_sr_growing_{window}'] = dn_srs[0] - dn_srs[-1]  # positive = SR increasing
        else:
            f[f'dn_sr_growing_{window}'] = None
        
        # Depth ratio trend
        if len(dn_depths) >= 3 and len(up_depths) >= 3:
            ratios = [d/max(u,1) for d,u in zip(dn_depths, up_depths)]
            f[f'depth_ratio_trend_{window}'] = ratios[0] - ratios[-1]  # positive = DN/UP ratio growing
        else:
            f[f'depth_ratio_trend_{window}'] = None
        
        # ═══ 5. MICROSTRUCTURE ═══
        ask_sums = [tk[ASK_SUM] or 0 for tk in w if tk[ASK_SUM]]
        if len(ask_sums) >= 3:
            f[f'ask_sum_deviation_{window}'] = max(ask_sums) - 1.02  # how far from normal
        else:
            f[f'ask_sum_deviation_{window}'] = None
        
        # Desync trend
        # Can't compute directly without age data, use ask_sum stability as proxy
        if len(ask_sums) >= 3:
            asm = sum(ask_sums)/len(ask_sums)
            f[f'desync_trend_{window}'] = math.sqrt(sum((a-asm)**2 for a in ask_sums)/max(len(ask_sums)-1,1))
        else:
            f[f'desync_trend_{window}'] = None
    
    return f

def compute_fires(markets, zone_lo, zone_hi):
    fires = []
    for mi, m in enumerate(markets):
        ticks = m['ticks']
        for ti, t in enumerate(ticks):
            cd = t[CD]
            if cd < 60 or cd > 290: continue
            ask = t[DN_ASK]
            if ask is None or not (zone_lo <= ask < zone_hi): continue
            won = (m['winner'] == 'DN')
            pnl = ((1.0-ask) if won else -ask) * 5
            feats = compute_dn_features(ticks, ti, cd)
            feats['pnl'] = pnl
            feats['won'] = won
            feats['market_idx'] = mi
            fires.append(feats)
            break
    return fires

def scan(fires, min_n=10):
    if len(fires) < min_n: return []
    skip = {'pnl','won','market_idx','ask','cd'}
    keys = [k for k in fires[0] if k not in skip and fires[0][k] is not None]
    
    results = []
    for key in keys:
        valid = [(f[key], f['pnl']) for f in fires if f.get(key) is not None]
        if len(valid) < min_n: continue
        vals = sorted(set(v for v,_ in valid))
        if len(vals) < 3: continue
        
        for pct in [15,25,35,50,65,75,85]:
            idx = int(len(vals)*pct/100)
            if idx >= len(vals): continue
            thr = vals[idx]
            
            for op in ['>','<']:
                sub = [p for v,p in valid if (v > thr if op == '>' else v < thr)]
                if len(sub) < min_n: continue
                n = len(sub); mu = sum(sub)/n
                if mu <= 0: continue
                wins = sum(1 for p in sub if p > 0)
                sd = math.sqrt(sum((x-mu)**2 for x in sub)/(n-1)) if n > 1 else 1
                se = sd/math.sqrt(n)
                sharpe = mu/sd if sd > 0 else 0
                
                results.append({
                    'filter': f"{key}{op}{thr:.4f}", 'n': n, 'mu': mu,
                    'wr': wins/n, 'total': sum(sub), 'sharpe': sharpe,
                    'ci_lo': mu-1.96*se, 'ci_hi': mu+1.96*se,
                    'fires': sub,
                })
    
    results.sort(key=lambda r: -r['sharpe'] * math.sqrt(r['n']))
    return results[:30]

def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    markets = load(fp)
    n = len(markets)
    split = int(n * 0.68)
    train = markets[:split]
    test = markets[split:]
    
    print(f"Loaded {n} markets. Train={len(train)}, Test={len(test)}")
    dn_rate = sum(1 for m in markets if m['winner']=='DN')/n
    print(f"DN base rate: {dn_rate:.1%}\n")
    
    for zone_lo, zone_hi, zone_name in ZONES:
        print(f"\n{'='*120}")
        print(f"  DN ZONE: {zone_name} (${zone_lo:.2f}-${zone_hi:.2f})")
        print(f"{'='*120}")
        
        train_fires = compute_fires(train, zone_lo, zone_hi)
        test_fires = compute_fires(test, zone_lo, zone_hi)
        
        if len(train_fires) < 15:
            print(f"  Skip: {len(train_fires)} train fires")
            continue
        
        blind_mu = sum(f['pnl'] for f in train_fires)/len(train_fires)
        test_blind = sum(f['pnl'] for f in test_fires)/len(test_fires) if test_fires else 0
        print(f"  Train: {len(train_fires)} fires, blind μ=${blind_mu:+.3f}")
        print(f"  Test:  {len(test_fires)} fires, blind μ=${test_blind:+.3f}")
        
        results = scan(train_fires, min_n=max(10, len(train_fires)//5))
        
        if not results:
            print(f"  No positive DN-specific filters found")
            continue
        
        print(f"\n  {'Filter':<55}{'n':<6}{'WR':<7}{'μ':<9}{'Sharpe':<8}{'CI':<22}{'OOS'}")
        print(f"  {'─'*115}")
        
        for r in results[:10]:
            # OOS validation
            key_op = r['filter']
            for op_c in ['>','<']:
                if op_c in key_op:
                    parts = key_op.split(op_c, 1)
                    key = parts[0]; thr = float(parts[1])
                    break
            
            if test_fires:
                if op_c == '>':
                    oos = [f['pnl'] for f in test_fires if f.get(key) is not None and f[key] > thr]
                else:
                    oos = [f['pnl'] for f in test_fires if f.get(key) is not None and f[key] < thr]
                if oos:
                    oos_mu = sum(oos)/len(oos)
                    oos_str = f"n={len(oos)} μ=${oos_mu:+.2f} {'✅' if oos_mu > 0 else '❌'}"
                else:
                    oos_str = "no fires"
            else:
                oos_str = "no test"
            
            print(f"  {r['filter']:<55}{r['n']:<6}{r['wr']:.0%}  "
                  f"${r['mu']:<+8.3f}{r['sharpe']:<+7.3f} "
                  f"[${r['ci_lo']:+.2f},${r['ci_hi']:+.2f}]  {oos_str}")
    
    print(f"\n\n{'='*120}")
    print("DN-SPECIFIC FEATURES TESTED:")
    print(f"{'='*120}")
    print("""
  BTC DROP DYNAMICS:
    bn_drop_accel_N      — is BTC falling FASTER? (negative = accelerating drop)
    bn_failed_recovery_N — how many bounce-then-drop patterns?
    bn_consec_neg_N      — longest consecutive BN<0 run / total
    bn_min_in_window_N   — deepest BTC drop in window
    bn_drop_persist_N    — longest unbroken negative BN streak
    
  CROWD PANIC:
    cv_velocity_N        — how fast is conviction changing?
    cv_reversal_N        — was crowd UP-leaning, now shifting DN?
    cv_accel_N           — is conviction change accelerating?
    spread_blowout_N     — are spreads widening? (uncertainty)
    
  REVERSAL PATTERNS:
    up_peak_decay_N      — how far has UP fallen from its peak?
    up_momentum_dying_N  — is UP's rise slowing down?
    up_ask_falling_N     — is UP ask dropping over the window?
    
  DEPTH ASYMMETRY:
    dn_depth_build_N     — is DN depth growing?
    depth_vel_diverge_N  — is DN depth growing FASTER than UP?
    dn_depth_stability_N — is DN depth steady (not oscillating)?
    zc_trajectory_N      — are zero-counts decreasing? (stability returning)
    dn_sr_growing_N      — is DN strength ratio increasing?
    depth_ratio_trend_N  — is DN/UP depth ratio growing?
    
  MICROSTRUCTURE:
    ask_sum_deviation_N  — how far from normal 1.02?
    desync_trend_N       — data staleness volatility
    
  If any of these show OOS ✅, they capture DN-specific patterns
  that the generic temporal scanner missed. Feed survivors into
  the combo scanner to find multi-factor DN strategies.
""")

if __name__ == "__main__":
    main()
