#!/usr/bin/env python3
"""
DN Pattern Scanner — New Hypotheses from Market 1777298400
============================================================
Tests DN-specific patterns NOT covered by existing strategies:

  H1: CHOPPY DWELL — DN stays in $0.30-$0.50 for 30/60/90+ seconds
      (undecided market → late resolution often favors DN)
  
  H2: BN REVERSAL — BN was positive (BTC up) but turning negative
      (crowd bought UP on rising BTC, now BTC dropping back → DN wins)
  
  H3: DEPTH DIVERGENCE — DN depth growing + UP depth shrinking
      during dwell period (smart money accumulating DN quietly)
  
  H4: LATE PRICE COMPRESSION — DN still >$0.40 at cd=90 but then crashes
      (late decisive move after extended uncertainty)
  
  H5: HIGH OSCILLATION — many lead changes (>5) in first 120s
      (choppy Type C → test if DN wins more often in choppy markets)
  
  H6: CV STUCK — conviction stays 0.55-0.70 for 60+ seconds
      (crowd uncertain → late resolution, DN has structural edge?)
  
  H7: PRICE MEMORY — DN touched $0.25-$0.35 then bounced back to $0.40+
      then dropped again (double-dip pattern → confirmed DN direction)
  
  H8: BN-PRICE DIVERGENCE — BN positive but DN price still <$0.50
      (BTC rising but market doesn't follow → smart money knows something)

Run: python3 dn_pattern_scanner.py market_history.jsonl > dn_patterns.txt 2>&1
"""
import json, math, sys

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

