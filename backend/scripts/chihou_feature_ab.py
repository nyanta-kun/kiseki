"""地方競馬 特徴量セットの A/B（ランキング品質ベース）

目的:
  1. **死んでいる外部指数2本（nk_idx_z / nk_rank_n）を落として良いか**
     `check_chihou_feature_health.py` で netkeiba 由来の2特徴が 2026-06 以降
     フォールバック占有率 100%（実質定数）と判明した。本番 v12 は netkeiba が
     生きていた時期に学習されているため、現在の serve は train/serve skew 状態にある。
  2. **未使用の高充足列を足して効くか**
     DB 棚卸しで判明した 100% 充足の未使用列を特徴化する:
       prize_log      : log1p(1着賞金)。地方はクラス体系が場ごとに独立しており、
                        賞金額が唯一「場をまたいで比較可能なレース格の連続量」
       prize_gap_prev : log(今走賞金) − log(前走賞金)。実質的な昇降級の連続量
                        （既存 class_drop_ratio は二値寄りの別実装）
       post_hour      : 発走時刻の時（10〜20）
       is_night       : ナイター（18時以降発走）
       appr_kg        : 騎手見習の減量kg（0/5/3/1）
       wt_type        : 重量種別コード（馬齢/定量/別定/ハンデ等）

評価は `chihou_rank_quality_review.py` と同じ HEAD/TAIL/ALL + レース単位 paired
bootstrap。目的関数は同レビューで最良だった **is_top3 二値（現行と同じ）** に固定し、
特徴量セットだけを動かす。

honest 分割は `src/chihou_protocol.py` 準拠（train ≤20250630 / valid 〜20251231 /
test 20260101〜20260630）。TEST_START(20260701) 以降は使わない。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_feature_ab.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.chihou_rank_quality_review import (  # noqa: E402
    DATA_START,
    VALID_END,
    connect,
    evaluate,
    paired_bootstrap,
    per_race_metrics,
    train_binary,
)
from scripts.train_chihou_market_lgb import ALL_FEATURES, fetch, prep  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.chihou_protocol import TRAIN_END  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_feature_ab")

MODELS_DIR = _root / "models"

# 上流停止で死んでいる外部指数（netkeiba 由来）
DEAD_FEATURES = ["nk_idx_z", "nk_rank_n"]
# 新規候補
NEW_FEATURES = ["prize_log", "prize_gap_prev", "post_hour", "is_night", "appr_kg", "wt_type"]

# 見習コード → 減量kg。'4' は仕様書に記載がないため 1kg 相当として扱う（実測 2,273件）
APPRENTICE_KG = {"0": 0.0, "1": 5.0, "2": 3.0, "3": 1.0, "4": 1.0}

EXTRA_SQL = """
SELECT
    r.id AS race_id, re.horse_id,
    r.prize_1st, r.post_time, r.weight_type_code,
    re.jockey_apprentice_code,
    LAG(r.prize_1st) OVER (PARTITION BY re.horse_id ORDER BY r.date, r.id) AS prev_prize
