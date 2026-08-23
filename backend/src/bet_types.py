"""券種名（bet_type）の正準表記 — 単一の出所。

`keiba.race_payouts` / `keiba.odds_history` / `keiba.latest_odds` は
`bet_type` で join される。表記が割れると **例外にならず 0 件** になるだけなので
気付けない。実際 2026-08-23 まで、同じ `jvlink_parser.py` が

    HR（払戻）→ race_payouts.bet_type = 'wide'
    O3（オッズ）→ odds_history.bet_type = 'quinella_place'

と別の名前を書いており、確定オッズの検証でワイドだけ結果が出なかった。

## ワイドを `wide` にした理由

JRA 公式英語名は "Quinella Place"（枠連 = "Bracket Quinella" と同系統）だが、

  - 精算の正本である `race_payouts` が `wide`（実データ 69,103 行）
  - `betting/backtest.py` の BET_TYPES、`betting/allocation.py` の BetType、
    `betting/odds_model.py` の TAKEOUT_RATE がすべて `wide`
  - `quinella_place` はソース 2 ファイルにしか存在しなかった

ため `wide` に寄せた。JV-Link の O3 レコードは仕様書上「オッズ3(ワイド)」で、
英語名の指定は無い（docs/jvdata-spec.md）ので仕様側の制約ではない。

## 注意

`betting/finish_order.py` は確率モデル内部でローマ字名
（tansho / fukusho / umaren / wide / sanrenpuku / sanrentan）を使う。
これは DB に触らない別語彙で、`models/finish_order_lambda.json` のキーと
対応しているため、ここでは統一しない。DB へ書き出す境界で必ず変換すること。
"""

from __future__ import annotations

from typing import Literal

# race_payouts / odds_history / latest_odds で共通の bet_type。
BET_TYPES: frozenset[str] = frozenset(
    {
        "win",       # 単勝
        "place",     # 複勝
        "bracket",   # 枠連 (JRA 英語名 Bracket Quinella)
        "quinella",  # 馬連 (Quinella)
        "wide",      # ワイド (JRA 英語名 Quinella Place)
        "exacta",    # 馬単 (Exacta)
        "trio",      # 三連複 (Trio)
        "trifecta",  # 三連単 (Trifecta)
    }
)

BetType = Literal[
    "win",
    "place",
    "bracket",
    "quinella",
    "wide",
    "exacta",
    "trio",
    "trifecta",
]

# 日本語表記（ログ・画面用）
BET_TYPE_JA: dict[str, str] = {
    "win": "単勝",
    "place": "複勝",
    "bracket": "枠連",
    "quinella": "馬連",
    "wide": "ワイド",
    "exacta": "馬単",
    "trio": "三連複",
    "trifecta": "三連単",
}

# 過去に書かれていた別名 → 正準表記。
# DB の移行は backend/scripts/rename_quinella_place_to_wide.py。
LEGACY_BET_TYPE_ALIASES: dict[str, str] = {
    "quinella_place": "wide",  # ~2026-08-23 の O3 オッズ
    "frame": "bracket",        # allocation.py の旧 Literal
}


def canonical_bet_type(bet_type: str) -> str:
    """旧表記を正準表記へ寄せる。未知の名前はそのまま返す。"""
    return LEGACY_BET_TYPE_ALIASES.get(bet_type, bet_type)