def compute_patterns(ticks):
    """Compute all hypothesis features for a market."""
    f = {}
    n = len(ticks)
    if n < 50: return None
    
    # Get DN ask trajectory
    dn_asks = [(t[CD], t[DN_ASK] or 0) for t in ticks if t[DN_ASK]]
    up_asks = [(t[CD], t[UP_ASK] or 0) for t in ticks if t[UP_ASK]]
    bns = [(t[CD], t[BN_DELTA] or 0) for t in ticks]
    cvs = [(t[CD], t[CROWD_CV] or 0) for t in ticks if t[CROWD_CV]]
    
    if not dn_asks: return None
    
    # ═══ H1: CHOPPY DWELL — time spent in $0.30-$0.50 ═══
    for zone_lo, zone_hi, label in [(0.30,0.50,'3050'),(0.35,0.50,'3550'),(0.25,0.45,'2545')]:
        ticks_in_zone = sum(1 for cd, dn in dn_asks if zone_lo <= dn <= zone_hi)
        f[f'dwell_{label}_ticks'] = ticks_in_zone
        f[f'dwell_{label}_pct'] = ticks_in_zone / max(len(dn_asks), 1)
        
        # Consecutive dwell — longest unbroken stretch in zone
        max_consec = 0; cur = 0
        for cd, dn in dn_asks:
            if zone_lo <= dn <= zone_hi:
                cur += 1; max_consec = max(max_consec, cur)
            else:
                cur = 0
        f[f'dwell_{label}_max_consec'] = max_consec
    
    # Time-weighted: how many SECONDS in zone (not ticks)
    for zone_lo, zone_hi, label in [(0.30,0.50,'3050')]:
        first_cd = None; last_cd = None
        for cd, dn in dn_asks:
            if zone_lo <= dn <= zone_hi:
                if first_cd is None: first_cd = cd
                last_cd = cd
        if first_cd and last_cd:
            f[f'dwell_{label}_seconds'] = first_cd - last_cd
        else:
            f[f'dwell_{label}_seconds'] = 0
    
    # ═══ H2: BN REVERSAL — BN positive then turning negative ═══
    # BN at different time points
    for target_cd in [240, 200, 150, 120, 90, 60]:
        bn_at = [bn for cd, bn in bns if abs(cd - target_cd) < 5]
        f[f'bn_at_{target_cd}'] = bn_at[0] if bn_at else 0
    
    # BN was positive in first half, negative in second half?
    bn_first_half = [bn for cd, bn in bns if cd > 150]
    bn_second_half = [bn for cd, bn in bns if 30 < cd <= 150]
    if bn_first_half and bn_second_half:
        f['bn_first_half_mean'] = sum(bn_first_half) / len(bn_first_half)
        f['bn_second_half_mean'] = sum(bn_second_half) / len(bn_second_half)
        f['bn_reversal'] = f['bn_first_half_mean'] - f['bn_second_half_mean']
        # Was BN positive early then negative late?
        f['bn_pos_to_neg'] = 1 if f['bn_first_half_mean'] > 0.01 and f['bn_second_half_mean'] < -0.01 else 0
    else:
        f['bn_first_half_mean'] = 0; f['bn_second_half_mean'] = 0
        f['bn_reversal'] = 0; f['bn_pos_to_neg'] = 0
    
    # ═══ H3: DEPTH DIVERGENCE during dwell ═══
    # DN depth growing + UP depth shrinking while DN in $0.30-$0.50
    dn_depths_in_zone = []
    up_depths_in_zone = []
    for t in ticks:
        dn = t[DN_ASK] or 0
        if 0.30 <= dn <= 0.50:
            dn_depths_in_zone.append(t[DN_DEPTH] or 0)
            up_depths_in_zone.append(t[UP_DEPTH] or 0)
    
    if len(dn_depths_in_zone) > 10:
        half = len(dn_depths_in_zone) // 2
        dn_d_early = sum(dn_depths_in_zone[-half:]) / half
        dn_d_late = sum(dn_depths_in_zone[:half]) / half
        up_d_early = sum(up_depths_in_zone[-half:]) / half
        up_d_late = sum(up_depths_in_zone[:half]) / half
        f['depth_diverge_dn'] = dn_d_late - dn_d_early  # positive = DN growing
        f['depth_diverge_up'] = up_d_late - up_d_early  # negative = UP shrinking
        f['depth_diverge_net'] = f['depth_diverge_dn'] - f['depth_diverge_up']
    else:
        f['depth_diverge_dn'] = 0; f['depth_diverge_up'] = 0; f['depth_diverge_net'] = 0
    
    # ═══ H4: LATE PRICE — DN price at various late checkpoints ═══
    for target_cd in [120, 90, 60, 45, 30]:
        dn_at = [dn for cd, dn in dn_asks if abs(cd - target_cd) < 5]
        f[f'dn_at_{target_cd}'] = dn_at[0] if dn_at else 0
    
    # Was DN still >$0.40 at cd=90?
    f['dn_late_high'] = 1 if f.get('dn_at_90', 0) > 0.40 else 0
    f['dn_late_mid'] = 1 if 0.30 <= f.get('dn_at_90', 0) <= 0.50 else 0
    
    # ═══ H5: HIGH OSCILLATION — lead changes in first 120s ═══
    lead_changes_120 = 0
    lead_changes_all = 0
    prev_leader = None
    for cd, dn in dn_asks:
        leader = "DN" if dn < 0.48 else ("UP" if dn > 0.52 else prev_leader)
        if leader and prev_leader and leader != prev_leader:
            lead_changes_all += 1
            if cd > 180:  # first 120s = cd > 180
                lead_changes_120 += 1
        if leader: prev_leader = leader
    f['lead_changes_120'] = lead_changes_120
    f['lead_changes_all'] = lead_changes_all
    
    # ═══ H6: CV STUCK — conviction in narrow range for extended period ═══
    cv_in_range = sum(1 for cd, cv in cvs if 0.55 <= cv <= 0.70)
    f['cv_stuck_pct'] = cv_in_range / max(len(cvs), 1)
    # CV std over market
    if cvs:
        cv_mean = sum(cv for _, cv in cvs) / len(cvs)
        cv_std = math.sqrt(sum((cv - cv_mean)**2 for _, cv in cvs) / max(len(cvs)-1, 1))
        f['cv_std'] = cv_std
        f['cv_mean'] = cv_mean
    else:
        f['cv_std'] = 0; f['cv_mean'] = 0
    
    # ═══ H7: PRICE MEMORY — double-dip pattern ═══
    # DN touched <$0.35, bounced to >$0.45, then dropped again <$0.35
    touched_low = False
    bounced = False
    double_dip = False
    for cd, dn in dn_asks:
        if not touched_low and dn < 0.35:
            touched_low = True
        elif touched_low and not bounced and dn > 0.45:
            bounced = True
        elif bounced and not double_dip and dn < 0.35:
            double_dip = True
    f['double_dip'] = 1 if double_dip else 0
    
    # ═══ H8: BN-PRICE DIVERGENCE ═══
    # BN positive but DN price <$0.50 (BTC up but market not following)
    bn_pos_dn_cheap = 0
    bn_pos_dn_cheap_n = 0
    for t in ticks:
        bn = t[BN_DELTA] or 0
        dn = t[DN_ASK] or 0
        cd = t[CD]
        if cd < 60: continue  # skip last minute
        if bn > 0.02 and dn < 0.45:
            bn_pos_dn_cheap += 1
        if bn > 0.02:
            bn_pos_dn_cheap_n += 1
    f['bn_pos_dn_cheap_pct'] = bn_pos_dn_cheap / max(bn_pos_dn_cheap_n, 1)
    f['bn_pos_dn_cheap_ticks'] = bn_pos_dn_cheap
    
    # ═══ ENTRY POINTS — where would we buy DN? ═══
    # Find DN ask at various checkpoints for PnL calculation
    for target_cd in [270, 240, 210, 180, 150, 120, 90]:
        dn_at = [dn for cd, dn in dn_asks if abs(cd - target_cd) < 3]
        f[f'entry_dn_{target_cd}'] = dn_at[0] if dn_at else None
    
    return f


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
    return {'n': n, 'mu': mu, 'wr': wins/n, 'total': sum(pnls),
            'ci_lo': mu-1.96*se, 'ci_hi': mu+1.96*se}