FROM chihou.races r
JOIN chihou.race_entries re ON re.race_id = r.id
WHERE r.course != '83' AND r.date BETWEEN %(s)s AND %(e)s
"""


def fetch_extra(conn, start: str, end: str) -> pd.DataFrame:
    """未使用列を取得し特徴化する。prev_prize は馬ごとの時系列 LAG（point-in-time）。"""
    cur = conn.cursor()
    cur.execute(EXTRA_SQL, {"s": start, "e": end})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()

    prize = pd.to_numeric(df["prize_1st"], errors="coerce")
    prev = pd.to_numeric(df["prev_prize"], errors="coerce")
    df["prize_log"] = np.log1p(prize.fillna(prize.median()))
    # 前走なし（初出走）は 0.0＝据え置き扱い
    df["prize_gap_prev"] = (np.log1p(prize) - np.log1p(prev)).fillna(0.0).clip(-3.0, 3.0)

    hh = df["post_time"].astype(str).str.slice(0, 2)
    df["post_hour"] = pd.to_numeric(hh, errors="coerce").fillna(15.0)
    df["is_night"] = (df["post_hour"] >= 18).astype(int)

    df["appr_kg"] = (df["jockey_apprentice_code"].astype(str)
                     .map(APPRENTICE_KG).fillna(0.0))
    df["wt_type"] = pd.to_numeric(df["weight_type_code"], errors="coerce").fillna(-1.0)

    return df[["race_id", "horse_id", *NEW_FEATURES]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--out-threshold", type=int, default=5)
    p.add_argument("--json-out", default=str(MODELS_DIR / "chihou_feature_ab.json"))
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    test_start, test_end = "20260101", "20260630"

    conn = connect()
    try:
        logger.info(f"データ取得 {DATA_START}〜{test_end}")
        df_raw = fetch(conn, DATA_START, test_end)
        df_hist = fetch_hist(conn)
        logger.info("前処理（prep）")
        df = prep(conn, df_raw, df_hist)
        logger.info("未使用列の取得・特徴化")
        extra = fetch_extra(conn, DATA_START, test_end)
    finally:
        conn.close()

    before = len(df)
    df = df.merge(extra, on=["race_id", "horse_id"], how="left")
    for c in NEW_FEATURES:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    miss = df[NEW_FEATURES].isna().mean().max()
    logger.info(f"結合 {before:,}→{len(df):,} 行 / 新規特徴の最大欠損率 {miss:.2%}")
    if miss > 0.02:
        logger.warning("新規特徴の欠損率が高い。結合キーを確認すること")
    for c in NEW_FEATURES:
        df[c] = df[c].fillna(df[c].median())

    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)

    tr = df[df["date"] <= TRAIN_END].copy()
    va = df[(df["date"] > TRAIN_END) & (df["date"] <= VALID_END)].copy()
    te = df[(df["date"] >= test_start) & (df["date"] <= test_end)].copy().reset_index(drop=True)
    logger.info(f"train {len(tr):,} / valid {len(va):,} / test {len(te):,} 行"
                f"（test {te['race_id'].nunique():,} レース）")

    y_tr = (tr["finish_position"] <= 3).astype(int).values
    y_va = (va["finish_position"] <= 3).astype(int).values

    alive = [f for f in ALL_FEATURES if f not in DEAD_FEATURES]
    variants: dict[str, list[str]] = {
        "base44(現行)": list(ALL_FEATURES),
        "base44 -dead2": alive,
        "base44 +new6": list(ALL_FEATURES) + NEW_FEATURES,
        "base44 -dead2 +new6": alive + NEW_FEATURES,
    }

    scores: dict[str, np.ndarray] = {}
    for name, feats in variants.items():
        logger.info(f"学習 {name}（{len(feats)}特徴）")
        scores[name] = train_binary(tr, va, te, y_tr, y_va, feats, seeds)

    # ── 本番で現在起きている train/serve skew の再現 ──
    # 学習時（〜2025-06）は netkeiba が生きていたが、serve 時（2026-06以降）は
    # 供給停止でフォールバック値に固定される。test 期間は netkeiba が生きているため
    # 上の A/B ではこの skew を測れない。test 側の nk 特徴だけを欠損値に潰して再現する。
    te_skew = te.copy()
    te_skew["nk_idx_z"] = 0.0
    te_skew["nk_rank_n"] = 0.5
    logger.info("学習 base44（serve時nk欠損を再現）")
    scores["base44 serve時nk欠損"] = train_binary(
        tr, va, te_skew, y_tr, y_va, list(ALL_FEATURES), seeds)
    variants["base44 serve時nk欠損"] = list(ALL_FEATURES)

    results = {n: evaluate(te, s, args.out_threshold) for n, s in scores.items()}

    metrics = ["HEAD_top1_win", "HEAD_top1_place", "HEAD_winner_in_top3", "HEAD_ndcg3",
               "TAIL_bot3_out_rate", "TAIL_winner_in_bot3", "TAIL_placer_in_bot30pct",
               "ALL_spearman"]
    print("\n" + "=" * 120)
    print(f"地方 特徴量セット A/B  test {test_start}〜{test_end} "
          f"({te['race_id'].nunique():,}R) 目的=is_top3二値 着外={args.out_threshold}着以下")
    print("=" * 120)
    hdr = f"{'variant':<22}" + "".join(
        f"{m.split('_', 1)[1]:>16}" for m in metrics)
    print(hdr)
    print("-" * len(hdr))
    for n, r in results.items():
        print(f"{n:<22}" + "".join(f"{r[m]:>16.4f}" for m in metrics))

    base = "base44(現行)"
    pr = {n: per_race_metrics(te, s) for n, s in scores.items()}
    boot: dict[str, dict[str, list[float]]] = {}
    print(f"\npaired bootstrap（基準 = {base} / 95%CI）")
    print(f"{'variant':<22}" + "".join(f"{m:>34}" for m in ["top1_win", "top1_place", "spearman"]))
    for n in variants:
        if n == base:
            continue
        cells, rec = "", {}
        for m in ["top1_win", "top1_place", "spearman"]:
            d, lo, hi = paired_bootstrap(pr[base], pr[n], m)
            sig = "*" if (lo > 0 or hi < 0) else " "
            cells += f"{d:>+11.4f} [{lo:>+7.4f},{hi:>+7.4f}]{sig}"
            rec[m] = [round(d, 5), round(lo, 5), round(hi, 5)]
        boot[n] = rec
        print(f"{n:<22}{cells}")
    print("* = 95%CI が 0 を跨がない")

    out = {
        "test_start": test_start, "test_end": test_end,
        "out_threshold": args.out_threshold, "seeds": seeds,
        "n_races": int(te["race_id"].nunique()),
        "variants": {n: len(f) for n, f in variants.items()},
        "results": results, "paired_bootstrap_vs_base": boot,
    }
    Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    logger.info(f"保存: {args.json_out}")


if __name__ == "__main__":
    main()
