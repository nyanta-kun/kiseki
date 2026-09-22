#!/usr/bin/env python3
"""型ラボの朝の生成を**1プロセスで**まとめて行う（2026-09-22 新設）。

    python scripts/type_lab_morning.py [--date YYYY-MM-DD]

  1. 7車の買い目（mode='live'）
  2. 9車の買い目（mode='live9'）
  3. 表示用の型（`race_shapes`・全車数）
  4. 出走表の指数（`wt_entries.pred_win_pct / pred_top2_pct / pred_top3_pct`）

## なぜ1本にまとめるのか

上の4つは**同じ日の同じ特徴量**しか要らないのに、別々のプロセスで動いていたため
`build_features_wt` が毎回走っていた。VPS 実測（2026-09-22 朝）:

    wave-picks-wt（旧ランク・4の書き手だった）   7分16秒
    build_type_lab_picks --n-entries 7           6分46秒
    build_race_shapes                            6分49秒
    ------------------------------------------------------
    朝バッチ全体                                24分52秒

特徴量構築は1回およそ6分50秒で、**朝の所要のほとんどがその重複**だった。
`build_day_features` のプロセス内キャッシュに乗せると1回で済む。

🔴 **4 は旧ランクの `wave-picks-wt` から移したもの。** 旧ランクは 2026-08-28 に
   全て入稿 OFF になったが、出走表の指数を書いているのが唯一この経路だったため
   止められずにいた。値の出どころが `lgbm_wt` から型ラボと同じ `lgbm_wt_eval` へ
   変わる（`predict_index_pct` の docstring 参照）。

⚠️ **失敗の伝播は `type_lab_daily.sh` と同じにしてある。** 7車の生成だけが
   致命的で、9車・型・指数は失敗しても記録して先へ進む（入稿と採点を巻き添えに
   しない）。終了コードは7車が落ちたときだけ 1。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.build_race_shapes as shapes          # noqa: E402
import scripts.build_type_lab_picks as B            # noqa: E402


def _stamp(msg: str) -> None:
    from datetime import datetime
    print(f"[type_lab_morning] {datetime.now():%F %T} {msg}", flush=True)


def _build_picks(day: str, n_entries: int, dry_run: bool = False) -> int:
    """`build_type_lab_picks` の live をモジュール経由で1回分回す。"""
    B.N_ENTRIES = n_entries
    B.MODE_TAG = "" if n_entries == 7 else str(n_entries)
    B.ONLY_KEYS = None
    rows = B.run_live(day)
    return len(rows) if dry_run else B.save(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--skip-index", action="store_true",
                    help="出走表の指数を書かない（生成だけ試したいとき）")
    ap.add_argument("--dry-run", action="store_true",
                    help="1件も保存せずに件数だけ出す。**当日の行を書き換えずに**"
                         "経路が通るかを確かめるために使う（既に入稿した買い目を"
                         "UPSERT で書き換える事故を避ける）")
    a = ap.parse_args()
    day = a.date or date.today().isoformat()
    if a.dry_run:
        _stamp("dry-run: 1件も保存しません")

    _stamp(f"build live {day}（7車）")
    n7 = _build_picks(day, 7, a.dry_run)
    _stamp(f"7車 {n7} 行")

    _stamp(f"build live9 {day}（9車）")
    try:
        n9 = _build_picks(day, 9, a.dry_run)
        _stamp(f"9車 {n9} 行")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _stamp("⚠️ 9車の生成に失敗（7車の入稿・採点は続行する）")

    _stamp("build shapes（表示用の型・全車数）")
    try:
        rows = shapes.build(day)
        _stamp(f"型 {len(rows) if a.dry_run else shapes.save(rows)} 行")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _stamp("⚠️ 型判定（表示用）の生成に失敗（入稿・採点は続行する）")

    if not a.skip_index:
        _stamp("write index（出走表の1着率・2着内率・3着内率）")
        try:
            idx = B.predict_index_pct(day)
            _stamp(f"指数 {len(idx) if a.dry_run else B.save_index_pct(idx)} 行")
            if a.dry_run and idx:
                _stamp(f"  先頭3件: {idx[:3]}")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            _stamp("⚠️ 指数の書き込みに失敗（入稿・採点は続行する）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
