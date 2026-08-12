"""netkeirin へ入稿・取消できる時間帯の判定（2026-08-13 新設）。

## なぜ要るのか

netkeirin は **発走の15分前を過ぎると商品を出せない**（取消もできない）。
それまでは「発走済みかどうか」だけを見ていたため、発走13分前のレースへ
入稿を試みて netkeirin 側で弾かれる、承認ボタンを押せてしまう、といった
「押せるのに通らない」状態が残っていた。

## この判定が要る場所（3つ）

1. keirin の入稿バッチ（波ごと）— 締切を過ぎたレースは候補から外す
2. kiseki の承認API（`/keirin/approve` `/keirin/cancel`）— 締切後は 409 で拒む
3. Web の確認画面 — ボタンを無効化して理由を出す

🔴 **判定をコピーしてはいけない。** 3箇所で別々に書くと、締切だけ変えたときに
   「画面は押せるのに API が拒む」「バッチだけ古い締切で出す」が静かに起きる。
   本ファイルが**唯一の正本**で、keirin 側は `keirin/src/submit_window.py` が
   ファイルとして読み込んで束縛する（`keirin_marquee.py` と同じ方式）。

⚠️ **標準ライブラリ以外を import しないこと。** keirin は自分の venv
   （FastAPI も SQLAlchemy も無い）からこのファイルを直接読む。依存を足すと
   **Web は無事なまま入稿だけが落ちる**。
"""

from __future__ import annotations

# 発走の何秒前で締め切るか。netkeirin の仕様（発走15分前）に合わせる。
SUBMIT_DEADLINE_SEC = 15 * 60


def seconds_until_deadline(start_at: int | float | None, now: float) -> float | None:
    """締切まで残り何秒か。`start_at` が無ければ None。

    負の値は「締切を過ぎている」。
    """
    if start_at is None:
        return None
    try:
        return (float(start_at) - SUBMIT_DEADLINE_SEC) - float(now)
    except (TypeError, ValueError):
        return None


def is_closed(start_at: int | float | None, now: float) -> bool:
    """入稿・取消の締切を過ぎているか。

    🔴 **発走時刻が取れないレースは「締切前」扱い**（False）にする。
       情報が無いことを理由に商品を落とすと、黙って商品が消える。
       これは `_load_started_races` 以来の方針を引き継いだもの。
    """
    remain = seconds_until_deadline(start_at, now)
    if remain is None:
        return False
    return remain <= 0
