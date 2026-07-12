"""荒れるレース事前分類器 — 推論インターフェース。

⚠️ 現状は本番未配線（PR#35 Wave1 の実験モジュール）。APIルーター・スケジューラの
どこからも呼ばれておらず、評価/学習スクリプトからのみ参照される。

学習済みモデル (models/chaos_classifier_v1.txt) を使って
1レースの特徴量から「荒れスコア」(0〜1) を返す。

使い方:
    from src.betting.race_selector import chaos_score, RaceFeatures

    features = RaceFeatures(
        head_count=16,
        distance=1600,
        is_turf=1,
        is_handicap=0,
        race_num=11,
        kai=1,
        day=2,
        grade_code=5,
        odds_top1=2.1,
        odds_top3_sum=7.5,
        odds_entropy=2.8,
        odds_gap12=1.5,
        odds_gap23=0.9,
        n_over10=8,
        wp_top1=0.28,
        wp_top3_sum=0.65,
        wp_entropy=2.3,
        wp_mkt_gap=1.0,
        wp_mkt_corr=0.8,
    )
    score = chaos_score(features)
    # score: float 0〜1。高いほど荒れる可能性が高いレース。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
_MODEL_PATH = _MODELS_DIR / "chaos_classifier_v1.txt"

# 特徴量リスト（train_chaos_classifier.py の FEATURES と同一順）
FEATURES = [
    "head_count",
    "distance",
    "is_turf",
    "is_handicap",
    "race_num",
    "kai",
    "day",
    "grade_code",
    "odds_top1",
    "odds_top3_sum",
    "odds_entropy",
    "odds_gap12",
    "odds_gap23",
    "n_over10",
    "wp_top1",
    "wp_top3_sum",
    "wp_entropy",
    "wp_mkt_gap",
    "wp_mkt_corr",
]


@dataclass
class RaceFeatures:
    """発走前に確定するレース特徴量。

    全フィールドは point-in-time 厳守:
    - レース属性: 出走確定後・発走前に確定
    - 市場構造（odds_*）: 確定単勝オッズ（締切前最終値）
    - モデル構造（wp_*）: v26 win_probability（発走前算出）

    欠損値は None を渡す。モデルは LightGBM で NaN を自動処理する。
    """

    # レース属性
    head_count: int
    """出走頭数"""

    distance: float
    """距離 (m)"""

    is_turf: int
    """芝=1, ダート=0"""

    is_handicap: int
    """ハンデ戦=1 (weight_type_code='3'), それ以外=0"""

    race_num: int
    """レース番号 (1〜12)"""

    kai: int
    """開催回次"""

    day: int
    """開催日次"""

    grade_code: int
    """グレードコード (G1=1, G2=2, G3=3, OP=4, 一般=5)"""

    # 市場構造（確定単勝オッズ: point-in-time = 締切前に確定）
    odds_top1: float | None
    """1番人気オッズ"""

    odds_top3_sum: float | None
    """上位3頭オッズ合計"""

    odds_entropy: float | None
    """オッズエントロピー（市場確率のシャノン情報量）"""

    odds_gap12: float | None
    """1-2番人気オッズ差"""

    odds_gap23: float | None
    """2-3番人気オッズ差"""

    n_over10: int | None
    """単勝10倍超頭数"""

    # モデル構造（v26 win_probability: 発走前算出）
    wp_top1: float | None
    """モデル1位 win_probability"""

    wp_top3_sum: float | None
    """モデル上位3頭 win_probability 合計"""

    wp_entropy: float | None
    """モデル win_probability エントロピー"""

    wp_mkt_gap: float | None
    """モデルランク1位と市場1番人気の一致フラグ (1=一致, 0=不一致)"""

    wp_mkt_corr: float | None
    """モデル確率と市場確率のスピアマン相関"""


def _load_model():
    """モデルを遅延ロードして返す。"""
    try:
        import lightgbm as lgb
    except ImportError as e:
        raise ImportError("lightgbm が必要です: pip install lightgbm") from e

    if not _MODEL_PATH.exists():
        raise FileNotFoundError(
            f"モデルファイルが見つかりません: {_MODEL_PATH}\n先に train_chaos_classifier.py を実行してください。"
        )
    return lgb.Booster(model_file=str(_MODEL_PATH))


# モジュールレベルキャッシュ（複数呼び出しでファイル再読み込みを防ぐ）
_cached_model = None


def _get_model():
    """シングルトンモデルを返す（遅延初期化）。"""
    global _cached_model
    if _cached_model is None:
        _cached_model = _load_model()
    return _cached_model


def _features_to_array(features: RaceFeatures) -> np.ndarray:
    """RaceFeatures を FEATURES 順の numpy 1D 配列に変換する。

    None/NaN は float NaN に変換（LightGBM は NaN を自動処理する）。
    """
    vals: list[float] = []
    for fname in FEATURES:
        v = getattr(features, fname, None)
        if v is None:
            vals.append(float("nan"))
        elif isinstance(v, float) and math.isnan(v):
            vals.append(float("nan"))
        else:
            vals.append(float(v))
    return np.array(vals, dtype=float)


def chaos_score(features: RaceFeatures) -> float:
    """発走前特徴量から「荒れスコア」(0〜1) を返す。

    スコアが高いほど三連単 100,000円以上の高配当が出る可能性が高い。

    引数:
        features: RaceFeatures インスタンス。欠損値は None でよい。

    戻り値:
        float: 0〜1 のスコア。モデル未ロードの場合は FileNotFoundError を送出。
    """
    model = _get_model()
    arr = _features_to_array(features)
    # LightGBM は (1, n_features) の 2D 配列を受け付ける
    pred = model.predict(arr.reshape(1, -1))
    return float(pred[0])


def chaos_score_batch(features_list: list[RaceFeatures]) -> np.ndarray:
    """複数レースのスコアを一括計算して numpy 配列で返す。

    引数:
        features_list: RaceFeatures のリスト。

    戻り値:
        np.ndarray: shape (len(features_list),) の float 配列。
    """
    if not features_list:
        return np.array([], dtype=float)
    model = _get_model()
    mat = np.stack([_features_to_array(f) for f in features_list], axis=0)
    return model.predict(mat)


def features_from_dict(d: dict) -> RaceFeatures:
    """dict から RaceFeatures を生成するファクトリ関数。

    d に含まれない/None のキーは None として扱われる。

    使い方:
        f = features_from_dict({
            "head_count": 16,
            "distance": 2000,
            "is_turf": 1,
            ...
        })
        score = chaos_score(f)
    """
    kwargs: dict = {}
    for fname in FEATURES:
        v = d.get(fname)
        if v is not None:
            try:
                kwargs[fname] = float(v)
            except (TypeError, ValueError):
                kwargs[fname] = None
        else:
            kwargs[fname] = None

    # head_count / race_num / kai / day / grade_code は int に変換
    for int_field in (
        "head_count",
        "race_num",
        "kai",
        "day",
        "grade_code",
        "n_over10",
        "is_turf",
        "is_handicap",
        "wp_mkt_gap",
    ):
        if kwargs.get(int_field) is not None:
            kwargs[int_field] = int(kwargs[int_field])

    return RaceFeatures(**kwargs)
