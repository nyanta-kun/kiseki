"""地方競馬「激走 / 見送り」判定（1レース最大1頭）の正本。

考え方は「人気馬が複勝圏の何枠を埋めそうか」と「その残りに割り込めそうな
人気薄がいるか」の2段。

    空き枠 room = 3 − Σ_{人気1〜3番} (q + mq) / 2
      q  = モデルの複勝確率（is_top3 ヘッド = place_probability）をレース内で合計3に正規化
      mq = 発走前単勝オッズから Harville で出した3着内確率（市場の見立て）
    候補 = 6番人気以下でモデル複勝確率 q が最大の1頭

    見送り: room < 1.3                 （人気馬が枠を埋める見込みが大きい）
    激走  : room ≥ 1.3 ∧ 候補の q > 人気1〜3番の q の最小
                                       （候補がモデル上は人気馬の誰かを押しのける）
    どちらでもない: 印なし

## 検証（docs/chihou_rebuild_2026_08.md 18章）

- 探索: 市場なし walk-forward 指数 × 発走6分前オッズ（2026-04-07〜08-13・3,990R）。
  的中率は候補自身の q でほぼ決まり（13%→30%）、room は「そのレースに人気薄が
  来るか」を分ける（35%→72%）。モデルの見立ては市場の見立てを揃えても効く
- 前向き確認（`chihou.place_picks`・2026-08-14〜09-27・1,562R・ルール凍結後に1回）:

      激走     541点(レースの35%)  複勝的中 23.8% [20.7, 27.2]  回収 0.790 [0.66, 0.92]
      人気薄全体（6番人気以下）      複勝的中 11.9%              回収 0.665
      人気薄が複勝圏に来た率   激走 66.4% / 印なし 59.4% / 見送り 44.4%

  再現・監視: `scripts/chihou_gekisou_research.py forward --start --end`

🔴 **回収率は 1.0 に届かない。** 「激走」は当たりやすさ（約2倍）の印であって
期待値の印ではない。画面でも収支を謳わないこと。

⚠️ 人気・市場確率は**発走前オッズ**から作る。確定オッズを渡すと look-ahead になる
（前身の条件がそれで崩壊した・`chihou_darkhorse_feasibility_2026_08_05.md`）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .buy_signal import chihou_popularity_ranks

# 複勝が3着まで払い戻される最小頭数（7頭以下は2着まで）。この判定は3枠が前提。
GEKISOU_MIN_FIELD: int = 8
# 「人気馬」= 人気1〜この順位まで
GEKISOU_FAV_TOP: int = 3
# 「人気薄」= この人気順位以下
GEKISOU_LONGSHOT_POP: int = 6
# 空き枠がこれ未満なら見送り
GEKISOU_ROOM_MIN: float = 1.3
# 複勝の枠数（8頭以上）
_PLACE_SLOTS: int = 3

# 激走馬の複勝確率（較正済み）= sigmoid(A + B × logit(市場の3着内確率 mq))。
# 探索窓（2026-04-07〜08-13・激走 1,100点）だけで当てはめたロジスティック回帰。
#
#   🔴 モデル複勝確率 q は使わない。激走馬に限ると q は実際より大きく出て
#      （平均 0.38 ↔ 的中 0.26）、しかも的中をほとんど分けない（AUC 0.52）。
#      mq と一緒に回帰すると係数は −0.07 で、前向き記録の当てはまりも改善しない。
#      市場の mq は逆に小さく出る（平均 0.18）が、的中を分ける（AUC 0.59）。
#   前向き記録（2026-08-14〜09-27・541点・この関数そのもので採点）での当てはまり:
#      予測 16.7% → 実際 15.6% / 22.7% → 22.7% / 27.4% → 23.2% / 31.8% → 31.5% / 36.6% → 44.4%(n=18)
#      全体では予測 25.2% ↔ 実際 23.8%（やや高めに出る）。Brier 0.177（定数 0.180）
#   ⚠️ 確率が高いほど回収率が高いわけではない（どの帯も 0.68〜0.91）。
GEKISOU_PROB_INTERCEPT: float = 0.005
GEKISOU_PROB_SLOPE: float = 0.678

STATUS_GEKISOU: str = "gekisou"
STATUS_MIOKURI: str = "miokuri"

# 判定ルールの署名。閾値を変えたら値も変わるので、集計時に世代を混ぜずに済む。
GEKISOU_RULE_VERSION: str = (
    f"room{GEKISOU_ROOM_MIN}_fav{GEKISOU_FAV_TOP}_pop{GEKISOU_LONGSHOT_POP}_n{GEKISOU_MIN_FIELD}_push"
)


@dataclass(frozen=True)
class GekisouVerdict:
    """1レースぶんの判定結果。

    Attributes:
        status: "gekisou" / "miokuri" / None（印なし・判定不能）
        room: 空き枠。判定できなければ None
        pick: 激走馬の馬番（status="gekisou" のときだけ）
        pick_pop: 激走馬の人気順位
        pick_q: 激走馬のモデル複勝確率（正規化後）
        fav_min_q: 人気1〜3番の中で最も低いモデル複勝確率（正規化後）
        pick_mq: 激走馬の市場の3着内確率（発走前単勝オッズから Harville）
        pick_place_prob: 激走馬が複勝圏に入る確率（較正済み・`gekisou_place_prob`）
    """

    status: str | None
    room: float | None = None
    pick: int | None = None
    pick_pop: int | None = None
    pick_q: float | None = None
    fav_min_q: float | None = None
    pick_mq: float | None = None
    pick_place_prob: float | None = None


NO_VERDICT = GekisouVerdict(status=None)


def harville_top_k(win_probs: list[float], k: int) -> list[float]:
    """勝率ベクトルから各馬の k 着以内確率を Harville 式で求める（k ≤ 3）。

    Args:
        win_probs: 各馬の勝率（合計が1でなくてもよい。内部で正規化する）
        k: 何着以内か（1〜3）

    Returns:
        入力と同じ順の k 着以内確率。
    """
    total = sum(win_probs)
    if total <= 0:
        return [0.0] * len(win_probs)
    p = [x / total for x in win_probs]
    n = len(p)
    out = [0.0] * n
    for i in range(n):
        acc = p[i]
        if k >= 2:
            for j in range(n):
                if j == i:
                    continue
                r1 = 1.0 - p[j]
                if r1 <= 0:
                    continue
                acc += p[j] * p[i] / r1
                if k >= 3:
                    for m in range(n):
                        if m in (i, j):
                            continue
                        r2 = r1 - p[m]
                        if r2 <= 0:
                            continue
                        acc += p[j] * p[m] / r1 * p[i] / r2
        out[i] = acc
    return out


def gekisou_place_prob(market_place_prob: float) -> float:
    """激走馬が複勝圏に入る確率（較正済み）を返す。

    Args:
        market_place_prob: その馬の市場の3着内確率（発走前単勝オッズから Harville）

    Returns:
        複勝圏に入る確率（0〜1）。激走に選ばれた馬に対してだけ較正してあるので、
        それ以外の馬に使ってはいけない。
    """
    p = min(max(market_place_prob, 1e-4), 1 - 1e-4)
    z = GEKISOU_PROB_INTERCEPT + GEKISOU_PROB_SLOPE * math.log(p / (1 - p))
    return 1.0 / (1.0 + math.exp(-z))


def judge_gekisou(
    runners: Mapping[int, tuple[float | None, float | None]],
) -> GekisouVerdict:
    """激走 / 見送りを判定する。

    Args:
        runners: 馬番 → (モデル複勝確率 place_probability, 発走前単勝オッズ)。
            単勝オッズが無い（取消・未発売）馬は出走馬として数えない。

    Returns:
        判定結果。出走馬（オッズのある馬）が8頭未満、またはその中に
        モデル確率の欠けた馬がいれば判定しない（`NO_VERDICT`）。
        欠けたまま正規化すると他馬の q が膨らみ、空き枠も押しのけも歪むため。
    """
    odds_by_hn = {hn: o for hn, (_p, o) in runners.items()}
    pop = chihou_popularity_ranks(odds_by_hn)
    field = sorted(pop, key=lambda hn: pop[hn])  # 人気順
    if len(field) < GEKISOU_MIN_FIELD:
        return NO_VERDICT
    probs = [runners[hn][0] for hn in field]
    if any(p is None or p <= 0 for p in probs):
        return NO_VERDICT

    p_sum = sum(float(p) for p in probs if p is not None)
    q = {hn: float(runners[hn][0] or 0.0) * _PLACE_SLOTS / p_sum for hn in field}
    odds = [float(runners[hn][1] or 0.0) for hn in field]
    mq_list = harville_top_k([1.0 / o for o in odds], _PLACE_SLOTS)
    mq = dict(zip(field, mq_list, strict=True))

    favs = [hn for hn in field if pop[hn] <= GEKISOU_FAV_TOP]
    room = _PLACE_SLOTS - sum((q[hn] + mq[hn]) / 2.0 for hn in favs)
    fav_min_q = min(q[hn] for hn in favs)

    if room < GEKISOU_ROOM_MIN:
        return GekisouVerdict(status=STATUS_MIOKURI, room=room, fav_min_q=fav_min_q)

    longshots = [hn for hn in field if pop[hn] >= GEKISOU_LONGSHOT_POP]
    # q が同じなら人気順（オッズが低い方）を先にする
    cand = max(longshots, key=lambda hn: (q[hn], -pop[hn]))
    if q[cand] > fav_min_q:
        return GekisouVerdict(
            status=STATUS_GEKISOU,
            room=room,
            pick=cand,
            pick_pop=pop[cand],
            pick_q=q[cand],
            fav_min_q=fav_min_q,
            pick_mq=mq[cand],
            pick_place_prob=gekisou_place_prob(mq[cand]),
        )
    return GekisouVerdict(status=None, room=room, fav_min_q=fav_min_q)
