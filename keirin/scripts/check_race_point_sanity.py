#!/usr/bin/env python3
"""race_point（WINTICKET競走得点）の日次収集直後の健全性チェック。

2026-07-23、日次08:00収集(daily_picks_wt.sh)の時点でWINTICKET側がその日の
race_pointをまだ確定しておらず、異常に低い暫定値（平均4.3、正常時62-90）を
拾ってしまう事象が発生した（暫定値は相対順序も確定値と入れ替わっており、
モデル特徴量として使うと指数・推奨の質が劣化する）。

本スクリプトは対象日の平均race_pointを直近日の中央値と比較し、
異常に低ければ非ゼロ終了する（daily_picks_wt.shが再収集→再チェックの
リトライに使う・詳細はCLAUDE.md/メモリ参照）。

## 🔴 日次だけでは過去の汚染を見つけられない（2026-09-20 監査 item5）

本チェックは `daily_picks_wt.sh` から**その日ぶんにしか掛かっていない**。
そのため 2026-06-12（43・61 会場の 24 レース・210 行）の汚染は
**誰にも検知されないまま残り続けた**（`docs/prediction-factors.md` が
「汚染は 2026-06-18〜07-23 で解消済み」と書いている窓の**外**）。
さらに再取得パイプライン（`pipeline_wt.py::_get_collected_keys`）は
結果が入った行をスキップするので、**自動経路では二度と直らない**。

→ `--scan` で**同じ規則を過去全期間へ遡って**掛けられるようにした。
   見つけるためのものなので**常に終了コード 0**（日次ゲートと違い、既知の
   汚染日があるだけで CI やバッチを止めない）。

使い方:
    PYTHONPATH=. .venv/bin/python3 scripts/check_race_point_sanity.py --date 2026-07-23
    PYTHONPATH=. .venv/bin/python3 scripts/check_race_point_sanity.py --scan
    PYTHONPATH=. .venv/bin/python3 scripts/check_race_point_sanity.py --scan --from 2026-01-01
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection

RATIO_THRESHOLD = 0.5   # 直近日中央値のこの割合を下回れば異常とみなす
BASELINE_DAYS = 7        # 直近何日分を基準にするか
MIN_ENTRIES = 10         # 対象日のサンプルがこれ未満なら判定不能としてOK扱い（未収集等）


def check(target_date: str) -> tuple[bool, str]:
    """returns (is_ok, message)"""
    with get_connection() as conn:
        today_row = conn.execute(
            "SELECT AVG(e.race_point) avg_rp, COUNT(*) n FROM wt_entries e "
            "JOIN wt_races r ON e.race_key = r.race_key "
            "WHERE r.race_date = ? AND e.race_point IS NOT NULL",
            (target_date,)).fetchone()
        today_avg = today_row["avg_rp"]
        today_n = today_row["n"]

        baseline_rows = conn.execute(
            "SELECT r.race_date, AVG(e.race_point) avg_rp FROM wt_entries e "
            "JOIN wt_races r ON e.race_key = r.race_key "
            "WHERE r.race_date < ? AND e.race_point IS NOT NULL "
            "GROUP BY r.race_date ORDER BY r.race_date DESC LIMIT ?",
            (target_date, BASELINE_DAYS)).fetchall()

    if today_n is None or today_n < MIN_ENTRIES:
        return True, f"{target_date}: サンプル数不足(n={today_n})のため判定スキップ"

    baseline_values = [r["avg_rp"] for r in baseline_rows if r["avg_rp"] is not None]
    if len(baseline_values) < 3:
        return True, f"{target_date}: 基準日データ不足のため判定スキップ"

    baseline_median = statistics.median(baseline_values)
    if baseline_median <= 0:
        return True, f"{target_date}: 基準中央値が0のため判定スキップ"

    ratio = today_avg / baseline_median
    if ratio < RATIO_THRESHOLD:
        return False, (
            f"{target_date}: race_point異常検知 — 平均{today_avg:.2f}"
            f"（直近{len(baseline_values)}日中央値{baseline_median:.2f}の{ratio*100:.0f}%・"
            f"閾値{RATIO_THRESHOLD*100:.0f}%未満）n={today_n}"
        )
    return True, (
        f"{target_date}: race_point正常 — 平均{today_avg:.2f}"
        f"（直近{len(baseline_values)}日中央値{baseline_median:.2f}の{ratio*100:.0f}%）n={today_n}"
    )


def _daily_avgs(date_from: str | None, date_to: str | None) -> list[tuple[str, float, int]]:
    """日ごとの (開催日, 平均race_point, 件数)。読み取りのみ。"""
    where, params = "WHERE e.race_point IS NOT NULL", []
    if date_from:
        where += " AND r.race_date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND r.race_date <= ?"
        params.append(date_to)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT r.race_date d, AVG(e.race_point) avg_rp, COUNT(*) n "
            "FROM wt_entries e JOIN wt_races r ON e.race_key = r.race_key "
            f"{where} GROUP BY r.race_date ORDER BY r.race_date", params).fetchall()
    return [(str(r["d"]), float(r["avg_rp"]), int(r["n"])) for r in rows
            if r["avg_rp"] is not None]


def anomalies(days: list[tuple[str, float, int]]
              ) -> list[tuple[str, float, float, int]]:
    """日ごとの平均から異常な開催日を拾う（純関数・2026-09-20 追加）。

    days: (開催日, 平均race_point, 件数) を**日付順**に並べたもの
    returns 異常と判定した (開催日, 平均, 基準中央値, 件数) の並び

    🔴 **規則は日次ゲートと同じもの**（直近 `BASELINE_DAYS` 日の中央値の
       `RATIO_THRESHOLD` 未満）。別の規則を書くと「日次は通ったのに
       遡ると異常」という説明できない状態になる。
    """
    out = []
    for i, (day, avg, n) in enumerate(days):
        if n < MIN_ENTRIES:
            continue
        base = [a for _, a, _ in days[max(0, i - BASELINE_DAYS):i]]
        if len(base) < 3:
            continue
        med = statistics.median(base)
        if med > 0 and avg / med < RATIO_THRESHOLD:
            out.append((day, avg, med, n))
    return out


def scan(date_from: str | None = None, date_to: str | None = None
         ) -> list[tuple[str, float, float, int]]:
    """DB を引いて `anomalies` を掛ける（読み取りのみ）。"""
    return anomalies(_daily_avgs(date_from, date_to))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="その日だけを検査する（日次バッチ用）")
    ap.add_argument("--scan", action="store_true",
                    help="過去へ遡って同じ規則を掛ける（報告のみ・常に終了コード0）")
    ap.add_argument("--from", dest="date_from", help="--scan の開始日")
    ap.add_argument("--to", dest="date_to", help="--scan の終了日")
    args = ap.parse_args()

    if args.scan:
        hits = scan(args.date_from, args.date_to)
        if not hits:
            print("[race_point_sanity] 遡及検査: 異常な開催日はありません")
        else:
            print(f"[race_point_sanity] 遡及検査: {len(hits)} 日に異常の疑い")
            for day, avg, med, n in hits:
                print(f"  {day}  平均 {avg:6.2f}  直近中央値 {med:6.2f}  "
                      f"({avg / med * 100:3.0f}%)  n={n}")
            print("  ⚠️ 自動では直らない（再取得は結果の入った行をスキップする）。"
                  "扱いは監査 item5 を参照。")
        sys.exit(0)                    # 🔴 見つけるための道具。止めるための道具ではない

    if not args.date:
        ap.error("--date か --scan のどちらかが要ります")
    is_ok, message = check(args.date)
    print(f"[race_point_sanity] {message}")
    sys.exit(0 if is_ok else 1)


if __name__ == "__main__":
    main()
