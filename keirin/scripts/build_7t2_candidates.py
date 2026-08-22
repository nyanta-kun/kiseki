#!/usr/bin/env python3
"""RANK_7T2（三連単・一撃枠・**ペーパー並走中**）の候補を生成する。

## 7T1 との関係

**買い目の作り方は 7T1 と完全に同じ**（`build_7t1_candidates.build` をそのまま呼ぶ）。
違うのは3点だけで、いずれも `src/strategy_wt.py` の RANK_7T2 セクションが正本:

  - 母集団: 決勝系×別ライン → **全7車**
  - 日次上限: 5 → **20**
  - 目標払戻: 15万円 → **20万円**（= 上限20件 × 1レース1万円）

🔴 **目標払戻 = 日次上限 × 10,000 は設計原理であって偶然ではない。**
   1レース1万円・1日N件なら 1件の的中で日次100%を超える条件が「払戻 >= N万円」。
   片方だけ動かさないこと。

⚠️ **入稿しない。** `netkeirin_submit_wt.RANK_CONFIGS` には登録していない
   （2026-08-22 ユーザー判断・前向きの実績を見てから決める）。
   検査は `tests/test_rank_7t2.py::test_7t2は入稿対象に入っていない`。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/build_7t2_candidates.py --date 2026-08-23
    # 過去日を honest に作る場合は月次vintageを明示する
    #   --eval-model lgbm_wt_eval_m2608 --win-model lgbm_wt_win_m2608

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.build_7t1_candidates import build  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7T2_TARGET_PAYOUT, rank_7t2_daily_select,
)
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402

SUFFIX = "s7t2"


def build_7t2(date_from: str, date_to: str, eval_model: str, win_model: str,
              require_model: bool = True) -> list[dict]:
    """7T2 の候補（選別後）。**7T1 と同じ生成器に別の目的値と母集団を渡すだけ**。"""
    return build(date_from, date_to, eval_model, win_model,
                 require_model=require_model,
                 target_payout=RANK_7T2_TARGET_PAYOUT,
                 daily_select=rank_7t2_daily_select,
                 label="7T2")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="対象日 YYYY-MM-DD")
    ap.add_argument("--out", default=None)
    ap.add_argument("--eval-model", default="lgbm_wt_eval")
    ap.add_argument("--win-model", default="lgbm_wt_win")
    ap.add_argument("--allow-missing-model", action="store_true")
    args = ap.parse_args()

    # 🔴 過去日を本番モデル（全期間学習）でスコアすると model-vintage look-ahead。
    assert_vintage_for_past(
        args.date, {"eval": args.eval_model, "win": args.win_model})

    cands = build_7t2(args.date, args.date, args.eval_model, args.win_model,
                      require_model=not args.allow_missing_model)
    path = Path(args.out) if args.out else (
        REPO / "data" / "picks" / f"wave_picks_wt_{args.date}_{SUFFIX}_candidates.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    print(f"[保存先] {path}  (7T2候補 {len(cands)}件)")


if __name__ == "__main__":
    main()
