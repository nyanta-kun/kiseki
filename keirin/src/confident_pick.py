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

## 🔴🔴 型ラボ（2026-09-02〜）は行の `legs` だけで決める

ユーザー指示（2026-09-02）:
> **夕方くらいまでのレース**のうち、**合成が3倍以上**で**期待値が最も高い**レース

    候補 … 発走 JST < 18時（`CONFIDENT_BEFORE_HOUR`）
         ∧ 合成オッズ >= 3.0 倍（`CONFIDENT_MIN_SYNTH_ODDS`）
    順位 … EV（`legs_expected_value`）が最大

これは 2026-08-28 の決定（`pred_min_payout >= 20,000` → Σp 最大）を**置き換える**。

🔴 **`legs` だけで完結する**（`race_expected_value` のように予測オッズを引き直さない）。
   行に焼き付いた `prob` / `pred_odds` / `stake` を使うので、
   ①モデルを再学習しても当時の選定を再現できる（`p3_order` と同じ理由）
   ②三連単のプランも同じ尺度に載る——旧 `race_expected_value` は
   `bet_type == "3連複"` 以外を全て None にするので、型ラボ 8プランのうち
   三連複は `D_hit` / `A_trio` だけ＝**そのままでは自信ありがその2つにしか付かない**。

🔴 **合成オッズは `pred_mean_payout / 予算` ではない。** ダッチ配分（`alloc="dutch"`）の
   プランでは一致するが、信頼度傾斜（`alloc="conf"` ＝ `A_hit` / `F_hit` / `F_pay`）では
   一致しない。必ず `1 / Σ(1/予測オッズ)` で出すこと。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from src.meeting_wave import NIGHT_FROM_HOUR
from src.odds_prediction import (
    OddsPredictionUnavailable,
    _pl_trio,
    load_race_inputs,
    predict_board,
)

log = logging.getLogger(__name__)

# 三連複の買い目だけを対象にする。`build_bet_detail` の bet_type 表記。
TRIO_BET_TYPE = "3連複"

#: 「自信あり」の候補にする**発走時刻の上限**（JST の「時」・これ以降は候補外）。
#:
#: 🔴 **`meeting_wave.NIGHT_FROM_HOUR` をそのまま使う。新しい定数を作らない。**
#:    入稿の波（朝7:00 / 昼13:00 / 夕18:00）と境界がずれると
#:    「夕の波で入稿したのに夕方扱いされない」レースが出て説明できなくなる。
#:    同じ 18 は `backend/api/keirin_meeting.MIDNIGHT_FROM_HOUR`（ミッドナイト境界）
#:    でもあり、実測の第1R発走は 8/10/11/15/16/20 時だけで 17〜19 時台が空いている
#:    ＝境界に余裕がある。
CONFIDENT_BEFORE_HOUR = NIGHT_FROM_HOUR

#: 「自信あり」の候補にする**合成オッズの下限**（倍）。ユーザー指示 2026-09-02。
#:
#: ⚠️ `B_hit` は `sigma_max = 1/3.0` で組むので**合成がちょうど 3.00 倍**に張り付く。
#:    `>=` と `>` で `B_hit` が丸ごと入るか出るかが変わる。「3倍以上」なので `>=`。
CONFIDENT_MIN_SYNTH_ODDS = 3.0


def start_hour_jst(start_at: str | int | None) -> float | None:
    """`wt_races.start_at`（UNIX秒）から JST の「時」を返す。読めなければ None。

    🔴 **`backend/api/keirin_meeting.first_hour_jst` と同じ式**。リポジトリが
       分かれていてコード共有できないため、値の一致は検査で縛る。

    >>> start_hour_jst(None) is None
    True
    >>> start_hour_jst("abc") is None
    True
    >>> start_hour_jst(0)          # 1970-01-01 00:00 UTC = 09:00 JST
    9.0
    """
    if start_at is None or start_at == "":
        return None
    try:
        return ((int(start_at) + 9 * 3600) % 86400) / 3600
    except (TypeError, ValueError):
        return None


