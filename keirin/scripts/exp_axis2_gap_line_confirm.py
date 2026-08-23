#!/usr/bin/env python3
"""軸2差し替え規則を **2024-25 の独立窓** で確認する（2026-08-23）。

## 確認する規則（探索窓 2026 で見つけたもの・事前登録）

    次の3条件が**すべて**成り立つときだけ軸2を差し替える:
      ① 現行の二軸（◎○）が**別ライン**
      ② 代替車が**軸1と同一ライン**
      ③ `p3[軸2] − p3[代替車] < 0.114`（**探索窓で決めた値をそのまま使う**）
    代替車 = ◎○ を除いた中で `P(軸1と共に3着内)` が最大の車

探索窓（2026・3,426R）の結果: 該当 578件で 32.01% → 38.58%（**+6.57pt**）。

🔴 **閾値を確認窓で引き直さないこと。** 引き直すと確認ではなく2度目の探索になる。
🔴 ペアモデルは**確認窓に含まれない 2026 で学習**する（向きを逆にする）。
⚠️ `bad` は 2024-25 の DB に無いため **bad なし近似**で軸を作る。
   2026 実測で本番の軸との一致 95.8%・二軸的中 Δ−0.01pt（`exp_axis_prod_baseline`）。
⚠️ `wt_entries.pred_*` は過去分がどの vintage で入ったかの記録が無い。
   規則の確認（両腕に同じ値を使う）には使えるが、**絶対値の水準は割り引いて読む**。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis_prod_baseline import load_cache4  # noqa: E402
from scripts.exp_trio_joint_partner import fit  # noqa: E402
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from scripts.exp_trio_pair_model import load_entries  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import rank_7s_select_axis  # noqa: E402

GAP_SMALL = 0.114          # 🔴 探索窓（2026）の中央値。確認窓で引き直さない
AXIS_SUM_MAX = 1.40


def load_window(lo: str, hi: str):
    """DB から 7車レースの pw / p3 / 印 / ライン / 着順を引く。"""
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); con.set_session(readonly=True)
    cur = con.cursor()
    cur.execute("""
        select e.race_key, e.frame_no, e.pred_win_pct, e.pred_top3_pct,
               e.prediction_mark, e.line_group, r.race_date
        from keirin.wt_entries e join keirin.wt_races r using(race_key)
        where r.race_date between %s and %s and r.n_entries = 7
          and e.pred_win_pct is not null and e.pred_top3_pct is not null
        order by e.race_key, e.frame_no
    """, (lo, hi))
    races = defaultdict(lambda: dict(pw={}, p3={}, mark={}, lg={}))
    for rk, fn, pw, p3, mk, lg, d in cur.fetchall():
        r = races[rk]
        r["date"] = d
        r["pw"][int(fn)] = float(pw) / 100.0
        r["p3"][int(fn)] = float(p3) / 100.0
        r["mark"][int(fn)] = int(mk or 0)
        r["lg"][int(fn)] = lg
    con.close()
    return {k: v for k, v in races.items() if len(v["p3"]) == 7}


def ci_diff(days, B=4000, seed=131):
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", default="2024-01-01")
    ap.add_argument("--to", dest="hi", default="2025-12-31")
    ap.add_argument("--train", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    args = ap.parse_args()

    # ── ペアモデルは 2026（確認窓に含まれない）で学習 ──
    tr = load_cache4(args.train)
    fin_tr = _load_finishes([r["key"] for r in tr])
    tr_rows = [dict(key=r["key"], date=r["date"], p3=r["p3"],
                    order=sorted(r["p3"], key=lambda c: (-r["p3"][c], c)))
               for r in tr if r["key"] in fin_tr]
    Xtr, ytr, _ = build_pairs(tr_rows, load_entries([r["key"] for r in tr_rows]), fin_tr)
    pm = fit(Xtr, ytr, args.rounds)
    print(f"ペアモデル学習: 2026 の {len(tr_rows):,}R（確認窓に含まれない）")

    W = load_window(args.lo, args.hi)
    fin = _load_finishes(list(W))
    keys = [k for k in W if k in fin]
    rows_for_pairs = [dict(key=k, date=W[k]["date"], p3=W[k]["p3"],
                           order=sorted(W[k]["p3"], key=lambda c: (-W[k]["p3"][c], c)))
                      for k in keys]
    Xp, _, mp = build_pairs(rows_for_pairs, load_entries(keys), fin)
    pair = defaultdict(dict)
    for (key, _, a, b, _, _), p in zip(mp, pm.predict(Xp)):
        pair[key][frozenset((a, b))] = float(p)
    print(f"確認窓 {args.lo}〜{args.hi}: 7車 {len(keys):,}R\n")

    rows = []
    for k in keys:
        w = W[k]
        sel = rank_7s_select_axis(w["pw"], w["p3"], {c: 0.0 for c in w["p3"]})
        if sel is None:
            continue
        a1, a2, _ = sel
        hon = next((c for c, v in w["mark"].items() if v == 1), None)
        tai = next((c for c, v in w["mark"].items() if v == 2), None)
        if hon is None or tai is None:
            continue
        if a1 not in (hon, tai) or a2 not in (hon, tai):
            continue
        p3 = w["p3"]
        if p3[a1] + p3[a2] > AXIS_SUM_MAX:
            continue
        cand = [c for c in p3 if c not in (a1, hon, tai)]
        if not cand or k not in pair:
            continue
        rep = max(cand, key=lambda c: pair[k].get(frozenset((a1, c)), 0.0))
        lg = w["lg"]
        t3 = {c for x in winning_trifectas(fin[k]) for c in x}
        rows.append(dict(
            date=w["date"], y=int(a1 in t3 and a2 in t3),
            a1_in=int(a1 in t3), rep_in=int(rep in t3),
            gap=p3[a2] - p3[rep],
            sl_rep=int(lg[a1] is not None and lg[a1] == lg[rep]),
            sl_axes=int(lg[a1] is not None and lg[a1] == lg[a2])))
    print(f"母集団 {len(rows):,}R（二軸が◎○ ∧ axis_sum<={AXIS_SUM_MAX}）"
          f"  現行の二軸的中 {np.mean([r['y'] for r in rows]):.2%}")
    print(f"  代替が軸1と同ライン {np.mean([r['sl_rep'] for r in rows]):.1%}"
          f" / 現行二軸が同ライン {np.mean([r['sl_axes'] for r in rows]):.1%}\n")

    def show(lab, sub):
        if len(sub) < 50:
            print(f"{lab:>30}{len(sub):>8,}  （件数不足）")
            return
        d = defaultdict(lambda: [0, 0, 0])
        for r in sub:
            z = d[r["date"]]
            z[0] += 1; z[1] += r["y"]; z[2] += int(r["a1_in"] and r["rep_in"])
        n = len(sub)
        hc = sum(z[1] for z in d.values()) / n
        hd = sum(z[2] for z in d.values()) / n
        lo, hi = ci_diff(d)
        f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
        print(f"{lab:>30}{n:>8,}{hc:>9.2%}{hd:>9.2%}"
              f"{f'Δ{(hd-hc)*100:+.2f}pt [{lo*100:+.1f},{hi*100:+.1f}]{f}':>26}")

    print(f"{'条件':>30}{'件数':>8}{'現行':>9}{'置換後':>9}{'（対現行）':>26}")
    rule = [r for r in rows if r["gap"] < GAP_SMALL and r["sl_rep"] and not r["sl_axes"]]
    show("🎯 規則（3条件すべて）", rule)
    show("差小 ∧ 代替同L ∧ 二軸同L",
         [r for r in rows if r["gap"] < GAP_SMALL and r["sl_rep"] and r["sl_axes"]])
    show("差小 ∧ 代替同ライン",
         [r for r in rows if r["gap"] < GAP_SMALL and r["sl_rep"]])
    show("二軸が別ライン ∧ 差小",
         [r for r in rows if not r["sl_axes"] and r["gap"] < GAP_SMALL])
    show("規則に当たらない全部",
         [r for r in rows if not (r["gap"] < GAP_SMALL and r["sl_rep"] and not r["sl_axes"])])
    show("母集団すべてに一律適用", rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
