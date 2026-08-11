"""看板レース（決勝・特選クラス）の判定（2026-08-10 新設）。

## なぜ kiseki 側に置くのか

2026-08-09 に「看板レースには必ず推奨を出す」方針になり、
自動入稿（keirin リポジトリ `scripts/submit_marquee_wt.py`）と
Web一覧の★表示の**2箇所で同じ判定**が要るようになった。

初版はフロント（`frontend/src/app/keirin/page.tsx`）に TypeScript で
キーワードを写していたが、**判定が3言語・2リポジトリに散る**のは
このプロジェクトが繰り返し事故を起こしている型なので、
kiseki 側の正本をここに置き、API が `is_marquee` を返す形へ移した。

✅ **2026-08-11 に一本化済み。このファイルが唯一の正本。**
   入稿の実行側（`keirin/src/marquee.py`）はここをファイル読み込みして
   キーワードと判定関数をそのまま束縛する（keirin が別リポジトリだった間は
   写していた）。**判定を変えるときはここだけを変える。**

⚠️ ここは**標準ライブラリ以外を import しないこと**。keirin 側は
   自分の venv（FastAPI も SQLAlchemy も入っていない）から
   `importlib` でこのファイルを直接読み込むため、依存を足すと
   入稿側が起動時に落ちる。

## 判定

    看板 = race_type に 決勝 / 特選 / 選抜 / 特秀 のいずれかを含む
           ただし「準決勝」は除く

⚠️ **レース番号（最終R＝決勝）で判定しない。** ガールズ決勝が 6R と 12R の
   両方に置かれる開催が実在する（2026-08-09 佐世保）。逆に最終Rが一般戦の
   こともある。

⚠️ **「準決勝」は「決勝」を部分一致で拾う。** 除外しないと準決勝
   （全体の約14.5%）が看板に入り、判定が意味を失う。
"""

from __future__ import annotations

MARQUEE_KEYWORDS: tuple[str, ...] = ("決勝", "特選", "選抜", "特秀")
MARQUEE_EXCLUDE: tuple[str, ...] = ("準決勝",)


def is_marquee_race(race_type: str | None) -> bool:
    """race_type が看板レース（決勝・特選クラス）か。

    ⚠️ 除外を先に見る（「準決勝」が「決勝」を部分一致で拾うため）。
    """
    if not race_type:
        return False
    if any(k in race_type for k in MARQUEE_EXCLUDE):
        return False
    return any(k in race_type for k in MARQUEE_KEYWORDS)
