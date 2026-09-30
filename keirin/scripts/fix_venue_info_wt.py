#!/usr/bin/env python3
"""`venue_info` のバンク周長・屋内フラグを `VENUE_STATIC`（正本）へ揃える（2026-09-30）。

背景は `src/database.py` の `VENUE_STATIC` の注記。`init_db()` は INSERT OR IGNORE なので
正本を直しても既存行は変わらない。このスクリプトが差分だけを UPDATE する。

⚠️ `bank_length_enc` / `is_indoor` はモデル入力。**週次再学習（日曜 23:30）の直前に当てる。**

使い方:
  PYTHONPATH=. .venv/bin/python3 scripts/fix_venue_info_wt.py          # 差分を表示するだけ
  PYTHONPATH=. .venv/bin/python3 scripts/fix_venue_info_wt.py --apply  # 差分を UPDATE する
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import VENUE_STATIC, get_connection  # noqa: E402


def diff_rows(current: list[dict]) -> list[tuple[str, str, int | None, int, int | None, int]]:
    """DB の行と正本を比べ、食い違う場だけ返す。

    Returns:
        (venue_code, name, 現在の周長, 正しい周長, 現在の屋内, 正しい屋内) のリスト。
        正本に無い場コードは対象外（触らない）。
    """
    out = []
    for r in current:
        code = str(r["venue_code"])
        if code not in VENUE_STATIC:
            continue
        name, bank, indoor, _pref = VENUE_STATIC[code]
        cur_bank, cur_indoor = r["bank_length"], r["is_indoor"]
        if cur_bank != bank or (cur_indoor or 0) != indoor:
            out.append((code, name, cur_bank, bank, cur_indoor, indoor))
    return out


def main() -> None:
    """CLI エントリ。"""
    ap = argparse.ArgumentParser(description="venue_info を VENUE_STATIC へ揃える")
    ap.add_argument("--apply", action="store_true", help="差分を UPDATE する（既定は表示だけ）")
    args = ap.parse_args()

    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT venue_code, bank_length, is_indoor FROM venue_info ORDER BY venue_code")]
        diffs = diff_rows(rows)
        if not diffs:
            print("venue_info は正本と一致しています。")
            return
        print(f"{'場':>4} {'名称':<8}{'周長':>12}{'屋内':>10}")
        for code, name, cb, nb, ci, ni in diffs:
            print(f"{code:>4} {name:<8}{cb!s:>5} → {nb:<4}{ci!s:>4} → {ni}")
        print(f"\n{len(diffs)} 場が食い違っています。")
        if not args.apply:
            print("（表示だけ。直すには --apply）")
            return
        for code, _name, _cb, nb, _ci, ni in diffs:
            conn.execute("UPDATE venue_info SET bank_length = ?, is_indoor = ? WHERE venue_code = ?",
                         (nb, ni, code))
        print(f"{len(diffs)} 場を更新しました。")


if __name__ == "__main__":
    main()
