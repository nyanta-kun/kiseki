#!/usr/bin/env python3
"""型ラボ 設計レポート — 設計の目標値と、その日の実績を**同じ分解**で並べる。

2026-09-10 新設（ユーザー指摘「個別に変更を積み上げると全体としての設計が崩れる。
狙うべきレース選定・推奨方法を定義し、外れをどの観点で見るかを正しくできるように」）。

設計の正本は `docs/type_lab/DESIGN.md`。本スクリプトはその第2層（商品の目標値）と
第4層（外れの2段分解）を機械的に出し直すだけで、**判断はしない**。

## 何を出すか

    ① 商品ごとの設計目標値（板・両窓）        DESIGN.md 2.2 の表
    ② 外れの2段分解（板・両窓）              DESIGN.md 4.2 の表
    ③ --live 指定日の実績を ①② と同じ形で   ← 単日を設計と比べる

## 🔴 単日の読み方（DESIGN.md 4.1）

- **全体の表示的中は KPI ではない**。当てにいく商品（設計 27%）と一撃商品（設計 5%）の
  混合物なので、一撃商品が多い日は必ず下がる。必ず商品ごとに見る。
- **ROI で判断しない**。この層は ±2.5pt の判別に約15.6年かかる。
- 単日の件数は 40件前後しかない。**目標値との差は「ずれた」であって「悪化した」ではない。**

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/type_lab_design_report.py
    PYTHONPATH=. .venv/bin/python scripts/type_lab_design_report.py --live 2026-09-09
    PYTHONPATH=. .venv/bin/python scripts/type_lab_design_report.py --live 2026-09-01:2026-09-09

板 `/tmp/miss_anatomy_rows.pkl` が無ければ ① ② は飛ばす（作り方は
`scripts/exp_type_lab/miss_anatomy_build.py`）。`--live` は DB だけで動く。
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import statistics as st
from pathlib import Path

BOARD_ROWS = Path("/tmp/miss_anatomy_rows.pkl")

#: 当てにいく商品（仕事＝当てる）。DESIGN.md 2.1
CORE_PLANS = ("A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_line")
#: 一撃商品（仕事＝大きい払戻を出す）。**表示的中で測ってはいけない**
ONESHOT_PLANS = ("A_ana", "F_sign", "F_pay",
                 "A_big", "B_big", "C_big", "D_big", "E_big", "F_big",
                 "A_sign", "B_sign", "C_sign", "D_sign", "E_sign")
PLAN_ORDER = ("A_hit", "A_trio", "A_ana", "B_hit", "C_hit",
              "D_hit", "E_hit", "F_hit", "F_pay", "F_line", "F_sign")


def kind(plan: str) -> str:
    return "一撃" if plan in ONESHOT_PLANS else "当て"


# ───────────────────────────── 分解 ─────────────────────────────

def finishers(win_combo: str | None) -> tuple[int, ...]:
    """"1-2-3"（三連単）と "1=2=3"（三連複）の両方を受ける。"""
    if not win_combo:
        return ()
    parts = str(win_combo).replace("=", "-").replace("→", "-").split("-")
    out = tuple(int(p) for p in parts if p.strip().isdigit())
    return out if len(out) == 3 else ()


def miss_bucket(hit: bool, set_hit: bool, both_in3: bool) -> str:
    """外れの内訳。DESIGN.md 4.2。

    >>> miss_bucket(True, True, True)
    'hit'
    >>> miss_bucket(False, True, True)
    'order'
    >>> miss_bucket(False, False, True)
    'partner'
    >>> miss_bucket(False, False, False)
    'axisbust'
    """
    if hit:
        return "hit"
    if set_hit:
        return "order"
    return "partner" if both_in3 else "axisbust"


def summarize(sub: list[dict], n_days: int) -> dict | None:
    """1つの商品群の KPI。`sub` は下の正規化済み dict のリスト。"""
    if not sub:
        return None
    n = len(sub)
    won = [r["pay"] for r in sub if r["pay"] > r["inv"]]
    return dict(
        n=n, per_day=n / max(n_days, 1),
        hit=100 * sum(1 for r in sub if r["hit"]) / n,
        shown=100 * len(won) / n,
        med=st.median(won) if won else 0.0,
        hp=sum(1 for r in sub if r["pay"] >= 100_000) / max(n_days, 1),
        roi=100 * sum(r["pay"] for r in sub) / sum(r["inv"] for r in sub),
    )


def two_stage(sub: list[dict]) -> dict | None:
    """的中率 = 軸2そろい率 × そろった時の的中率 ＋ 崩れても当たる分。"""
    if not sub:
        return None
    both = [r for r in sub if r["both_in3"]]
    nb = [r for r in sub if not r["both_in3"]]
    miss = [r for r in sub if not r["hit"]]
    m = len(miss) or 1
    return dict(
        n=len(sub),
        both=100 * len(both) / len(sub),
        h_both=100 * sum(1 for r in both if r["hit"]) / len(both) if both else 0.0,
        h_nb=100 * sum(1 for r in nb if r["hit"]) / len(nb) if nb else 0.0,
        hit=100 * sum(1 for r in sub if r["hit"]) / len(sub),
        order=100 * sum(1 for r in miss if r["set_hit"]) / m,
        partner=100 * sum(1 for r in miss
                          if not r["set_hit"] and r["both_in3"]) / m,
        bust=100 * sum(1 for r in miss if not r["both_in3"]) / m,
    )


# ───────────────────────────── 台 ─────────────────────────────

def board_rows(window: str) -> list[dict]:
    """`miss_anatomy_build.py` が焼いた行を正規化して返す。"""
    rows = pickle.loads(BOARD_ROWS.read_bytes())
    return [dict(plan=r["plan"], date=r["date"], hit=bool(r["hit"]),
                 set_hit=bool(r["set_hit"]), both_in3=bool(r["both_in3"]),
                 pay=float(r["pay"]), inv=float(r["inv"]))
            for r in rows if r["win"] == window]


def live_rows(day_from: str, day_to: str) -> list[dict]:
    """実入稿（`netkeirin_submissions` と一致する `type_lab_picks` 行）。

    🔴 生成しただけの行ではなく**売った行だけ**を見る。売っていない商品の
       成績を混ぜると、設計目標値（＝売る前提の値）と比べられなくなる。
    """
    import psycopg2                                    # noqa: PLC0415
    dsn = os.environ.get("KEIRIN_DB_URL")
    if not dsn:
        raise SystemExit("KEIRIN_DB_URL が未設定です")
    sql = """
        SELECT p.race_date, p.plan_key, p.axis1, p.axis2, p.win_combo,
               p.hit, p.payout, p.budget, p.legs
        FROM keirin.type_lab_picks p
        JOIN keirin.netkeirin_submissions s
          ON s.race_key = p.race_key AND s.rank_key = p.plan_key
         AND s.deleted_at IS NULL
        WHERE p.mode LIKE 'live%%' AND p.settled_at IS NOT NULL
          AND p.race_date BETWEEN %s AND %s
    """
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, (day_from, day_to))
        raw = cur.fetchall()

    out = []
    for date, plan, a1, a2, win_combo, hit, payout, budget, legs in raw:
        fin = finishers(win_combo)
        if not fin:
            continue
        if isinstance(legs, str):
            legs = json.loads(legs)
        bought = {frozenset(int(x) for x
                            in str(leg["combo"]).replace("=", "-").split("-"))
                  for leg in (legs or [])}
        axes = {a for a in (a1, a2) if a is not None}
        out.append(dict(plan=str(plan), date=str(date), hit=bool(hit),
                        set_hit=frozenset(fin) in bought,
                        both_in3=bool(axes) and axes <= set(fin),
                        pay=float(payout or 0), inv=float(budget or 0)))
    return out


# ───────────────────────────── 表示 ─────────────────────────────

def print_kpi(title: str, rows: list[dict]) -> None:
    n_days = len({r["date"] for r in rows})
    print(f"\n===== {title}  n={len(rows)}  {n_days}日 =====")
    core = [r for r in rows if r["plan"] in CORE_PLANS]
    one = [r for r in rows if r["plan"] in ONESHOT_PLANS]
    print("  ── 群ごと（DESIGN.md 2.1）──")
    for lab, sub in (("全商品", rows), ("当てにいく商品", core), ("一撃商品", one)):
        s = summarize(sub, n_days)
        if s:
            print(f"    {lab:8s} {s['per_day']:6.2f}件/日  表示的中 {s['shown']:6.2f}%  "
                  f"払戻中央 {s['med']:8.0f}  10万+/日 {s['hp']:.3f}  ROI {s['roi']:6.1f}%")
    print("  ── 商品ごと（DESIGN.md 2.2）──")
    print(f"    {'商品':9s}{'仕事':4s}{'件/日':>7s}{'的中%':>7s}{'表示的中%':>9s}"
          f"{'払戻中央':>9s}{'10万+/日':>9s}{'ROI%':>7s}")
    for p in PLAN_ORDER:
        s = summarize([r for r in rows if r["plan"] == p], n_days)
        if s:
            print(f"    {p:9s}{kind(p):4s}{s['per_day']:7.2f}{s['hit']:7.2f}"
                  f"{s['shown']:9.2f}{s['med']:9.0f}{s['hp']:9.3f}{s['roi']:7.1f}")


def print_two_stage(title: str, rows: list[dict]) -> None:
    print(f"\n  ── 外れの2段分解（DESIGN.md 4.2）: {title} ──")
    print("    的中率 = 軸2そろい率（読み） × そろった時の的中率（買い目） ＋ 崩れても当たる分")
    print(f"    {'商品':9s}{'n':>6s}{'軸2そろい%':>11s}{'そろった時%':>12s}"
          f"{'崩れた時%':>10s}{'的中%':>7s}   {'順序違い/相手外し/軸崩壊'}")
    for p in [*PLAN_ORDER, "*当て*"]:
        sub = ([r for r in rows if r["plan"] in CORE_PLANS] if p == "*当て*"
               else [r for r in rows if r["plan"] == p])
        if p != "*当て*" and p in ONESHOT_PLANS:
            continue                                   # 一撃商品は的中率で測らない
        t = two_stage(sub)
        if not t:
            continue
        print(f"    {p:9s}{t['n']:6d}{t['both']:11.2f}{t['h_both']:12.2f}"
              f"{t['h_nb']:10.2f}{t['hit']:7.2f}   "
              f"{t['order']:5.1f} / {t['partner']:5.1f} / {t['bust']:5.1f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", metavar="DAY[:DAY]",
                    help="実入稿を設計目標と同じ形で出す（日または範囲）")
    ap.add_argument("--no-board", action="store_true", help="板の目標値を出さない")
    args = ap.parse_args()

    if not args.no_board and BOARD_ROWS.exists():
        for win, label in (("explore", "設計目標値 探索窓 2024-07〜2025-12"),
                           ("confirm", "設計目標値 確認窓 2026-01〜08（本番相当）")):
            rows = board_rows(win)
            print_kpi(label, rows)
            print_two_stage(label, rows)
    elif not args.no_board:
        print(f"⚠️ {BOARD_ROWS} が無いので設計目標値は飛ばします"
              "（scripts/exp_type_lab/miss_anatomy_build.py で作る）")

    if args.live:
        d0, _, d1 = args.live.partition(":")
        rows = live_rows(d0, d1 or d0)
        if not rows:
            print(f"\n⚠️ {args.live} に採点済みの実入稿がありません")
            return
        print_kpi(f"実入稿 {d0}〜{d1 or d0}", rows)
        print_two_stage(f"実入稿 {d0}〜{d1 or d0}", rows)
        print("\n  🔴 単日は 40件前後しかない。目標値との差は「ずれた」であって"
              "「悪化した」ではない（DESIGN.md 4.1）。")


if __name__ == "__main__":
    main()
