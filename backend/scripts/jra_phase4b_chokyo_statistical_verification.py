"""Phase4-B 調教(坂路)特徴量効果検証の統計的厳密化スクリプト。

`train_v26_chokyo.py`（2026-07-26 拡張版）で確認された「坂路特徴量追加で
複勝的中率が +0.5〜0.9pt 改善する」という結果について、その差が
「5 seed の学習分散」の範囲内でしか検証されておらず、「test 集合 1,200
レースのサンプリング誤差」を考慮していない問題を解消する。

DB 書き込みは一切行わない研究用スクリプト（読み取り専用クエリのみ）。
データ取得・特徴量エンジニアリング・学習・評価の実装はすべて
`train_v26_chokyo.py` / `train_v26_lightgbm.py` の既存関数を import して
再利用し、独自の再実装はしない。

やること:
  1. レース単位ブートストラップ（test集合を重複ありで1,000回再抽出）で
     simple5-baseline / full8-baseline の複勝的中率差の 95% 信頼区間を算出。
     区間が 0 を跨ぐかどうかで「統計的に有意な差か」を判定する。
  2. Drop1 テスト: full8(8特徴)から特徴を1つずつ除いた7特徴モデルを
     8パターン学習し、full8 との複勝的中率差から各特徴の寄与を特定する。

使い方:
  PYTHONPATH=. .venv/bin/python scripts/jra_phase4b_chokyo_statistical_verification.py \
      --boot-seeds 2 --n-boot 1000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

load_dotenv(_root.parent / ".env")

from scripts.train_v26_chokyo import (  # noqa: E402
    CHOKYO_FEATURES_FULL,
    CHOKYO_FEATURES_SIMPLE,
    _train,
    attach_chokyo,
    fetch_dataset_ver,
    load_slope,
)
from scripts.train_v26_lightgbm import ALL_FEATURES, featurize  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase4b_stat_verify")


def top1_place_hits(df_test: pd.DataFrame, scores: np.ndarray) -> pd.Series:
    """レース単位で「指数1位馬が複勝(3着以内)的中したか」の0/1 Seriesを返す。

    index=race_id, value∈{0,1}。レース順序は df_test 内の race_id 出現順に
    依存しないよう、常に race_id でソートして返す（複数モデル間の突合を
    確実にするため）。
    """
    df = df_test.copy()
    df["score"] = scores
    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    top1 = df.loc[df.groupby("race_id")["score"].idxmax()]
    hits = (top1["finish_position"] <= 3).astype(int)
    hits.index = top1["race_id"].values
    return hits.sort_index()


def bootstrap_diff_ci(
    arr_a: np.ndarray, arr_b: np.ndarray, n_boot: int, rng: np.random.Generator
) -> dict:
    """レース単位ブートストラップで mean(arr_a) - mean(arr_b) の95%CIを返す。

    arr_a, arr_b は同じレース順(同じ長さ)に整列済みであること(paired)。
    各試行で同一の再抽出インデックスを両方の配列に適用する(paired bootstrap)。
    """
    n = len(arr_a)
    assert len(arr_b) == n
    idx = rng.integers(0, n, size=(n_boot, n))
    diffs = arr_a[idx].mean(axis=1) - arr_b[idx].mean(axis=1)
    point = float(arr_a.mean() - arr_b.mean())
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {
        "point_estimate_pt": round(point * 100, 3),
        "ci95_low_pt": round(float(lo) * 100, 3),
        "ci95_high_pt": round(float(hi) * 100, 3),
        "crosses_zero": bool(lo <= 0 <= hi),
        "n_boot": n_boot,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--num-leaves", type=int, default=31)
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--min-data-in-leaf", type=int, default=100)
    p.add_argument("--lambda-l1", type=float, default=0.1)
    p.add_argument("--lambda-l2", type=float, default=0.1)
    p.add_argument("--feature-fraction", type=float, default=0.7)
    p.add_argument("--bagging-fraction", type=float, default=0.7)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--num-iterations", type=int, default=500)
    # train_v26_chokyo.py の既定(拡張窓)をそのまま踏襲
    p.add_argument("--train-start", default="20250701")
    p.add_argument("--train-end", default="20260131")
    p.add_argument("--valid-start", default="20260201")
    p.add_argument("--valid-end", default="20260315")
    p.add_argument("--test-start", default="20260316")
    p.add_argument("--test-end", default="20260726")
    p.add_argument("--slope-floor", default="20250527")
    p.add_argument("--indices-version", type=int, default=26)
    p.add_argument("--boot-seeds", type=int, default=2,
                   help="baseline/simple5/full8/drop1 の学習に使う seed 数(平均で評価)")
    p.add_argument("--n-boot", type=int, default=1000, help="ブートストラップ試行回数")
    p.add_argument("--boot-rng-seed", type=int, default=42)
    p.add_argument("--out", default=str(_root / "models" / "v26_phase4b_bootstrap_drop1.json"))
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)

    cstats, works = load_slope(conn, args.test_end, args.slope_floor)

    df_train = attach_chokyo(
        featurize(fetch_dataset_ver(conn, args.train_start, args.train_end, args.indices_version)),
        works, cstats,
    )
    df_valid = attach_chokyo(
        featurize(fetch_dataset_ver(conn, args.valid_start, args.valid_end, args.indices_version)),
        works, cstats,
    )
    df_test = attach_chokyo(
        featurize(fetch_dataset_ver(conn, args.test_start, args.test_end, args.indices_version)),
        works, cstats,
    )
    conn.close()

    for d in (df_train, df_valid, df_test):
        d["y"] = (pd.to_numeric(d["finish_position"], errors="coerce") <= 3).astype(int)

    n_races_test = df_test["race_id"].nunique()
    logger.info(
        f"レース数: train={len(df_train['race_id'].unique()):,} "
        f"valid={len(df_valid['race_id'].unique()):,} test={n_races_test:,}"
    )

    # ---------------------------------------------------------------
    # 1) baseline / simple5 / full8 を boot-seeds 回学習し、
    #    レース単位 0/1 複勝的中配列を seed 平均して保持する。
    # ---------------------------------------------------------------
    arms = {
        "base": ALL_FEATURES,
        "simple5": ALL_FEATURES + CHOKYO_FEATURES_SIMPLE,
        "full8": ALL_FEATURES + CHOKYO_FEATURES_FULL,
    }
    drop1_arms = {
        f"full8_drop_{f}": ALL_FEATURES + [c for c in CHOKYO_FEATURES_FULL if c != f]
        for f in CHOKYO_FEATURES_FULL
    }
    all_arms = {**arms, **drop1_arms}

    # hits_by_seed[arm][seed] = pd.Series(index=race_id, value 0/1)
    hits_by_seed: dict[str, list[pd.Series]] = {a: [] for a in all_arms}
    place_pct_by_seed: dict[str, list[float]] = {a: [] for a in all_arms}

    for seed in range(args.boot_seeds):
        for arm, feats in all_arms.items():
            logger.info(f"=== seed {seed}: {arm} ({len(feats)}特徴) ===")
            model = _train(df_train, df_valid, feats, args, seed)
            scores = model.predict(df_test[feats].values, num_iteration=model.best_iteration)
            hits = top1_place_hits(df_test, scores)
            hits_by_seed[arm].append(hits)
            place_pct_by_seed[arm].append(float(hits.mean() * 100))

    # レース順序を全 seed・全 arm で共通化(race_id ソート済みなのでindexは一致するはず)
    race_ids_ref = hits_by_seed["base"][0].index
    for arm, series_list in hits_by_seed.items():
        for s in series_list:
            assert list(s.index) == list(race_ids_ref), f"race_id順序不一致: {arm}"

    # seed平均のレース単位配列(値は{0, 0.5, 1, ...}の平均値。ブートストラップ入力)
    avg_hits: dict[str, np.ndarray] = {
        arm: np.mean([s.values for s in series_list], axis=0)
        for arm, series_list in hits_by_seed.items()
    }

    # ---------------------------------------------------------------
    # 2) レース単位ペアブートストラップで simple5-base, full8-base の95%CI
    # ---------------------------------------------------------------
    rng = np.random.default_rng(args.boot_rng_seed)
    boot_results = {
        "simple5_vs_base": bootstrap_diff_ci(
            avg_hits["simple5"], avg_hits["base"], args.n_boot, rng
        ),
        "full8_vs_base": bootstrap_diff_ci(
            avg_hits["full8"], avg_hits["base"], args.n_boot, rng
        ),
    }

    print("\n" + "=" * 80)
    print(f"[ブートストラップ 95%CI] test={args.test_start}〜{args.test_end} "
          f"({n_races_test}レース) × {args.n_boot}回再抽出 × {args.boot_seeds}seed平均")
    print("-" * 80)
    for k, v in boot_results.items():
        flag = "0を跨ぐ(有意とは言えない)" if v["crosses_zero"] else "0を跨がない(統計的に有意)"
        print(f"  {k}: 点推定 {v['point_estimate_pt']:+.3f}pt  "
              f"95%CI [{v['ci95_low_pt']:+.3f}, {v['ci95_high_pt']:+.3f}]pt  -> {flag}")
    print("=" * 80)

    # ---------------------------------------------------------------
    # 3) Drop1 テスト: full8 と各 drop1 の複勝的中率(seed平均)を比較
    # ---------------------------------------------------------------
    full8_pct_mean = float(np.mean(place_pct_by_seed["full8"]))
    drop1_table = []
    for f in CHOKYO_FEATURES_FULL:
        arm = f"full8_drop_{f}"
        pct_mean = float(np.mean(place_pct_by_seed[arm]))
        diff = pct_mean - full8_pct_mean
        drop1_table.append({
            "dropped_feature": f,
            "place_pct_mean": round(pct_mean, 3),
            "diff_vs_full8_pt": round(diff, 3),
        })
    drop1_table.sort(key=lambda r: r["diff_vs_full8_pt"])  # 最も悪化(=寄与大)が先頭

    print("\n" + "=" * 80)
    print(f"[Drop1テスト] full8複勝的中率(seed平均) = {full8_pct_mean:.3f}%")
    print("-" * 80)
    print(f"{'除いた特徴':<26}{'複勝的中率%':>14}{'full8比Δ':>14}")
    for row in drop1_table:
        print(f"{row['dropped_feature']:<26}{row['place_pct_mean']:>14.3f}"
              f"{row['diff_vs_full8_pt']:>+14.3f}")
    print("=" * 80)
    print("(Δが大きくマイナス = その特徴を除くと悪化 = 寄与が大きい)")
    print("(Δが0付近 = その特徴を除いても変わらない = 冗長)")

    # ---------------------------------------------------------------
    # 保存
    # ---------------------------------------------------------------
    summary = {
        "generated_at": datetime.now().isoformat(),
        "boot_seeds": args.boot_seeds,
        "n_boot": args.n_boot,
        "indices_version": args.indices_version,
        "windows": {
            "train": [args.train_start, args.train_end],
            "valid": [args.valid_start, args.valid_end],
            "test": [args.test_start, args.test_end],
        },
        "n_races_test": int(n_races_test),
        "arm_place_pct_seed_mean": {
            arm: round(float(np.mean(v)), 3) for arm, v in place_pct_by_seed.items()
        },
        "arm_place_pct_seed_values": {
            arm: [round(x, 3) for x in v] for arm, v in place_pct_by_seed.items()
        },
        "bootstrap_ci": boot_results,
        "drop1_table": drop1_table,
    }

    out_path = Path(args.out)
    if out_path.exists():
        logger.warning(f"{out_path} は既に存在するため上書きします")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    logger.info(f"サマリー保存: {out_path}")


if __name__ == "__main__":
    main()