def synthetic_odds(legs) -> float | None:
    """買い目の**合成オッズ** = 1 / Σ(1/予測オッズ)。1点でも欠けたら None。

    🔴 **配分に依らない**（買い目の集合だけで決まる）。だから
       `pred_mean_payout / 予算` で代用してはいけない——信頼度傾斜のプランでは別物。
    🔴 **1点でも欠けたら None**。残りだけで合成すると点数が少ないほど大きく出るので
       **欠測のあるレースほど有利に見える**（`backend` の
       `_calc_synth_odds_from_lines` が踏んだのと同じ罠）。

    >>> round(synthetic_odds([{"pred_odds": 10}, {"pred_odds": 10}]), 4)
    5.0
    >>> synthetic_odds([{"pred_odds": 10}, {"pred_odds": None}]) is None
    True
    >>> synthetic_odds([]) is None
    True
    """
    if not legs:
        return None
    total = 0.0
    for lg in legs:
        o = lg.get("pred_odds")
        try:
            o = float(o)
        except (TypeError, ValueError):
            return None
        if o <= 0:
            return None
        total += 1.0 / o
    return (1.0 / total) if total > 0 else None


def legs_expected_value(legs) -> float | None:
    """行に焼き付いた買い目の **EV** = Σ(確率×賭け金×予測オッズ) ÷ Σ賭け金。

    `expected_value_from_lines` と同じ定義だが、**盤面を引き直さず `legs` だけで出す**
    ので三連単のプランにも使える（`bet_type` を見ない）。

    🔴 **1点でも欠けたら None**（部分計算をしない）。一部だけで足すと
       点数の少ない商品が不当に高く出る。

    >>> round(legs_expected_value(
    ...     [{"prob": 0.1, "stake": 5000, "pred_odds": 10},
    ...      {"prob": 0.1, "stake": 5000, "pred_odds": 20}]), 4)
    1.5
    >>> legs_expected_value([{"prob": None, "stake": 100, "pred_odds": 10}]) is None
    True
    >>> legs_expected_value([]) is None
    True
    """
    if not legs:
        return None
    total = ev = 0.0
    for lg in legs:
        try:
            p = float(lg["prob"])
            stake = float(lg["stake"])
            odds = float(lg["pred_odds"])
        except (KeyError, TypeError, ValueError):
            return None
        if stake <= 0 or odds <= 0:
            return None
        total += stake
        ev += p * stake * odds
    return (ev / total) if total > 0 else None


def type_lab_confident_score(legs, start_at) -> float | None:
    """型ラボ1商品の「自信あり」スコア（＝EV）。候補外なら None。

    候補の条件（ユーザー指示 2026-09-02）:
      ① 発走 JST < `CONFIDENT_BEFORE_HOUR`（18時）
      ② 合成オッズ >= `CONFIDENT_MIN_SYNTH_ODDS`（3.0倍）

    🔴 **発走時刻が読めないレースは候補にしない。** 「分からないものは通す」を
       ここで採ると、時刻の取れない開催だけが終日どこからでも選ばれてしまい
       ①の意味が消える（ゲートの「通す」思想は*商品を落とさない*ためのもので、
       *1つしかない枠の取り合い*には当てはまらない）。

    >>> legs = [{"prob": 0.1, "stake": 5000, "pred_odds": 10},
    ...         {"prob": 0.1, "stake": 5000, "pred_odds": 20}]
    >>> round(type_lab_confident_score(legs, 0), 4)      # 09:00 JST・合成 6.67倍
    1.5
    >>> type_lab_confident_score(legs, 0 + 9 * 3600) is None   # 18:00 JST
    True
    >>> type_lab_confident_score([{"prob": 0.5, "stake": 10000, "pred_odds": 2}], 0) is None
    True
    >>> type_lab_confident_score(legs, None) is None
    True
    """
    hour = start_hour_jst(start_at)
    if hour is None or hour >= CONFIDENT_BEFORE_HOUR:
        return None
    synth = synthetic_odds(legs)
    if synth is None or synth < CONFIDENT_MIN_SYNTH_ODDS:
        return None
    return legs_expected_value(legs)


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
