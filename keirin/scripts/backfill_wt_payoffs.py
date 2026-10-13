#!/usr/bin/env python3
"""公式払戻（`wt_payoffs`）を過去ぶん取り込む（2026-10-13 新設）。

    python scripts/backfill_wt_payoffs.py --scope sold            # 売った商品＋型ラボ live の的中
    python scripts/backfill_wt_payoffs.py --scope all --from 2025-01-01

採点は 2026-10-13 から公式払戻だけで払戻を作る（`src/official_payoffs.py`）。
それ以前の的中は `wt_payoffs` に行が無いので、取り込むまで**採点し直すと保留**になる。
取り込みが要るのは**払戻を使うレースだけ**（＝当たりのあるレース。外れは着順で確定する）:

- `sold`: 入稿した全レース（Web の実売集計はキャッシュを作り直すので全部要る）＋
  型ラボ live / live9 の的中レース
- `all`: 上に加えて型ラボ paper / paper9 の的中レース

新しい日付から順に取る（途中で止めても直近が先に埋まる）。取り込み済みは
`official_payoffs.collect` が飛ばすので、何度流してもよい。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import official_payoffs  # noqa: E402
from src.database import get_connection  # noqa: E402


def _targets(scope: str, date_from: str, date_to: str) -> list[str]:
    modes = ("live", "live9") if scope == "sold" else ("live", "live9", "paper", "paper9")
    with get_connection() as c:
        keys = {str(r[0]) for r in c.execute(
            "SELECT DISTINCT ns.race_key FROM netkeirin_submissions ns "
            "JOIN wt_races wr ON wr.race_key = ns.race_key "
            "WHERE ns.deleted_at IS NULL AND wr.race_date BETWEEN ? AND ?",
            (date_from, date_to)).fetchall()}
        ph = ",".join("?" * len(modes))
        keys |= {str(r[0]) for r in c.execute(
            f"SELECT DISTINCT race_key FROM type_lab_picks WHERE hit AND mode IN ({ph}) "
            "AND race_date BETWEEN ? AND ?", (*modes, date_from, date_to)).fetchall()}
    return sorted(keys, reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=("sold", "all"), default="sold")
    ap.add_argument("--from", dest="date_from", default="2025-01-01")
    ap.add_argument("--to", dest="date_to", default="2100-01-01")
    ap.add_argument("--chunk", type=int, default=200, help="進捗を出す単位（レース数）")
    a = ap.parse_args()

    keys = _targets(a.scope, a.date_from, a.date_to)
    todo = official_payoffs.missing(keys)
    print(f"対象 {len(keys)} レース / 未取り込み {len(todo)} レース", flush=True)
    total: dict[str, int] = {}
    for i in range(0, len(todo), a.chunk):
        st = official_payoffs.collect(todo[i:i + a.chunk])
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
        print(f"  {min(i + a.chunk, len(todo))}/{len(todo)} {todo[i][:8]}〜 {st}", flush=True)
    print(f"完了 {total}")


if __name__ == "__main__":
    main()
