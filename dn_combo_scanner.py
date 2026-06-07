#!/usr/bin/env python3
"""
DN Multi-Factor Combo Scanner
================================
Uses DN-specific temporal families from OOS survivors:
  F1: BN_DROP_PERSIST (BTC consistently dropping)
  F2: BN_DROP_DEPTH (how deep BTC dropped)
  F3: CV_REVERSAL (crowd shifting from UP confidence)
  F4: DN_DEPTH_BUILD (DN depth accumulating)
  F5: BN_DROP_ACCEL (BTC drop accelerating)

Run: python3 dn_combo_scanner.py market_history.jsonl > dn_combo.txt 2>&1
"""
import json, math, sys, itertools

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

def compute_dn_factors(ticks, ti, cd):
    t = ticks[ti]
    f = {}
    bn = t[BN_DELTA] or 0
    cv = t[CROWD_CV] or 0
    cl = t[CL_DELTA] if len(t) > CL_DELTA else 0
    dn_depth = t[DN_DEPTH] or 0
    
    f['ask'] = t[DN_ASK] or 0
    f['cd'] = cd
    f['cl'] = cl or 0
    
    for window in [30, 60, 120]:
        w = get_window(ticks, ti, cd, window)
        if len(w) < 5:
            for k in [f'bn_consec_neg_{window}', f'bn_min_{window}',
                      f'bn_drop_persist_{window}', f'cv_reversal_{window}',
                      f'cv_accel_{window}', f'dn_depth_build_{window}',
                      f'bn_drop_accel_{window}']:
                f[k] = None
            continue
        
        # F1: BN drop persistence
        bn_vals = [tk[BN_DELTA] or 0 for tk in w]
        max_neg = 0; cur_neg = 0
        for b in bn_vals:
            if b < 0: cur_neg += 1; max_neg = max(max_neg, cur_neg)
            else: cur_neg = 0
        f[f'bn_consec_neg_{window}'] = max_neg / max(len(bn_vals), 1)
        f[f'bn_drop_persist_{window}'] = max_neg
        
        # F2: BN drop depth
        f[f'bn_min_{window}'] = min(bn_vals) if bn_vals else 0
        
        # F3: CV reversal
        cvs = [tk[CROWD_CV] or 0 for tk in w if tk[CROWD_CV]]
        if len(cvs) >= 4:
            early = sum(cvs[-len(cvs)//2:]) / max(len(cvs)//2, 1)
            late = sum(cvs[:len(cvs)//2]) / max(len(cvs)//2, 1)
            f[f'cv_reversal_{window}'] = early - late
        else:
            f[f'cv_reversal_{window}'] = None
        
        if len(cvs) >= 3:
            third = len(cvs) // 3
            if third > 0:
                cv_e = sum(cvs[-third:]) / third
                cv_m = sum(cvs[third:2*third]) / max(third, 1)
                cv_l = sum(cvs[:third]) / third
                f[f'cv_accel_{window}'] = (cv_l - cv_m) - (cv_m - cv_e)
            else:
                f[f'cv_accel_{window}'] = None
        else:
            f[f'cv_accel_{window}'] = None
        
        # F4: DN depth building
        dn_depths = [tk[DN_DEPTH] or 0 for tk in w]
        if len(dn_depths) >= 3:
            f[f'dn_depth_build_{window}'] = dn_depths[0] - dn_depths[-1]
        else:
            f[f'dn_depth_build_{window}'] = None
        
        # F5: BN drop acceleration
        half = len(bn_vals) // 2
        if half > 0:
            first_half = sum(bn_vals[half:]) / max(len(bn_vals) - half, 1)
            second_half = sum(bn_vals[:half]) / max(half, 1)
            f[f'bn_drop_accel_{window}'] = second_half - first_half
        else:
            f[f'bn_drop_accel_{window}'] = None
    
    return f

# DN-specific families from OOS survivors
FAMILIES = {
    'F1_persist': [
        ('bn_neg_60>0.32', lambda f: f.get('bn_consec_neg_60') is not None and f['bn_consec_neg_60'] > 0.32),
        ('bn_neg_60>0.41', lambda f: f.get('bn_consec_neg_60') is not None and f['bn_consec_neg_60'] > 0.41),
        ('bn_neg_30>0.50', lambda f: f.get('bn_consec_neg_30') is not None and f['bn_consec_neg_30'] > 0.50),
        ('drop_persist_60>100', lambda f: f.get('bn_drop_persist_60') is not None and f['bn_drop_persist_60'] > 100),
        ('drop_persist_60>149', lambda f: f.get('bn_drop_persist_60') is not None and f['bn_drop_persist_60'] > 149),
    ],
    'F2_depth': [
        ('bn_min_30<-0.005', lambda f: f.get('bn_min_30') is not None and f['bn_min_30'] < -0.005),
        ('bn_min_60<-0.017', lambda f: f.get('bn_min_60') is not None and f['bn_min_60'] < -0.017),
        ('bn_min_120<-0.021', lambda f: f.get('bn_min_120') is not None and f['bn_min_120'] < -0.021),
        ('bn_min_30<-0.015', lambda f: f.get('bn_min_30') is not None and f['bn_min_30'] < -0.015),
    ],
    'F3_cv': [
        ('cv_rev_120<-0.07', lambda f: f.get('cv_reversal_120') is not None and f['cv_reversal_120'] < -0.07),
        ('cv_rev_120<-0.17', lambda f: f.get('cv_reversal_120') is not None and f['cv_reversal_120'] < -0.17),
        ('cv_rev_60<-0.05', lambda f: f.get('cv_reversal_60') is not None and f['cv_reversal_60'] < -0.05),
        ('cv_accel_120<0.04', lambda f: f.get('cv_accel_120') is not None and f['cv_accel_120'] < 0.04),
    ],
    'F4_dn_build': [
        ('dn_build_120>148', lambda f: f.get('dn_depth_build_120') is not None and f['dn_depth_build_120'] > 148),
        ('dn_build_120>284', lambda f: f.get('dn_depth_build_120') is not None and f['dn_depth_build_120'] > 284),
        ('dn_build_60>100', lambda f: f.get('dn_depth_build_60') is not None and f['dn_depth_build_60'] > 100),
        ('dn_build_30>50', lambda f: f.get('dn_depth_build_30') is not None and f['dn_depth_build_30'] > 50),
    ],
    'F5_accel': [
        ('drop_accel_120<-0.016', lambda f: f.get('bn_drop_accel_120') is not None and f['bn_drop_accel_120'] < -0.016),
        ('drop_accel_120<-0.009', lambda f: f.get('bn_drop_accel_120') is not None and f['bn_drop_accel_120'] < -0.009),
        ('drop_accel_60<-0.013', lambda f: f.get('bn_drop_accel_60') is not None and f['bn_drop_accel_60'] < -0.013),
        ('drop_accel_30<-0.011', lambda f: f.get('bn_drop_accel_30') is not None and f['bn_drop_accel_30'] < -0.011),
    ],
}

CL_GATES = [('cl_any', lambda f: True), ('cl>0', lambda f: f['cl'] > 0)]

def stats(pnls):
    n = len(pnls)
    if n == 0: return None
    mu = sum(pnls)/n
    wins = sum(1 for p in pnls if p > 0)
    if n > 1:
        sd = math.sqrt(sum((x-mu)**2 for x in pnls)/(n-1))
        se = sd/math.sqrt(n)
    else:
        sd = 0; se = 0
    avg_w = sum(p for p in pnls if p > 0)/max(wins,1)
    avg_l = abs(sum(p for p in pnls if p <= 0))/max(n-wins,1)
    b = avg_w/max(avg_l,0.01)
    p = wins/n
    kelly = max(0,(p*b-(1-p))/b) if b > 0 else 0
    sharpe = mu/sd if sd > 0 else 0
    return {'n':n,'mu':mu,'wr':wins/n,'total':sum(pnls),'sd':sd,
            'sharpe':sharpe,'kelly':kelly}

def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    markets = load(fp)
    n = len(markets)
    split = int(n * 0.68)
    train_mkts = markets[:split]
    test_mkts = markets[split:]
    fold_size = n // 3
    folds = [(markets[:fold_size], markets[fold_size:2*fold_size]),
             (markets[fold_size:2*fold_size], markets[2*fold_size:]),
             (markets[:2*fold_size], markets[2*fold_size:])]
    
    print(f"Loaded {n} markets. Train={len(train_mkts)}, Test={len(test_mkts)}\n")
    
    family_names = list(FAMILIES.keys())
    
    for zone_lo, zone_hi, zone_name in ZONES:
        print(f"\n{'='*130}")
        print(f"  DN ZONE: {zone_name} (${zone_lo:.2f}-${zone_hi:.2f})")
        print(f"{'='*130}")
        
        def extract(mkts):
            fires = []
            for mi, m in enumerate(mkts):
                ticks = m['ticks']
                for ti, t in enumerate(ticks):
                    cd = t[CD]
                    if cd < 60 or cd > 290: continue
                    ask = t[DN_ASK]
                    if ask is None or not (zone_lo <= ask < zone_hi): continue
                    won = (m['winner'] == 'DN')
                    pnl = ((1.0-ask) if won else -ask) * 5
                    factors = compute_dn_factors(ticks, ti, cd)
                    factors['pnl'] = pnl
                    factors['won'] = won
                    fires.append(factors)
                    break
            return fires
        
        train_fires = extract(train_mkts)
        test_fires = extract(test_mkts)
        
        if len(train_fires) < 15:
            print(f"  Skip: {len(train_fires)} train fires")
            continue
        
        blind_mu = sum(f['pnl'] for f in train_fires)/len(train_fires)
        test_blind = sum(f['pnl'] for f in test_fires)/len(test_fires) if test_fires else 0
        print(f"  Train: {len(train_fires)} fires, blind μ=${blind_mu:+.3f}")
        print(f"  Test:  {len(test_fires)} fires, blind μ=${test_blind:+.3f}")
        
        results = []
        for depth in [1, 2, 3, 4]:
            for fc in itertools.combinations(family_names, depth):
                vls = [FAMILIES[f] for f in fc]
                for tc in itertools.product(*vls):
                    for cn, cf in CL_GATES:
                        lp = [t[0] for t in tc]
                        if cn != 'cl_any': lp.append(cn)
                        label = " + ".join(lp)
                        fns = [t[1] for t in tc] + ([cf] if cn != 'cl_any' else [])
                        
                        tr_sub = [f['pnl'] for f in train_fires if all(fn(f) for fn in fns)]
                        if len(tr_sub) < 6: continue
                        s = stats(tr_sub)
                        if s is None or s['mu'] <= 0: continue
                        
                        te_sub = [f['pnl'] for f in test_fires if all(fn(f) for fn in fns)]
                        te_s = stats(te_sub) if len(te_sub) >= 3 else None
                        
                        wf_mus = []
                        for wt, wte in folds:
                            wtr = extract(wt); wtes = extract(wte)
                            ws = [f['pnl'] for f in wtes if all(fn(f) for fn in fns)]
                            if ws: wf_mus.append(sum(ws)/len(ws))
                        wf_all = all(m > 0 for m in wf_mus) if wf_mus else False
                        
                        results.append({
                            'label':label,'depth':depth,'train':s,
                            'test':te_s,'test_n':len(te_sub),
                            'wf_all':wf_all,'wf_n':len(wf_mus),
                        })
        
        if not results:
            print(f"  No positive DN combos")
            continue
        
        def score(r):
            om = r['test']['mu'] if r['test'] else 0
            on = r['test_n']
            wb = 2.0 if r['wf_all'] else 1.0
            return om * math.sqrt(max(on,1)) * wb
        
        results.sort(key=lambda r: -score(r))
        
        print(f"\n  {'Combo':<70}{'D':<3}{'TrN':<5}{'Trμ':<9}{'OOSn':<5}{'OOSμ':<9}{'WF':<5}{'Sh':<7}{'K':<5}")
        print(f"  {'─'*120}")
        
        shown = 0
        for r in results[:25]:
            ts = r['train']; oos = r['test']
            if oos and oos['mu'] > 0:
                wf = f"{'✅' if r['wf_all'] else '⚠️'}{r['wf_n']}"
                print(f"  {r['label']:<70}{r['depth']:<3}{ts['n']:<5}${ts['mu']:<+8.3f}"
                      f"{r['test_n']:<5}${oos['mu']:<+8.3f}{wf:<5}"
                      f"{ts['sharpe']:+.2f}  {ts['kelly']:.0%}")
                shown += 1
            if shown >= 12: break
        if shown == 0:
            print(f"  No combos with positive OOS μ")

if __name__ == "__main__":
    main()
