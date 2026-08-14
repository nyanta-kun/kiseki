"""中央競馬(JRA) モデル検証の「正規プロトコル」定義。

2026-08-14 の監査（`docs/jra_rebuild_2026_08.md` 7章）で、中央には
**TRAIN/VAL/TEST の取り決めも TEST 開封台帳も存在しない**ことが判明した。
スクリプトごとに境界がばらばらで、`jra_verify_signals.py` に至っては
他のスクリプトが valid と呼ぶ期間を test と呼んでいた。
加えて 2026年の窓は v27 の合成係数選定・着外率の閾値選定・ランキング再設計提案・
本監査と、既に何度も採否判断に使われている（＝焼けている）。

運用ルール（地方 `chihou_protocol.py` / 競輪の「正規プロトコル」を移植）:
  1. 学習には TRAIN_END までのデータのみ使用する
  2. 条件探索・A/Bスイープは VAL_START〜VAL_END の期間で繰り返し試してよい
     （ただしこの期間は既に多重比較を経ているため、ここで得た結論だけで
     「効いた」と断定しないこと）
  3. TEST_START 以降は「一度きり評価」用。条件探索・閾値スイープに使い回さない。
     使った場合は record_test_usage() で使用履歴を残し、以後は同じ期間を
     再利用しないこと（burned として扱う）

## 地方と違い **四半期** ローリングにしている

中央は開催が週2日で **年 約3,460レース（月 約288レース）**しかない。
指数1位馬の勝率（約28%）を月次 TEST で測ると標準誤差が約 2.6pt、
tier S に絞ると母集団が月 約55レースまで落ちて約 6.5pt になる。
**改善の実効サイズ（0.4〜0.5pt）を月次では原理的に判定できない。**
四半期なら約865レースで標準誤差 約1.5pt。半期（約1.1pt）はさらに鋭いが、
中央は季節性（夏の小倉・冬の中山等）が強く窓ごとに開催地が偏るため四半期とした。

代償として**本番モデルは最大3か月古くなる**。四半期ごとの
`scripts/jra_quarterly_rollover.py` で作り直す。
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

# 学習に使ってよい期間の上限（この日以前のデータのみで学習する）。
# 既存スクリプト（train_jra_reg_rank / train_jra_out_rate / jra_rank_quality_review）が
# 事実上の標準として使ってきた境界に合わせた。**ここは動かさない。**
TRAIN_END: str = "20250630"

# 条件探索・A/Bスイープに使ってよい期間の開始日
VAL_START: str = "20250701"


def _quarter_start(d: datetime.date) -> datetime.date:
    """d が属する四半期の初日。"""
    return datetime.date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


def _default_test_start(today: datetime.date | None = None) -> str:
    """一度きり評価用 test 期間の開始日 = **当四半期の初日**。

    Why:
      TEST_START を固定すると本番モデルの学習終端（= TEST_START の前日）も
      固定され、**四半期を追うごとにモデルが古くなる**。かといって都度手で
      動かすと忘れる。「当四半期は未使用のまま残し、前四半期までは学習に使う」を
      日付から導けば状態を持たずに自動更新できる。

    再現性のため `JRA_TEST_START=YYYYMMDD` で固定できる
    （過去の分析を当時の境界で再現したいときに使う）。
    """
    return _quarter_start(today or datetime.date.today()).strftime("%Y%m%d")


TEST_START: str = os.getenv("JRA_TEST_START") or _default_test_start()

# 探索に使ってよい期間の終わり = TEST_START の前日（TEST_START に追随して伸びる）
VAL_END: str = (
    datetime.datetime.strptime(TEST_START, "%Y%m%d").date() - datetime.timedelta(days=1)
).strftime("%Y%m%d")

# 本番モデルの学習終端。TEST を汚さないため TEST_START の前日まで。
TRAIN_DATA_END: str = VAL_END

# 2026年の窓を使い回した過去の意思決定（2026-08-14 監査時点で判明分・網羅ではない）。
# いずれも「honest test 2026-01〜08」と称して同じ期間を評価に使っている。
BURNED_DECISIONS: list[tuple[str, str]] = [
    ("v27 合成係数 V27_OUT_WEIGHT=0.5 の選定", "composite.py（honest 2窓で比較）"),
    ("着外率の足切り閾値 OUT_PROB_CUTOFF=0.80 の選定", "train_jra_out_rate.py"),
    ("v26→v27 の目的関数変更（LambdaRank → 順位回帰）の採否", "train_jra_reg_rank.py"),
    ("ランキング品質の再設計提案", "jra_rank_redesign_proposal.py"),
    ("recommend_rank の market_agree 第一分岐化", "confidence.py"),
    ("train/serve 不整合の監査（DM・馬場・馬体重）", "jra_train_serve_skew_audit.py"),
]

_LEDGER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "JRA_TEST_USAGE_LEDGER.md"


def record_test_usage(decision: str, script: str, note: str = "") -> None:
    """TEST_START 以降のデータを採否判断に使ったら必ず呼ぶこと。

    使用履歴を JRA_TEST_USAGE_LEDGER.md に追記し、以後の多重比較リスクを
    追跡可能にする。呼ぶだけで「この期間は既に使った」という事実が残るため、
    同じ test 期間を安易に何度も使う心理的抑止力にもする。
    """
    today = datetime.date.today().isoformat()
    line = f"- {today} `TEST_START={TEST_START}` **{script}**: {decision}"
    if note:
        line += f" — {note}"
    line += "\n"
    if not _LEDGER_PATH.exists():
        _LEDGER_PATH.write_text(
            "# JRA TEST_START 使用履歴台帳\n\n"
            "TEST_START（当四半期の初日・四半期ローリング）以降のデータを\n"
            "採否判断に使った記録。同一期間を条件探索に使い回さないための追跡台帳。\n\n"
            "定義は `backend/src/jra_protocol.py`。\n\n"
        )
    with _LEDGER_PATH.open("a") as f:
        f.write(line)


def describe() -> str:
    """現在の境界を1行で返す（レポート・ログ用）。"""
    return (
        f"TRAIN ≤{TRAIN_END} / VAL {VAL_START}〜{VAL_END} / TEST {TEST_START}〜 "
        f"(本番学習終端 {TRAIN_DATA_END})"
    )
