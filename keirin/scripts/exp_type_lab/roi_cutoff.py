#!/usr/bin/env python3
"""「信頼度の低いレースを足切りして1日のROIを上げられるか」（2026-09-04・ユーザー依頼）。

## 先に本番を読む（CLAUDE.md）

足切りは**既に一部入っている**: `backend/src/services/keirin_type_lab_gate.py` の
`AXIS_GATE_MIN` ＝ 各プランの中で軸信頼（上位2車の3着内率の和）が
**探索窓のプラン内 p20 未満**なら売らない。2026-09-03 に効くプランだけへ絞られ、
現在掛かっているのは **A_hit / D_hit / E_hit / F_hit の4つだけ**。

## ここで測ること

腕（すべて同じ買い目・売るか売らないかだけが違う）:
  0 ゲート無し / 1 現行 / 2 全プランp20 / 3 全プランp40 / 4 現行4プランをp40
  5〜 別の信頼度量（Σp=買い目の合計確率 / 平均想定払戻 / pw_ent / rp_sd）で同率を落とす

🔴 **件数を減らす腕には必ず無作為対照20本**（CLAUDE.md・`race_filter` の否定と同型）。
🔴 ROI は ±2.5pt に約15.6年かかる層なので、**レース単位ブートストラップのCI**と
   **日次ROIの分布**（負ける日の割合）も一緒に出す。
🔴 確認窓(2026)が本番相当（予測オッズの train_end 2025-12-31）。
"""
from __future__ import annotations
import pickle, sys
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from typef_racetype import (ctx, _plan_for, AXIS_GATE_MIN, MIN_MEAN_PAYOUT, MIN_POINT_ODDS)
from src.type_lab import PLANS, allocate, build_legs, mean_expected_payout, SIGNBOARD_RACE_TYPES

CACHE = Path("/tmp/roi_cutoff_rows.pkl")


def run_named(x, key: str):
    plan = PLANS[key]
    pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    if mean <= MIN_MEAN_PAYOUT or min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    if plan.bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st), mean=mean,
                sump=float(sum(prb.get(c, 0.0) for c in st)))


def build() -> list[dict]:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = z["AXIS_SUM"].astype(float)
    RP = z["A_race_point"].astype(float)
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 4000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            trio_ok = (run_named(x, "A_trio") is not None) if tp[i] == "A" else False
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok, sign_rt)
            r = run_named(x, key)
            if not r:
                continue
            r.update(win=win, plan=key, axis=float(axs[i]), pw_ent=float(x.shape.pw_ent),
                     rp_sd=float(RP[i].std()), rtype=rt[i], type=tp[i])
            rows.append(r)
    return rows


if CACHE.exists():
    ROWS = pickle.load(CACHE.open("rb"))
else:
    ROWS = build()
    pickle.dump(ROWS, CACHE.open("wb"))
print(f"組めたレース {len(ROWS):,}  探索 {sum(r['win']=='explore' for r in ROWS):,} / "
      f"確認 {sum(r['win']=='confirm' for r in ROWS):,}")


# ═══════════════════════════ 評価 ═══════════════════════════
from collections import defaultdict            # noqa: E402
from statistics import median                  # noqa: E402

EX = [r for r in ROWS if r["win"] == "explore"]
CF = [r for r in ROWS if r["win"] == "confirm"]


def pct_thresholds(rows, field, q):
    """探索窓のプラン内 q パーセンタイル（本番のゲートと同じ作り方）。"""
    by = defaultdict(list)
    for r in EX:
        by[r["plan"]].append(r[field])
    return {p: float(np.percentile(v, q)) for p, v in by.items()}


def stats(rows, ndays):
    if not rows:
        return None
    inv = sum(r["inv"] for r in rows); pay = sum(r["pay"] for r in rows)
    hits = [r for r in rows if r["pay"] > 0]
    shown = [r for r in hits if r["pay"] >= r["inv"]]
    byday = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        byday[r["date"]][0] += r["inv"]; byday[r["date"]][1] += r["pay"]
    droi = sorted(p / i * 100 for i, p in byday.values() if i > 0)
    return dict(n=len(rows), perday=len(rows) / ndays, shown=len(shown) / len(rows) * 100,
                roi=pay / inv * 100, inv_day=inv / ndays, net_day=(pay - inv) / ndays,
                big=sum(1 for r in hits if r["pay"] >= 100_000) / ndays,
                droi_med=median(droi) if droi else 0.0,
                lose=sum(1 for x in droi if x < 100) / len(droi) * 100 if droi else 0.0,
                lose50=sum(1 for x in droi if x < 50) / len(droi) * 100 if droi else 0.0)


