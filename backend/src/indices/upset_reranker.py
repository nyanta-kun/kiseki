"""人気薄(単勝10-15倍)複勝圏リランカー — serving 側スコアラー.

「人気がない馬が3着以内に好走する馬」抽出の中核。オッズを特徴に使わない
logistic 回帰で人気薄を並べ替え、帯[10,15)×スコア上位1/4×バッジで抽出する。

検証 (memory: upset_place_extraction.md, 2026-06-11・train/val/test 3分割で再実施):
  - test(2026-01〜06, 凍結1回評価): A2 精度 33.1% CI[0.283,0.381]
    (帯base 27.0%・市場同数 29.0% / 全人気薄base 10.5%)
  - 発走前オッズ(-10分)判定: 35.3% (市場同数 26.1%) — 実運用入力で成立
  - A2(バッジ>=1) 約7頭/日・閾値は学習期帯内 3/4 分位(検証期間で選定)
  - 複勝ROIは~0.82 (黒字でない・精度特化)。市場比エッジの長期持続は未確認
    → scripts/monitor_upset_picks.py で月次精度監視が運用必須条件

アーティファクト: backend/models/upset_reranker.v1.json (純JSON・sklearn 不要)。
学習: scripts/train_upset_reranker.py（半期ごとに再学習を推奨）。
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

# 帯定義（バックテスト確定値）。15倍超は精度が構造的に落ちる([15,30)で19%止まり)
UPSET_MIN_ODDS: float = 10.0
UPSET_BAND_MIN: float = 10.0
UPSET_BAND_MAX: float = 15.0

# 学習・serving 共通の特徴定義
BASE_FEATURES: tuple[str, ...] = (
    "pp", "wp", "comp_rank", "bdm_rank", "tdm_rank", "kc_rank",
    "b_ana", "badge_cnt", "hc", "n_unpop",
)
SUB_INDEX_COLUMNS: tuple[str, ...] = (
    "speed_index", "adjusted_speed_index", "last_3f_index", "course_aptitude",
    "distance_aptitude", "position_advantage", "jockey_index", "pace_index",
    "rotation_index", "rebound_index", "career_phase_index", "distance_change_index",
)

_ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "models" / "upset_reranker.v1.json"


class UpsetScore(TypedDict):
    """1頭分のリランカー出力."""

    ns: float
    """非オッズスコア（複勝圏確率の logistic 出力）。"""

    badge_cnt: int
    """バッジ数（穴ぐさ + netkeiba≤3 + kichiuma≤3 + DM battle≤2）。"""


def _rank_desc(values: dict[int, float | None]) -> dict[int, float | None]:
    """非 None 値を降順順位(1=最高, 同値は min 方式)にする。None は None のまま."""
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


class UpsetReranker:
    """JSON アーティファクトから復元する純 Python の logistic スコアラー."""

    def __init__(self, artifact: dict[str, Any]) -> None:
        """アーティファクト dict から初期化する."""
        self.features: list[str] = artifact["features"]
        self.median: dict[str, float] = artifact["median"]
        self.mean: list[float] = artifact["mean"]
        self.scale: list[float] = artifact["scale"]
        self.coef: list[float] = artifact["coef"]
        self.intercept: float = artifact["intercept"]
        self.threshold: float = artifact["threshold"]
        self.trained_at: str = artifact.get("trained_at", "")

    def _score_row(self, feat: dict[str, float | None]) -> float:
        logit = self.intercept
        for i, name in enumerate(self.features):
            v = feat.get(name)
            if v is None:
                v = self.median[name]
            logit += self.coef[i] * (float(v) - self.mean[i]) / self.scale[i]
        return 1.0 / (1.0 + math.exp(-logit))

    def score_race(
        self, horses: list[dict[str, Any]], head_count: int | None
    ) -> dict[int, UpsetScore]:
        """レース内の人気薄(単勝>=10)全馬の ns スコアとバッジ数を計算する。

        Args:
            horses: _collect_race_data() の horses dict リスト。
                place_probability / win_probability / composite_index / win_odds /
                jvan_battle_dm / jvan_time_dm / km_rank / nb_ave_rank / anagusa_rank
                と SUB_INDEX_COLUMNS の各キーを参照する。
            head_count: 出走頭数。

        Returns:
            {horse_number: UpsetScore}（人気薄のみ。オッズ未取得馬は含まない）
        """
        def col(key: str) -> dict[int, float | None]:
            return {
                h["horse_number"]: (float(h[key]) if h.get(key) is not None else None)
                for h in horses
            }

        comp_rank = _rank_desc(col("composite_index"))
        bdm_rank = _rank_desc(col("jvan_battle_dm"))
        tdm_rank = _rank_desc(col("jvan_time_dm"))
        sub_ranks = {c: _rank_desc(col(c)) for c in SUB_INDEX_COLUMNS}

        unpop = [
            h for h in horses
            if h.get("win_odds") is not None and float(h["win_odds"]) >= UPSET_MIN_ODDS
        ]
        n_unpop = len(unpop)

        out: dict[int, UpsetScore] = {}
        for h in unpop:
            hn = h["horse_number"]
            km_rank = h.get("km_rank")
            nb_ave_rank = h.get("nb_ave_rank")
            b_ana = 1 if h.get("anagusa_rank") in ("A", "B", "C") else 0
            b_nk = 1 if nb_ave_rank is not None and nb_ave_rank <= 3 else 0
            b_kc = 1 if km_rank is not None and km_rank <= 3 else 0
            bdm = bdm_rank.get(hn)
            b_dm = 1 if bdm is not None and bdm <= 2 else 0
            badge_cnt = b_ana + b_nk + b_kc + b_dm

            feat: dict[str, float | None] = {
                "pp": h.get("place_probability"),
                "wp": h.get("win_probability"),
                "comp_rank": comp_rank.get(hn),
                "bdm_rank": bdm,
                "tdm_rank": tdm_rank.get(hn),
                "kc_rank": float(km_rank) if km_rank is not None else None,
                "b_ana": float(b_ana),
                "badge_cnt": float(badge_cnt),
                "hc": float(head_count) if head_count else None,
                "n_unpop": float(n_unpop),
            }
            for c in SUB_INDEX_COLUMNS:
                feat[c] = h.get(c)
                feat[c + "_rk"] = sub_ranks[c].get(hn)

            out[hn] = UpsetScore(ns=self._score_row(feat), badge_cnt=badge_cnt)
        return out


@lru_cache(maxsize=1)
def get_upset_reranker() -> UpsetReranker | None:
    """アーティファクトをロードして返す（無ければ None＝機能オフ）."""
    if not _ARTIFACT_PATH.exists():
        return None
    return UpsetReranker(json.loads(_ARTIFACT_PATH.read_text()))
