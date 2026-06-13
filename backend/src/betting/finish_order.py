"""条件付き着順確率モデル（T03）

3手法の比較インターフェースを提供する:
  1. harville  — 1着確率の鏡像（ベースライン）
  2. henery    — 割引指数モデル (p_i^λ で再正規化)
  3. lgb       — 2-3着専用 LightGBM + 1着確率 (v26) 組み合わせ

統一インターフェース:
  combo_probability(win_probs, combo, bet_type, method) -> float
  enumerate_combo_probs(win_probs, bet_type, method)    -> dict[tuple, float]

18頭・三連単 4896点が 1秒以内で列挙できるよう実装する。
"""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import lightgbm as lgb

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------


def _normalize(probs: dict[int, float]) -> dict[int, float]:
    """合計が 1.0 になるよう正規化する。ゼロ除算は等分で処理。"""
    total = sum(probs.values())
    if total <= 1e-12:
        n = len(probs)
        return {k: 1.0 / n for k in probs}
    return {k: v / total for k, v in probs.items()}


def _harville_joint(win_probs: dict[int, float], order: tuple[int, ...]) -> float:
    """Harville 公式で指定着順の同時確率 P(i1=1着, i2=2着, ...) を計算する。

    P(i1=1着, i2=2着, i3=3着)
      = p(i1) * p(i2)/(1-p(i1)) * p(i3)/(1-p(i1)-p(i2))
    """
    remaining = dict(win_probs)
    prob = 1.0
    for horse in order:
        total = sum(remaining.values())
        if total <= 1e-12:
            return 0.0
        prob *= remaining[horse] / total
        del remaining[horse]
    return prob


def _henery_adjusted(win_probs: dict[int, float], lam: float) -> dict[int, float]:
    """Henery/Stern 割引指数モデル: p_i → p_i^λ で再正規化。

    λ=1.0 のとき Harville と同一。
    λ<1.0 のとき低確率馬の2-3着確率が引き上げられる。
    """
    adjusted = {k: max(v, 1e-12) ** lam for k, v in win_probs.items()}
    return _normalize(adjusted)


def _harville_joint_from_list(probs_list: list[float], order_idx: tuple[int, ...]) -> float:
    """リスト版 Harville（インデックス指定）。"""
    remaining = list(probs_list)
    prob = 1.0
    for idx in order_idx:
        total = sum(remaining)
        if total <= 1e-12:
            return 0.0
        prob *= remaining[idx] / total
        remaining[idx] = 0.0
    return prob


# ---------------------------------------------------------------------------
# λ パラメータ読み込み
# ---------------------------------------------------------------------------


def _load_lambda_params() -> dict[str, float]:
    """fit_finish_order_lambda.py が出力した JSON を読み込む。

    ファイルがなければデフォルト λ=0.82（Henery 推奨値）を返す。
    """
    path = MODELS_DIR / "finish_order_lambda.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    logger.debug("finish_order_lambda.json が見つからないためデフォルト λ を使用")
    return {"tansho": 0.82, "umaren": 0.82, "wide": 0.82, "sanrenpuku": 0.82, "sanrentan": 0.82}


_LAMBDA_CACHE: dict[str, float] | None = None


def get_lambda_params() -> dict[str, float]:
    """λ パラメータのキャッシュ付き読み込み。"""
    global _LAMBDA_CACHE
    if _LAMBDA_CACHE is None:
        _LAMBDA_CACHE = _load_lambda_params()
    return _LAMBDA_CACHE


# ---------------------------------------------------------------------------
# LGB モデル読み込み
# ---------------------------------------------------------------------------

_LGB_PLACE_MODEL: lgb.Booster | None = None
_LGB_SHOW_MODEL: lgb.Booster | None = None


