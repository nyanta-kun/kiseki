#!/usr/bin/env python3
"""先週を新旧どちらのルールでも組み直して比べる（2026-09-23）。

🔴 **モデルは両腕で同じものを使う**（月次 vintage `*_m2609`＝学習は 2026-08-31 まで＝
   9月のレースは OOS）。本番モデルは 2026-09-21 に再学習されており、9/16〜20 を
   学習に含む＝当てると in-sample になるので使わない。
🔴 したがってこれは「あの朝の予測でどうだったか」ではなく
   **「同じ板に旧ルールと新ルールを当てたらどう違うか」**の比較。
   実売の実測（`sold_performance_report.py`）とは母集団も予測も一致しない。

採点は正本 `src/sold_performance.settle_submission`（同着・欠車返還つき）。
"""
from __future__ import annotations
import argparse, json, statistics, sys
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import lineup_arms as R                                              # noqa: E402
from lineup_sim import _G                                            # noqa: E402
import src.type_lab as TL                                            # noqa: E402
from src.type_lab import PLANS, MIN_PAYOUT_MULT                      # noqa: E402
from src.database import get_connection                              # noqa: E402
from src.entrants import valid_cars_by_race                          # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt              # noqa: E402
from src.sold_performance import settle_submission                   # noqa: E402
import scripts.build_type_lab_picks as B                             # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--start", default="2026-09-16")
ap.add_argument("--end", default="2026-09-22")
ap.add_argument("--eval-model", default="lgbm_wt_eval_m2609")
ap.add_argument("--win-model", default="lgbm_wt_win_m2609")
a = ap.parse_args()


class Ctx:
    pass


def load_day(day: str) -> list[Ctx]:
    from src import odds_prediction_tf as odds_tf
    keys = B._keys_of_date(day)
    if not keys:
        return []
    meta_all, ent_all = B._load_race_meta(keys), B._load_entries(keys)
    p3, pw = B.predict_p3_pw(day, a.eval_model, a.win_model)
    out = []
    for rk in keys:
        ent = ent_all.get(rk)
        if not ent or len(ent) != 7 or rk not in p3 or len(p3[rk]) != 7:
            continue
        if B._lineup_issue(ent):
            continue
        cars = {c: dict(p3=p3[rk][c], pw=(pw.get(rk) or {}).get(c), **ent[c])
                for c in ent if c in p3[rk]}
        try:
            board = odds_tf.predict_board(sorted(p3[rk]), p3[rk], pw[rk],
                                          {c: ent[c]["meta"] for c in ent})
        except Exception:                                            # noqa: BLE001
            continue
        m = meta_all.get(rk)
        if not m:
            continue
        shape = B.race_shape(
            {c: v["p3"] for c, v in cars.items()},
            {c: v["line_group"] for c, v in cars.items()},
            {c: v["line_pos"] for c, v in cars.items()},
            {c: v["style"] for c, v in cars.items()},
            {c: v["race_point"] for c, v in cars.items()},
            {c: v["behind"] for c, v in cars.items()},
            m.get("day_index") or 0,
            {c: v["pw"] for c, v in cars.items() if v.get("pw") is not None} or None)
        if shape is None:
            continue
        x = Ctx()
        x.shape = shape
        x.po_tf = {tuple(k): float(v) for k, v in board.items() if v and v > 0}
        lg = {c: v["line_group"] for c, v in cars.items()}
        lp = {c: v["line_pos"] for c, v in cars.items()}
        x.pr_tf = B._pl_board(p3[rk], pw[rk], lg, lp)
        x.ord_tf = B._pl_board_order(p3[rk], pw[rk], lg, lp)
        x.po_t3, x.pr_t3 = B._fold_to_trio(x.po_tf, x.pr_tf)
        x.date, x.key = day, rk
        x.rtype = m.get("race_type") or ""
        x.cupg = str(m.get("cup_grade") or "")
        rp = [v["race_point"] for v in cars.values() if v.get("race_point")]
        x.rp_sd = float(statistics.pstdev(rp)) if len(rp) >= 2 else None
        out.append(x)
    return out


