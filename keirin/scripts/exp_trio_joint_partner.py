#!/usr/bin/env python3
"""相手を「**二軸と合わせて3着内にくる確率**」で選ぶ（三者同時確率）。

## ユーザー指摘（2026-08-23・引き継ぎの未着手項目 1）

> 相手は「二軸と合わせて3着内にくる確率」で選ぶべきではないか

🔴 **これまで一度もやっていない。** 相手の選び方は
   ①p3 の順位（現行＝順位3）②p3 の値（`exp_trio_partner_p3value`）
   ③手書きの構造規則 のいずれかで、**軸2車を条件に置いた確率**では選んでいない。
   [[keirin_trio_partner_and_axis2_2026_08_23]] の「順位5が+8pt」は
   独立窓で撤回済みなので、**順位という粗い量ではない選び方**を測り直す。

## 二軸が固定なら「条件付き」と「三者同時」は同じもの

    P(3車すべて3着内 | 軸2車) = P(軸2車が3着内) × P(相手も3着内 | 軸2車が3着内)

レース内で軸2車は固定なので第1項は定数。**argmax は一致する**。
したがって直接 `P(3車すべて3着内)` を学習すれば足りる。
そして 7車の三連複では「3車すべて3着内」＝**その買い目の的中**そのもの。
つまり本スクリプトは **買い目の的中確率を直接モデル化**している。

## 3つの腕（同一レースで対応比較）

| 腕 | 相手の選び方 |
|---|---|
| 現行 | p3 順位3 |
| 参考 | p3 順位5（**撤回済みの旧結論**・対照として置く） |
| **A** | 軸2車を固定し、5候補から `argmax P(trio的中)` |
| **B** | 軸も動かし 35 通りから `argmax P(trio的中)`（上限の目安） |

🔴 **学習 2024-01〜2025-12 / 検定 2026-01〜08**。年をまたぐ独立窓
   （同一年内の前後分割を確認窓と呼んで撤回した前例があるため）。
🔴 一次指標は**三連複の的中率**。ROI は市場が織り込むので**必ず分けて出す**。
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
from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485

FEATS_A = [
    "p3_c", "rank_c", "gap_up", "gap_dn", "p3_rel",
    "p3_a1", "p3_a2", "axis_sum", "prod3", "sum3",
    "same_a1", "same_a2", "adj_a1", "adj_a2",
    "lsize_c", "lpos_c", "leader_c", "rp_c", "rp_gap_a2",
    "n_lines", "max_lsize", "p3_ent", "p3_std",
    "axis_same", "axis_leader", "n_lines_in_trio",
]
FEATS_B = [
    "p3_1", "p3_2", "p3_3", "p3_sum", "p3_prod",
    "rank_1", "rank_2", "rank_3", "rank_sum",
    "same_12", "same_13", "same_23", "n_lines_in_trio",
    "lsize_1", "lsize_2", "lsize_3", "lpos_1", "lpos_2", "lpos_3",
    "n_leaders", "rp_sum", "rp_std",
    "n_lines", "max_lsize", "axis_sum", "p3_ent", "p3_std",
]


def load_any(path):
    """`trio_rank_cache`（order 入り）と `tf_shape_cache4`（win 入り）の両方を読む。"""
    out = []
    with open(path) as f:
        for x in f:
            r = json.loads(x)
            p3 = {int(k): v for k, v in r["p3"].items()}
            if "order" in r:
                order = r["order"]
            else:
                if not r.get("win"):
                    continue
                order = [c for c, _ in sorted(p3.items(),
                                              key=lambda kv: (-kv[1], kv[0]))]
            out.append(dict(key=r["race_key"], date=r["race_date"],
                            p3=p3, order=order))
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def load_entries(keys):
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    out = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute(
            "select race_key, frame_no, line_group, line_pos, line_size, "
            "       is_line_leader, race_point from keirin.wt_entries "
            "where race_key = any(%s)", (keys[i:i + 2000],))
        for rk, fn, g, p, sz, ld, rp in cur.fetchall():
            out[rk][int(fn)] = dict(lg=g, lp=_num(p), lsize=_num(sz),
                                    leader=int(bool(ld)),
                                    rp=float(rp) if rp is not None else 0.0)
    con.close()
    return out


def load_boards(keys):
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    bd = defaultdict(dict)
    for i in range(0, len(keys), 2000):
        cur.execute("select race_key, combination, odds_value from keirin.wt_odds "
                    "where bet_type='trio' and race_key=any(%s) and odds_value>0",
                    (keys[i:i + 2000],))
        for rk, c, o in cur.fetchall():
            s = frozenset(int(x) for x in str(c).replace("=", "-").split("-"))
            if len(s) == 3:
                bd[rk][s] = float(o)
    con.close()
    return bd


def race_context(order, p3, e):
    """レース全体の構造（両モデル共通）。"""
    groups = {e[c]["lg"] for c in order if e[c]["lg"] is not None}
    vals = np.array([p3[c] for c in order], dtype=float)
    q = vals / max(vals.sum(), 1e-9)
    return dict(
        n_lines=float(len(groups) or 1),
        max_lsize=max((e[c]["lsize"] or 0) for c in order),
        axis_sum=p3[order[0]] + p3[order[1]],
        p3_ent=float(-(q * np.log(q + 1e-12)).sum() / np.log(len(q))),
        p3_std=float(vals.std()),
    )


def _same(e, a, b):
    return int(e[a]["lg"] is not None and e[a]["lg"] == e[b]["lg"])


def _adj(e, a, b):
    return int(_same(e, a, b) and abs(e[a]["lp"] - e[b]["lp"]) == 1)


def build_A(races, ent, fins):
    """軸2車固定・相手5候補（1レース5行）。"""
    X, y, meta = [], [], []
    for r in races:
        e = ent.get(r["key"]); o3 = fins.get(r["key"])
        if not e or not o3:
            continue
        o, p3 = r["order"], r["p3"]
        if len(o) < 7 or len(e) < 7:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        ctx = race_context(o, p3, e)
        a1, a2 = o[0], o[1]
        vals = [p3[c] for c in o]
        for i in range(2, 7):
            c = o[i]
            nxt = vals[i + 1] if i + 1 < len(vals) else 0.0
            ec = e[c]
            X.append([
                p3[c], float(i + 1), p3[a2] - p3[c], p3[c] - nxt,
                p3[c] / max(p3[a2], 1e-9),
                p3[a1], p3[a2], ctx["axis_sum"], p3[a1] * p3[a2] * p3[c],
                p3[a1] + p3[a2] + p3[c],
                _same(e, a1, c), _same(e, a2, c), _adj(e, a1, c), _adj(e, a2, c),
                ec["lsize"], ec["lp"], ec["leader"], ec["rp"],
                abs(ec["rp"] - e[a2]["rp"]),
                ctx["n_lines"], ctx["max_lsize"], ctx["p3_ent"], ctx["p3_std"],
                _same(e, a1, a2), int(_same(e, a1, a2) and
                                      {e[a1]["lp"], e[a2]["lp"]} == {1.0, 2.0}),
                float(len({e[x]["lg"] for x in (a1, a2, c)})),
            ])
            y.append(int(frozenset((a1, a2, c)) in wins))
            meta.append((r["key"], r["date"], a1, a2, c, i + 1))
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int8), meta


def build_B(races, ent, fins):
    """35通りすべて（1レース35行）。"""
    X, y, meta = [], [], []
    for r in races:
        e = ent.get(r["key"]); o3 = fins.get(r["key"])
        if not e or not o3:
            continue
        o, p3 = r["order"], r["p3"]
        if len(o) < 7 or len(e) < 7:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        ctx = race_context(o, p3, e)
        rank = {c: i + 1 for i, c in enumerate(o)}
        for combo in itertools.combinations(o, 3):
            c1, c2, c3 = sorted(combo, key=lambda c: -p3[c])
            rps = [e[c]["rp"] for c in (c1, c2, c3)]
            X.append([
                p3[c1], p3[c2], p3[c3], p3[c1] + p3[c2] + p3[c3],
                p3[c1] * p3[c2] * p3[c3],
                float(rank[c1]), float(rank[c2]), float(rank[c3]),
                float(rank[c1] + rank[c2] + rank[c3]),
                _same(e, c1, c2), _same(e, c1, c3), _same(e, c2, c3),
                float(len({e[c]["lg"] for c in (c1, c2, c3)})),
                e[c1]["lsize"], e[c2]["lsize"], e[c3]["lsize"],
                e[c1]["lp"], e[c2]["lp"], e[c3]["lp"],
                float(sum(e[c]["leader"] for c in (c1, c2, c3))),
                float(sum(rps)), float(np.std(rps)),
                ctx["n_lines"], ctx["max_lsize"], ctx["axis_sum"],
                ctx["p3_ent"], ctx["p3_std"],
            ])
            y.append(int(frozenset(combo) in wins))
            meta.append((r["key"], r["date"], frozenset(combo)))
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int8), meta


def fit(X, y, rounds):
    import lightgbm as lgb
    return lgb.train(dict(objective="binary", learning_rate=0.05, num_leaves=31,
                          min_data_in_leaf=200, feature_fraction=0.8,
                          bagging_fraction=0.8, bagging_freq=1,
                          verbose=-1, seed=7),
                     lgb.Dataset(X, label=y), num_boost_round=rounds)


def day_ci(days, B=4000, seed=41):
    """日ブロック bootstrap。days=[(bet, pay_ref, pay_alt), ...] の集計済み。"""
    rng = np.random.default_rng(seed)
    a = np.array(days, dtype=float)
    idx = rng.integers(0, len(a), size=(B, len(a)))
    tot = a[idx, 0].sum(1)
    d = np.sort(a[idx, 2].sum(1) / tot - a[idx, 1].sum(1) / tot)
    lo_alt = np.sort(a[idx, 2].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)], lo_alt[int(B * .025)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--skip-b", action="store_true")
    ap.add_argument("--swap", action="store_true",
                    help="学習と検定の窓を入れ替える（逆向きの独立窓での確認）")
    ap.add_argument("--test-from", default="")
    ap.add_argument("--test-to", default="")
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr
    if args.test_from or args.test_to:
        te = [r for r in te
              if (not args.test_from or r["date"] >= args.test_from)
              and (not args.test_to or r["date"] <= args.test_to)]
    def span(v):
        return f"{min(r['date'] for r in v)}〜{max(r['date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）")

    ent_tr = load_entries([r["key"] for r in tr])
    ent_te = load_entries([r["key"] for r in te])
    fin_tr = _load_finishes([r["key"] for r in tr])
    fin_te = _load_finishes([r["key"] for r in te])
    board = load_boards([r["key"] for r in te])
    stake = unit_stake(1)

    # ── 分解: 二軸の的中と、そこに相手が乗る条件付き確率 ──
    pair_hit = trio_hit = n0 = 0
    for r in te:
        e = ent_te.get(r["key"]); o3 = fin_te.get(r["key"])
        if not e or not o3 or len(r["order"]) < 7 or len(e) < 7:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        top3 = {c for w in winning_trifectas(o3) for c in w}
        o = r["order"]
        n0 += 1
        pair_hit += int(o[0] in top3 and o[1] in top3)
        trio_hit += int(frozenset((o[0], o[1], o[2])) in wins)
    print(f"\n【分解・検定窓 {n0:,}R】")
    print(f"  二軸（p3上位2）の的中          : {pair_hit/n0:.2%}")
    print(f"  現行（相手=順位3）の三連複的中 : {trio_hit/n0:.2%}")
    print(f"  → 二軸が当たった中で相手も乗る率: {trio_hit/max(pair_hit,1):.2%}"
          f"   （残り5車から1つ当てる無作為は 20.00%）")

    Xtr, ytr, _ = build_A(tr, ent_tr, fin_tr)
    Xte, yte, mte = build_A(te, ent_te, fin_te)
    print(f"\n候補行 学習 {len(Xtr):,} / 検定 {len(Xte):,}"
          f"   的中率 学習 {ytr.mean():.2%} / 検定 {yte.mean():.2%}")
    mA = fit(Xtr, ytr, args.rounds)
    pA = mA.predict(Xte)

    by_race = defaultdict(list)
    date_of, axes_of = {}, {}
    for (key, date, a1, a2, c, rk), p, t in zip(mte, pA, yte):
        by_race[key].append((float(p), c, rk, int(t)))
        date_of[key] = date
        axes_of[key] = (a1, a2)

    # ── 腕ごとの評価（board にある買い目だけ・全腕が揃うレースに限る）──
    arms = {"現行(順位3)": None, "参考(順位5)": None, "A:三者同時確率": None,
            "無作為(5)": None}
    rng = np.random.default_rng(11)
    rows = defaultdict(list)   # arm -> [(date, hit, pay)]
    rank_dist = defaultdict(int)
    n_eval = agree = 0
    for key, v in by_race.items():
        bd = board.get(key)
        if not bd or len(v) != 5:
            continue
        a1, a2 = axes_of[key]
        pick = {
            "現行(順位3)": next(x for x in v if x[2] == 3),
            "参考(順位5)": next(x for x in v if x[2] == 5),
            "A:三者同時確率": max(v, key=lambda x: x[0]),
            "無作為(5)": v[int(rng.integers(0, 5))],
        }
        ks = {a: frozenset((a1, a2, x[1])) for a, x in pick.items()}
        if any(k not in bd for k in ks.values()):
            continue
        n_eval += 1
        rank_dist[pick["A:三者同時確率"][2]] += 1
        agree += int(pick["A:三者同時確率"][2] == 3)
        for a, x in pick.items():
            pay = int(bd[ks[a]] * 100) * stake // 100 if x[3] else 0
            rows[a].append((date_of[key], x[3], pay))

    print(f"\n【相手の選び方・検定窓 {n_eval:,}R（1点買い）】")
    print(f"{'腕':>16}{'的中%':>9}{'ROI':>9}{'ROI下限':>9}{'中央払戻':>10}")
    days_ref = None
    ref_hit = None
    for a in ["現行(順位3)", "参考(順位5)", "無作為(5)", "A:三者同時確率"]:
        seg = rows[a]
        by = defaultdict(lambda: [0.0, 0.0, 0])
        for d, h, p in seg:
            z = by[d]; z[0] += stake; z[1] += p; z[2] += h
        vlist = list(by.values())
        bet = sum(z[0] for z in vlist); pay = sum(z[1] for z in vlist)
        hit = sum(z[2] for z in vlist) / len(seg)
        pl = sorted(p for _, h, p in seg if p > 0)
        if a == "現行(順位3)":
            days_ref = by
            ref_hit = hit
        dd = [(by[d][0], days_ref[d][1], by[d][1]) for d in by]
        _, _, lo = day_ci(dd)
        mk = " 🟢" if lo > PAYOUT_RATE else ""
        print(f"{a:>16}{hit:>9.2%}{pay/bet:>9.1%}{lo:>9.1%}"
              f"{(np.median(pl) if pl else 0):>10,.0f}{mk}")

    # 現行との対応差（的中率・ROI）
    for a in ["A:三者同時確率"]:
        by_a = defaultdict(lambda: [0.0, 0.0, 0])
        by_c = defaultdict(lambda: [0.0, 0.0, 0])
        for (d, h, p), (d2, h2, p2) in zip(rows[a], rows["現行(順位3)"]):
            assert d == d2
            z = by_a[d]; z[0] += stake; z[1] += p; z[2] += h
            z = by_c[d]; z[0] += stake; z[1] += p2; z[2] += h2
        dd_roi = [(by_a[d][0], by_c[d][1], by_a[d][1]) for d in by_a]
        dd_hit = [(by_a[d][0] / stake, by_c[d][2], by_a[d][2]) for d in by_a]
        lo_r, hi_r, _ = day_ci(dd_roi)
        lo_h, hi_h, _ = day_ci(dd_hit)
        hit_a = sum(z[2] for z in by_a.values()) / n_eval
        print(f"\n  {a} − 現行:")
        print(f"    的中率 Δ{(hit_a-ref_hit)*100:+.2f}pt "
              f"[{lo_h*100:+.2f},{hi_h*100:+.2f}]"
              f"{'  🟢有意' if lo_h > 0 else '  （有意でない）'}")
        print(f"    ROI    Δ{(sum(z[1] for z in by_a.values())/sum(z[0] for z in by_a.values()) - sum(z[1] for z in by_c.values())/sum(z[0] for z in by_c.values()))*100:+.1f}pt "
              f"[{lo_r*100:+.1f},{hi_r*100:+.1f}]"
              f"{'  🟢有意' if lo_r > 0 else '  （有意でない）'}")
    print(f"    現行と同じ相手を選んだ率: {agree/n_eval:.1%}")
    print("    選ばれた相手の順位分布: " +
          " / ".join(f"順位{k}:{rank_dist[k]/n_eval:.1%}" for k in sorted(rank_dist)))
    imp = sorted(zip(FEATS_A, mA.feature_importance("gain")), key=lambda x: -x[1])
    print("    寄与上位: " + " / ".join(k for k, _ in imp[:8]))

    if args.skip_b:
        return 0
    # ── B: 軸も動かす（35通り）──
    XtrB, ytrB, _ = build_B(tr, ent_tr, fin_tr)
    XteB, yteB, mteB = build_B(te, ent_te, fin_te)
    print(f"\n三連複行 学習 {len(XtrB):,} / 検定 {len(XteB):,}"
          f"   的中率 {ytrB.mean():.2%} / {yteB.mean():.2%}")
    mB = fit(XtrB, ytrB, args.rounds)
    pB = mB.predict(XteB)
    bestB = {}
    for (key, date, s), p, t in zip(mteB, pB, yteB):
        cur = bestB.get(key)
        if cur is None or p > cur[0]:
            bestB[key] = (float(p), s, int(t))
    rowsB, sameB, nB = [], 0, 0
    for key, (_, s, t) in bestB.items():
        bd = board.get(key)
        if not bd or s not in bd or key not in by_race:
            continue
        a1, a2 = axes_of[key]
        v = by_race[key]
        if len(v) != 5:
            continue
        cur3 = next(x for x in v if x[2] == 3)
        if frozenset((a1, a2, cur3[1])) not in bd:
            continue
        nB += 1
        sameB += int(s == frozenset((a1, a2, cur3[1])))
        rowsB.append((date_of[key], t,
                      int(bd[s] * 100) * stake // 100 if t else 0,
                      cur3[3],
                      int(bd[frozenset((a1, a2, cur3[1]))] * 100) * stake // 100
                      if cur3[3] else 0,
                      int(a1 in s and a2 in s)))
    by_b = defaultdict(lambda: [0.0, 0.0, 0, 0.0, 0])
    for d, t, p, tc, pc, _ in rowsB:
        z = by_b[d]; z[0] += stake; z[1] += p; z[2] += t; z[3] += pc; z[4] += tc
    dd = [(by_b[d][0], by_b[d][3], by_b[d][1]) for d in by_b]
    lo_r, hi_r, lo_abs = day_ci(dd)
    hb = sum(z[2] for z in by_b.values()) / nB
    hc = sum(z[4] for z in by_b.values()) / nB
    bet = sum(z[0] for z in by_b.values())
    print(f"\n【B: 35通りから argmax・{nB:,}R】")
    print(f"  的中  現行 {hc:.2%} → B {hb:.2%}   Δ{(hb-hc)*100:+.2f}pt")
    print(f"  ROI   現行 {sum(z[3] for z in by_b.values())/bet:.1%}"
          f" → B {sum(z[1] for z in by_b.values())/bet:.1%}"
          f"   Δ{lo_r*100:+.1f}〜{hi_r*100:+.1f}pt(95%CI)"
          f"{'  🟢有意' if lo_r > 0 else '  （有意でない）'}")
    print(f"  B が現行と完全一致した率: {sameB/nB:.1%}"
          f" / 現行の二軸を含んだ率: {sum(x[5] for x in rowsB)/nB:.1%}")
    impB = sorted(zip(FEATS_B, mB.feature_importance("gain")), key=lambda x: -x[1])
    print("  寄与上位: " + " / ".join(k for k, _ in impB[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
