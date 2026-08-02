"""地方 足切り（Web グレーアウト）ルールの再較正

背景:
  フロント `ChihouRaceDetailClient.isCutOff` は
    gap = レース最高 composite − その馬の composite として
    `gap >= 20 || (gap >= 15 && composite順位 >= 5)`
  という**指数差の固定閾値**。これは composite が min-max 15〜85（幅が常に 70.00）
  だった前提の値であり、v13 で中心化線形スケール（レース内幅 平均 29.2）に変えると
  **ほとんど誰も足切りされなくなる**。閾値を測り直す必要がある。

  JRA は同じ問題を「指数差ルール → 着外率モデル」で解決したが、地方は
  `chihou.calculated_indices` に `out_probability` 列が無く alembic 移行が要るため、
  本スクリプトではまず **同じ指数差ルールの閾値を新スケール向けに較正**する。
  （着外率ヘッドの導入は次段階）

評価指標（JRA の足切り検証と揃える）:
  cut_rate        : 足切りされる馬の割合
  cut_out_rate    : 足切りされた馬のうち実際に馬券圏外(4着以下)だった割合 ← 高いほど良い
  winner_cut_rate : 1着馬を足切りしてしまった割合                        ← 低いほど良い
  placer_cut_rate : 3着内馬を足切りしてしまった割合                      ← 低いほど良い

honest 分割は `src/chihou_protocol.py` 準拠。TEST_START 以降は使わない。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_cutoff_review.py
"""

from __future__ import annotations

import argparse
import json
import logging
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

from scripts.chihou_composite_scale_review import centered_scale, minmax_15_85  # noqa: E402
from scripts.chihou_rank_quality_review import (  # noqa: E402
    DATA_START,
    VALID_END,
    connect,
    train_binary,
)
from scripts.train_chihou_market_lgb import ALL_FEATURES, fetch, prep  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.chihou_protocol import TRAIN_END  # noqa: E402
from src.indices.chihou_calculator import CHIHOU_INDEX_SCALE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_cutoff")

MODELS_DIR = _root / "models"


def apply_rule(comp: np.ndarray, gap_hard: float, gap_soft: float,
               rank_min: int) -> np.ndarray:
    """現行フロントと同形のルール: gap>=hard または (gap>=soft かつ 順位>=rank_min)。"""
    gap = comp.max() - comp
    order = np.argsort(-comp, kind="stable")
    rank = np.empty(len(comp), dtype=int)
    rank[order] = np.arange(1, len(comp) + 1)
    return (gap >= gap_hard) | ((gap >= gap_soft) & (rank >= rank_min))


