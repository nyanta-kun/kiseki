"""毎レース1頭の人気薄を推奨する 複勝EVモデル — serving 側スコアラー。

「人気薄(単勝>=10倍)から、複勝圏(3着以内)への期待度と複勝最低オッズによる
期待値(EV)を算出し、レースごとに最も妙味のある1頭を選ぶ」モデル。

設計の要点 (memory: betting_strategy_findings / upset_place_extraction の知見を反映):
  1. 複勝圏確率 P は logistic + isotonic 較正で **honest な確率**にする。
     人気薄(高オッズ)は素の予測が複勝率を過大評価する(pred0.30→act0.20)ため、
     較正なしの EV は深い人気薄ほど不当に高く出る。較正でこれを矯正する。
  2. 複勝最低オッズは win_odds から近似(odds_impute)。履歴の place_odds は
     入着馬の払戻しか無いため、全候補を同列に比較するには近似が必要。
     (実 ROI 検証は入着=実 place_odds・非入着=0 で別途行う)
  3. EV = P_cal × place_odds_hat。
  4. **適度な的中率フロア**: P_cal >= floor を満たす候補の中から EV 最大を選ぶ。
     これがユーザー要件「下位人気の高オッズで不適切に期待値が上がらないよう、
     適度な的中率を満たした上で期待値を算出」の実装。floor を外すと的中率が
     17.6%まで落ち(深い人気薄を拾う)、floor=0.20 で 25.7%へ回復する(OOS実測)。

検証 (memory: 追記予定。train<2025-07 / test 2025-07〜2026-06, 3,133レース):
  - 採用案 EV最大+P>=0.20: 的中25.7% / 複勝ROI 0.806 CI[0.750,0.864] (cov 83%)
  - 2026純フォワード: 的中26.6% / ROI 0.809 (市場最低オッズ 26.2%)
  - 較正 test ECE 0.006。ROI は ~0.81 で +EV ではない(効率市場)。
    → 用途は「妙味のある人気薄1頭の選定・表示」。月次的中率モニタ推奨。

アーティファクト: backend/models/place_ev_model.v1.json (純JSON・sklearn 不要)。
学習: scripts/train_place_ev_model.py（半期ごと再学習を推奨）。
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

# 人気薄ユニバース下限。バックテストはすべて >=10 倍で実施。
UNDERDOG_MIN_ODDS: float = 10.0

# 複勝最低オッズ下限。複勝が 2.0 倍未満（=ほぼ元返し）になる馬は妙味が薄いため
# 推奨対象から除外する（ユーザー要件 2026-06-13）。実オッズが取れれば実値、
# 無ければ近似値で判定する。
MIN_PLACE_ODDS: float = 2.0

# 学習・serving 共通のサブ指数列（upset_reranker と同じソース）。
SUB_INDEX_COLUMNS: tuple[str, ...] = (
    "speed_index", "adjusted_speed_index", "last_3f_index", "course_aptitude",
    "distance_aptitude", "position_advantage", "jockey_index", "pace_index",
    "rotation_index", "rebound_index", "career_phase_index", "distance_change_index",
)

_ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "models" / "place_ev_model.v1.json"


class PlacePick(TypedDict):
    """レース1頭分の推奨出力。"""

    horse_number: int
    place_probability: float
    """較正済み複勝圏確率 P_cal。"""
    place_odds_hat: float
    """近似複勝最低オッズ。"""
    expected_value: float
    """EV = P_cal × place_odds_hat。"""
    badge_cnt: int
    """バッジ数(穴ぐさ + netkeiba≤3 + kichiuma≤3 + DM battle≤2)。"""
    meets_floor: bool
    """的中率フロア(P_cal >= floor)を満たすか。"""


def _rank_desc(values: dict[int, float | None]) -> dict[int, float | None]:
    """非 None 値を降順順位(1=最高, 同値は min 方式)にする。None は None のまま。"""
    items = [(hn, v) for hn, v in values.items() if v is not None]
    items.sort(key=lambda x: -x[1])
    ranks: dict[int, float | None] = dict.fromkeys(values)
    prev_v: float | None = None
    prev_rank = 0
    for i, (hn, v) in enumerate(items, start=1):
        rank = prev_rank if v == prev_v else i
        ranks[hn] = float(rank)
        prev_v, prev_rank = v, rank
    return ranks


def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    """単調増加 xs に対する線形補間(np.interp 相当・純 Python)。範囲外はクリップ。"""
    if not xs:
        return x
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    x0, x1, y0, y1 = xs[lo], xs[lo + 1], ys[lo], ys[lo + 1]
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


class PlaceEvModel:
    """JSON アーティファクトから復元する純 Python の複勝EVモデル。"""

    def __init__(self, artifact: dict[str, Any]) -> None:
        """アーティファクト dict から初期化する。"""
        self.features: list[str] = artifact["features"]
        self.median: dict[str, float] = artifact["median"]
        self.mean: list[float] = artifact["mean"]
        self.scale: list[float] = artifact["scale"]
        self.coef: list[float] = artifact["coef"]
        self.intercept: float = artifact["intercept"]
        self.floor: float = artifact["floor"]
        self.cal_x: list[float] = artifact["calibration"]["x"]
        self.cal_y: list[float] = artifact["calibration"]["y"]
        self.odds_impute: list[float] = artifact["odds_impute"]
        self.min_odds: float = artifact.get("min_odds", UNDERDOG_MIN_ODDS)
        self.min_place_odds: float = artifact.get("min_place_odds", MIN_PLACE_ODDS)
        self.trained_at: str = artifact.get("trained_at", "")

    def _logit_prob(self, feat: dict[str, float | None]) -> float:
        logit = self.intercept
        for i, name in enumerate(self.features):
            v = feat.get(name)
            if v is None:
                v = self.median[name]
            logit += self.coef[i] * (float(v) - self.mean[i]) / self.scale[i]
        return 1.0 / (1.0 + math.exp(-logit))

    def _calibrate(self, p_raw: float) -> float:
        return _interp(p_raw, self.cal_x, self.cal_y)

    def impute_place_odds(self, win_odds: float, head_count: int | float) -> float:
        """win_odds と頭数から複勝最低オッズを近似する。

        log(place_odds) = c0 + c1·log(win_odds) + c2·hc + c3·log(win_odds)^2。
        """
        c0, c1, c2, c3 = self.odds_impute
        lo = math.log(win_odds)
        return math.exp(c0 + c1 * lo + c2 * float(head_count) + c3 * lo * lo)

    def score_race(
        self, horses: list[dict[str, Any]], head_count: int | None
    ) -> dict[int, PlacePick]:
        """レース内の人気薄(単勝>=min_odds)全馬の P_cal / EV / バッジを計算する。

        Args:
            horses: recommender の horses dict リスト(upset_reranker と同じキーを参照)。
            head_count: 出走頭数。

        Returns:
            {horse_number: PlacePick}(人気薄のみ。オッズ未取得馬は含まない)。
        """
        def col(key: str) -> dict[int, float | None]:
            return {
                h["horse_number"]: (float(h[key]) if h.get(key) is not None else None)
                for h in horses
            }

        comp_rank = _rank_desc(col("composite_index"))
        pp_rank = _rank_desc(col("place_probability"))
        bdm_rank = _rank_desc(col("jvan_battle_dm"))
        tdm_rank = _rank_desc(col("jvan_time_dm"))
        sub_ranks = {c: _rank_desc(col(c)) for c in SUB_INDEX_COLUMNS}

        unpop = [
            h for h in horses
            if h.get("win_odds") is not None and float(h["win_odds"]) >= self.min_odds
        ]
        n_unpop = len(unpop)
        is_turf = 1.0 if (horses and horses[0].get("surface") == "芝") else 0.0

        out: dict[int, PlacePick] = {}
        for h in unpop:
            hn = h["horse_number"]
            win_odds = float(h["win_odds"])
            km_rank = h.get("km_rank")
            nb_ave_rank = h.get("nb_ave_rank")
            ag = h.get("anagusa_rank")
            b_ana = 1 if ag in ("A", "B", "C") else 0
            b_anaAB = 1 if ag in ("A", "B") else 0
            b_nk = 1 if nb_ave_rank is not None and nb_ave_rank <= 3 else 0
            b_kc = 1 if km_rank is not None and km_rank <= 3 else 0
            bdm = bdm_rank.get(hn)
            b_dm = 1 if bdm is not None and bdm <= 2 else 0
            badge_cnt = b_ana + b_nk + b_kc + b_dm
            badge_any = 1 if badge_cnt > 0 else 0

            feat: dict[str, float | None] = {
                "log_odds": math.log(win_odds),
                "pp": h.get("place_probability"),
                "wp": h.get("win_probability"),
                "comp_rank": comp_rank.get(hn),
                "pp_rank": pp_rank.get(hn),
                "bdm_rank": bdm,
                "tdm_rank": tdm_rank.get(hn),
                "kc_rank": float(km_rank) if km_rank is not None else None,
                "b_ana": float(b_ana),
                "b_anaAB": float(b_anaAB),
                "badge_cnt": float(badge_cnt),
                "badge_any": float(badge_any),
                "hc": float(head_count) if head_count else None,
                "n_unpop": float(n_unpop),
                "is_turf": is_turf,
                "distance": h.get("distance"),
            }
            for c in SUB_INDEX_COLUMNS:
                feat[c] = h.get(c)
                feat[c + "_rk"] = sub_ranks[c].get(hn)

            p_cal = self._calibrate(self._logit_prob(feat))
            place_odds_hat = self.impute_place_odds(win_odds, head_count or 12)
            out[hn] = PlacePick(
                horse_number=hn,
                place_probability=round(p_cal, 4),
                place_odds_hat=round(place_odds_hat, 2),
                expected_value=round(p_cal * place_odds_hat, 3),
                badge_cnt=badge_cnt,
                meets_floor=p_cal >= self.floor,
            )
        return out

    def pick_race(
        self, horses: list[dict[str, Any]], head_count: int | None
    ) -> PlacePick | None:
        """レースで推奨する人気薄1頭を返す。

        的中率フロア(P_cal >= floor) かつ 複勝最低オッズ >= min_place_odds(既定2.0)
        を満たす候補の中から EV 最大を選ぶ。満たす候補が無ければ None(=推奨なし)。
        複勝オッズは実オッズ(horse["place_odds"])が取れればそれを、無ければ近似値を使う。
        """
        place_actual = {
            h["horse_number"]: h.get("place_odds")
            for h in horses
            if h.get("place_odds") is not None
        }
        picks = self.score_race(horses, head_count)
        eligible = []
        for hn, p in picks.items():
            if not p["meets_floor"]:
                continue
            place_odds = place_actual.get(hn)
            if place_odds is None:
                place_odds = p["place_odds_hat"]
            if float(place_odds) < self.min_place_odds:
                continue
            eligible.append(p)
        if not eligible:
            return None
        return max(eligible, key=lambda p: p["expected_value"])


@lru_cache(maxsize=1)
def get_place_ev_model() -> PlaceEvModel | None:
    """アーティファクトをロードして返す(無ければ None＝機能オフ)。"""
    if not _ARTIFACT_PATH.exists():
        return None
    return PlaceEvModel(json.loads(_ARTIFACT_PATH.read_text()))