# ── 旧ルール（2026-09-21 時点） ────────────────────────────────
OLD_PLANS = {
    "A_hit": replace(PLANS["A_hit"], structure="prob_top", alloc="conf", target=0,
                     max_odds=0.0, max_legs=3, floor_mult=MIN_PAYOUT_MULT),
    "B_hit": replace(PLANS["B_hit"], structure="prob_top", alloc="conf", target=0,
                     max_odds=0.0, max_legs=8, sigma_max=1 / 3.0,
                     floor_mult=MIN_PAYOUT_MULT),
    "C_hit": replace(PLANS["C_hit"], structure="prob_top", alloc="conf", target=0,
                     max_odds=0.0, min_odds=15.0, max_legs=12,
                     floor_mult=MIN_PAYOUT_MULT, underband_min=5.0, tau_adaptive=True),
}
OLD_GATE_MIN = {"A_hit": 1.596, "A_trio": 1.528, "B_hit": 1.539, "C_hit": 1.500,
                "D_hit": 1.304, "E_hit": 1.284, "F_hit": 1.246, "F_sign": 1.267}

days = []
d, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
while d <= end:
    days.append(d.isoformat()); d += timedelta(days=1)

import pickle
_cp = Path(f"/tmp/replay_ctx_{a.start}_{a.end}_{a.eval_model}.pkl")
if _cp.exists():
    cache = pickle.loads(_cp.read_bytes())
    print(f"キャッシュ読込 {_cp}（{len(cache)}R）", flush=True)
else:
    cache = {}
    for day in days:
        got = load_day(day)
        print(f"[{day}] 7車 {len(got)}R", flush=True)
        for i, x in enumerate(got):
            cache[f"{day}#{i}"] = x
    _cp.write_bytes(pickle.dumps(cache))
ok = list(cache)
keys = sorted({cache[i].key for i in ok})

finishes = defaultdict(list)
with get_connection() as c:
    for i in range(0, len(keys), 900):
        ch = keys[i:i + 900]
        q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
             "WHERE race_key IN (%s) AND finish_order BETWEEN 1 AND 3" % ",".join("?" * len(ch)))
        for r in c.execute(q, ch):
            d_ = dict(r)
            finishes[d_["race_key"]].append((int(d_["finish_order"]), int(d_["frame_no"])))
finishes = {k: sorted(v) for k, v in finishes.items()}
raw = _load_payouts_wt(keys)
valid = valid_cars_by_race(keys)

from src.sold_performance import winning_combo_labels
payouts = {}
for rk, rows in finishes.items():
    pm, m = raw.get(rk, {}), {}
    for label in winning_combo_labels(rows):
        got = (pm.get(("trio", frozenset(int(x) for x in label.split("="))))
               if "=" in label else
               pm.get(("trifecta", tuple(int(x) for x in label.split("-")))))
        if got:
            m[label] = int(got)
    payouts[rk] = m


def score(recs):
    """lineup_arms.run の出力を正本で採点し直す。"""
    out = []
    for r in recs:
        trio = r["trio"]
        lines = [{"bet_type": "3連複" if trio else "3連単",
                  "combo": ("=" if trio else "-").join(
                      str(x) for x in (sorted(c) if trio else c)),
                  "stake": int(v)} for c, v in r["stakes"].items()]
        s = settle_submission({"total": sum(r["stakes"].values()), "lines": lines},
                              finishes.get(r["race_key"]), payouts.get(r["race_key"]),
                              valid.get(r["race_key"]))
        if s is None or not s.settled:
            continue
        out.append(dict(day=r["day"], plan=r["plan"], bet=s.bet, pay=s.payout,
                        hit=s.hit, net=s.net_hit))
    return out


def summarize(rows, nd):
    n = len(rows)
    bet = sum(r["bet"] for r in rows); pay = sum(r["pay"] for r in rows)
    p = sorted(r["pay"] for r in rows if r["net"])
    return dict(n=n, perday=n / nd, net=sum(r["net"] for r in rows) / n,
                roi=pay / bet if bet else 0,
                med=statistics.median(p) if p else 0, mx=max(p) if p else 0,
                mean=statistics.mean(p) if p else 0,
                b3=sum(1 for v in p if v >= 30_000) / nd,
                b4=sum(1 for v in p if v >= 40_000) / nd,
                b10=sum(1 for v in p if v >= 100_000) / nd,
                bet_day=bet / nd)


# 🔴 板の採点（`lineup_sim.settle`）は使わない。ここでは正本
#    （`sold_performance.settle_submission`・同着と欠車返還つき）で採点し直すので、
#    `run` の中の採点は無効化して買い目だけ受け取る。
R.settle = lambda x, st, trio: (float(sum(st.values())), 0.0)


def run(label, plans, gate_min, big_slots):
    _G.AXIS_GATE_MIN = dict(gate_min)
    TL.HIGHPAY_BIG_SLOTS = big_slots
    recs = R.run(label, plans, ok, cache)
    _G.AXIS_GATE_MIN = dict(NEW_GATE_MIN)
    TL.HIGHPAY_BIG_SLOTS = NEW_BIG
    return score(recs)


