"""地方競馬(chihou) モデル検証の「正規プロトコル」定義。

2026-07-23 監査（memory: chihou_survivor_bias_audit_2026_07_23）で、v9→v12の各改修
（外部指数採用・馬場特徴・コーナー/調教師特徴・Optunaハイパラ確認等）が
ほぼ同一のholdout期間（2025-07〜）を6回以上使い回しており、多重比較過学習の
リスクが中〜高と判定された。この反省から、以後の特徴量・閾値の採否判断は
必ずこのモジュールの定数を使うこと。

運用ルール（keirinの「正規プロトコル」を移植）:
  1. 学習には TRAIN_END までのデータのみ使用する
  2. 条件探索・A/Bスイープは VAL_START〜VAL_END の期間で繰り返し試してよい
     （ただしこの期間は既に多重比較を経ているため、ここで得た結論だけで
     「黒字/赤字」を断定しないこと）
  3. TEST_START 以降は「一度きり評価」用。条件探索・閾値スイープに使い回さない。
     使った場合は record_test_usage() で使用履歴を残し、以後は同じ期間を
     再利用しないこと（burnedとして扱う）
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

# 学習に使ってよい期間の上限（この日以前のデータのみで学習する）。
# 検証スクリプトが honest 再学習をするときの固定境界。ここは動かさない。
TRAIN_END: str = "20250630"

# 条件探索・A/Bスイープに使ってよい期間の開始日
# （2026-07-23監査時点で「既に焼けている」区間）
VAL_START: str = "20250701"


def _default_test_start(today: datetime.date | None = None) -> str:
    """一度きり評価用 test 期間の開始日 = **当月1日**。

    2026-08-03 に固定値 "20260701" から月次ローリングへ変更した。

    Why:
      TEST_START を固定すると、本番モデルの学習終端（= TEST_START の前日）も
      固定され、**モデルが月を追うごとに古くなる**。かといって都度手で動かすと
      忘れる。「当月は未使用のまま残し、先月までは学習に使う」を日付から導けば
      状態を持たずに月次で自動更新できる。

    運用（`scripts/chihou_monthly_rollover.py` が毎月1日に実行）:
      1. 先月を **その時点でデプロイ済みのモデル**で一度きり評価し台帳に記録
         （そのモデルは先々月までで学習されているので評価は honest）
      2. 先月までを含めて再学習
      3. デプロイ後に v13 を再バックフィル

    再現性のため `CHIHOU_TEST_START=YYYYMMDD` で固定できる
    （過去の分析を当時の境界で再現したいときに使う）。
    """
    d = today or datetime.date.today()
    return d.replace(day=1).strftime("%Y%m%d")


TEST_START: str = os.getenv("CHIHOU_TEST_START") or _default_test_start()

# 探索に使ってよい期間の終わり = TEST_START の前日（TEST_START に追随して伸びる）
VAL_END: str = (
    datetime.datetime.strptime(TEST_START, "%Y%m%d").date() - datetime.timedelta(days=1)
).strftime("%Y%m%d")

# VAL_START〜today を使い回した過去の意思決定履歴（2026-07-23 監査時点で判明分・網羅ではない）
BURNED_DECISIONS: list[tuple[str, str]] = [
    ("Phase1 線形 vs LGB 採用判断", "chihou_model_compare.py"),
    ("外部指数特徴 採用", "ab_chihou_external_features.py"),
    ("馬場特徴 採用", "ab_chihou_track_condition.py"),
    ("v11 市場乖離特徴 採用", "train_chihou_market_lgb.py"),
    ("v12 コーナー/調教師/乗替特徴 採用", "ab_chihou_corner_trainer.py"),
    ("Optuna ハイパラ 最終確認", "optuna_chihou_prod_lgb.py"),
]

_LEDGER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "CHIHOU_TEST_USAGE_LEDGER.md"


def record_test_usage(decision: str, script: str, note: str = "") -> None:
    """TEST_START以降のデータを採否判断に使ったら必ず呼ぶこと。

    使用履歴を CHIHOU_TEST_USAGE_LEDGER.md に追記し、以後の多重比較リスクを
    追跡可能にする。呼ぶだけで「この期間は既に使った」という事実が残るため、
    同じtest期間を安易に何度も使う心理的抑止力にもする。
    """
    today = datetime.date.today().isoformat()
    line = f"- {today} **{script}**: {decision}"
    if note:
        line += f" — {note}"
    line += "\n"
    if not _LEDGER_PATH.exists():
        _LEDGER_PATH.write_text(
            "# chihou TEST_START 使用履歴台帳\n\n"
            "TEST_START（当月1日・月次ローリング）以降のデータを採否判断に使った記録。"
            "同一期間を条件探索に使い回さないための追跡台帳。\n\n"
        )
    with _LEDGER_PATH.open("a") as f:
        f.write(line)