def _load_lgb_models() -> tuple[lgb.Booster | None, lgb.Booster | None]:
    """2着以内・3着以内 LGB モデルをロードする。

    Returns:
        (place_model, show_model) — どちらかが None の場合はモデル未学習。
    """
    global _LGB_PLACE_MODEL, _LGB_SHOW_MODEL
    if _LGB_PLACE_MODEL is None:
        place_path = MODELS_DIR / "finish_order_lgb_place.txt"
        show_path = MODELS_DIR / "finish_order_lgb_show.txt"
        if place_path.exists() and show_path.exists():
            import lightgbm as lgb_module
            _LGB_PLACE_MODEL = lgb_module.Booster(model_file=str(place_path))
            _LGB_SHOW_MODEL = lgb_module.Booster(model_file=str(show_path))
        else:
            logger.debug("finish_order LGB モデルファイルが見つかりません。henery にフォールバック")
    return _LGB_PLACE_MODEL, _LGB_SHOW_MODEL


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def combo_probability(
    win_probs: dict[int, float],
    combo: tuple[int, ...],
    bet_type: str,
    method: str = "harville",
    lgb_place_scores: dict[int, float] | None = None,
    lgb_show_scores: dict[int, float] | None = None,
) -> float:
    """指定組合せの確率を返す。

    Args:
        win_probs: 馬番→1着確率の辞書（合計≈1.0）
        combo:     着順組合せ（馬番のタプル）
                   馬連/ワイド = 順不同 2頭, 三連複 = 順不同 3頭
                   三連単 = 1-2-3着順 3頭, 複勝 = 1頭
        bet_type:  "tansho"(単勝) / "fukusho"(複勝) / "umaren"(馬連)
                   / "wide"(ワイド) / "sanrenpuku"(三連複) / "sanrentan"(三連単)
        method:    "harville" / "henery" / "lgb"
        lgb_place_scores: lgb メソッド使用時の 2着以内スコア(馬番→float)
        lgb_show_scores:  lgb メソッド使用時の 3着以内スコア(馬番→float)

    Returns:
        組合せが的中する確率 [0, 1]
    """
    if not combo or not win_probs:
        return 0.0

    # win_probs を正規化
    wp = _normalize(win_probs)

    if bet_type == "tansho":
        # 単勝: P(i = 1着)
        horse = combo[0]
        return wp.get(horse, 0.0)

    if bet_type == "fukusho":
        # 複勝: P(i = 1着 or 2着 or 3着)
        return _place_prob_single(wp, combo[0], method, lgb_place_scores, lgb_show_scores)

    if bet_type == "umaren":
        # 馬連: P(i,j が 1-2着・順不同)
        if len(combo) != 2:
            return 0.0
        a, b = combo
        return _combo_prob_unordered(wp, (a, b), 2, method, lgb_place_scores, lgb_show_scores)

    if bet_type == "wide":
        # ワイド: P(i,j が 3着以内・順不同)
        if len(combo) != 2:
            return 0.0
        a, b = combo
        return _combo_prob_wide(wp, a, b, method, lgb_place_scores, lgb_show_scores)

    if bet_type == "sanrenpuku":
        # 三連複: P(i,j,k が 1-2-3着・順不同)
        if len(combo) != 3:
            return 0.0
        return _combo_prob_unordered(wp, combo, 3, method, lgb_place_scores, lgb_show_scores)

    if bet_type == "sanrentan":
        # 三連単: P(i=1着, j=2着, k=3着)
        if len(combo) != 3:
            return 0.0
        return _combo_prob_ordered(wp, combo, method, lgb_place_scores, lgb_show_scores)

    raise ValueError(f"未知の bet_type: {bet_type}")


def enumerate_combo_probs(
    win_probs: dict[int, float],
    bet_type: str,
    method: str = "harville",
    lgb_place_scores: dict[int, float] | None = None,
    lgb_show_scores: dict[int, float] | None = None,
) -> dict[tuple[int, ...], float]:
    """全組合せの確率辞書を返す。

    18頭・三連単 4896点でも 1秒以内で完了する実装を保証する。
    三連複は順不同（最小番号が先頭）、三連単は指定順で格納する。

    Args:
        win_probs: 馬番→1着確率
        bet_type:  "umaren" / "wide" / "sanrenpuku" / "sanrentan"
        method:    "harville" / "henery" / "lgb"

    Returns:
        {combo: probability} — 全ての確率の合計は 1.0 に近い（端数あり）
    """
    wp = _normalize(win_probs)
    horses = sorted(wp.keys())
    result: dict[tuple[int, ...], float] = {}

    # LGB の事前スコア計算（メソッド = lgb のとき）
    place_s, show_s = lgb_place_scores, lgb_show_scores

    if bet_type == "umaren":
        for a, b in itertools.combinations(horses, 2):
            p = _combo_prob_unordered(wp, (a, b), 2, method, place_s, show_s)
            result[(a, b)] = p
        return result

    if bet_type == "wide":
        for a, b in itertools.combinations(horses, 2):
            p = _combo_prob_wide(wp, a, b, method, place_s, show_s)
            result[(a, b)] = p
        return result

    if bet_type == "sanrenpuku":
        for trip in itertools.combinations(horses, 3):
            p = _combo_prob_unordered(wp, trip, 3, method, place_s, show_s)
            result[trip] = p
        return result

    if bet_type == "sanrentan":
        for perm in itertools.permutations(horses, 3):
            p = _combo_prob_ordered(wp, perm, method, place_s, show_s)
            result[perm] = p
        return result

    raise ValueError(f"enumerate_combo_probs: 未対応 bet_type={bet_type}")


# ---------------------------------------------------------------------------
# 内部実装ヘルパー
# ---------------------------------------------------------------------------


