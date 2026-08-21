"""競輪 モデル検証の「正規プロトコル」定義（2026-08-21 新設）。

## なぜ要るのか

中央は `backend/src/jra_protocol.py`、地方は `chihou_protocol.py` を持つのに
**競輪だけ両方とも無かった**。窓はハーネスごとにベタ書き
（`scripts/exp_form_features_ab.WINDOWS = {w1..w4}`）で、**暦が進んでも TEST が
前進しない**。その結果 2026-08-21 に **w1〜w6 を1日で使い切った**
（[[keirin_n3_audit_round4_2026_08_21]]）。

`TRAIN_FROM` が S/B ラベルの都合で 2024-04-01 に固定されているため、
**古い方向へは窓を作れない**。前進させる以外に手が無い。

## 運用ルール（中央・地方と同じ）

1. 学習は **expanding**（`TRAIN_FROM` 〜 その窓の直前）。競輪は walk-forward が
   既定なので、中央のような固定 `TRAIN_END` は置かない
2. 条件探索・A/Bスイープは **VAL**（`TRAIN_FROM`〜`TEST_START` の前日）で
   何度でも試してよい。ただし**この期間は既に多重比較を経ている**ので、
   ここで得た結論だけで「効いた」と断定しない
3. **TEST_START 以降は一度きり評価**。使ったら `record_test_usage()` を呼ぶ

## 🔴 競輪固有: **1窓の合否で判定してはいけない**

追っている効果量は **+0.3pt 級**で、窓ごとのばらつきが**同じ大きさ**ある。
2026-08-21 の Elo残差の実測（Δ二軸・6窓）:

    +0.27 / +0.42 / +0.39 / +0.13 / +0.22 / +0.55   → 平均 +0.33pt・**SD 0.15pt**

さらに、**ウォームアップ期間を変えただけ**で w5 が +0.22 → −0.01 に反転した
（[[keirin_orphan_signals_ab_2026_08_21]]）。1窓 ≒ 5,500レースでは
標準誤差が効果量と同オーダーになる。

    必要窓数 k のときの標準誤差 ≒ 0.15 / √k
      k=1 → 0.15pt（効果 0.3pt に対し 2σ・実際に符号が反転した）
      k=2 → 0.11pt
      k=4 → **0.075pt**（効果 0.3pt に対し 4σ）

→ **`MIN_TEST_WINDOWS = 4`**。四半期ローリングなので **1年ぶんの TEST が要る**。
   これは厳しいが、**これ未満で出した結論は今日1日で3回ひっくり返っている**。

⚠️ **窓を増やせない代わりに効果量の大きい変更を狙うこと。** +0.3pt を追うと
   1年待つ設計になる。買い方・商品側の変更（N-7 の足切り +3〜4pt、
   7M1 の EV順 +0.30件/日）は1〜2窓で判定できる大きさがある。

## TEST の粒度が四半期な理由

7車レースは月 約2,000本で、1四半期の TEST 窓が約 5,500レース（実測 w5 n=5,562）。
月次にすると 1,800レースで SD が 0.26pt まで開き、`MIN_TEST_WINDOWS` を
12（＝1年）に増やしても総情報量は変わらない。窓の管理コストだけ増える。
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

#: 学習データの下限。**動かさない。** S/B ラベル（2024-01〜）の 90日窓が
#: 充足する時点で、`exp_form_features_ab.TRAIN_FROM` と同値。
#: 🔴 ここより古い方向へ TEST 窓を作ることはできない。
TRAIN_FROM: str = "2024-04-01"


def _quarter_start(d: datetime.date) -> datetime.date:
    """d が属する四半期の初日。"""
    return datetime.date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


def _default_test_start(today: datetime.date | None = None) -> str:
    """当四半期の初日。暦が進めば TEST も自動で前進する。"""
    return _quarter_start(today or datetime.date.today()).isoformat()


#: 一度きり評価に使う期間の開始日（四半期ローリング）。
#: `KEIRIN_TEST_START=YYYY-MM-DD` で固定できる（過去分析の再現用）。
TEST_START: str = os.getenv("KEIRIN_TEST_START") or _default_test_start()

#: 条件探索・A/Bに使ってよい期間の終わり（TEST_START の前日）。
VAL_END: str = (
    datetime.date.fromisoformat(TEST_START) - datetime.timedelta(days=1)
).isoformat()

#: 🔴 **採否を1窓で決めない。** 根拠は本モジュール冒頭。
MIN_TEST_WINDOWS: int = 4

#: 🔴 **既に採否判断へ使った（＝焼けた）窓。** ここに重なる期間で新しい結論を
#: 出してはいけない。`assert_not_burned()` が機械的に弾く。
BURNED_WINDOWS: list[tuple[str, str, str]] = [
    # (開始, 終了, 何に使ったか)
    ("2026-04-13", "2026-07-15", "w1: N-2 特徴量A/B・誤り②回収A/B の掃引窓"),
    ("2026-01-01", "2026-04-12", "w2: 同上"),
    ("2025-10-01", "2025-12-31", "w3: N-2 の確認窓・Elo残差の一次判定"),
    ("2025-07-01", "2025-09-30", "w4: 同上"),
    ("2025-04-01", "2025-06-30", "w5: Elo残差の Δ二軸 追加判定（2026-08-21）"),
    ("2025-01-01", "2025-03-31", "w6: 同上"),
]

_LEDGER_PATH = Path(__file__).resolve().parents[1] / "docs" / "KEIRIN_TEST_USAGE_LEDGER.md"


def clean_test_start() -> str:
    """**焼けた窓と重ならない**最初の TEST 開始日。

    🔴 `TEST_START`（当四半期の初日）が焼けた窓に食い込むことがある。
       実際 2026-08-21 時点で当四半期は 2026-07-01 始まりだが、w1 の探索が
       **2026-07-15 まで伸びている**ので、そのまま使うと探索済みの2週間を
       一度きり評価に混ぜてしまう。
    ⚠️ 返るのは「焼けの翌日」であって四半期の境界ではない。
       窓を四半期で揃えたい場合は、次の四半期初日まで待つこと。
    """
    cur = TEST_START
    for _ in range(len(BURNED_WINDOWS) + 1):
        hit = None
        for b0, b1, _why in BURNED_WINDOWS:
            if cur <= b1 and cur >= b0:
                hit = b1
                break
        if hit is None:
            return cur
        cur = (datetime.date.fromisoformat(hit)
               + datetime.timedelta(days=1)).isoformat()
    return cur


def is_burned(start: str, end: str) -> str | None:
    """`start`〜`end` が焼けた窓と重なるなら、その説明を返す。無ければ None。"""
    for b0, b1, why in BURNED_WINDOWS:
        if start <= b1 and end >= b0:
            return f"{b0}〜{b1}（{why}）"
    return None


def assert_not_burned(start: str, end: str, *, who: str = "") -> None:
    """焼けた窓で採否判断をしようとしたら止める。

    🔴 **警告ではなく例外**。競輪は 2026-08-21 に「発見と検証に同じ窓を使う」
       誤りを2回踏んでおり（[[keirin_verification_design_audit_2026_08_21]] /
       [[keirin_7s_gate_resweep_2026_08_21]]）、気づける仕組みが無かった。
    """
    hit = is_burned(start, end)
    if hit:
        raise SystemExit(
            f"{who or 'この検証'}: 期間 {start}〜{end} は既に採否判断へ使った窓と"
            f"重なります → {hit}\n"
            f"探索なら構いませんが、**結論を出すのに使ってはいけません**。\n"
            f"一度きり評価は TEST_START={TEST_START} 以降を使ってください"
            f"（`src/keirin_protocol.describe()`）。"
        )


def record_test_usage(decision: str, script: str, note: str = "") -> None:
    """TEST_START 以降のデータを採否判断に使ったら必ず呼ぶこと。"""
    today = datetime.date.today().isoformat()
    line = f"- {today} `TEST_START={TEST_START}` **{script}**: {decision}"
    if note:
        line += f" — {note}"
    line += "\n"
    if not _LEDGER_PATH.exists():
        _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LEDGER_PATH.write_text(
            "# 競輪 TEST_START 使用履歴台帳\n\n"
            "TEST_START（当四半期の初日・四半期ローリング）以降のデータを\n"
            "採否判断に使った記録。同一期間を条件探索に使い回さないための追跡台帳。\n\n"
            "定義は `keirin/src/keirin_protocol.py`。\n"
            "🔴 採否は **MIN_TEST_WINDOWS(=4) 窓の合議**で決めること（1窓では"
            "効果量とばらつきが同オーダー）。\n\n"
        )
    with _LEDGER_PATH.open("a") as f:
        f.write(line)


def describe() -> str:
    """現在の境界を1行で返す（レポート・ログ用）。"""
    clean = clean_test_start()
    tail = "" if clean == TEST_START else f" ⚠️ 焼けを避けると {clean}〜"
    return (f"TRAIN {TRAIN_FROM}〜 / VAL 〜{VAL_END} / TEST {TEST_START}〜{tail} "
            f"(採否は {MIN_TEST_WINDOWS} 窓の合議・焼けた窓 {len(BURNED_WINDOWS)}件)")


if __name__ == "__main__":
    print(describe())
    print(f"焼けを避けた TEST 開始日: {clean_test_start()}")
    print("\n焼けた窓:")
    for b0, b1, why in BURNED_WINDOWS:
        print(f"  {b0}〜{b1}  {why}")
