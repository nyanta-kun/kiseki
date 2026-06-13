"""Henery/Stern 割引指数モデルの λ パラメータをフィットする (T03)。

券種ごとに最適な λ ∈ (0, 1] を実際の払戻データから MLE で推定する。
λ=1.0 のとき Harville と同一（ベースライン）。

出力: backend/models/finish_order_lambda.json

使用例:
    python scripts/fit_finish_order_lambda.py --start 20230101 --end 20250630
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
import psycopg2
from scipy.optimize import minimize_scalar  # type: ignore[import]

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fit_lambda")

MODELS_DIR = _root / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# データ取得クエリ
# ---------------------------------------------------------------------------

_BASE_QUERY = """
SELECT
    ci.race_id,
    ci.horse_id,
    ci.win_probability,
    rr.finish_position,
    r.head_count
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = 26
  AND ci.win_probability IS NOT NULL
  AND r.date BETWEEN %(start)s AND %(end)s
  AND r.head_count >= 8
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND COALESCE(rr.abnormality_code, 0) = 0
ORDER BY ci.race_id, rr.finish_position
"""


def fetch_race_data(conn: "psycopg2.connection", start: str, end: str) -> list[dict]:
    """計算済み指数と着順を取得する。期間チャンク不要（3ヶ月〜1年窓想定）。"""
    cur = conn.cursor()
    cur.execute(_BASE_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    logger.info(f"取得: {len(rows):,}行 ({start}〜{end})")
    return [dict(zip(cols, r)) for r in rows]


def group_by_race(records: list[dict]) -> dict[int, list[dict]]:
    """race_id でグループ化する。"""
    groups: dict[int, list[dict]] = {}
    for r in records:
        groups.setdefault(r["race_id"], []).append(r)
    return groups


# ---------------------------------------------------------------------------
# 対数尤度計算
# ---------------------------------------------------------------------------


def _henery_probs(win_probs: list[float], lam: float) -> list[float]:
    """Henery 割引: p_i^λ で正規化。"""
    adjusted = [max(p, 1e-12) ** lam for p in win_probs]
    total = sum(adjusted)
    if total <= 1e-12:
        n = len(adjusted)
        return [1.0 / n] * n
    return [a / total for a in adjusted]


def _harville_joint_list(probs: list[float], order_indices: list[int]) -> float:
    """Harville 同時確率（リスト版）。"""
    rem = list(probs)
    p = 1.0
    for idx in order_indices:
        total = sum(rem)
        if total <= 1e-12:
            return 1e-15
        p *= rem[idx] / total
        rem[idx] = 0.0
    return max(p, 1e-15)


def _neg_log_likelihood_umaren(lam: float, race_groups: dict[int, list[dict]]) -> float:
    """馬連（1-2着組合せ）の負対数尤度。"""
    nll = 0.0
    for records in race_groups.values():
        sorted_r = sorted(records, key=lambda x: x["finish_position"])
        probs = [float(r["win_probability"]) for r in sorted_r]
        probs_h = _henery_probs(probs, lam)
        # 実際の 1-2着馬インデックス
        idx1 = 0  # finish_position=1
        idx2 = 1  # finish_position=2
        p_joint = _harville_joint_list(probs_h, [idx1, idx2])
        # 馬連は順不同なので P(1-2着) + P(2-1着)
        p_rev = _harville_joint_list(probs_h, [idx2, idx1])
        nll -= np.log(p_joint + p_rev)
    return nll


def _neg_log_likelihood_sanrenpuku(lam: float, race_groups: dict[int, list[dict]]) -> float:
    """三連複（1-2-3着順不同）の負対数尤度。"""
    nll = 0.0
    for records in race_groups.values():
        sorted_r = sorted(records, key=lambda x: x["finish_position"])
        if len(sorted_r) < 3:
            continue
        probs = [float(r["win_probability"]) for r in sorted_r]
        probs_h = _henery_probs(probs, lam)
        # 全6通りの順列の和
        p_total = 0.0
        for perm in [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]:
            p_total += _harville_joint_list(probs_h, list(perm))
        nll -= np.log(max(p_total, 1e-15))
    return nll


def _neg_log_likelihood_sanrentan(lam: float, race_groups: dict[int, list[dict]]) -> float:
    """三連単（1-2-3着指定順）の負対数尤度。"""
    nll = 0.0
    for records in race_groups.values():
        sorted_r = sorted(records, key=lambda x: x["finish_position"])
        if len(sorted_r) < 3:
            continue
        probs = [float(r["win_probability"]) for r in sorted_r]
        probs_h = _henery_probs(probs, lam)
        p = _harville_joint_list(probs_h, [0, 1, 2])
        nll -= np.log(p)
    return nll


def _neg_log_likelihood_fukusho(lam: float, race_groups: dict[int, list[dict]]) -> float:
    """複勝（3着以内）の負対数尤度。"""
    from itertools import permutations

    nll = 0.0
    for records in race_groups.values():
        sorted_r = sorted(records, key=lambda x: x["finish_position"])
        n = len(sorted_r)
        place_within = 3 if n >= 8 else 2
        probs = [float(r["win_probability"]) for r in sorted_r]
        probs_h = _henery_probs(probs, lam)

        # 各着順馬の複勝確率を計算して尤度に加算
        # target = place_within 着以内に入った馬たちの確率
        place_horses = [i for i, r in enumerate(sorted_r) if r["finish_position"] <= place_within]
        for idx in place_horses:
            # P(horse idx が place_within 着以内)
            pi = probs_h[idx]
            others = [probs_h[j] for j in range(n) if j != idx]
            p2 = sum(
                pj * (pi / (1.0 - pj)) for pj in others if 1.0 - pj > 1e-9
            )
            if place_within == 3:
                p3 = 0.0
                for ji, pj in enumerate(others):
                    denom_j = 1.0 - pj
                    if denom_j <= 1e-9:
                        continue
                    for ki, pk in enumerate(others):
                        if ki == ji:
                            continue
                        p_k_given_j = pk / denom_j
                        denom_jk = 1.0 - pj - pk
                        if denom_jk <= 1e-9:
                            continue
                        p3 += pj * p_k_given_j * (pi / denom_jk)
                nll -= np.log(max(min(pi + p2 + p3, 1.0), 1e-15))
            else:
                nll -= np.log(max(min(pi + p2, 1.0), 1e-15))
    return nll


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

BET_TYPES = {
    "umaren": _neg_log_likelihood_umaren,
    "sanrenpuku": _neg_log_likelihood_sanrenpuku,
    "sanrentan": _neg_log_likelihood_sanrentan,
    "fukusho": _neg_log_likelihood_fukusho,
}


def fit_lambda(nll_fn, race_groups: dict, name: str) -> tuple[float, float]:
    """brent 法で λ ∈ [0.3, 1.0] を最適化する。"""
    # Harville (λ=1.0) の NLL をベースラインに
    baseline_nll = nll_fn(1.0, race_groups)
    result = minimize_scalar(
        lambda lam: nll_fn(lam, race_groups),
        bounds=(0.3, 1.0),
        method="bounded",
        options={"xatol": 1e-4, "maxiter": 200},
    )
    lam_opt = float(result.x)
    opt_nll = float(result.fun)
    nll_gain = baseline_nll - opt_nll
    logger.info(
        f"[{name}] λ={lam_opt:.4f}  NLL={opt_nll:.2f}  Δ(baseline)={nll_gain:.2f}"
    )
    return lam_opt, nll_gain


def main() -> None:
    """エントリポイント。"""
    p = argparse.ArgumentParser(description="Finish order lambda fitting")
    p.add_argument("--start", default="20230101", help="学習開始日 (YYYYMMDD)")
    p.add_argument("--end", default="20250630", help="学習終了日 (YYYYMMDD)")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    records = fetch_race_data(conn, args.start, args.end)
    conn.close()

    if not records:
        logger.error("データが取得できませんでした。DB 接続・期間設定を確認してください。")
        sys.exit(1)

    race_groups = group_by_race(records)
    logger.info(f"レース数: {len(race_groups):,}")

    result: dict[str, float] = {}
    for bet_type, nll_fn in BET_TYPES.items():
        lam, gain = fit_lambda(nll_fn, race_groups, bet_type)
        result[bet_type] = lam
        result[f"{bet_type}_nll_gain"] = round(gain, 4)

    # wide は umaren と同じ λ で近似（3着以内の組合せ）
    result["wide"] = result["umaren"]
    # tansho は λ 無関係（1着のみ）だが整合性のため 1.0 を格納
    result["tansho"] = 1.0

    out_path = MODELS_DIR / "finish_order_lambda.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"保存: {out_path}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