def _place_prob_single(
    wp: dict[int, float],
    horse: int,
    method: str,
    lgb_place_scores: dict[int, float] | None,
    lgb_show_scores: dict[int, float] | None,
) -> float:
    """1頭の複勝確率 P(horse が 3着以内) を計算。"""
    n = len(wp)
    place_within = 3 if n >= 8 else 2

    if n <= place_within:
        return 1.0  # 全頭複勝対象

    if method == "harville":
        return _harville_place_prob_single(wp, horse, place_within)

    if method == "henery":
        lam_key = "fukusho"
        lam = get_lambda_params().get(lam_key, 0.82)
        eff_wp = _henery_adjusted(wp, lam)
        return _harville_place_prob_single(eff_wp, horse, place_within)

    if method == "lgb":
        if place_within == 3 and lgb_show_scores is not None:
            scores = _normalize(lgb_show_scores)
            return min(scores.get(horse, 0.0), 1.0)
        if place_within == 2 and lgb_place_scores is not None:
            scores = _normalize(lgb_place_scores)
            return min(scores.get(horse, 0.0), 1.0)
        # フォールバック: harville
        return _harville_place_prob_single(wp, horse, place_within)

    raise ValueError(f"未知の method: {method}")


def _harville_place_prob_single(
    wp: dict[int, float], horse: int, place_within: int
) -> float:
    """Harville 公式で 1頭の複勝確率を計算。"""
    pi = wp.get(horse, 0.0)
    others = {k: v for k, v in wp.items() if k != horse}

    # 2着確率
    p2 = 0.0
    for j, pj in others.items():
        denom_j = 1.0 - pj
        if denom_j <= 1e-9:
            continue
        p2 += pj * (pi / denom_j)

    if place_within == 2:
        return min(pi + p2, 1.0)

    # 3着確率
    p3 = 0.0
    for j, pj in others.items():
        denom_j = 1.0 - pj
        if denom_j <= 1e-9:
            continue
        for k, pk in others.items():
            if k == j:
                continue
            p_k_given_j = pk / denom_j
            denom_jk = 1.0 - pj - pk
            if denom_jk <= 1e-9:
                continue
            p3 += pj * p_k_given_j * (pi / denom_jk)

    return min(pi + p2 + p3, 1.0)


def _combo_prob_unordered(
    wp: dict[int, float],
    combo: tuple[int, ...],
    places: int,
    method: str,
    lgb_place_scores: dict[int, float] | None,
    lgb_show_scores: dict[int, float] | None,
) -> float:
    """順不同組合せ確率: 全順列の和。

    馬連 (places=2): Σ P(a=1着,b=2着) + P(b=1着,a=2着)
    三連複 (places=3): Σ_{全順列} P(perm)
    """
    if method == "lgb" and lgb_show_scores is not None and places == 3:
        # LGB + Harville ハイブリッド: P(1着) from v26, P(2-3着|1着) から show_scores で調整
        return _lgb_combo_unordered(wp, lgb_place_scores, lgb_show_scores, combo, places)

    if method == "henery":
        lam_key = "umaren" if places == 2 else "sanrenpuku"
        lam = get_lambda_params().get(lam_key, 0.82)
        eff = _henery_adjusted(wp, lam)
    else:
        eff = wp  # harville or lgb fallback

    total = 0.0
    for perm in itertools.permutations(combo, places):
        total += _harville_joint(eff, perm)
    return min(total, 1.0)


def _combo_prob_ordered(
    wp: dict[int, float],
    combo: tuple[int, ...],
    method: str,
    lgb_place_scores: dict[int, float] | None,
    lgb_show_scores: dict[int, float] | None,
) -> float:
    """三連単（指定順）確率。"""
    if method == "lgb" and lgb_show_scores is not None:
        return _lgb_combo_ordered(wp, lgb_place_scores, lgb_show_scores, combo)

    if method == "henery":
        lam = get_lambda_params().get("sanrentan", 0.82)
        eff = _henery_adjusted(wp, lam)
    elif method in ("harville", "lgb"):
        # lgb フォールバック（モデルなし）またはハーヴィル
        eff = wp
    else:
        raise ValueError(f"未知の method: {method}")

    return _harville_joint(eff, combo)


