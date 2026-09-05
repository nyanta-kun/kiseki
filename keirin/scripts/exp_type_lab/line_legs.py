#!/usr/bin/env python3
"""ライン決着への差し替えを測る（2026-09-05）。

発端はユーザー観察「他の並びの買い目は買っていて外している」。

🔴 **台は `/tmp/race_type_board.npz` ではなく DB**（`keirin.type_lab_picks` の
   実際に生成された行 + `keirin.wt_odds` の確定オッズ）。本番が実際に組んだ
   買い目そのものを使うため、盤面を作り直す必要がない代わりに、
   **並べ替えは確定オッズ**で行っている（本番は予測オッズ）。向きは変わらないが
   量は前向き実測で確かめること。

窓: 探索 2025年 / 確認 2026年。7車と9車は**必ず分けて**見ること。

使い方:
    python scripts/exp_type_lab/line_legs.py            # 7車
    python scripts/exp_type_lab/line_legs.py --n-entries 9
    python scripts/exp_type_lab/line_legs.py --compose  # 買い目と決着の構成のズレ
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
from collections import Counter, defaultdict

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.type_lab import (                   # noqa: E402
    GATE_FALLBACK, PLANS, RaceShape, allocate, apply_line_swap,
    sell_plans_for, _lines_of,
)
from src.stake_allocation import MIN_MEAN_PAYOUT   # noqa: E402

SELL = ("A_hit", "A_trio", "A_ana", "B_hit", "C_hit",
        "D_hit", "E_hit", "F_hit", "F_pay", "F_sign")


def _sold_only(rows: list[dict]) -> list[dict]:
    """**現行ルールが実際に入稿する1レース1プランだけ**に絞る。

    🔴 これを通さないと全プランの行が並び、全体の数字が「出していない商品」で
       薄まる（実測: Δ +1.88pt が +0.97pt に見える）。判定は正本 `sell_plans_for`。
       `trio_ok` は同じレースの `A_trio` 行がゲートを通るかで近似する。
    """
    trio_ok = {r["race_key"]: float(r["pred_mean_payout"] or 0) > MIN_MEAN_PAYOUT
               for r in rows if r["plan_key"] == "A_trio"}
    out = []
    for r in rows:
        if r["bet_type"] != "trifecta":
            continue          # 三連複は順序リスクが無いので差し替えの対象外
        keys = {p.key for p in sell_plans_for(
            r["type_label"], int(r["n_entries"] or 7), r["race_type"],
            pw_ent=(float(r["pw_ent"]) if r["pw_ent"] is not None else None),
            trio_ok=trio_ok.get(r["race_key"]))}
        if r["plan_key"] not in keys:
            continue
        # 🔴 **入稿ゲートに落ちる行は母集団から外す**（実際には売られていない）。
        #    帯のフォールバックを持つ `F_hit` だけは落ちても組み直されるので残す。
        if (float(r["pred_mean_payout"] or 0) <= MIN_MEAN_PAYOUT
                and r["plan_key"] not in GATE_FALLBACK):
            continue
        out.append(r)
    return out


def _fetch(n_entries: int):
    """入稿相当の行（1レース1プラン）と、その並び・確定オッズを返す。"""
    with psycopg2.connect(os.environ["KEIRIN_DB_URL"]) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT race_key, race_date::text AS race_date, plan_key, type_label,"
                "       race_type, n_entries, pw_ent, pred_mean_payout,"
                "       bet_type, n_legs, budget, payout, win_combo,"
                "       (SELECT string_agg(e->>'combo', ',' ORDER BY ord)"
                "          FROM jsonb_array_elements(legs) WITH ORDINALITY t(e, ord))"
                "         AS combos"
                "  FROM keirin.type_lab_picks"
                " WHERE settled_at IS NOT NULL AND win_combo IS NOT NULL"
                "   AND n_entries = %s AND race_date >= '2025-01-01'",
                (n_entries,))
            picks = _sold_only([dict(r) for r in cur.fetchall()])
        # 🔴 **オッズは必要なレースだけ引く。** 期間で引くと1,000万行を超えて
        #    数分〜数十分かかる（実測）。`race_key` を渡して絞ること。
        rks = sorted({p["race_key"] for p in picks})
        with conn.cursor() as cur:
            cur.execute("SELECT race_key, frame_no, line_group FROM keirin.wt_entries"
                        " WHERE race_key = ANY(%s)", (rks,))
            lines = cur.fetchall()
            cur.execute("SELECT race_key, combination, odds_value FROM keirin.wt_odds"
                        " WHERE bet_type = 'trifecta' AND race_key = ANY(%s)", (rks,))
            odds = cur.fetchall()
    lg: dict[str, dict[int, int]] = defaultdict(dict)
    for rk, fn, g in lines:
        lg[rk][int(fn)] = int(g or 0)
    bd: dict[str, dict[tuple, float]] = defaultdict(dict)
    for rk, c, v in odds:
        if v and v > 0:
            bd[rk][tuple(int(x) for x in c.split("-"))] = float(v)
    return picks, {k: _lines_of(v) for k, v in lg.items()}, bd, dict(lg)


def _run(picks, lines, board, plan_filter=None):
    out = []
    for p in picks:
        rk = p["race_key"]
        legs = [tuple(int(x) for x in c.split("-")) for c in (p["combos"] or "").split(",") if c]
        win = tuple(int(x) for x in p["win_combo"].replace("=", "-").split("-"))
        b = board.get(rk)
        base = win in legs
        if not b or not legs or not all(x in b for x in legs):
            out.append((base, base, float(p["payout"] or 0), 0.0, False, p)); continue
        prob = {k: 1.0 / v for k, v in b.items()}
        z = sum(prob.values()); prob = {k: v / z for k, v in prob.items()}
        plan = PLANS[p["plan_key"]]
        shape = RaceShape(p["type_label"], 1.5, 0, 0.1, True,
                          tuple(sorted({c for L in legs for c in L})), 0.0,
                          lines.get(rk, ()))
        st0 = allocate(legs, b, prob, plan)
        if not st0:
            out.append((base, base, float(p["payout"] or 0), 0.0, False, p)); continue
        nl, st = apply_line_swap(shape, plan, legs, st0, b, prob)
        hit = win in nl
        out.append((base, hit, float(p["payout"] or 0),
                    st[win] * b[win] if hit else 0.0, list(nl) != list(legs), p))
    return out


def _report(rows, n_entries):
    print(f"=== ライン決着への差し替え（{n_entries}車・確定オッズで並べ替え）===")
    for yr in ("2025", "2026"):
        r = [x for x in rows if str(x[5]["race_date"])[:4] == yr]
        if not r:
            continue
        n = len(r)
        b = 100 * sum(x[0] for x in r) / n
        h = 100 * sum(x[1] for x in r) / n
        print(f"-- {yr}年 n={n}  的中 {b:.2f}% → {h:.2f}%  Δ{h - b:+.2f}pt"
              f"  発動 {100 * sum(x[4] for x in r) / n:.0f}%"
              f"  ROI {100 * sum(x[2] for x in r) / (10000 * n):.1f}%"
              f" → {100 * sum(x[3] for x in r) / (10000 * n):.1f}%")
    print("\nプラン別 Δ的中（両窓）:")
    for key in sorted({x[5]["plan_key"] for x in rows}):
        line = f"  {key:7s}"
        for yr in ("2025", "2026"):
            r = [x for x in rows if x[5]["plan_key"] == key
                 and str(x[5]["race_date"])[:4] == yr]
            if not r:
                line += f"  {yr}: -"; continue
            b = 100 * sum(x[0] for x in r) / len(r)
            h = 100 * sum(x[1] for x in r) / len(r)
            line += (f"  {yr}: {b:5.2f}→{h:5.2f} ({h - b:+5.2f}pt"
                     f" n={len(r):5d} 発動{100 * sum(x[4] for x in r) / len(r):3.0f}%)")
        print(line)


def _compose(picks, raw):
    """買い目と実際の決着の「ライン構成」のズレ。差し替えの根拠そのもの。

    🔴 **ここは生の `line_group` で数える**（`_lines_of` は3車以上しか残さないので、
       2車ラインが「単騎を含む」へ落ちて 2ライン(2+1) が消える）。
    """
    def cls(st, rk):
        g = raw.get(rk, {})
        gs = [g.get(c, 0) for c in st]
        if 0 in gs:
            return "単騎を含む"
        return {1: "同一ライン3車", 2: "2ライン(2+1)", 3: "3ライン バラ"}[len(set(gs))]
    buy, wins = Counter(), Counter()
    for p in picks:
        rk = p["race_key"]
        legs = [tuple(int(x) for x in c.split("-")) for c in (p["combos"] or "").split(",") if c]
        if not legs:
            continue
        for st in {frozenset(c) for c in legs}:
            buy[cls(st, rk)] += 1
        wins[cls(frozenset(int(x) for x in p["win_combo"].replace("=", "-").split("-")), rk)] += 1
    tb, tw = sum(buy.values()), sum(wins.values())
    print("=== 買い目と決着のライン構成 ===")
    print(f"{'形':16s} {'買い目%':>8s} {'決着%':>8s} {'差':>7s}")
    for k in sorted(set(buy) | set(wins), key=lambda k: -wins[k]):
        a, b = 100 * buy[k] / tb, 100 * wins[k] / tw
        print(f"{k:16s} {a:8.1f} {b:8.1f} {a - b:+7.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-entries", type=int, default=7, choices=(7, 9))
    ap.add_argument("--compose", action="store_true")
    a = ap.parse_args()
    picks, lines, board, raw = _fetch(a.n_entries)
    if a.compose:
        _compose(picks, raw)
        return
    _report(_run(picks, lines, board), a.n_entries)


if __name__ == "__main__":
    main()