NEW_GATE_MIN = dict(_G.AXIS_GATE_MIN)
NEW_BIG = TL.HIGHPAY_BIG_SLOTS
nd = len({cache[i].date for i in ok})

arms = [
    ("旧ルール（〜9/21）", OLD_PLANS, OLD_GATE_MIN, frozenset({2, 4})),
    ("新ルール（9/22〜）", {}, NEW_GATE_MIN, NEW_BIG),
    ("  └ 商品だけ新・ゲート旧", {}, OLD_GATE_MIN, NEW_BIG),
]
HEAD = (f"{'腕':<22}{'件/日':>7}{'表示的中':>9}{'ROI':>7}{'平均払戻':>9}{'中央払戻':>9}"
        f"{'最高払戻':>10}{'3万+/日':>8}{'4万+/日':>8}{'10万+/日':>9}")
print(f"\n=== 先週（{a.start}〜{a.end}・{nd}日・7車のみ）を同じ板で組み直す ===")
print(f"※ モデルは両腕とも {a.eval_model} / {a.win_model}（学習は 2026-08-31 まで＝9月は OOS）\n")
print(HEAD); print("-" * len(HEAD))
res = {}
for label, plans, gm, big in arms:
    rows = run(label, plans, gm, big)
    res[label] = rows
    s_ = summarize(rows, nd)
    print(f"{label:<22}{s_['perday']:>7.1f}{s_['net']:>9.1%}{s_['roi']:>7.1%}"
          f"{s_['mean']:>9,.0f}{s_['med']:>9,.0f}{s_['mx']:>10,.0f}"
          f"{s_['b3']:>8.2f}{s_['b4']:>8.2f}{s_['b10']:>9.3f}")

print("\n=== プラン別（旧 → 新） ===")
old_g, new_g = defaultdict(list), defaultdict(list)
for r in res["旧ルール（〜9/21）"]:
    old_g[r["plan"]].append(r)
for r in res["新ルール（9/22〜）"]:
    new_g[r["plan"]].append(r)
print(f"{'商品':<9}{'旧 件':>6}{'新 件':>6}{'旧 的中':>8}{'新 的中':>8}{'旧 ROI':>8}{'新 ROI':>8}"
      f"{'旧 中央':>9}{'新 中央':>9}")
for k in sorted(set(old_g) | set(new_g)):
    o, n_ = old_g.get(k, []), new_g.get(k, [])
    def f(rows, key):
        if not rows:
            return "—"
        s2 = summarize(rows, nd)
        return {"net": f"{s2['net']:.1%}", "roi": f"{s2['roi']:.1%}",
                "med": f"{s2['med']:,.0f}"}[key]
    print(f"{k:<9}{len(o):>6}{len(n_):>6}{f(o,'net'):>8}{f(n_,'net'):>8}"
          f"{f(o,'roi'):>8}{f(n_,'roi'):>8}{f(o,'med'):>9}{f(n_,'med'):>9}")


# ── 日ブートストラップ（この1週間だけの不確かさ） ──────────────
import random as _rnd
def _boot(A, Bv, n=3000):
    ga, gb = defaultdict(list), defaultdict(list)
    for r in A: ga[r["day"]].append(r)
    for r in Bv: gb[r["day"]].append(r)
    ds = sorted(set(ga) | set(gb)); rnd = _rnd.Random(5)
    acc = defaultdict(list)
    for _ in range(n):
        smp = [rnd.choice(ds) for _ in ds]
        xa = [r for d in smp for r in ga.get(d, ())]
        xb = [r for d in smp for r in gb.get(d, ())]
        if not xa or not xb: continue
        sa, sb = summarize(xa, len(ds)), summarize(xb, len(ds))
        for k in ("net", "roi"):
            acc[k].append((sb[k] - sa[k]) * 100)
        for k in ("b3", "b4", "perday"):
            acc[k].append(sb[k] - sa[k])
    def ci(v):
        v = sorted(v); return sum(v)/len(v), v[int(.025*len(v))], v[int(.975*len(v))]
    return {k: ci(v) for k, v in acc.items()}

print("\n=== 先週だけの差（新 − 旧・日ブートストラップ3,000本・7日なのでCIは広い） ===")
bb = _boot(res["旧ルール（〜9/21）"], res["新ルール（9/22〜）"])
for k, lab in (("perday","件/日"),("net","表示的中pt"),("roi","ROIpt"),
               ("b3","3万+/日"),("b4","4万+/日")):
    m, lo, hi = bb[k]
    print(f"  {lab:<12}{m:+8.2f}  95%CI [{lo:+.2f}, {hi:+.2f}]")