def evaluate_rule(df: pd.DataFrame, comp_col: str, gap_hard: float,
                  gap_soft: float, rank_min: int) -> dict:
    cut = np.zeros(len(df), dtype=bool)
    for _, g in df.groupby("race_id", sort=False):
        idx = g.index.to_numpy()
        cut[idx] = apply_rule(g[comp_col].to_numpy(dtype=float),
                              gap_hard, gap_soft, rank_min)
    fp = df["finish_position"].to_numpy()
    n_win = int((fp == 1).sum())
    n_place = int((fp <= 3).sum())
    return {
        "cut_rate": round(float(cut.mean()), 4),
        "cut_out_rate": round(float((fp[cut] >= 4).mean()) if cut.any() else float("nan"), 4),
        "winner_cut_rate": round(float(cut[fp == 1].sum() / max(1, n_win)), 4),
        "placer_cut_rate": round(float(cut[fp <= 3].sum() / max(1, n_place)), 4),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--json-out", default=str(MODELS_DIR / "chihou_cutoff_review.json"))
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
    finally:
        conn.close()

    df["finish_position"] = pd.to_numeric(df["finish_position"], errors="coerce")
    df = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)

    tr = df[df["date"] <= TRAIN_END].copy()
    va = df[(df["date"] > TRAIN_END) & (df["date"] <= VALID_END)].copy()
    te = df[(df["date"] >= test_start) & (df["date"] <= test_end)].copy().reset_index(drop=True)
    logger.info(f"test {len(te):,}頭 / {te['race_id'].nunique():,}R")

    logger.info("is_top3 学習")
    p_t3 = train_binary(tr, va, te, (tr["finish_position"] <= 3).astype(int).values,
                        (va["finish_position"] <= 3).astype(int).values,
                        list(ALL_FEATURES), seeds)

    te = te.copy()
    te["_p"] = p_t3
    te["comp_old"] = te.groupby("race_id")["_p"].transform(
        lambda s: pd.Series(minmax_15_85(s.to_numpy()), index=s.index))
    te["comp_new"] = te.groupby("race_id")["_p"].transform(
        lambda s: pd.Series(centered_scale(s.to_numpy(), CHIHOU_INDEX_SCALE), index=s.index))

    # 着外率ヘッドを別に学習し、「専用モデル vs 指数差ルール」を同じ除外率で比べる。
    # JRA は指数差ルールを着外率モデルへ置換したが、地方は 7〜12頭立てで
    # 「着外」の意味が違うため、置換に見合うかを実測してから決める。
    logger.info(f"p_out 学習（着外={5}着以下）")
    p_out = train_binary(tr, va, te,
                         (tr["finish_position"] >= 5).astype(int).values,
                         (va["finish_position"] >= 5).astype(int).values,
                         list(ALL_FEATURES), seeds)
    te["p_out"] = p_out

    def eval_pout(thr: float) -> dict:
        cut = te["p_out"].to_numpy() >= thr
        fp = te["finish_position"].to_numpy()
        return {
            "cut_rate": round(float(cut.mean()), 4),
            "cut_out_rate": round(float((fp[cut] >= 4).mean()) if cut.any() else float("nan"), 4),
            "winner_cut_rate": round(float(cut[fp == 1].sum() / max(1, int((fp == 1).sum()))), 4),
            "placer_cut_rate": round(float(cut[fp <= 3].sum() / max(1, int((fp <= 3).sum()))), 4),
        }

    rows = []
    rows.append(("現行ルール(旧スケール) 20/15/5", "comp_old",
                 evaluate_rule(te, "comp_old", 20.0, 15.0, 5)))
    rows.append(("現行ルールを新スケールにそのまま適用 20/15/5", "comp_new",
                 evaluate_rule(te, "comp_new", 20.0, 15.0, 5)))
    for hard, soft, rmin in [
        (40.0, 40.0, 99),   # 実質「hard のみ」= 順位条件なし
        (35.0, 30.0, 5), (32.0, 26.0, 5), (30.0, 24.0, 5),
        (28.0, 22.0, 5), (26.0, 20.0, 5), (24.0, 18.0, 5),
        (30.0, 24.0, 7), (28.0, 22.0, 7), (26.0, 20.0, 7),
        (14.0, 10.0, 5), (10.0, 7.0, 5),
    ]:
        label = (f"新スケール {hard:g}のみ" if rmin >= 99
                 else f"新スケール {hard:g}/{soft:g}/順位{rmin}")
        rows.append((label, "comp_new", evaluate_rule(te, "comp_new", hard, soft, rmin)))

    # 着外率モデル（除外率が指数差ルールと近い帯を中心に掃引）
    for thr in [0.90, 0.88, 0.86, 0.84, 0.82, 0.80, 0.75, 0.70]:
        rows.append((f"着外率モデル p_out>={thr:.2f}", "p_out", eval_pout(thr)))

    print("\n" + "=" * 104)
    print(f"地方 足切りルール較正  test {test_start}〜{test_end} "
          f"({te['race_id'].nunique():,}R / {len(te):,}頭)")
    print("=" * 104)
    print(f"{'rule':<44}{'除外率':>10}{'除外馬の着外率':>16}{'1着取りこぼし':>16}{'3着内取りこぼし':>16}")
    print("-" * 104)
    for label, _, r in rows:
        print(f"{label:<44}{r['cut_rate']:>10.1%}{r['cut_out_rate']:>16.1%}"
              f"{r['winner_cut_rate']:>16.1%}{r['placer_cut_rate']:>16.1%}")
    print("\n除外馬の着外率は高いほど良い / 1着・3着内の取りこぼしは低いほど良い")
    print("※ 参考: JRA の着外率モデルは 除外30%・除外馬の実着外率88.6%・1着取りこぼし4.8%")

    Path(args.json_out).write_text(json.dumps(
        {"test_start": test_start, "test_end": test_end, "seeds": seeds,
         "n_races": int(te["race_id"].nunique()),
         "rules": [{"label": lb, "scale": sc, **r} for lb, sc, r in rows]},
        ensure_ascii=False, indent=2))
    logger.info(f"保存: {args.json_out}")


if __name__ == "__main__":
    main()
