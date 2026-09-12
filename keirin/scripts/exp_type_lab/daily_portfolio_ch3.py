"""§3 型構成が日次のばらつきをどこまで決めるか / §4 看板・高額枠の役割。"""
import sys, pickle; sys.path.insert(0,'scripts/exp_type_lab'); 
import numpy as np
from collections import defaultdict, Counter
import daily_portfolio as D
from src.type_lab import HIGHPAY_SLOTS_PER_DAY, highpay_plan_for

HP = pickle.load(open("/tmp/daily_portfolio_hp.pkl", "rb"))

def plan_table(rows, nd):
    print(f"  {'プラン':10s} {'件/日':>6s} {'表示的中%':>9s} {'的中%':>7s} {'ROI%':>7s} "
          f"{'払戻中央':>9s} {'10万+/件%':>9s} {'10万+/日':>8s}")
    for p in sorted({r['plan'] for r in rows}):
        s = [r for r in rows if r['plan'] == p]
        hits = [r for r in s if r['pay'] > 0]
        shown = [r for r in s if r['pay'] > r['inv']]
        big = [r for r in s if r['pay'] >= 100_000]
        print(f"  {p:10s} {len(s)/nd:6.2f} {len(shown)/len(s)*100:9.2f} "
              f"{len(hits)/len(s)*100:7.2f} "
              f"{sum(r['pay'] for r in s)/sum(r['inv'] for r in s)*100:7.1f} "
              f"{np.median([r['pay'] for r in hits]) if hits else 0:9,.0f} "
              f"{len(big)/len(s)*100:9.2f} {len(big)/nd:8.3f}")

for nm, rows in (("探索 2024-07〜2025-12", D.EX), ("確認 2026-01〜08", D.CF)):
    sold = D.produce(rows)
    nd = len(set(r['date'] for r in sold))
    print("\n" + "=" * 100)
    print(f"■ {nm}  実際に出る商品（本番再現） {len(sold):,}件 / {nd}日")
    print("=" * 100)
    plan_table(sold, nd)

    # ── 開催日目 × 型 の構成 ──
    print("\n  開催日目ごとの型構成（%）と、その日目の実測表示的中")
    print(f"  {'日目':>4s} {'件':>7s} " + " ".join(f"{t:>6s}" for t in "ABCDEF")
          + f" {'表示的中%':>9s}")
    for di in range(1, 7):
        s = [r for r in sold if int(r['dayidx']) == di]
        if len(s) < 50:
            continue
        c = Counter(r['type'] for r in s)
        print(f"  {di:4d} {len(s):7,} "
              + " ".join(f"{c.get(t,0)/len(s)*100:6.1f}" for t in "ABCDEF")
              + f" {sum(1 for r in s if r['pay']>r['inv'])/len(s)*100:9.2f}")

    # ── 「その日の構成から予測される表示的中」の分布 ──
    base = {}
    for p in {r['plan'] for r in sold}:
        s = [r for r in sold if r['plan'] == p]
        base[p] = sum(1 for r in s if r['pay'] > r['inv']) / len(s)
    dd = defaultdict(lambda: [0, 0, 0.0])
    for r in sold:
        a = dd[r['date']]; a[0] += 1; a[1] += (r['pay'] > r['inv']); a[2] += base[r['plan']]
    exp = np.array([a[2] / a[0] * 100 for a in dd.values()])
    obs = np.array([a[1] / a[0] * 100 for a in dd.values()])
    print(f"\n  日次の構成期待値 E: 中央{np.median(exp):.2f}% "
          f"[p10 {np.percentile(exp,10):.2f}, p90 {np.percentile(exp,90):.2f}] SD {exp.std(ddof=1):.2f}pt")
    print(f"  日次の実測      O: 中央{np.median(obs):.2f}% "
          f"[p10 {np.percentile(obs,10):.2f}, p90 {np.percentile(obs,90):.2f}] SD {obs.std(ddof=1):.2f}pt")
    print(f"  → 構成で説明できる分散は全体の {exp.var(ddof=1)/obs.var(ddof=1)*100:.1f}%"
          f"（残りは1商品ごとの当たり外れ）")

    # ── §4 高額枠を足したときの日次KPI ──
    byday = defaultdict(list)
    ok = [r for r in rows if r['gate']]
    for r in ok:
        byday[(r['date'], r['wave'])].append(r)
    dropped = defaultdict(list)          # date -> 上限で捨てた行
    keep = set(id(r) for r in sold)
    for r in ok:
        if id(r) not in keep and not r['exempt']:
            dropped[r['date']].append(r)
    added = []
    for d, cand in dropped.items():
        cand = sorted(cand, key=lambda r: -r['prio'])
        n = 0
        for r in cand:
            if n >= HIGHPAY_SLOTS_PER_DAY:
                break
            want = highpay_plan_for(r['type'], 7, n)
            if not want:
                continue
            avail = HP.get(r['race_key']) or {}
            hp = avail.get(want) or next(iter(avail.values()), None)
            if hp is None:
                continue
            x = dict(hp); x.update(date=d, dayidx=r['dayidx'], venue=r['venue'],
                                   race_key=r['race_key'], cup=r.get('cup'),
                                   shown=1 if hp['pay'] > hp['inv'] else 0)
            added.append(x); n += 1
    print(f"\n  高額枠: {len(added):,}件 = {len(added)/nd:.2f}本/日（設計 {HIGHPAY_SLOTS_PER_DAY}本）")
    hits = [r for r in added if r['pay'] > 0]
    big = [r for r in added if r['pay'] >= 100_000]
    print(f"    表示的中 {sum(1 for r in added if r['pay']>r['inv'])/len(added)*100:.2f}% / "
          f"ROI {sum(r['pay'] for r in added)/sum(r['inv'] for r in added)*100:.1f}% / "
          f"払戻中央 {np.median([r['pay'] for r in hits]):,.0f}円 / "
          f"10万+ {len(big)/len(added)*100:.2f}%（{len(big)/nd:.3f}件/日）")
    both = sold + added
    print(D.HDR); print(D.line("  通常のみ", D.kpi(sold)))
    print(D.line("  通常＋高額枠（現行）", D.kpi(both)))
