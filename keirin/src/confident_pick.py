"""勝負アイコン「自信あり」を付ける1レースの選定（2026-08-13 新設）。

## なぜ要るのか

netkeirin の「自信あり」アイコンは **1日に1つしか付けられない**。
従来は `CONFIDENT_RANKS = {"7SS"}` として **7SS の入稿すべて**に付けていたため、
7SS が複数出た日は先に入稿したものが取り、選んだわけではないレースに付いていた。

ユーザー決定（2026-08-13）: **朝の時点で当日全レースを見て、期待値が最も高い
1レースだけに付ける**。

## 期待値の定義

    EV = Σ(その目の的中確率 × 賭け金 × オッズ) ÷ 総賭け金

「合成オッズ × 的中率」の厳密な一般化。全点が同じオッズなら両者は一致し、
点ごとにオッズが違うときはこちらが正しい（1.0 で収支トントン）。

- **オッズは予測オッズを使う**（`src.odds_prediction.predicted_trio_board`）。
  🔴 朝の板は夜開催で 63.4% が未確定なので、板で比べると**朝に開催がある場だけが
     有利になる**。終日を同じ土俵で比べるために予測オッズで統一する。
- **的中確率は Plackett-Luce の三連複確率**（`src.odds_prediction._pl_trio`）。
  選手ごとの1着率 pw から「3車とも3着以内」を順列6通りの和で厳密に出す。

## 対象

**三連複の買い目だけ**（ユーザー決定 2026-08-13）。三連単（7H1/7H2/9H1/7T1）は
着順つきなのでこの確率モデルでは扱えない。混ぜると尺度が違うものを比べることになる。

⚠️ **EV は購入判断の根拠に使ってはいけない**（競輪の市場は効率的で、モデル由来の
   期待値による選別は繰り返し否定されている）。ここでの用途は
   **「1つしか無い枠をどこに置くか」という相対比較**に限られる。
   絶対値が 1.0 を超えているかどうかには意味が無い。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from src.odds_prediction import (
    OddsPredictionUnavailable,
    _pl_trio,
    load_race_inputs,
    predict_board,
)

log = logging.getLogger(__name__)

# 三連複の買い目だけを対象にする。`build_bet_detail` の bet_type 表記。
TRIO_BET_TYPE = "3連複"


def _parse_trio_combo(combo: str) -> frozenset[int] | None:
    """"1=2=5" → frozenset({1,2,5})。三連単（"-" 区切り）や壊れた値は None。"""
    text = str(combo)
    if "-" in text:
        return None
    try:
        cars = [int(c) for c in text.split("=")]
    except ValueError:
        return None
    if len(cars) != 3 or len(set(cars)) != 3:
        return None
    return frozenset(cars)


def expected_value_from_lines(
    lines: list[Mapping], odds_board: Mapping[frozenset, float],
    probs: Mapping[frozenset, float],
) -> float | None:
    """買い目と盤面から EV を出す。1点でも欠けたら None（部分計算をしない）。

    🔴 一部だけで計算すると、点数の少ないランクが不当に高く出る。
    """
    if not lines:
        return None
    total = 0.0
    ev = 0.0
    for ln in lines:
        if str(ln.get("bet_type") or "") != TRIO_BET_TYPE:
            return None
        cars = _parse_trio_combo(ln.get("combo", ""))
        if cars is None:
            return None
        odds = odds_board.get(cars)
        p = probs.get(cars)
        if not odds or odds <= 0 or p is None:
            return None
        try:
            stake = float(ln.get("stake") or 0)
        except (TypeError, ValueError):
            return None
        if stake <= 0:
            return None
        total += stake
        ev += p * stake * float(odds)
    if total <= 0:
        return None
    return ev / total


def race_expected_value(race_key: str, bet_detail: str | None) -> float | None:
    """入稿済み/入稿案1件の EV。計算できなければ None（理由はログに残す）。

    🔴 **無言で None にしない。** 予測オッズが引けないのに選定だけ進むと、
       「候補から静かに落ちた」ことに誰も気づけない。
    """
    if not bet_detail:
        return None
    try:
        detail = json.loads(bet_detail)
    except (TypeError, ValueError):
        log.warning("[confident] %s: bet_detail を読めません", race_key)
        return None
    lines = detail.get("lines") or []
    if not lines:
        return None
    # 三連単ランクはここで静かに落とす（対象外なのでログも出さない）。
    if any(str(x.get("bet_type") or "") != TRIO_BET_TYPE for x in lines):
        return None
    base = str(race_key).split("#")[0]
    try:
        cars, p3, pw, meta = load_race_inputs(base)
        board = predict_board(cars, p3, pw, meta)
        probs = _pl_trio(pw, cars)
    except OddsPredictionUnavailable as e:
        log.warning("[confident] %s: 予測オッズを使えません: %s", base, e)
        return None
    except Exception as e:  # noqa: BLE001 — 1レースの失敗で選定を止めない
        log.warning("[confident] %s: 予測オッズで想定外の失敗: %r", base, e)
        return None
    ev = expected_value_from_lines(lines, board, probs)
    if ev is None:
        log.warning("[confident] %s: 買い目の一部が盤面に無く EV を出せません", base)
    return ev


def pick_best(candidates: list[tuple[str, str, float | None]]
              ) -> tuple[str, str] | None:
    """(race_key, rank_key, ev) の一覧から EV 最大の1件を返す。

    EV が None のものは候補にしない。同値のときは race_key → rank_key の順で
    安定させる（**実行のたびに結果が変わらないこと**が運用上重要）。
    """
    usable = [(rk, rank, ev) for rk, rank, ev in candidates if ev is not None]
    if not usable:
        return None
    top = max(t[2] for t in usable)
    # 同値は race_key → rank_key で決める。`max()` の「最初に来たもの」に頼ると
    # 入力の順序（＝DB の並び）が変わった日に結果が変わる。
    tied = sorted((t for t in usable if t[2] == top), key=lambda t: (t[0], t[1]))
    return tied[0][0], tied[0][1]
