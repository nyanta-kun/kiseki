#!/usr/bin/env python3
"""型ラボの入稿データ（タイトル・コメント・印）を実データで組んで表示する。

    # 各プラン1件ずつ（既定）
    python scripts/preview_type_lab_submission.py --mode paper --from 2026-08-01 --to 2026-08-26

    # 特定プランを3件
    python scripts/preview_type_lab_submission.py --plan D_hit --limit 3

    # 出走表HTMLも付ける（実際の入稿と同じ形）
    python scripts/preview_type_lab_submission.py --with-entry-table

🔴 **表示するだけで、何も入稿しない・DB を書き換えない。**
   型ラボは検証中で `netkeirin_submit_wt.py` からは呼ばれていない。
   文面の正本は `src/type_lab_submission.py`。

⚠️ 出走表HTMLは `netkeirin_submit_wt._build_entry_table` を**そのまま呼ぶ**。
   写して持つと、本番の列（2着内率の出し入れ等）が変わったとき静かに食い違う。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.type_lab_submission import build_submission  # noqa: E402

PLAN_ORDER = ["A_hit", "A_pay", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_pay"]

_SQL = """
    SELECT race_key, race_date, venue_name, race_no, race_type, n_entries,
           type_label, axis_sum, arare, axis1, axis2, p3_order, plan_key, bet_type,
           n_legs, budget, legs, pred_mean_payout, pred_min_payout,
           settled_at, win_combo, hit, payout
    FROM type_lab_picks
    WHERE mode = ? AND race_date BETWEEN ? AND ? AND plan_key = ?
    ORDER BY race_date DESC, race_key
    LIMIT ?
"""


def _rows(mode: str, d1: str, d2: str, plan: str, limit: int) -> list[dict]:
    cols = ("race_key race_date venue_name race_no race_type n_entries type_label "
            "axis_sum arare axis1 axis2 p3_order plan_key bet_type n_legs budget legs "
            "pred_mean_payout pred_min_payout settled_at win_combo hit payout").split()
    with get_connection() as c:
        rows = c.execute(_SQL, (mode, d1, d2, plan, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, tuple(r)))
        d["legs"] = json.loads(d["legs"]) if isinstance(d["legs"], str) else (d["legs"] or [])
        out.append(d)
    return out


def _render(row: dict, with_table: bool) -> str:
    table = None
    if with_table:
        # 本番の実装をそのまま使う（写さない）
        from netkeirin_submit_wt import _build_entry_table
        marks = build_submission(row)["marks"]
        table = _build_entry_table(row["race_key"], marks)
    sub = build_submission(row, table)
    legs = row["legs"]
    order = "-".join(str(x) for x in (row["p3_order"] or "").split("-"))
    lines = [
        "=" * 74,
        f"{row['venue_name']}{row['race_no']}R  {row['race_type']}  "
        f"{row['n_entries']}車  {row['race_date']}  [{row['plan_key']} / 型{row['type_label']}]",
        f"  指数順 {order}   軸信頼 {row['axis_sum']}  荒れ度 {row['arare']}",
        f"  想定払戻 平均 {int(row['pred_mean_payout'] or 0):,}円 / 最低 "
        f"{int(row['pred_min_payout'] or 0):,}円   予算 {int(row['budget']):,}円",
        "",
        f"■ タイトル\n  {sub['title']}",
        "",
        "■ 印",
        "  " + "  ".join(f"{c}{sub['marks'][c]}" for c in sorted(sub["marks"])),
        "  " + f"（買っていない車＝無印: "
               f"{sorted(set(range(1, int(row['n_entries']) + 1)) - set(sub['marks'])) or 'なし'}）",
        "",
        f"■ 買い目（{row['n_legs']}点）",
    ]
    for leg in legs:
        lines.append(f"  {leg['combo']:>8}  {int(leg['stake']):>6,}円  "
                     f"予測{float(leg['pred_odds']):>7.1f}倍  "
                     f"想定払戻{int(leg['stake'] * float(leg['pred_odds'])):>8,}円")
    lines += ["", "■ コメント", ""]
    lines += ["  " + ln for ln in sub["comment"].splitlines()]
    if row["settled_at"] is not None:
        res = (f"的中 {int(row['payout'] or 0):,}円" if row["hit"] else "不的中")
        lines += ["", f"■ 結果  決着 {row['win_combo']}  → {res}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="paper",
                    choices=("paper", "paper9", "live", "live9"))
    ap.add_argument("--from", dest="date_from", default="2026-08-01")
    ap.add_argument("--to", dest="date_to", default="2026-08-26")
    ap.add_argument("--plan", help="1プランだけ見る（既定は全8プラン）")
    ap.add_argument("--limit", type=int, default=1, help="プランごとの件数")
    ap.add_argument("--with-entry-table", action="store_true",
                    help="出走表HTMLも付ける（実際の入稿と同じ形）")
    a = ap.parse_args()

    plans = [a.plan] if a.plan else PLAN_ORDER
    n = 0
    for plan in plans:
        for row in _rows(a.mode, a.date_from, a.date_to, plan, a.limit):
            print(_render(row, a.with_entry_table))
            print()
            n += 1
    if not n:
        print("該当する行がありません（--mode / 期間 / --plan を見直してください）")


if __name__ == "__main__":
    main()
