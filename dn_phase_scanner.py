#!/usr/bin/env python3
"""
DN Phase Pattern Scanner
==========================
Tests 7 new DN indicators extracted from market 1777298400 analysis:
  1. dwell_time_30_50      — seconds DN in $0.30-$0.50 before crash
  2. drop_vel_50_30        — speed of $0.50→$0.30 drop
  3. bn_neg_pct_drift      — % BN<0 during drift phase
  4. depth_ratio_growth    — DN/UP depth ratio change during drop
  5. cv_drop_during_drift  — conviction change during drift
  6. up_no_reversal        — UP trend after DN drops below threshold
  7. sr_dominance          — DN SR vs UP SR during drop

Tests singles, then 2/3-way combos with OOS + walk-forward.

Run: python3 dn_phase_scanner.py market_history.jsonl > dn_phase.txt 2>&1
"""
import json, math, sys, itertools
from collections import defaultdict

CD,UP_BID,UP_ASK,DN_BID,DN_ASK=0,1,2,3,4
UP_DEPTH,DN_DEPTH,UP_ZC,DN_ZC,UP_SR,DN_SR=5,6,7,8,9,10
BN_PRICE,BN_DELTA=11,12; UP_EMA,DN_EMA=13,14
CROWD_CV=20

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

def compute_phase_features(ticks, entry_cd, entry_price):
    """Compute all 7 phase features at a specific entry point."""
    f = {'entry_cd': entry_cd, 'entry_price': entry_price}
    
    # Find ticks before entry point
    pre_ticks = [t for t in ticks if t[CD] >= entry_cd and t[CD] < entry_cd + 120]
    
    if len(pre_ticks) < 10:
        return None
    
    # ── 1. DWELL TIME in $0.30-$0.50 ──
    dwell_secs = 0
    in_drift = False
    drift_start_cd = None
    for t in pre_ticks:
        dn = t[DN_ASK] or 0
        if 0.30 <= dn < 0.50:
            if not in_drift:
                in_drift = True
                drift_start_cd = t[CD]
        elif dn < 0.30 and in_drift:
            dwell_secs = drift_start_cd - t[CD]
            break
    f['dwell_30_50'] = dwell_secs
    
    # ── 2. DROP VELOCITY $0.50 → $0.30 ──
    first_50 = None
    first_30 = None
    for t in pre_ticks:
        dn = t[DN_ASK] or 0
        if dn <= 0.50 and first_50 is None:
            first_50 = t[CD]
        if dn <= 0.30 and first_30 is None:
            first_30 = t[CD]
            break
    if first_50 is not None and first_30 is not None:
        dt = first_50 - first_30
        f['drop_vel'] = 0.20 / max(dt, 0.5)
    else:
        f['drop_vel'] = 0
    
    # ── 3. BN NEGATIVE % during drift ($0.30-$0.50) ──
    bn_drift = [t[BN_DELTA] or 0 for t in pre_ticks 
                if (t[DN_ASK] or 0) >= 0.30 and (t[DN_ASK] or 0) < 0.50]
    if bn_drift:
        f['bn_neg_pct'] = sum(1 for b in bn_drift if b < 0) / len(bn_drift)
        f['bn_avg_drift'] = sum(bn_drift) / len(bn_drift)
    else:
        f['bn_neg_pct'] = 0
        f['bn_avg_drift'] = 0
    
    # ── 4. DEPTH RATIO GROWTH during drop ──
    dr_data = []
    for t in pre_ticks:
        dn = t[DN_ASK] or 0
        if 0.20 <= dn < 0.40:
            dn_d = t[DN_DEPTH] or 0
            up_d = t[UP_DEPTH] or 0
            if up_d > 0:
                dr_data.append(dn_d / up_d)
    if len(dr_data) >= 4:
        third = max(len(dr_data) // 3, 1)
        dr_early = sum(dr_data[:third]) / third
        dr_late = sum(dr_data[-third:]) / third
        f['dr_growth'] = dr_late - dr_early
    else:
        f['dr_growth'] = 0
    
    # ── 5. CV DROP during drift ──
    cv_drift = [t[CROWD_CV] or 0 for t in pre_ticks 
                if (t[DN_ASK] or 0) >= 0.30 and (t[DN_ASK] or 0) < 0.50
                and t[CROWD_CV]]
    if len(cv_drift) >= 4:
        third = max(len(cv_drift) // 3, 1)
        cv_early = sum(cv_drift[:third]) / third
        cv_late = sum(cv_drift[-third:]) / third
        f['cv_drop'] = cv_early - cv_late  # positive = CV dropping
    else:
        f['cv_drop'] = 0
    
    # ── 6. UP NO REVERSAL after DN drops below $0.30 ──
    up_after = []
    for t in pre_ticks:
        dn = t[DN_ASK] or 0
        up = t[UP_ASK] or 0
        if dn < 0.30 and up > 0:
            up_after.append(up)
    if len(up_after) >= 3:
        f['up_trend_after'] = up_after[-1] - up_after[0]  # positive = UP rising (no DN recovery)
    else:
        f['up_trend_after'] = 0
    
    # ── 7. SR DOMINANCE during drop ──
    sr_diff_data = []
    for t in pre_ticks:
        dn = t[DN_ASK] or 0
        dn_sr = t[DN_SR] or 0
        up_sr = t[UP_SR] or 0
        if 0.20 <= dn < 0.40 and (dn_sr > 0 or up_sr > 0):
            sr_diff_data.append(dn_sr - up_sr)
    f['sr_dominance'] = sum(sr_diff_data) / max(len(sr_diff_data), 1) if sr_diff_data else 0
    
    # ── BONUS: Additional phase features ──
    
    # 8. Did DN touch $0.20-$0.30 zone at all?
    touched_deep = any((t[DN_ASK] or 0) < 0.30 for t in pre_ticks)
    f['touched_deep'] = 1 if touched_deep else 0
    
    # 9. Max DN depth seen during pre-entry window
    max_dn_depth = max((t[DN_DEPTH] or 0) for t in pre_ticks)
    f['max_dn_depth'] = max_dn_depth
    
    # 10. Price range (volatility) in pre-entry window
    dn_prices = [t[DN_ASK] or 0 for t in pre_ticks if t[DN_ASK]]
    f['price_range'] = max(dn_prices) - min(dn_prices) if dn_prices else 0
    
    # 11. Time since DN first dropped below $0.40
    first_below_40 = None
    for t in pre_ticks:
        if (t[DN_ASK] or 0) < 0.40 and first_below_40 is None:
            first_below_40 = t[CD]
    f['time_since_40'] = (first_below_40 - entry_cd) if first_below_40 else 0
    
    return f

def extract_fires(markets, zone_lo, zone_hi):
    """Extract DN fires at specific entry points."""
    fires = []
    for mi, m in enumerate(markets):
        ticks = m['ticks']
        winner = m['winner']
        
        # Find first tick where DN_ASK enters the zone
        for ti, t in enumerate(ticks):
            cd = t[CD]
            if cd < 60 or cd > 280: continue
            dn_ask = t[DN_ASK]
            if dn_ask is None or not (zone_lo <= dn_ask < zone_hi): continue
            
            won = (winner == 'DN')
            pnl = ((1.0 - dn_ask) if won else -dn_ask) * 5
            
            feats = compute_phase_features(ticks, cd, dn_ask)
            if feats is None: continue
            
            feats['pnl'] = pnl
            feats['won'] = won
            feats['market_idx'] = mi
            fires.append(feats)
            break
    return fires

def stats(pnls):
    n = len(pnls)
    if n == 0: return None
    mu = sum(pnls)/n
    wins = sum(1 for p in pnls if p > 0)
    if n > 1:
        sd = math.sqrt(sum((x-mu)**2 for x in pnls)/(n-1))
        se = sd/math.sqrt(n)
    else: sd = 0; se = 0
    avg_w = sum(p for p in pnls if p > 0)/max(wins,1)
    avg_l = abs(sum(p for p in pnls if p <= 0))/max(n-wins,1)
    b = avg_w/max(avg_l,0.01)
    p = wins/n
    kelly = max(0,(p*b-(1-p))/b) if b > 0 else 0
    sharpe = mu/sd if sd > 0 else 0
    return {'n':n,'mu':mu,'wr':wins/n,'sd':sd,'sharpe':sharpe,'kelly':kelly}

def scan_singles(fires, min_n=10):
    """Scan all features for single-threshold edge."""
    skip = {'pnl','won','market_idx','entry_cd','entry_price'}
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
                mu = sum(sub)/len(sub)
                if mu <= 0: continue
                s = stats(sub)
                if s is None: continue
                results.append({
                    'filter': f"{key}{op}{thr:.4f}", 'stats': s,
                    'key': key, 'op': op, 'thr': thr,
                })
    results.sort(key=lambda r: -r['stats']['sharpe'] * math.sqrt(r['stats']['n']))
    return results[:20]

def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    markets = load(fp)
    n = len(markets)
    split = int(n * 0.68)
    train = markets[:split]
    test = markets[split:]
    fold_size = n // 3
    folds = [(markets[:fold_size], markets[fold_size:2*fold_size]),
             (markets[fold_size:2*fold_size], markets[2*fold_size:]),
             (markets[:2*fold_size], markets[2*fold_size:])]
    
    print(f"Loaded {n} markets. Train={len(train)}, Test={len(test)}\n")
    
    ZONES = [
        (0.20, 0.30, "$0.20-0.30"),
        (0.25, 0.35, "$0.25-0.35"),
        (0.30, 0.40, "$0.30-0.40"),
        (0.35, 0.45, "$0.35-0.45"),
        (0.20, 0.40, "$0.20-0.40"),
        (0.25, 0.45, "$0.25-0.45"),
    ]
    
    for zlo, zhi, zname in ZONES:
        print(f"\n{'='*120}")
        print(f"  DN ENTRY ZONE: {zname}")
        print(f"{'='*120}")
        
        train_fires = extract_fires(train, zlo, zhi)
        test_fires = extract_fires(test, zlo, zhi)
        
        if len(train_fires) < 15:
            print(f"  Skip: {len(train_fires)} train fires")
            continue
        
        blind = stats([f['pnl'] for f in train_fires])
        test_blind = stats([f['pnl'] for f in test_fires]) if test_fires else None
        print(f"  Train: {len(train_fires)} fires, blind μ=${blind['mu']:+.3f}")
        if test_blind:
            print(f"  Test:  {len(test_fires)} fires, blind μ=${test_blind['mu']:+.3f}")
        
        singles = scan_singles(train_fires, min_n=max(8, len(train_fires)//5))
        
        if not singles:
            print(f"  No positive filters found")
            continue
        
        print(f"\n  {'Filter':<45}{'n':<6}{'WR':<7}{'μ':<9}{'Sharpe':<8}{'Kelly':<7}{'OOS'}")
        print(f"  {'─'*100}")
        
        for r in singles[:10]:
            s = r['stats']
            # OOS
            key, op, thr = r['key'], r['op'], r['thr']
            if test_fires:
                oos_sub = [f['pnl'] for f in test_fires 
                          if f.get(key) is not None and 
                          (f[key] > thr if op == '>' else f[key] < thr)]
                if oos_sub:
                    oos_mu = sum(oos_sub)/len(oos_sub)
                    oos_str = f"n={len(oos_sub)} μ=${oos_mu:+.2f} {'✅' if oos_mu > 0 else '❌'}"
                else:
                    oos_str = "no fires"
            else:
                oos_str = "no test"
            
            # Walk-forward
            wf_pos = 0
            for wt, wte in folds:
                wtr = extract_fires(wt, zlo, zhi)
                wtes = extract_fires(wte, zlo, zhi)
                ws = [f['pnl'] for f in wtes 
                     if f.get(key) is not None and 
                     (f[key] > thr if op == '>' else f[key] < thr)]
                if ws and sum(ws)/len(ws) > 0: wf_pos += 1
            
            wf_str = f"WF{'✅' if wf_pos==3 else '⚠️'}{wf_pos}"
            
            print(f"  {r['filter']:<45}{s['n']:<6}{s['wr']:.0%}  "
                  f"${s['mu']:<+8.3f}{s['sharpe']:<+7.3f} {s['kelly']:.0%}   "
                  f"{oos_str} {wf_str}")
        
        # Combo scan (top 8 singles, 2-way combos)
        if len(singles) >= 2:
            print(f"\n  Top 2-way combos:")
            print(f"  {'Combo':<70}{'n':<5}{'μ':<9}{'OOS'}")
            print(f"  {'─'*100}")
            
            shown = 0
            for i in range(min(8, len(singles))):
                for j in range(i+1, min(8, len(singles))):
                    ki, oi, ti = singles[i]['key'], singles[i]['op'], singles[i]['thr']
                    kj, oj, tj = singles[j]['key'], singles[j]['op'], singles[j]['thr']
                    
                    combo_sub = [f['pnl'] for f in train_fires
                                if f.get(ki) is not None and f.get(kj) is not None
                                and (f[ki] > ti if oi == '>' else f[ki] < ti)
                                and (f[kj] > tj if oj == '>' else f[kj] < tj)]
                    if len(combo_sub) < 6: continue
                    mu = sum(combo_sub)/len(combo_sub)
                    if mu <= 0: continue
                    
                    # OOS
                    oos_combo = [f['pnl'] for f in test_fires
                                if f.get(ki) is not None and f.get(kj) is not None
                                and (f[ki] > ti if oi == '>' else f[ki] < ti)
                                and (f[kj] > tj if oj == '>' else f[kj] < tj)]
                    if oos_combo:
                        oos_mu = sum(oos_combo)/len(oos_combo)
                        oos_str = f"n={len(oos_combo)} μ=${oos_mu:+.2f} {'✅' if oos_mu > 0 else '❌'}"
                    else:
                        oos_str = "no fires"
                    
                    label = f"{singles[i]['filter']} + {singles[j]['filter']}"
                    if oos_combo and oos_mu > 0:
                        print(f"  {label[:70]:<70}{len(combo_sub):<5}${mu:<+8.3f}{oos_str}")
                        shown += 1
                    if shown >= 8: break
                if shown >= 8: break
            if shown == 0:
                print(f"  No positive OOS combos")

if __name__ == "__main__":
    main()
