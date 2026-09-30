#!/usr/bin/env python3
"""朝の出走表（AI印・並び・選手成績）を退避し、最終値とのずれを測る（2026-09-30 新設）。

🔴 なぜ要るか:
  `wt_entries` は `collect-wt` のたびに INSERT OR REPLACE で上書きされ、結果が確定するまで
  全列が最新値に置き換わる。**モデルの学習は発走後の最終値、配信は朝07時の値**を読むのに、
  朝の値がどこにも残らないため、この train/serve skew の大きさを事後に測れなかった
  （2026-09-30 総合監査 X1 の P1。AI印 `prediction_mark` は gain 1位の入力）。

  そこで朝の `collect-wt` 直後に、**発走前に意味を持つ列だけ**をファイルへ退避する。
  DB に新しいテーブルを作らない（DDL は Alembic 経由のみ＝shared 柱の作業になるため）。

保存先: data/snapshots/wt_entries_morning/YYYY-MM-DD.csv.gz（既にあれば上書きしない＝初回値を保持）

使い方:
  # 取得（cron・既定は当日）
  PYTHONPATH=. .venv/bin/python3 scripts/snapshot_morning_entries_wt.py [YYYY-MM-DD]

  # 計測（朝の退避 vs 現在の wt_entries）
  PYTHONPATH=. .venv/bin/python3 scripts/snapshot_morning_entries_wt.py --report --from YYYY-MM-DD --to YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import csv
import gzip
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots" / "wt_entries_morning"

#: 退避する列。結果（finish_order / factor / res_* / final_half）と自社の予測値
#: （pred_*_pct）は発走前の入力ではないので含めない。
KEY_COLS: tuple[str, ...] = ("race_key", "frame_no")
SNAP_COLS: tuple[str, ...] = (
    "player_id", "player_class", "gear_ratio", "style", "race_point",
    "prediction_mark",
    "s_count", "h_count", "b_count",
    "front_runner", "stalker", "deep_closer", "marker",
    "first_rate", "second_rate", "third_rate",
    "ex_spurt_pct", "ex_thrust_pct", "ex_left_behind_pct", "ex_split_line_pct", "ex_snatch_pct",
    "line_group", "line_size", "line_pos", "is_line_leader", "n_lines",
)
#: レース単位で「並びが変わった」とみなす列。
LINE_COLS: tuple[str, ...] = ("line_group", "line_size", "line_pos", "is_line_leader", "n_lines")


def snapshot_path(target_date: str) -> Path:
    """target_date の退避ファイルのパスを返す。"""
    return SNAPSHOT_DIR / f"{target_date}.csv.gz"


def _fetch(date_from: str, date_to: str) -> list[dict]:
    cols = ", ".join(f"e.{c}" for c in (*KEY_COLS, *SNAP_COLS))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT r.race_date, {cols} FROM wt_entries e "
            "JOIN wt_races r ON r.race_key = e.race_key "
            "WHERE r.race_date BETWEEN ? AND ? "
            "ORDER BY e.race_key, e.frame_no",
            (date_from, date_to),
        ).fetchall()
    return [dict(r) for r in rows]


def write_snapshot(rows: list[dict], path: Path, captured_at: str) -> int:
    """rows を gzip CSV に書く。既にファイルがあれば何もしない（初回＝朝の値を保持）。

    Returns:
        書いた行数（既存ファイルがあった場合は 0）。
    """
    if path.exists():
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fields = ["captured_at", *KEY_COLS, *SNAP_COLS]
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({"captured_at": captured_at, **r})
    tmp.replace(path)
    return len(rows)


def read_snapshot(path: Path) -> list[dict]:
    """退避ファイルを読む（値はすべて文字列。空欄は ''）。"""
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _norm(v: object) -> str:
    """DB 値と CSV 値を同じ土俵で比べるための正規化（None→''、数値は float 表記）。"""
    if v is None:
        return ""
    s = str(v).strip()
    try:
        return repr(float(s))
    except ValueError:
        return s


def compare(morning: list[dict], final: list[dict]) -> dict:
    """朝と最終を (race_key, frame_no) で突き合わせ、列ごと・レースごとの変化を数える。

    Returns:
        {"n_rows", "n_races", "col_changed": {列: 件数}, "col_filled": {列: 朝は空で最終は値あり},
         "races_mark_changed", "races_line_changed", "races_missing_line_morning"}
    """
    fin = {(r["race_key"], int(r["frame_no"])): r for r in final}
    col_changed: dict[str, int] = defaultdict(int)
    col_filled: dict[str, int] = defaultdict(int)
    race_mark: dict[str, bool] = {}
    race_line: dict[str, bool] = {}
    race_line_missing: dict[str, bool] = {}
    n = 0
    for m in morning:
        key = (m["race_key"], int(m["frame_no"]))
        f = fin.get(key)
        if f is None:
            continue
        n += 1
        rk = m["race_key"]
        race_mark.setdefault(rk, False)
        race_line.setdefault(rk, False)
        race_line_missing.setdefault(rk, False)
        for c in SNAP_COLS:
            a, b = _norm(m.get(c)), _norm(f.get(c))
            if a == b:
                continue
            if a == "" and b != "":
                col_filled[c] += 1
            else:
                col_changed[c] += 1
            if c == "prediction_mark":
                race_mark[rk] = True
            if c in LINE_COLS:
                race_line[rk] = True
        if _norm(m.get("n_lines")) in ("", "0.0"):
            race_line_missing[rk] = True
    return {
        "n_rows": n,
        "n_races": len(race_mark),
        "col_changed": dict(col_changed),
        "col_filled": dict(col_filled),
        "races_mark_changed": sum(race_mark.values()),
        "races_line_changed": sum(race_line.values()),
        "races_missing_line_morning": sum(race_line_missing.values()),
    }


def snapshot(target_date: str) -> None:
    """target_date の出走表を退避する。"""
    path = snapshot_path(target_date)
    if path.exists():
        print(f"[entries-snapshot] {target_date}: 既に退避済み（初回値を保持）: {path}")
        return
    rows = _fetch(target_date, target_date)
    n = write_snapshot(rows, path, datetime.now().isoformat(timespec="seconds"))
    n_races = len({r["race_key"] for r in rows})
    print(f"[entries-snapshot] {target_date}: {n:,} 行 / {n_races:,} レースを退避 → {path}")


def report(date_from: str, date_to: str) -> None:
    """期間内の退避ファイルと現在の wt_entries を比べて表示する。"""
    d, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    morning: list[dict] = []
    days = 0
    while d <= end:
        p = snapshot_path(d.isoformat())
        if p.exists():
            morning.extend(read_snapshot(p))
            days += 1
        d += timedelta(days=1)
    if not morning:
        print("[report] 退避ファイルがありません。")
        return
    res = compare(morning, _fetch(date_from, date_to))
    nr, ne = res["n_races"], res["n_rows"]
    print(f"朝の出走表 → 最終値  {date_from}〜{date_to}（{days}日・{nr:,}R・{ne:,}車）")
    print(f"  AI印が1車でも変わったレース: {res['races_mark_changed']:,} ({res['races_mark_changed'] / max(nr, 1):.1%})")
    print(f"  並びが1車でも変わったレース: {res['races_line_changed']:,} ({res['races_line_changed'] / max(nr, 1):.1%})")
    print(f"  朝に並びが空だったレース:   {res['races_missing_line_morning']:,} ({res['races_missing_line_morning'] / max(nr, 1):.1%})")
    print(f"\n  {'列':<20}{'値が変化':>10}{'率':>8}{'朝は空→埋まる':>14}")
    for c in SNAP_COLS:
        ch, fl = res["col_changed"].get(c, 0), res["col_filled"].get(c, 0)
        if ch or fl:
            print(f"  {c:<20}{ch:>10,}{ch / max(ne, 1):>8.1%}{fl:>14,}")


def main() -> None:
    """CLI エントリ。"""
    ap = argparse.ArgumentParser(description="朝の出走表の退避と、最終値とのずれの計測")
    ap.add_argument("target_date", nargs="?", default=None, help="退避する日 YYYY-MM-DD（既定: 当日）")
    ap.add_argument("--report", action="store_true", help="朝の退避と現在の値を比べる")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    args = ap.parse_args()
    if args.report:
        today = date.today().isoformat()
        report(args.date_from or today, args.date_to or today)
    else:
        snapshot(args.target_date or date.today().isoformat())


if __name__ == "__main__":
    main()
