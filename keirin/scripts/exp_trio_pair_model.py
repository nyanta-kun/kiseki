#!/usr/bin/env python3
"""二軸の上限を上げる — **ペア単位の同時3着内確率**を直接学習する。

## ユーザー指摘（2026-08-23）

> 現在の各モデルは同ラインから二軸をとり、**提示された確率が上限になっている**。
> レース出走メンバから条件に合わせた軸2の選出により、この上限の引き上げが必要

🔴 **そのとおり。** 現行は `p3`（1車ごとの限界確率）の上位2車を機械的に採っており、
   `argmax P(i と j がともに3着内)` を解いていない。3着の枠は3つしかないので
   **車どうしは競合**し、ライン構造で同時に来やすい組・来にくい組があるのに、
   **その同時確率を一度もモデル化していない**。

手書きのヒューリスティック（同ライン優先・ライン先頭＋番手など6規則）では
二軸的中は 53.5〜54.0% から動かなかった（逆向きにすると 35.2% まで落ちるので
構造は実在する）。**ペア単位で学習させて上限が動くかを見る。**

## 設計

- 母集団: 7車レースの **21ペアすべて**（1レース21行）
- 目的変数: **両者が3着内に入ったか**（`both_top3`）
- 特徴量: 2車の p3・順位・ライン関係（同ライン/隣接/先頭番手）・ライン規模・
  競走得点差・級班・脚質、およびレース全体の構造（ライン数・最大ライン規模・
  上位2車のp3合計・エントロピー）
- 学習 **2024-01〜2025-12** / 検定 **2026-01〜08**（完全に分離）
- 予測が最大のペアを軸2車として採り、**二軸的中率**を現行と比べる

🔴 **一次指標は二軸的中率**（ユーザーの問いそのもの）。ROI は副次。
🔴 **ペア確率の argmax が現行と同じペアを選ぶ割合**も出す。
   ほぼ同じなら「学習しても現行が既に最適」＝上限は動かないという結論になる。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402

FEATS = ["p3_a", "p3_b", "p3_prod", "p3_sum", "rank_a", "rank_b", "rank_gap",
         "same_line", "adjacent", "leader_pair", "lsize_a", "lsize_b",
         "lpos_a", "lpos_b", "rp_a", "rp_b", "rp_gap",
         "n_lines", "max_lsize", "axis_sum", "p3_entropy", "p3_std"]


def load_entries(keys):
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
    out = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute(
            "select race_key, frame_no, line_group, line_pos, line_size, "
            "       is_line_leader, race_point from keirin.wt_entries "
            "where race_key = any(%s)", (keys[i:i + 2000],))
        for rk, fn, g, p, sz, ld, rp in cur.fetchall():
            out[rk][int(fn)] = dict(lg=g, lp=p, lsize=sz, leader=ld,
                                    rp=float(rp) if rp is not None else 0.0)
    return out


def build_rows(races, ent, fins):
    """1レース21ペアの特徴量と目的変数。"""
    X, y, meta = [], [], []
    for r in races:
        e = ent.get(r["key"]); o3 = fins.get(r["key"])
        if not e or not o3:
            continue
        p3 = r["p3"]
        order = r["order"]
        if len(order) < 7 or len(e) < 7:
            continue
        top3 = {c for w in winning_trifectas(o3) for c in w}
        rank = {c: i + 1 for i, c in enumerate(order)}
        groups = {e[c]["lg"] for c in order if e[c]["lg"] is not None}
        n_lines = len(groups) or 1
        max_ls = max((e[c]["lsize"] or 0) for c in order)
        vals = np.array([p3[c] for c in order], dtype=float)
        q = vals / max(vals.sum(), 1e-9)
        ent_ = float(-(q * np.log(q + 1e-12)).sum() / np.log(len(q)))
        for a, b in itertools.combinations(order, 2):
            ea, eb = e[a], e[b]
            same = int(ea["lg"] is not None and ea["lg"] == eb["lg"])
            try:
                adj = int(same and abs(int(ea["lp"]) - int(eb["lp"])) == 1)
                lead = int(same and {str(ea["lp"]), str(eb["lp"])} == {"1", "2"})
            except (TypeError, ValueError):
                adj = lead = 0
            X.append([p3[a], p3[b], p3[a] * p3[b], p3[a] + p3[b],
                      rank[a], rank[b], abs(rank[a] - rank[b]),
                      same, adj, lead,
                      ea["lsize"] or 0, eb["lsize"] or 0,
                      _num(ea["lp"]), _num(eb["lp"]), ea["rp"], eb["rp"],
                      abs(ea["rp"] - eb["rp"]),
                      n_lines, max_ls, p3[order[0]] + p3[order[1]], ent_,
                      float(vals.std())])
            y.append(int(a in top3 and b in top3))
            meta.append((r["key"], r["date"], a, b, rank[a], rank[b]))
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int8), meta


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    args = ap.parse_args()
    import lightgbm as lgb

    tr = [dict(key=r["race_key"], date=r["race_date"], order=r["order"],
               p3={int(k): v for k, v in r["p3"].items()})
          for r in map(json.loads, open(args.train))]
    te = []
    with open(args.test) as f:
        for x in f:
            r = json.loads(x)
            if not r.get("win"):
                continue
            p3 = {int(k): v for k, v in r["p3"].items()}
            te.append(dict(key=r["race_key"], date=r["race_date"], p3=p3,
                           order=[c for c, _ in sorted(p3.items(),
                                                       key=lambda kv: (-kv[1], kv[0]))]))
    print(f"学習 {len(tr):,}R（2024-01〜2025-12） / 検定 {len(te):,}R（2026-01〜08）")

    ent_tr = load_entries([r["key"] for r in tr])
    ent_te = load_entries([r["key"] for r in te])
    fin_tr = _load_finishes([r["key"] for r in tr])
    fin_te = _load_finishes([r["key"] for r in te])
    Xtr, ytr, _ = build_rows(tr, ent_tr, fin_tr)
    Xte, yte, mte = build_rows(te, ent_te, fin_te)
    print(f"ペア行 学習 {len(Xtr):,} / 検定 {len(Xte):,}"
          f"  基準率 学習 {ytr.mean():.2%} / 検定 {yte.mean():.2%}\n")

    m = lgb.train(dict(objective="binary", learning_rate=0.05, num_leaves=31,
                       min_data_in_leaf=200, feature_fraction=0.8,
                       bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=7),
                  lgb.Dataset(Xtr, label=ytr), num_boost_round=400)
    pred = m.predict(Xte)

    by_race = defaultdict(list)
    date_of = {}
    for (key, date, a, b, ra, rb), p, t in zip(mte, pred, yte):
        by_race[key].append((p, a, b, ra, rb, t))
        date_of[key] = date
    n = both_model = both_cur = same_pick = 0
    for key, v in by_race.items():
        best = max(v, key=lambda x: x[0])
        cur = next((x for x in v if {x[3], x[4]} == {1, 2}), None)
        if cur is None:
            continue
        n += 1
        # 🔴 `yte` は int8。そのまま加算すると累積側も int8 になって
        #    128 を超えた時点でオーバーフローする（負の的中率が出る）。
        both_model += int(best[5])
        both_cur += int(cur[5])
        same_pick += ({best[1], best[2]} == {cur[1], cur[2]})
    print(f"検定 {n:,}R")
    print(f"  現行（p3上位2車）の二軸的中     : {both_cur/n:.2%}")
    print(f"  ペアモデルの argmax の二軸的中  : {both_model/n:.2%}"
          f"   Δ{(both_model-both_cur)/n*100:+.2f}pt")
    print(f"  argmax が現行と同じペアだった率 : {same_pick/n:.1%}")
    # 🔴 同一レースの対応比較。日ブロックで差の CI を取る。
    import random
    random.seed(41)
    by_day = defaultdict(lambda: [0, 0, 0])
    diff_n = diff_m = diff_c = 0
    for key, v in by_race.items():
        best = max(v, key=lambda x: x[0])
        cur = next((x for x in v if {x[3], x[4]} == {1, 2}), None)
        if cur is None:
            continue
        d = date_of[key]
        a = by_day[d]
        a[0] += 1; a[1] += int(cur[5]); a[2] += int(best[5])
        if {best[1], best[2]} != {cur[1], cur[2]}:
            diff_n += 1; diff_m += int(best[5]); diff_c += int(cur[5])
    days = list(by_day.values())
    B = 4000
    dd = []
    for _ in range(B):
        s_ = [days[random.randrange(len(days))] for _ in days]
        tot = sum(x[0] for x in s_)
        dd.append(sum(x[2] for x in s_) / tot - sum(x[1] for x in s_) / tot)
    dd.sort()
    print(f"  Δ の95%CI: [{dd[100]*100:+.2f}, {dd[-100]*100:+.2f}]pt"
          f"{'  🟢有意' if dd[100] > 0 else '  （有意でない）'}")
    print(f"\n  ペアが変わったレース {diff_n:,}件（{diff_n/n:.1%}）:")
    print(f"    そこでの二軸的中  現行 {diff_c/diff_n:.2%} → モデル {diff_m/diff_n:.2%}"
          f"  Δ{(diff_m-diff_c)/diff_n*100:+.2f}pt")
    # ── 商品への伝播: 軸2車＋相手（軸を除いた p3 上位から3番目）の1点買い ──
    # 🔴 二軸的中が上がっても ROI が上がるとは限らない（市場が織り込む）。必ず分けて出す。
    con2 = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur2 = con2.cursor()
    bkeys = list(by_race)
    bd = defaultdict(dict)
    for i in range(0, len(bkeys), 2000):
        cur2.execute("select race_key, combination, odds_value from keirin.wt_odds "
                     "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                     (bkeys[i:i + 2000],))
        for rk, c, o in cur2.fetchall():
            ss = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
            if len(ss) == 3:
                bd[rk][ss] = float(o)
    order_of = {r["key"]: r["order"] for r in te}
    win_of = {}
    for x in open(args.test):
        rr = json.loads(x)
        if rr.get("win"):
            win_of[rr["race_key"]] = {frozenset(int(y) for y in w.split("-"))
                                      for w in rr["win"]}
    from src.strategy_wt import unit_stake
    stake = unit_stake(1)
    pr = []
    for key, v in by_race.items():
        b_ = bd.get(key); o_ = order_of.get(key); w_ = win_of.get(key)
        if not b_ or not o_ or not w_:
            continue
        best = max(v, key=lambda x: x[0])
        cur_ = next((x for x in v if {x[3], x[4]} == {1, 2}), None)
        if cur_ is None:
            continue
        def bet(a1, a2):
            rest = [c for c in o_ if c not in (a1, a2)]
            if len(rest) < 3:
                return None
            k = frozenset((a1, a2, rest[2]))
            if k not in b_:
                return None
            return int(b_[k] * 100) * stake // 100 if k in w_ else 0
        pc = bet(cur_[1], cur_[2]); pm = bet(best[1], best[2])
        if pc is None or pm is None:
            continue
        pr.append((date_of[key], pc, pm))
    byd = defaultdict(lambda: [0, 0, 0])
    for d, pc, pm in pr:
        a = byd[d]; a[0] += stake; a[1] += pc; a[2] += pm
    vv = list(byd.values())
    dd2 = []
    for _ in range(4000):
        s_ = [vv[random.randrange(len(vv))] for _ in vv]
        tot = sum(x[0] for x in s_)
        dd2.append(sum(x[2] for x in s_) / tot - sum(x[1] for x in s_) / tot)
    dd2.sort()
    tot = sum(x[0] for x in vv)
    r0 = sum(x[1] for x in vv) / tot; r1 = sum(x[2] for x in vv) / tot
    print(f"\n  商品ROI（軸2車＋相手1点・{len(pr):,}R）:")
    print(f"    現行 {r0:.1%} → ペアモデル {r1:.1%}"
          f"   Δ{(r1-r0)*100:+.1f}pt [{dd2[100]*100:+.1f},{dd2[-100]*100:+.1f}]"
          f"{'  🟢有意' if dd2[100] > 0 else '  （有意でない）'}")
    imp = sorted(zip(FEATS, m.feature_importance("gain")), key=lambda x: -x[1])
    print("\n  寄与上位: " + " / ".join(f"{k}" for k, _ in imp[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
