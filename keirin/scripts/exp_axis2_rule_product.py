#!/usr/bin/env python3
"""軸2差し替え規則を **7S の実際の買い方** に翻訳して測る（2026-08-23）。

## 規則（両窓で確認済み・`exp_axis2_gap_line_confirm`）

    ① 現行の二軸（◎○）が別ライン ∧ ② 代替車が軸1と同一ライン
    ∧ ③ p3[軸2] − p3[代替車] < 0.114
    代替車 = ◎○ を除いた中で P(軸1と共に3着内) 最大

二軸的中: 探索(2026) 32.01→38.58%（+6.57pt） / 確認(2024-25) 33.00→38.36%（+5.36pt）

## ここで測るもの

7S は **軸2車 + 残り5車の総流し（5点・1レース10,000円）**。
5点で残り全車を覆うので **的中条件＝二軸的中**（上と同じ）。
変わるのは**配当**——○（人気車）を軸から外すと、当たったときの払戻が上がるはず。
したがって **ROI は的中率より大きく動きうる**。

🔴 ROI は Σ払戻 ÷ Σ投資。1レース1万円なので件数がそのまま投資額になる。
🔴 両窓とも同じ規則・同じ閾値（探索窓で決めた 0.114）で当てる。
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
from scripts.exp_axis2_gap_line_confirm import (  # noqa: E402
    AXIS_SUM_MAX, GAP_SMALL, load_window)
from scripts.exp_axis_prod_baseline import load_cache4  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_boards  # noqa: E402
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from scripts.exp_trio_pair_model import load_entries  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import rank_7s_select_axis, unit_stake  # noqa: E402


def boot(days, B=4000, seed=151):
    """days: {date: [bet, pay_cur, pay_new]} → (roi_cur, roi_new, lo, hi)"""
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return (v[:, 1].sum() / v[:, 0].sum(), v[:, 2].sum() / v[:, 0].sum(),
            d[int(B * .025)], d[int(B * .975)])


def evaluate(rows, board, label):
    """5点総流し（軸2車＋残り全車）で 現行 vs 置換 を比べる。"""
    st = unit_stake(5)
    d = defaultdict(lambda: [0.0, 0.0, 0.0])
    nc = nn = n = 0
    pc, pn = [], []
    for r in rows:
        b = board.get(r["key"])
        if not b:
            continue
        def legs(a1, a2):
            rest = [c for c in r["all"] if c not in (a1, a2)]
            ks = [frozenset((a1, a2, c)) for c in rest]
            return [k for k in ks if k in b]
        kc = legs(r["a1"], r["a2"])
        kn = legs(r["a1"], r["rep"])
        if len(kc) < 5 or len(kn) < 5:
            continue
        n += 1
        hc = [k for k in kc if k in r["wins"]]
        hn = [k for k in kn if k in r["wins"]]
        payc = sum(int(b[k] * 100) * st // 100 for k in hc)
        payn = sum(int(b[k] * 100) * st // 100 for k in hn)
        z = d[r["date"]]
        z[0] += 5 * st; z[1] += payc; z[2] += payn
        nc += bool(hc); nn += bool(hn)
        if hc:
            pc.append(payc)
        if hn:
            pn.append(payn)
    if n < 50:
        print(f"{label:>26}  件数不足（{n}）")
        return
    rc, rn, lo, hi = boot(d)
    f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
    print(f"{label:>26}{n:>7,}"
          f"{nc/n:>9.2%}{nn/n:>9.2%}{(nn-nc)/n*100:>+8.2f}"
          f"{rc:>9.1%}{rn:>9.1%}"
          f"{f'{(rn-rc)*100:+.1f}pt [{lo*100:+.1f},{hi*100:+.1f}]{f}':>24}"
          f"{np.median(pc) if pc else 0:>10,.0f}{np.median(pn) if pn else 0:>10,.0f}")


def rows_2026(train_cache, rounds):
    te = load_cache4("data/exp/tf_shape_cache4.jsonl")
    fin = _load_finishes([r["key"] for r in te])
    te = [r for r in te if r["key"] in fin and len(r["p3"]) >= 7]
    # ペアモデルは 2024-25 で学習（この窓に含まれない）
    from scripts.exp_trio_joint_partner import load_any
    tr = load_any(train_cache)
    Xtr, ytr, _ = build_pairs(tr, load_entries([r["key"] for r in tr]),
                              _load_finishes([r["key"] for r in tr]))
    pm = fit(Xtr, ytr, rounds)
    rr = [dict(key=r["key"], date=r["date"], p3=r["p3"],
               order=sorted(r["p3"], key=lambda c: (-r["p3"][c], c))) for r in te]
    Xp, _, mp = build_pairs(rr, load_entries([r["key"] for r in rr]), fin)
    pair = defaultdict(dict)
    for (k, _, a, b, _, _), p in zip(mp, pm.predict(Xp)):
        pair[k][frozenset((a, b))] = float(p)
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); con.set_session(readonly=True)
    cur = con.cursor()
    lgs = defaultdict(dict)
    ks = [r["key"] for r in te]
    for i in range(0, len(ks), 2000):
        cur.execute("select race_key, frame_no, line_group from keirin.wt_entries "
                    "where race_key = any(%s)", (ks[i:i + 2000],))
        for rk, fn, lg in cur.fetchall():
            lgs[rk][int(fn)] = lg
    con.close()
    out = []
    for r in te:
        sel = rank_7s_select_axis(r["pw"], r["p3"], r["bad"])
        if sel is None or r["key"] not in pair:
            continue
        a1, a2, _ = sel
        mk = r["mark"]
        hon = next((c for c, v in mk.items() if v == 1), None)
        tai = next((c for c, v in mk.items() if v == 2), None)
        if hon is None or tai is None or a1 not in (hon, tai) or a2 not in (hon, tai):
            continue
        if r["p3"][a1] + r["p3"][a2] > AXIS_SUM_MAX:
            continue
        cand = [c for c in r["p3"] if c not in (a1, hon, tai)]
        if not cand:
            continue
        rep = max(cand, key=lambda c: pair[r["key"]].get(frozenset((a1, c)), 0.0))
        lg = lgs[r["key"]]
        # 🔴 モデル不要版: 候補を「軸1と同ライン ∧ ◎○でない」に絞り p3 順で採る
        cand_sl = [c for c in cand if lg.get(a1) is not None and lg.get(c) == lg.get(a1)]
        rep_p3 = max(cand_sl, key=lambda c: r["p3"][c]) if cand_sl else None
        t3 = {c for w in winning_trifectas(fin[r["key"]]) for c in w}
        out.append(dict(key=r["key"], date=r["date"], a1=a1, a2=a2, rep=rep,
                        rep_p3=rep_p3, t3=t3,
                        gap_p3=(r["p3"][a2] - r["p3"][rep_p3]) if rep_p3 else 9.0,
                        y=int(a1 in t3 and a2 in t3), a1_in=int(a1 in t3),
                        all=list(r["p3"]), gap=r["p3"][a2] - r["p3"][rep],
                        sl_rep=int(lg.get(a1) is not None and lg.get(a1) == lg.get(rep)),
                        sl_axes=int(lg.get(a1) is not None and lg.get(a1) == lg.get(a2)),
                        wins={frozenset(w) for w in winning_trifectas(fin[r["key"]])}))
    return out


def rows_2425(rounds):
    tr = load_cache4("data/exp/tf_shape_cache4.jsonl")
    fin_tr = _load_finishes([r["key"] for r in tr])
    rr = [dict(key=r["key"], date=r["date"], p3=r["p3"],
               order=sorted(r["p3"], key=lambda c: (-r["p3"][c], c)))
          for r in tr if r["key"] in fin_tr]
    Xtr, ytr, _ = build_pairs(rr, load_entries([r["key"] for r in rr]), fin_tr)
    pm = fit(Xtr, ytr, rounds)
    W = load_window("2024-01-01", "2025-12-31")
    fin = _load_finishes(list(W))
    keys = [k for k in W if k in fin]
    rfp = [dict(key=k, date=W[k]["date"], p3=W[k]["p3"],
                order=sorted(W[k]["p3"], key=lambda c: (-W[k]["p3"][c], c))) for k in keys]
    Xp, _, mp = build_pairs(rfp, load_entries(keys), fin)
    pair = defaultdict(dict)
    for (k, _, a, b, _, _), p in zip(mp, pm.predict(Xp)):
        pair[k][frozenset((a, b))] = float(p)
    out = []
    for k in keys:
        w = W[k]
        sel = rank_7s_select_axis(w["pw"], w["p3"], {c: 0.0 for c in w["p3"]})
        if sel is None or k not in pair:
            continue
        a1, a2, _ = sel
        hon = next((c for c, v in w["mark"].items() if v == 1), None)
        tai = next((c for c, v in w["mark"].items() if v == 2), None)
        if hon is None or tai is None or a1 not in (hon, tai) or a2 not in (hon, tai):
            continue
        if w["p3"][a1] + w["p3"][a2] > AXIS_SUM_MAX:
            continue
        cand = [c for c in w["p3"] if c not in (a1, hon, tai)]
        if not cand:
            continue
        rep = max(cand, key=lambda c: pair[k].get(frozenset((a1, c)), 0.0))
        lg = w["lg"]
        cand_sl = [c for c in cand if lg[a1] is not None and lg[c] == lg[a1]]
        rep_p3 = max(cand_sl, key=lambda c: w["p3"][c]) if cand_sl else None
        t3 = {c for x in winning_trifectas(fin[k]) for c in x}
        out.append(dict(key=k, date=w["date"], a1=a1, a2=a2, rep=rep,
                        rep_p3=rep_p3, t3=t3,
                        gap_p3=(w["p3"][a2] - w["p3"][rep_p3]) if rep_p3 else 9.0,
                        y=int(a1 in t3 and a2 in t3), a1_in=int(a1 in t3),
                        all=list(w["p3"]), gap=w["p3"][a2] - w["p3"][rep],
                        sl_rep=int(lg[a1] is not None and lg[a1] == lg[rep]),
                        sl_axes=int(lg[a1] is not None and lg[a1] == lg[a2]),
                        wins={frozenset(x) for x in winning_trifectas(fin[k])}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=400)
    args = ap.parse_args()
    hdr = (f"{'窓 / 条件':>26}{'件数':>7}{'現行的中':>9}{'置換的中':>9}{'Δpt':>8}"
           f"{'現行ROI':>9}{'置換ROI':>9}{'（ROI差）':>24}{'現中央':>10}{'置中央':>10}")
    print("【7S の買い方＝軸2車＋残り全車の総流し5点・1レース10,000円】")
    print(hdr)
    for name, rows in (("探索(2026)", rows_2026("data/exp/trio_rank_cache.jsonl", args.rounds)),
                       ("確認(2024-25)", rows_2425(args.rounds))):
        board = load_boards([r["key"] for r in rows])
        rule = [r for r in rows if r["gap"] < GAP_SMALL and r["sl_rep"] and not r["sl_axes"]]
        evaluate(rule, board, f"{name} 🎯規則該当")
        evaluate([r for r in rows if not (r["gap"] < GAP_SMALL and r["sl_rep"]
                                          and not r["sl_axes"])],
                 board, f"{name} 規則外（参考）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
