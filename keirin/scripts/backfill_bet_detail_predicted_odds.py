"""既存の `netkeirin_submissions.bet_detail` で欠けている三連複オッズを予測値で埋める。

## なぜ要るか

板（`wt_odds` / 朝の `wt_odds_snapshot`）は買った目を必ずしも網羅せず、
欠けた点は `odds: null` で保存されていた。Web では「オッズ未取得」となり、
**最低払戻も期待値も出せない**（実測で三連複85点・18入稿が該当）。

予測オッズ（`src.odds_prediction`）は構造だけから作れるので、板が無い点の
表示を埋められる。以後の入稿は `netkeirin_submit_wt.build_bet_detail` が
入稿時に埋めるので、このスクリプトは**過去分の一度きりの補完**。

## 触らないもの

- 🔴 **板由来のオッズ（`odds_source="board"` / 既存の非 null）は上書きしない。**
  板があるならそれが実際に付いていた値。
- 🔴 **`stake` は一切触らない。** 金額配分は入稿時点の想定オッズで決まっており、
  ここで埋める値とは無関係（表示のためだけの補完）。
- ⚠️ **三連単は埋めない。** このモデルが予測するのは三連複だけ。
  着順の分だけ別物なので、作れないものを作らない。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_bet_detail_predicted_odds.py --dry-run
    PYTHONPATH=. .venv/bin/python scripts/backfill_bet_detail_predicted_odds.py
    PYTHONPATH=. .venv/bin/python scripts/backfill_bet_detail_predicted_odds.py --from-date 2026-08-01
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.odds_prediction import (  # noqa: E402
    OddsPredictionUnavailable,
    predicted_trio_board,
)

TRIO_LABEL = "3連複"


def _combo_key(combo: str) -> frozenset[int] | None:
    """`1=2=4` → frozenset。三連単（`-` 区切り）や壊れた値は None。

    ⚠️ 区切り文字が券種の区別そのもの。`-` を受け入れると三連単を
       三連複の盤面で埋めてしまう。
    """
    if "=" not in combo:
        return None
    try:
        cars = [int(x) for x in combo.split("=")]
    except ValueError:
        return None
    return frozenset(cars) if len(cars) == 3 else None


def fill_lines(detail: dict, board: dict) -> tuple[dict, int]:
    """`bet_detail` の欠けた三連複オッズを予測盤面で埋める。埋めた点数も返す。"""
    n = 0
    for line in detail.get("lines") or []:
        if line.get("odds") is not None:
            # 板由来。既存値は必ず残し、印だけ補う（過去分には印が無い）。
            line.setdefault("odds_source", "board")
            continue
        if line.get("bet_type") != TRIO_LABEL:
            continue  # 三連単は埋めない
        key = _combo_key(str(line.get("combo", "")))
        if key is None:
            continue
        o = board.get(key)
        if not o or o <= 0:
            continue
        line["odds"] = round(float(o), 1)
        line["odds_source"] = "predicted"
        n += 1
    return detail, n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-date", help="YYYY-MM-DD 以降のレースだけを対象にする")
    ap.add_argument("--dry-run", action="store_true", help="DBを更新せず件数だけ出す")
    args = ap.parse_args()

    # 🔴 **「埋める必要があるか」を SQL で判定しない。**
    #    古い行は `odds` キー自体が無く（オッズ保存前の形式）、
    #    `LIKE '%"odds": null%'` では**取りこぼす**（実測で 8/07 分がまるごと漏れた）。
    #    判定は `fill_lines()` の1箇所に持たせ、SQL は候補を広く取るだけにする。
    where = "bet_detail IS NOT NULL"
    params: list = []
    if args.from_date:
        where += " AND race_key >= ?"
        params.append(args.from_date.replace("-", "") + "_")

    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT race_key, rank_key, bet_detail FROM netkeirin_submissions "
            f"WHERE {where} ORDER BY race_key", params)]

    print(f"対象: {len(rows)} 入稿", flush=True)
    n_filled = n_rows = 0
    boards: dict[str, dict] = {}
    updates: list[tuple[str, str, str]] = []
    for r in rows:
        rk = str(r["race_key"]).split("#")[0]
        if rk not in boards:
            try:
                boards[rk] = dict(predicted_trio_board(rk))
            except OddsPredictionUnavailable as e:
                print(f"  {rk}: 予測盤面なし（skip）: {e}", flush=True)
                boards[rk] = {}
            except Exception as e:  # noqa: BLE001
                print(f"  {rk}: 想定外の失敗（skip）: {e!r}", flush=True)
                boards[rk] = {}
        try:
            detail = json.loads(r["bet_detail"])
        except (TypeError, ValueError):
            print(f"  {rk}/{r['rank_key']}: bet_detail が壊れています（skip）", flush=True)
            continue
        detail, n = fill_lines(detail, boards[rk])
        if n == 0:
            continue
        n_filled += n
        n_rows += 1
        updates.append((json.dumps(detail, ensure_ascii=False),
                        str(r["race_key"]), str(r["rank_key"])))
        print(f"  {rk}/{r['rank_key']}: {n}点を予測で補完", flush=True)

    if args.dry_run:
        print(f"dry-run: {n_rows} 入稿 / {n_filled} 点（DB更新なし）", flush=True)
        return 0

    if updates:
        with get_connection() as conn:
            for detail_json, race_key, rank_key in updates:
                conn.execute(
                    "UPDATE netkeirin_submissions SET bet_detail = ? "
                    "WHERE race_key = ? AND rank_key = ?",
                    (detail_json, race_key, rank_key))
            conn.commit()
    print(f"完了: {n_rows} 入稿 / {n_filled} 点を補完", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