def _combo_prob_wide(
    wp: dict[int, float],
    a: int,
    b: int,
    method: str,
    lgb_place_scores: dict[int, float] | None,
    lgb_show_scores: dict[int, float] | None,
) -> float:
    """ワイド確率 P(a,b が 3着以内・順不同)。

    = P(a,b が 1-2着) + P(a が 1着・b が 3着) + P(b が 1着・a が 3着)
    + P(他が 1着・a,b が 2-3着)
    """
    n = len(wp)
    if n < 3:
        # 2頭以下は全頭複勝対象なので確率 = 1
        return 1.0

    if method == "henery":
        lam = get_lambda_params().get("wide", 0.82)
        eff = _henery_adjusted(wp, lam)
    elif method == "lgb" and lgb_show_scores is not None:
        eff = _normalize(lgb_show_scores)
    else:
        eff = wp

    # ワイド確率 = 1 - P(a と b が両方4着以下) の余事象
    # 実装: 全順列の中で a,b が共に 3着以内に入るものを列挙
    # → ブルートフォースは O(n^3) だが n≤18 で問題なし
    # 効率化: combo_prob_unordered の変形で {a,b} が 1-3位内に収まる確率
    total = 0.0
    others = [h for h in eff if h != a and h != b]

    # ケース1: a=1着, b=2着
    total += _harville_joint(eff, (a, b))
    # ケース2: b=1着, a=2着
    total += _harville_joint(eff, (b, a))
    # ケース3: a=1着, x=2着, b=3着 (for all x != a,b)
    for x in others:
        total += _harville_joint(eff, (a, x, b))
    # ケース4: b=1着, x=2着, a=3着
    for x in others:
        total += _harville_joint(eff, (b, x, a))
    # ケース5: x=1着, a=2着, b=3着
    for x in others:
        total += _harville_joint(eff, (x, a, b))
    # ケース6: x=1着, b=2着, a=3着
    for x in others:
        total += _harville_joint(eff, (x, b, a))

    return min(total, 1.0)


# ---------------------------------------------------------------------------
# LGB ハイブリッド計算
# ---------------------------------------------------------------------------


def _lgb_combo_ordered(
    wp: dict[int, float],
    lgb_place_scores: dict[int, float] | None,
    lgb_show_scores: dict[int, float] | None,
    combo: tuple[int, ...],
) -> float:
    """LGB ハイブリッド三連単確率。

    P(i=1着) × P(j=2着|i≠) × P(k=3着|i,j≠)
    各条件付き確率は LGB スコアを条件に合わせて再正規化。
    """
    i, j, k = combo
    # P(i = 1着) from v26 win_probs (Harville ベース)
    p1 = wp.get(i, 0.0)

    # P(j = 2着 | i が 1着) — 残馬の place_scores を再正規化
    remaining_for_2 = {h: s for h, s in (lgb_place_scores or wp).items() if h != i}
    if not remaining_for_2:
        return 0.0
    norm2 = _normalize(remaining_for_2)
    p2_given_1 = norm2.get(j, 0.0)

    # P(k = 3着 | i,j 除外後) — 残馬の show_scores を再正規化
    remaining_for_3 = {h: s for h, s in (lgb_show_scores or wp).items() if h != i and h != j}
    if not remaining_for_3:
        return 0.0
    norm3 = _normalize(remaining_for_3)
    p3_given_12 = norm3.get(k, 0.0)

    return p1 * p2_given_1 * p3_given_12


def _lgb_combo_unordered(
    wp: dict[int, float],
    lgb_place_scores: dict[int, float] | None,
    lgb_show_scores: dict[int, float] | None,
    combo: tuple[int, ...],
    places: int,
) -> float:
    """LGB ハイブリッド順不同確率（全順列の和）。"""
    total = 0.0
    for perm in itertools.permutations(combo, places):
        if places == 2:
            i, j = perm
            p1 = wp.get(i, 0.0)
            remaining = {h: s for h, s in (lgb_place_scores or wp).items() if h != i}
            norm2 = _normalize(remaining) if remaining else {}
            p2 = norm2.get(j, 0.0)
            total += p1 * p2
        else:
            total += _lgb_combo_ordered(wp, lgb_place_scores, lgb_show_scores, perm)
    return min(total, 1.0)


# ---------------------------------------------------------------------------
# predict_lgb_scores: DB不使用の推論用ユーティリティ
# ---------------------------------------------------------------------------


def predict_lgb_scores(features: dict[int, list[float]]) -> tuple[dict[int, float], dict[int, float]]:
    """学習済み LGB モデルで 2着以内・3着以内スコアを予測する。

    Args:
        features: {horse_id: feature_vector} の辞書
                  特徴量順は train_finish_order_lgb.py の FEATURES と一致

    Returns:
        (place_scores, show_scores) — 各 {horse_id: raw_score}
        モデル未ロードの場合は空辞書を返す。
    """
    place_model, show_model = _load_lgb_models()
    if place_model is None or show_model is None:
        return {}, {}

    horse_ids = list(features.keys())
    X = np.array([features[h] for h in horse_ids], dtype=np.float32)
    place_preds = place_model.predict(X)
    show_preds = show_model.predict(X)
    return (
        {h: float(s) for h, s in zip(horse_ids, place_preds)},
        {h: float(s) for h, s in zip(horse_ids, show_preds)},
    )
