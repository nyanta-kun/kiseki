"""地方競馬 人気薄(単勝10-15倍)複勝圏リランカー — serving 側スコアラー.

「人気がない馬が3着以内に好走する馬」抽出の地方版。v10 モデル確率は学習期間が
全期間に及ぶ(in-sample)ため使わず、リークフリーな生指数＋外部指数のみで構成する。

検証 (memory: upset_place_extraction.md 地方編, 2026-06-11):
  - test OOS (2026-01〜06, 確定オッズ): A2 精度 37.4% (帯base 31.8%・市場同数 33.8%)
  - 発走前オッズ判定 (-20〜-2分): A2 精度 30.3-32.3% vs 市場同数 19.5-26.6%
    = 締切前の市場が織り込む前の時間帯にこそエッジ (+4〜13pt)
  - 月次 35-42% で安定・帯内リフト Q4-Q1 +9.7pt
  - 複勝ROIは~0.83 (黒字でない・的中精度特化)

アーティファクト: backend/models/chihou_upset_reranker.v1.json (純JSON)。
学習: scripts/train_chihou_upset_reranker.py（半期ごと再学習を推奨）。
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

from .upset_reranker import _rank_desc

CHIHOU_UPSET_BAND_MIN: float = 10.0
CHIHOU_UPSET_BAND_MAX: float = 15.0

# 学習・serving 共通: chihou.calculated_indices の生指数 (v10 確率は不使用)
CHIHOU_IDX_COLUMNS: tuple[str, ...] = (
    "speed_index", "last3f_index", "jockey_index", "rotation_index", "last_margin_index",
)

_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "chihou_upset_reranker.v1.json"
)


class ChihouUpsetScore(TypedDict):
    """1頭分のリランカー出力."""

    ns: float
    """非オッズスコア（複勝圏確率の logistic 出力）。"""

    badge_cnt: int
    """バッジ数（kichiuma レース内3位以内 + netkeiba レース内3位以内）。"""


class ChihouUpsetReranker:
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
        self, rows: list[dict[str, Any]], head_count: int | None
    ) -> dict[int, ChihouUpsetScore]:
        """レース内の人気薄(単勝>=10)全馬の ns スコアとバッジ数を計算する。

        Args:
            rows: 馬ごとの dict リスト。必要キー:
                horse_number / win_odds / CHIHOU_IDX_COLUMNS の各指数 /
                kc_sp (kichiuma sp_score) / nk_idx (netkeiba idx_ave 数値化)。
            head_count: 出走頭数。

        Returns:
            {horse_number: ChihouUpsetScore}（単勝>=10 の馬のみ）
        """
        def col(key: str) -> dict[int, float | None]:
            return {
                r["horse_number"]: (float(r[key]) if r.get(key) is not None else None)
                for r in rows
            }

        idx_ranks = {c: _rank_desc(col(c)) for c in CHIHOU_IDX_COLUMNS}
        kc_rank = _rank_desc(col("kc_sp"))
        nk_rank = _rank_desc(col("nk_idx"))

        unpop = [
            r for r in rows
            if r.get("win_odds") is not None
            and float(r["win_odds"]) >= CHIHOU_UPSET_BAND_MIN
        ]
        n_unpop = len(unpop)

        out: dict[int, ChihouUpsetScore] = {}
        for r in unpop:
            hn = r["horse_number"]
            kc_r = kc_rank.get(hn)
            nk_r = nk_rank.get(hn)
            b_kc = 1 if kc_r is not None and kc_r <= 3 else 0
            b_nk = 1 if nk_r is not None and nk_r <= 3 else 0
            badge_cnt = b_kc + b_nk

            feat: dict[str, float | None] = {
                "kc_sp_rk": kc_r,
                "nk_idx_rk": nk_r,
                "b_kc": float(b_kc),
                "b_nk": float(b_nk),
                "badge_cnt": float(badge_cnt),
                "hc": float(head_count) if head_count else None,
                "n_unpop": float(n_unpop),
            }
            for c in CHIHOU_IDX_COLUMNS:
                feat[c] = r.get(c)
                feat[c + "_rk"] = idx_ranks[c].get(hn)

            out[hn] = ChihouUpsetScore(ns=self._score_row(feat), badge_cnt=badge_cnt)
        return out

    def axis_tier(
        self, win_odds: float | None, ns: float | None, badge_cnt: int | None
    ) -> str | None:
        """穴軸判定: 単勝[10,15) ∧ ns>=閾値 ∧ バッジ1+ → "standard"/"strong"(バッジ2)."""
        if win_odds is None or not (
            CHIHOU_UPSET_BAND_MIN <= float(win_odds) < CHIHOU_UPSET_BAND_MAX
        ):
            return None
        if ns is None or ns < self.threshold:
            return None
        if badge_cnt is None or badge_cnt < 1:
            return None
        return "strong" if badge_cnt >= 2 else "standard"


@lru_cache(maxsize=1)
def get_chihou_upset_reranker() -> ChihouUpsetReranker | None:
    """アーティファクトをロードして返す（無ければ None＝機能オフ）."""
    if not _ARTIFACT_PATH.exists():
        return None
    return ChihouUpsetReranker(json.loads(_ARTIFACT_PATH.read_text()))
