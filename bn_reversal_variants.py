#!/usr/bin/env python3
"""
BN Reversal Variant Scanner
==============================
H2b (BN reversal > 0.03) showed 88% WR in-sample, 89% OOS.
This scanner derives MULTIPLE strategies from that single signal by varying:

  AXIS 1: Entry timing (when do we detect the reversal?)
    - Early: cd=210 (detect after ~90s of data)
    - Mid:   cd=150 (detect after ~150s)
    - Late:  cd=90  (detect after ~210s, highest confidence)
    
  AXIS 2: Reversal threshold (how strong must the reversal be?)
    - Weak:   BN drop > 0.02
    - Medium: BN drop > 0.03
    - Strong: BN drop > 0.05
    - Extreme: BN drop > 0.08
    
  AXIS 3: Entry price zone (buy DN at what price?)
    - Deep:     DN ask $0.10-$0.25 (high payout, late entry)
    - Mid-low:  DN ask $0.25-$0.35
    - Mid:      DN ask $0.35-$0.50 (earlier entry, lower payout)
    - Any:      DN ask $0.10-$0.50 (take whatever's available)
    
  AXIS 4: Combo with partial signals from earlier scan
    - + depth_diverge > 100 (DN depth growing)
    - + lead_changes > 3 (some choppiness)
    - + cv < 0.65 (crowd not fully committed)
    - + dwell_3050 > 20% (some time spent in mid zone)

Total: ~120 variants tested with train/test OOS + walk-forward

Run: python3 bn_reversal_variants.py market_history.jsonl > bn_variants.txt 2>&1
"""
import json, math, sys

CD,UP_BID,UP_ASK,DN_BID,DN_ASK=0,1,2,3,4
UP_DEPTH,DN_DEPTH=5,6; UP_SR,DN_SR=9,10
BN_DELTA=12; CROWD_CV=20

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

def compute_features(ticks):
    """Compute BN reversal features at multiple time checkpoints."""
    f = {}
    n = len(ticks)
    if n < 50: return None
    
    dn_asks = [(t[CD], t[DN_ASK] or 0) for t in ticks if t[DN_ASK]]
    bns = [(t[CD], t[BN_DELTA] or 0) for t in ticks]
    cvs = [(t[CD], t[CROWD_CV] or 0) for t in ticks if t[CROWD_CV]]
    
    if not dn_asks or not bns: return None
    
    # BN at various checkpoints
    for target in [270, 240, 210, 180, 150, 120, 90, 60]:
        bn_at = [bn for cd, bn in bns if abs(cd - target) < 3]
        f[f'bn_{target}'] = bn_at[0] if bn_at else None
        dn_at = [dn for cd, dn in dn_asks if abs(cd - target) < 3]
        f[f'dn_{target}'] = dn_at[0] if dn_at else None
    
    # BN reversal at different split points
    for split_cd in [210, 180, 150, 120, 90]:
        bn_early = [bn for cd, bn in bns if cd > split_cd]
        bn_late = [bn for cd, bn in bns if 30 < cd <= split_cd]
        if bn_early and bn_late:
            early_mean = sum(bn_early) / len(bn_early)
            late_mean = sum(bn_late) / len(bn_late)
            f[f'bn_rev_{split_cd}'] = early_mean - late_mean
            f[f'bn_early_{split_cd}'] = early_mean
            f[f'bn_late_{split_cd}'] = late_mean
        else:
            f[f'bn_rev_{split_cd}'] = 0
            f[f'bn_early_{split_cd}'] = 0
            f[f'bn_late_{split_cd}'] = 0
    
    # BN velocity (rate of change)
    for window in [30, 60, 120]:
        for check_cd in [150, 120, 90]:
            bn_now = f.get(f'bn_{check_cd}')
            bn_before = f.get(f'bn_{check_cd + window}')
            if bn_now is not None and bn_before is not None:
                f[f'bn_vel_{window}_{check_cd}'] = (bn_now - bn_before) / max(window, 1)
            else:
                f[f'bn_vel_{window}_{check_cd}'] = None
    
    # BN was positive in early period then went negative
    for split_cd in [180, 150, 120]:
        early = f.get(f'bn_early_{split_cd}', 0)
        late = f.get(f'bn_late_{split_cd}', 0)
        f[f'bn_pos_to_neg_{split_cd}'] = 1 if early > 0.01 and late < -0.01 else 0
        f[f'bn_pos_early_{split_cd}'] = 1 if early > 0.01 else 0
    
    # Depth divergence during mid-period
    dn_depths = []
    up_depths = []
    for t in ticks:
        dn = t[DN_ASK] or 0
        if 0.30 <= dn <= 0.50:
            dn_depths.append(t[DN_DEPTH] or 0)
            up_depths.append(t[UP_DEPTH] or 0)
    if len(dn_depths) > 10:
        half = len(dn_depths) // 2
        f['depth_div'] = (sum(dn_depths[:half])/half - sum(dn_depths[half:])/max(len(dn_depths)-half,1)) - \
                         (sum(up_depths[:half])/half - sum(up_depths[half:])/max(len(up_depths)-half,1))
    else:
        f['depth_div'] = 0
    
    # Lead changes
    changes = 0; prev = None
    for cd, dn in dn_asks:
        leader = "DN" if dn < 0.48 else ("UP" if dn > 0.52 else prev)
        if leader and prev and leader != prev: changes += 1
        if leader: prev = leader
    f['lead_changes'] = changes
    
    # CV at detection point
    for target in [180, 150, 120, 90]:
        cv_at = [cv for cd, cv in cvs if abs(cd - target) < 5]
        f[f'cv_{target}'] = cv_at[0] if cv_at else 0
    
    # Dwell percentage
    dwell = sum(1 for cd, dn in dn_asks if 0.30 <= dn <= 0.50)
    f['dwell_pct'] = dwell / max(len(dn_asks), 1)
    
    # BN peak before reversal
    _bn_gt120 = [bn for cd, bn in bns if cd > 120]
    f['bn_peak'] = max(_bn_gt120) if _bn_gt120 else 0
    f['bn_peak_to_current'] = f['bn_peak'] - (f.get('bn_90') or 0)
    
    return f