def boot_day(base, arm, nb=1500, seed=11):
    """日を再標本化して ΔROI（arm − base）の95%CI。日次の話なので日で resample する。"""
    days = sorted({r["date"] for r in base} | {r["date"] for r in arm})
    bidx = defaultdict(lambda: [0.0, 0.0]); aidx = defaultdict(lambda: [0.0, 0.0])
    for r in base:
        bidx[r["date"]][0] += r["inv"]; bidx[r["date"]][1] += r["pay"]
    for r in arm:
        aidx[r["date"]][0] += r["inv"]; aidx[r["date"]][1] += r["pay"]
    B = np.array([[bidx[d][0], bidx[d][1]] for d in days])
    A = np.array([[aidx[d][0], aidx[d][1]] for d in days])
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(nb):
        s = rng.integers(0, len(days), len(days))
        b, a = B[s].sum(0), A[s].sum(0)
        if b[0] > 0 and a[0] > 0:
            out.append(a[1] / a[0] * 100 - b[1] / b[0] * 100)
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def keep_axis(rows, thr, plans=None):
    return [r for r in rows if (plans is not None and r["plan"] not in plans)
            or r["axis"] >= thr.get(r["plan"], 0.0)]


def keep_field(rows, field, thr, low_is_bad=True):
    return [r for r in rows if (r[field] >= thr.get(r["plan"], -1e9) if low_is_bad
                                else r[field] <= thr.get(r["plan"], 1e9))]


CUR = set(AXIS_GATE_MIN)
ARMS = {}
ARMS["0 足切り無し"] = lambda rows: rows
ARMS["1 現行（軸信頼p20・4プラン）"] = lambda rows: keep_axis(rows, AXIS_GATE_MIN, CUR)
for q, lab in ((20, "p20=下位1/5"), (40, "p40=下位2/5")):
    t = pct_thresholds(EX, "axis", q)
    ARMS[f"2 軸信頼 全プラン {lab}"] = (lambda t=t: (lambda rows: keep_axis(rows, t)))()
t40 = pct_thresholds(EX, "axis", 40)
ARMS["3 現行4プランを p40 へ"] = lambda rows: keep_axis(rows, t40, CUR)
for q in (20, 40):
    t = pct_thresholds(EX, "sump", q)
    ARMS[f"4 Σp(的中確率) 全プラン p{q}"] = (lambda t=t: (lambda rows: keep_field(rows, "sump", t)))()
tS = pct_thresholds(EX, "sump", 80)
ARMS["5 Σp 上位1/5を落とす（逆向き）"] = lambda rows: keep_field(rows, "sump", tS, False)
tM = pct_thresholds(EX, "mean", 20)
ARMS["6 想定払戻 全プラン p20"] = lambda rows: keep_field(rows, "mean", tM)
tE = pct_thresholds(EX, "pw_ent", 80)
ARMS["7 pw_ent 上位1/5を落とす"] = lambda rows: keep_field(rows, "pw_ent", tE, False)
tR = pct_thresholds(EX, "rp_sd", 20)
ARMS["8 rp_sd 全プラン p20"] = lambda rows: keep_field(rows, "rp_sd", tR)

for wname, rows in (("確認 2026-01〜08（本番相当）", CF), ("探索 2024-07〜2025-12", EX)):
    nd = len({r["date"] for r in rows})
    base = ARMS["1 現行（軸信頼p20・4プラン）"](rows)
    print("\n" + "=" * 118)
    print(f"■ {wname}   全 {len(rows):,}商品 / {nd}日")
    print("=" * 118)
    print(f"  {'腕':30s} {'件/日':>6s} {'表示的中%':>9s} {'ROI%':>7s} {'ΔROI vs現行 95%CI':>21s} "
          f"{'投資/日':>9s} {'収支/日':>9s} {'日次ROI中央':>11s} {'負け日%':>7s} {'10万+/日':>8s}")
    for name, fn in ARMS.items():
        sub = fn(rows)
        s = stats(sub, nd)
        ci = boot_day(base, sub) if name != "1 現行（軸信頼p20・4プラン）" else None
        cis = f"[{ci[0]:+6.1f},{ci[1]:+6.1f}]" if ci else "(基準)"
        print(f"  {name:30s} {s['perday']:6.2f} {s['shown']:9.2f} {s['roi']:7.1f} {cis:>21s} "
              f"{s['inv_day']:9,.0f} {s['net_day']:+9,.0f} {s['droi_med']:11.1f} "
              f"{s['lose']:7.1f} {s['big']:8.3f}")

    # 無作為対照（同数を無作為に落とす）
    print(f"\n  ── 無作為対照（同じ件数を無作為に落とす・20seed）──")
    for name in list(ARMS)[1:]:
        sub = ARMS[name](rows)
        if len(sub) == len(rows):
            continue
        keep = len(sub)
        cr, cs = [], []
        for seed in range(20):
            rng = np.random.default_rng(seed)
            pick = rng.choice(len(rows), size=keep, replace=False)
            st = stats([rows[j] for j in pick], nd)
            cr.append(st["roi"]); cs.append(st["shown"])
        s = stats(sub, nd)
        print(f"    {name:30s} ROI 選別 {s['roi']:5.1f} ↔ 無作為中央 {np.median(cr):5.1f} "
              f"[{min(cr):5.1f},{max(cr):5.1f}] 勝ち {sum(s['roi'] > c for c in cr):2d}/20   "
              f"表示的中 {s['shown']:5.2f} ↔ {np.median(cs):5.2f} "
              f"勝ち {sum(s['shown'] > c for c in cs):2d}/20")