def main():
    fp = sys.argv[1] if len(sys.argv) > 1 else "market_history.jsonl"
    print(f"Loading {fp}...")
    markets = load(fp)
    n = len(markets)
    split = int(n * 0.68)
    train = markets[:split]
    test = markets[split:]
    print(f"Loaded {n} markets. Train={len(train)} Test={len(test)}")
    
    dn_wins = sum(1 for m in markets if m['winner'] == 'DN')
    print(f"DN base rate: {dn_wins}/{n} = {dn_wins/n:.1%}\n")
    
    # Compute patterns for all markets
    all_patterns = []
    for m in markets:
        p = compute_patterns(m['ticks'])
        if p:
            p['winner'] = m['winner']
            p['slug'] = m.get('slug', '')
            all_patterns.append(p)
    
    print(f"Computed patterns for {len(all_patterns)} markets\n")
    
    # Test each hypothesis
    hypotheses = [
        # (name, filter_fn, entry_cd, description)
        ("H1a: dwell_3050 > 30% of ticks", 
         lambda p: p['dwell_3050_pct'] > 0.30, 180,
         "DN spent >30% of market in $0.30-$0.50"),
        ("H1b: dwell_3050 > 50% of ticks",
         lambda p: p['dwell_3050_pct'] > 0.50, 180,
         "DN spent >50% of market in $0.30-$0.50"),
        ("H1c: dwell_3050_seconds > 60s",
         lambda p: p['dwell_3050_seconds'] > 60, 180,
         "DN was in $0.30-$0.50 for >60 consecutive seconds"),
        ("H1d: dwell_3050_seconds > 120s",
         lambda p: p['dwell_3050_seconds'] > 120, 150,
         "DN was in $0.30-$0.50 for >120 seconds"),
        
        ("H2a: BN reversal (pos early, neg late)",
         lambda p: p['bn_pos_to_neg'] == 1, 120,
         "BN positive first half, negative second half"),
        ("H2b: BN reversal > 0.03",
         lambda p: p['bn_reversal'] > 0.03, 120,
         "BN dropped by >3bps from first to second half"),
        
        ("H3a: depth diverge net > 200",
         lambda p: p['depth_diverge_net'] > 200, 180,
         "DN depth growing + UP depth shrinking during dwell"),
        ("H3b: depth diverge net > 500",
         lambda p: p['depth_diverge_net'] > 500, 180,
         "Strong depth divergence during dwell"),
        
        ("H4a: DN still >$0.40 at cd=90",
         lambda p: p['dn_late_high'] == 1, 90,
         "DN was still expensive at cd=90 (late resolution)"),
        ("H4b: DN in $0.30-$0.50 at cd=90",
         lambda p: p['dn_late_mid'] == 1, 90,
         "DN was mid-price at cd=90 (undecided)"),
        
        ("H5a: lead changes > 5 in first 120s",
         lambda p: p['lead_changes_120'] > 5, 150,
         "Choppy market — many lead flips early"),
        ("H5b: lead changes > 10 total",
         lambda p: p['lead_changes_all'] > 10, 150,
         "Very choppy market"),
        
        ("H6a: CV stuck (0.55-0.70) > 40%",
         lambda p: p['cv_stuck_pct'] > 0.40, 150,
         "Crowd undecided for >40% of market"),
        ("H6b: CV std < 0.10",
         lambda p: p['cv_std'] < 0.10, 150,
         "Crowd conviction barely moved"),
        
        ("H7: double-dip pattern",
         lambda p: p['double_dip'] == 1, 150,
         "DN touched <$0.35, bounced >$0.45, dropped again <$0.35"),
        
        ("H8a: BN pos but DN cheap >30% of time",
         lambda p: p['bn_pos_dn_cheap_pct'] > 0.30, 150,
         "BTC rising but DN price stays low (divergence)"),
        ("H8b: BN pos but DN cheap >50% of time",
         lambda p: p['bn_pos_dn_cheap_pct'] > 0.50, 150,
         "Strong BN-price divergence"),
    ]
    
    # Also test COMBINATIONS
    combo_hypotheses = [
        ("C1: dwell>30% + depth_div>200",
         lambda p: p['dwell_3050_pct'] > 0.30 and p['depth_diverge_net'] > 200, 180,
         "Extended dwell + smart money accumulating"),
        ("C2: dwell>30% + BN reversal",
         lambda p: p['dwell_3050_pct'] > 0.30 and p['bn_reversal'] > 0.03, 150,
         "Extended dwell + BTC turning against crowd"),
        ("C3: dwell>30% + double_dip",
         lambda p: p['dwell_3050_pct'] > 0.30 and p['double_dip'] == 1, 150,
         "Extended dwell + price confirmed direction"),
        ("C4: lead_changes>5 + depth_div>200",
         lambda p: p['lead_changes_120'] > 5 and p['depth_diverge_net'] > 200, 150,
         "Choppy but depth favors DN"),
        ("C5: cv_stuck + dwell>30%",
         lambda p: p['cv_stuck_pct'] > 0.40 and p['dwell_3050_pct'] > 0.30, 150,
         "Crowd uncertain + price dwelling"),
        ("C6: BN_diverge + depth_div>200",
         lambda p: p['bn_pos_dn_cheap_pct'] > 0.30 and p['depth_diverge_net'] > 200, 150,
         "BTC up but DN depth growing"),
        ("C7: dwell>50% + cv_stuck + depth_div>200",
         lambda p: p['dwell_3050_pct'] > 0.50 and p['cv_stuck_pct'] > 0.40 and p['depth_diverge_net'] > 200, 180,
         "Heavy dwell + uncertain crowd + depth signal"),
        ("C8: double_dip + BN reversal",
         lambda p: p['double_dip'] == 1 and p['bn_reversal'] > 0.03, 150,
         "Price confirmed + BTC turning"),
    ]
    
    all_hyp = hypotheses + combo_hypotheses
    
    # Split into train/test
    train_p = all_patterns[:split]
    test_p = all_patterns[split:]
    
    print(f"{'='*120}")
    print(f"{'Hypothesis':<55}{'Trn n':<7}{'Trn WR':<8}{'Trn μ':<9}{'OOS n':<7}{'OOS WR':<8}{'OOS μ':<9}{'Verdict'}")
    print(f"{'─'*120}")
    
    for name, filter_fn, entry_cd, desc in all_hyp:
        # Train
        train_match = [p for p in train_p if filter_fn(p)]
        test_match = [p for p in test_p if filter_fn(p)]
        
        # PnL: buy DN at the entry_cd checkpoint
        def get_pnl(p, ecd):
            entry_key = f'entry_dn_{ecd}'
            entry = p.get(entry_key)
            if entry is None or entry <= 0.03 or entry >= 0.95:
                return None
            won = p['winner'] == 'DN'
            return ((1.0 - entry) if won else -entry) * 5
        
        train_pnls = [pnl for pnl in (get_pnl(p, entry_cd) for p in train_match) if pnl is not None]
        test_pnls = [pnl for pnl in (get_pnl(p, entry_cd) for p in test_match) if pnl is not None]
        
        if not train_pnls:
            continue
        
        tr = stats(train_pnls)
        te = stats(test_pnls) if test_pnls else None
        
        verdict = ""
        if te and te['mu'] > 0 and te['n'] >= 3:
            verdict = "✅ OOS+"
        elif te and te['mu'] <= 0:
            verdict = "❌ OOS-"
        else:
            verdict = "⚠️ no OOS"
        
        oos_str = f"{te['n']:<7}{te['wr']:.0%}    ${te['mu']:<+8.3f}" if te else f"{'—':<7}{'—':<8}{'—':<9}"
        print(f"  {name:<53}{tr['n']:<7}{tr['wr']:.0%}    ${tr['mu']:<+8.3f}{oos_str}{verdict}")
    
    print(f"\n{'='*120}")
    print("INTERPRETATION:")
    print(f"{'='*120}")
    print("""
  ✅ OOS+ = positive out-of-sample → candidate for new strategy
  ❌ OOS- = negative out-of-sample → overfit, discard
  ⚠️ no OOS = insufficient test data → need more markets
  
  Look for hypotheses where:
    1. Train WR > 55% (above 52% base rate)
    2. Train μ > $0.30 (meaningful edge per fire)  
    3. OOS μ > $0 (holds out of sample)
    4. n > 15 (enough data to be meaningful)
  
  BEST candidates should then be tested with walk-forward
  validation (3-fold) before implementation.
  
  Entry price is at the hypothesis entry_cd checkpoint.
  Example: H1a fires at cd=180, buys DN at whatever price it is then.
""")

if __name__ == "__main__":
    main()