def stats(pnls):
    n = len(pnls)
    if n == 0: return None
    mu = sum(pnls)/n
    wins = sum(1 for p in pnls if p > 0)
    sd = math.sqrt(sum((x-mu)**2 for x in pnls)/(n-1)) if n > 1 else 1
    se = sd/math.sqrt(n)
    kelly = 0
    if wins > 0 and wins < n:
        avg_w = sum(p for p in pnls if p > 0)/wins
        avg_l = abs(sum(p for p in pnls if p <= 0))/(n-wins)
        b = avg_w/max(avg_l,0.01)
        p = wins/n
        kelly = max(0,(p*b-(1-p))/b)
    return {'n':n,'mu':mu,'wr':wins/n,'total':sum(pnls),
            'ci_lo':mu-1.96*se,'ci_hi':mu+1.96*se,'kelly':kelly}

def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    markets = load(fp)
    n = len(markets)
    split = int(n * 0.68)
    
    # Walk-forward folds
    fs = n // 3
    folds = [(markets[:fs], markets[fs:2*fs]),
             (markets[fs:2*fs], markets[2*fs:]),
             (markets[:2*fs], markets[2*fs:])]
    
    print(f"Loaded {n} markets. Train={split} Test={n-split}")
    
    # Compute features
    all_data = []
    for m in markets:
        f = compute_features(m['ticks'])
        if f:
            f['winner'] = m['winner']
            all_data.append(f)
    
    train_d = all_data[:split]
    test_d = all_data[split:]
    print(f"Computed features for {len(all_data)} markets\n")
    
    # Define variants
    variants = []
    
    # Axis 1 × Axis 2: timing × threshold
    for split_cd, timing_label in [(210,'early'),(180,'mid-early'),(150,'mid'),(120,'mid-late'),(90,'late')]:
        for threshold, thr_label in [(0.02,'weak'),(0.03,'med'),(0.05,'strong'),(0.08,'extreme')]:
            rev_key = f'bn_rev_{split_cd}'
            
            # Axis 3: entry price zones
            for zone_lo, zone_hi, zone_label in [(0.10,0.50,'any'),(0.10,0.25,'deep'),
                                                   (0.25,0.35,'midlow'),(0.35,0.50,'mid')]:
                def make_filter(rk=rev_key, th=threshold, zlo=zone_lo, zhi=zone_hi, scd=split_cd):
                    def filt(p):
                        rev = p.get(rk, 0)
                        dn = p.get(f'dn_{scd}')
                        return rev > th and dn is not None and zlo <= dn < zhi
                    return filt
                
                name = f"rev_{timing_label}_{thr_label}_{zone_label}"
                variants.append((name, make_filter(), split_cd, 
                                f"BN_rev>{threshold} at cd={split_cd}, DN ${zone_lo}-${zone_hi}"))
            
            # Axis 4: combos with other signals
            for combo_name, combo_fn_maker in [
                ("depth", lambda rk=rev_key, th=threshold, scd=split_cd: 
                    lambda p: p.get(rk,0) > th and p.get('depth_div',0) > 100),
                ("leads", lambda rk=rev_key, th=threshold, scd=split_cd:
                    lambda p: p.get(rk,0) > th and p.get('lead_changes',0) > 3),
                ("lowcv", lambda rk=rev_key, th=threshold, scd=split_cd:
                    lambda p: p.get(rk,0) > th and p.get(f'cv_{scd}',1) < 0.65),
                ("dwell", lambda rk=rev_key, th=threshold, scd=split_cd:
                    lambda p: p.get(rk,0) > th and p.get('dwell_pct',0) > 0.20),
                ("postoneg", lambda rk=rev_key, th=threshold, scd=split_cd:
                    lambda p: p.get(rk,0) > th and p.get(f'bn_pos_to_neg_{scd}',0) == 1),
            ]:
                name = f"rev_{timing_label}_{thr_label}+{combo_name}"
                variants.append((name, combo_fn_maker(), split_cd,
                                f"BN_rev>{threshold} at cd={split_cd} + {combo_name}"))
    
    # Also test BN velocity variants
    for window in [60, 120]:
        for check_cd in [150, 120, 90]:
            for threshold in [-0.0003, -0.0005, -0.001]:
                vel_key = f'bn_vel_{window}_{check_cd}'
                def make_vel(vk=vel_key, th=threshold, ccd=check_cd):
                    def filt(p):
                        vel = p.get(vk)
                        dn = p.get(f'dn_{ccd}')
                        return vel is not None and vel < th and dn is not None and 0.10 <= dn < 0.50
                    return filt
                name = f"vel_{window}s_cd{check_cd}_<{threshold}"
                variants.append((name, make_vel(), check_cd,
                                f"BN velocity {window}s < {threshold} at cd={check_cd}"))
    
    # BN peak-to-current drop
    for peak_drop in [0.03, 0.05, 0.08, 0.10]:
        def make_peak(pd=peak_drop):
            return lambda p: p.get('bn_peak_to_current', 0) > pd
        name = f"peak_drop>{peak_drop}"
        variants.append((name, make_peak(), 120, f"BN peak drop > {peak_drop}"))
    
    print(f"Testing {len(variants)} variants...\n")
    
    # Test all variants
    results = []
    for name, filter_fn, entry_cd, desc in variants:
        train_match = [p for p in train_d if filter_fn(p)]
        test_match = [p for p in test_d if filter_fn(p)]
        
        def get_pnl(p, ecd):
            dn = p.get(f'dn_{ecd}')
            if dn is None or dn <= 0.03 or dn >= 0.95: return None
            won = p['winner'] == 'DN'
            return ((1.0 - dn) if won else -dn) * 5
        
        train_pnls = [pnl for pnl in (get_pnl(p, entry_cd) for p in train_match) if pnl is not None]
        test_pnls = [pnl for pnl in (get_pnl(p, entry_cd) for p in test_match) if pnl is not None]
        
        if len(train_pnls) < 5: continue
        
        tr = stats(train_pnls)
        te = stats(test_pnls) if len(test_pnls) >= 3 else None
        
        # Walk-forward
        wf_mus = []
        for wf_tr, wf_te in folds:
            wf_tr_d = [compute_features(m['ticks']) for m in wf_tr]
            wf_te_d = [compute_features(m['ticks']) for m in wf_te]
            wf_tr_d = [dict(**f, winner=m['winner']) for f, m in zip(wf_tr_d, wf_tr) if f]
            wf_te_d = [dict(**f, winner=m['winner']) for f, m in zip(wf_te_d, wf_te) if f]
            wf_match = [p for p in wf_te_d if filter_fn(p)]
            wf_pnls = [pnl for pnl in (get_pnl(p, entry_cd) for p in wf_match) if pnl is not None]
            if wf_pnls:
                wf_mus.append(sum(wf_pnls)/len(wf_pnls))
        wf_all = all(m > 0 for m in wf_mus) if wf_mus else False
        
        if tr['mu'] > 0:  # only keep positive-edge variants
            results.append({
                'name': name, 'desc': desc, 'train': tr, 'test': te,
                'test_n': len(test_pnls), 'wf_all': wf_all, 'wf_n': len(wf_mus),
            })
    
    # Sort by OOS performance
    def score(r):
        if r['test'] and r['test']['mu'] > 0:
            return r['test']['mu'] * math.sqrt(r['test_n']) * (2 if r['wf_all'] else 1)
        return -999
    results.sort(key=lambda r: -score(r))
    
    # Show top results
    print(f"{'='*130}")
    print(f"TOP VARIANTS (OOS positive, sorted by OOS score)")
    print(f"{'='*130}")
    print(f"  {'Name':<50}{'TrN':<5}{'TrWR':<6}{'Trμ':<9}{'OOSn':<5}{'OOSWR':<7}{'OOSμ':<9}{'WF':<5}{'K':<5}")
    print(f"  {'─'*105}")
    
    shown = 0
    for r in results:
        te = r['test']
        if te and te['mu'] > 0:
            wf = f"{'✅' if r['wf_all'] else '⚠️'}{r['wf_n']}"
            tr = r['train']
            print(f"  {r['name']:<50}{tr['n']:<5}{tr['wr']:.0%}  ${tr['mu']:<+8.2f}"
                  f"{r['test_n']:<5}{te['wr']:.0%}   ${te['mu']:<+8.2f}{wf:<5}{tr['kelly']:.0%}")
            shown += 1
            if shown >= 25: break
    
    if shown == 0:
        print("  No variants with positive OOS")
    
    print(f"\n  Total variants tested: {len(variants)}")
    print(f"  Variants with positive train: {len(results)}")
    print(f"  Variants with positive OOS: {shown}")

if __name__ == "__main__":
    main()
