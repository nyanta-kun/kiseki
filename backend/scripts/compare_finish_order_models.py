"""3手法の着順確率モデル比較レポートを生成する (T03)。

比較対象:
  1. harville  — ベースライン
  2. henery    — λ 割引指数モデル（λ は finish_order_lambda.json を参照）
  3. lgb       — 2-3着専用 LightGBM（モデルファイルがある場合のみ）

評価指標:
  - 券種別 log-loss: 馬連・三連複・三連単の的中組合せ確率の対数尤度
  - 較正: 予測確率 decile vs 実現率

使用例:
    python scripts/compare_finish_order_models.py --start 20250101 --end 20250331
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path

import numpy as np
import psycopg2

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from src.betting.finish_order import (
    _normalize,
    combo_probability,
    enumerate_combo_probs,
    get_lambda_params,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("compare_finish_order")

# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

_QUERY = """
SELECT
    ci.race_id,
    ci.horse_id,
    ci.win_probability,
    rr.finish_position,
    -- 馬体重・斤量
    re.frame_number, re.horse_weight, re.weight_carried, rr.weight_change,
    -- レースメタ
    r.distance, r.head_count, r.surface, r.condition,
    -- v24 サブ指数
    ci.speed_index, ci.last_3f_index, ci.jockey_index,
    ci.course_aptitude, ci.pace_index, ci.rotation_index
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = 26
  AND ci.win_probability IS NOT NULL
  AND r.date BETWEEN %(start)s AND %(end)s
  AND r.head_count >= 8
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND COALESCE(rr.abnormality_code, 0) = 0
ORDER BY ci.race_id, rr.finish_position
"""


def fetch_data(conn: "psycopg2.connection", start: str, end: str) -> dict[int, list[dict]]:
    """データ取得してレース単位にグループ化する。"""
    cur = conn.cursor()
    cur.execute(_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()

    from collections import defaultdict
    groups: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        d = dict(zip(cols, row))
        groups[d["race_id"]].append(d)

    # 着順でソート
    for rid in groups:
        groups[rid].sort(key=lambda x: x["finish_position"] or 99)

    n_races = len(groups)
    n_horses = sum(len(v) for v in groups.values())
    logger.info(f"取得: {n_races:,}レース / {n_horses:,}頭 ({start}〜{end})")
    return dict(groups)


# ---------------------------------------------------------------------------
# LGB スコア取得（モデルが存在する場合）
# ---------------------------------------------------------------------------

# train_finish_order_lgb.py の FEATURES と同一順序であること（point-in-time 特徴のみ）
PLACE_FEATURES = [
    "win_probability",
    "horse_weight", "weight_carried", "weight_change",
    "frame_number",
    "distance", "head_count",
    "is_turf", "is_dirt", "is_jump",
    "is_good", "is_yaya", "is_heavy", "is_bad",
    "speed_index", "last_3f_index", "jockey_index",
    "course_aptitude", "pace_index", "rotation_index",
]

MODELS_DIR = _root / "models"


def _featurize_row(row: dict) -> list[float]:
    """1行分の特徴量ベクトルを生成する（train_finish_order_lgb.py と同一ロジック）。"""
    s = str(row.get("surface") or "")
    c = str(row.get("condition") or "")

    def safe_float(v: object, default: float = 0.0) -> float:
        try:
            return float(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    return [
        safe_float(row.get("win_probability")),
        safe_float(row.get("horse_weight")),
        safe_float(row.get("weight_carried")),
        safe_float(row.get("weight_change")),
        safe_float(row.get("frame_number")),
        safe_float(row.get("distance")),
        safe_float(row.get("head_count")),
        1 if s.startswith("芝") else 0,
        1 if s.startswith("ダ") else 0,
        1 if s.startswith("障") else 0,
        1 if c == "良" else 0,
        1 if c == "稍" else 0,
        1 if c == "重" else 0,
        1 if c == "不" else 0,
        safe_float(row.get("speed_index")),
        safe_float(row.get("last_3f_index")),
        safe_float(row.get("jockey_index")),
        safe_float(row.get("course_aptitude")),
        safe_float(row.get("pace_index")),
        safe_float(row.get("rotation_index")),
    ]


def load_lgb_scores(race_groups: dict[int, list[dict]]) -> dict[int, dict[str, dict[int, float]]]:
    """LGB モデルが存在する場合に、レース×馬のスコアを事前計算する。

    Returns:
        {race_id: {"place": {horse_id: score}, "show": {horse_id: score}}}
    """
    place_path = MODELS_DIR / "finish_order_lgb_place.txt"
    show_path = MODELS_DIR / "finish_order_lgb_show.txt"

    if not (place_path.exists() and show_path.exists()):
        logger.warning("LGB モデルファイルが見つかりません。LGB 手法をスキップします。")
        return {}

    import lightgbm as lgb_module
    place_model = lgb_module.Booster(model_file=str(place_path))
    show_model = lgb_module.Booster(model_file=str(show_path))

    if place_model.feature_name() != PLACE_FEATURES:
        raise RuntimeError(
            "学習済みモデルの特徴量と PLACE_FEATURES が不一致です。"
            f" model={place_model.feature_name()} / expected={PLACE_FEATURES}"
        )

    import numpy as np
    result: dict[int, dict[str, dict[int, float]]] = {}

    for race_id, records in race_groups.items():
        horse_ids = [r["horse_id"] for r in records]
        X = np.array([_featurize_row(r) for r in records], dtype=np.float32)

        # NaN を 0 で補完
        X = np.nan_to_num(X, nan=0.0)

        place_preds = place_model.predict(X)
        show_preds = show_model.predict(X)

        result[race_id] = {
            "place": {h: float(p) for h, p in zip(horse_ids, place_preds)},
            "show": {h: float(p) for h, p in zip(horse_ids, show_preds)},
        }

    logger.info(f"LGB スコア計算完了: {len(result):,}レース")
    return result


# ---------------------------------------------------------------------------
# 評価
# ---------------------------------------------------------------------------


def compute_logloss(
    race_groups: dict[int, list[dict]],
    method: str,
    lgb_scores: dict | None = None,
) -> dict[str, float]:
    """券種別 log-loss を計算する。

    各レースの実際の的中組合せに対して予測確率の負対数尤度を計算し平均する。

    Returns:
        {"umaren": ll, "sanrenpuku": ll, "sanrentan": ll}
    """
    bet_types = ["umaren", "sanrenpuku", "sanrentan"]
    losses: dict[str, list[float]] = {bt: [] for bt in bet_types}

    for race_id, records in race_groups.items():
        # win_probs
        wp = _normalize({r["horse_id"]: float(r["win_probability"] or 0) for r in records})

        # 的中馬の特定
        sorted_r = sorted(records, key=lambda x: x["finish_position"] or 99)
        if len(sorted_r) < 3:
            continue

        h1 = sorted_r[0]["horse_id"]
        h2 = sorted_r[1]["horse_id"]
        h3 = sorted_r[2]["horse_id"]

        # LGB スコア取得
        place_s = lgb_scores.get(race_id, {}).get("place") if lgb_scores else None
        show_s = lgb_scores.get(race_id, {}).get("show") if lgb_scores else None

        for bet_type in bet_types:
            if bet_type == "umaren":
                # 馬連: 1-2着順不同
                combo: tuple[int, ...] = tuple(sorted([h1, h2]))
            elif bet_type == "sanrenpuku":
                # 三連複: 1-2-3着順不同
                combo = tuple(sorted([h1, h2, h3]))
            else:
                # 三連単: 1-2-3着指定順
                combo = (h1, h2, h3)

            prob = combo_probability(wp, combo, bet_type, method, place_s, show_s)
            prob = max(prob, 1e-10)
            losses[bet_type].append(-math.log(prob))

    return {bt: round(float(np.mean(v)), 6) if v else float("nan") for bt, v in losses.items()}


def compute_calibration(
    race_groups: dict[int, list[dict]],
    method: str,
    bet_type: str = "umaren",
    n_deciles: int = 10,
    lgb_scores: dict | None = None,
) -> list[dict]:
    """馬連の予測確率 decile vs 実現率を計算する。"""
    preds = []
    actuals = []

    for race_id, records in race_groups.items():
        wp = _normalize({r["horse_id"]: float(r["win_probability"] or 0) for r in records})
        sorted_r = sorted(records, key=lambda x: x["finish_position"] or 99)
        if len(sorted_r) < 2:
            continue
        h1 = sorted_r[0]["horse_id"]
        h2 = sorted_r[1]["horse_id"]

        # LGB スコア取得
        place_s = lgb_scores.get(race_id, {}).get("place") if lgb_scores else None
        show_s = lgb_scores.get(race_id, {}).get("show") if lgb_scores else None

        # 全馬連組合せの確率
        combos = enumerate_combo_probs(wp, bet_type, method, place_s, show_s)
        actual_combo = tuple(sorted([h1, h2]))

        for combo, prob in combos.items():
            preds.append(prob)
            actuals.append(1 if combo == actual_combo else 0)

    preds_arr = np.array(preds)
    actuals_arr = np.array(actuals)
    decile_edges = np.percentile(preds_arr, np.linspace(0, 100, n_deciles + 1))

    calib = []
    for i in range(n_deciles):
        lo, hi = decile_edges[i], decile_edges[i + 1]
        mask = (preds_arr >= lo) & (preds_arr < hi if i < n_deciles - 1 else preds_arr <= hi)
        if mask.sum() == 0:
            continue
        calib.append({
            "decile": i + 1,
            "pred_mean": round(float(preds_arr[mask].mean()), 6),
            "actual_rate": round(float(actuals_arr[mask].mean()), 6),
            "n": int(mask.sum()),
        })
    return calib


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """エントリポイント。"""
    p = argparse.ArgumentParser(description="Finish order model comparison")
    p.add_argument("--start", required=True, help="開始日 (YYYYMMDD)")
    p.add_argument("--end", required=True, help="終了日 (YYYYMMDD)")
    p.add_argument("--no-lgb", action="store_true", help="LGB 手法をスキップ")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    race_groups = fetch_data(conn, args.start, args.end)
    conn.close()

    if not race_groups:
        logger.error("データが取得できませんでした。")
        sys.exit(1)

    # λ パラメータ表示
    lam_params = get_lambda_params()
    logger.info(f"λ パラメータ: {lam_params}")

    # LGB スコア事前計算
    lgb_scores: dict | None = None
    methods = ["harville", "henery"]
    if not args.no_lgb:
        lgb_scores = load_lgb_scores(race_groups)
        if lgb_scores:
            methods.append("lgb")

    # log-loss 比較
    print("\n" + "=" * 70)
    print(f"着順確率モデル比較: {args.start}〜{args.end}  ({len(race_groups):,}レース)")
    print("=" * 70)
    print(f"{'手法':<10}  {'馬連 LL':>10}  {'三連複 LL':>10}  {'三連単 LL':>10}")
    print("-" * 50)

    all_results: dict[str, dict] = {}
    for method in methods:
        logger.info(f"評価中: {method}")
        ll_dict = compute_logloss(race_groups, method, lgb_scores if method == "lgb" else None)
        all_results[method] = ll_dict
        print(
            f"{method:<10}  {ll_dict['umaren']:>10.5f}  "
            f"{ll_dict['sanrenpuku']:>10.5f}  {ll_dict['sanrentan']:>10.5f}"
        )

    print("=" * 70)

    # 較正表（harville vs henery）
    print("\n=== 馬連 較正表（harville）===")
    calib_h = compute_calibration(race_groups, "harville", "umaren")
    print(f"{'Decile':<8}  {'予測確率':>10}  {'実現率':>10}  {'n':>8}")
    for row in calib_h:
        print(f"{row['decile']:<8}  {row['pred_mean']:>10.6f}  {row['actual_rate']:>10.6f}  {row['n']:>8,}")

    if "henery" in methods:
        print("\n=== 馬連 較正表（henery）===")
        calib_e = compute_calibration(race_groups, "henery", "umaren")
        print(f"{'Decile':<8}  {'予測確率':>10}  {'実現率':>10}  {'n':>8}")
        for row in calib_e:
            print(f"{row['decile']:<8}  {row['pred_mean']:>10.6f}  {row['actual_rate']:>10.6f}  {row['n']:>8,}")

    # JSON 出力
    report = {
        "period": {"start": args.start, "end": args.end},
        "n_races": len(race_groups),
        "lambda_params": lam_params,
        "log_loss": all_results,
        "calibration_umaren_harville": calib_h,
    }
    print("\n=== JSON サマリ ===")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    # 採用推奨
    print("\n=== 採用推奨 ===")
    best = min(all_results, key=lambda m: all_results[m].get("sanrenpuku", 1e9))
    print(f"三連複 log-loss 最小: {best}")
    best_tan = min(all_results, key=lambda m: all_results[m].get("sanrentan", 1e9))
    print(f"三連単 log-loss 最小: {best_tan}")
    print("※ log-loss が小さいほど予測確率が実際の的中に近い")
    print("※ Harville との差 < 0.001 なら Harville を推奨（シンプルさ優先）")


if __name__ == "__main__":
    main()
