"""地方 composite のスケール方式レビュー（min-max 15-85 の廃止検討）

問題（DB 実測で確認済み・2026-08-02）:
  `chihou_calculator._scale_to_index_local` はレース内 min-max → 15〜85 固定。
  そのため **全レースで幅がぴったり 70.00（sd=0.000）** になり、
  `calculate_race_confidence` の
    - 分散スコア(25点): レース内 sd >= 8 で満点 → **地方は 100% のレースで満点＝完全な定数**
    - 指数差スコア(40点): 加重 gap >= gap_full_score(10) で満点 → **63% のレースで満点**
  となる。100点中 65点が実質機能しておらず、信頼度は頭数(20点)と勝率集中(15点)だけで
  決まっている。JRA は v27 実装時にこの方式を「禁止」と結論している
  （memory: jra_rank_quality_redesign_2026_08_02。min-max にすると tier S が
   19.0%→30.2% に膨張した）。参考: JRA v27 は 平均sd=9.24 / 分散満点77% / gap満点18.4%。

本スクリプトが測ること:
  1. 新スケール `composite = 50 + C * (p - mean_race(p))` の C を掃引し、
     レース内 sd と gap_1_2 の分布・飽和率を見て C を決める
     （z(p)*C*sd(p) は sd が約分されて C*(p-mean) と同値。単一スコアなので素直に後者を使う）
  2. 新旧それぞれで信頼度 tier(S/A/B/C) を計算し、
     **tier ごとの 1位馬 勝率・複勝率の分離** と tier 分布を比較する
     → 「tier が当たり外れを分離できているか」で新旧を判定する
  3. gap_full_score の掃引

重要: min-max も線形スケールも **レース内の順位は変えない**（どちらも単調変換）。
したがって本改修で top1 の顔ぶれは一切変わらない。変わるのは信頼度 tier だけである。

honest 分割は `src/chihou_protocol.py` 準拠。TEST_START 以降は使わない。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_composite_scale_review.py
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
from src.indices.confidence import (  # noqa: E402
    CHIHOU_DISPERSION_FULL_SCORE,
    CHIHOU_GAP_FULL_SCORE,
    calculate_race_confidence,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_scale")

MODELS_DIR = _root / "models"
C_GRID = [20.0, 40.0, 60.0, 80.0, 120.0]
GAP_GRID = [4.0, 6.0, 8.0, 10.0]


def minmax_15_85(p: np.ndarray) -> np.ndarray:
    """現行方式。レース内 min-max → 15〜85。"""
    lo, hi = p.min(), p.max()
    if len(p) <= 1 or hi - lo < 1e-12:
        return np.full(len(p), 50.0)
    return 15.0 + (p - lo) / (hi - lo) * 70.0


def centered_scale(p: np.ndarray, c: float) -> np.ndarray:
    """新方式。50 + C * (p - レース内平均)。レース本来のばらつきが残る。"""
    return np.clip(50.0 + c * (p - p.mean()), 0.0, 100.0)


def tier_table(races: list[dict], label: str) -> pd.DataFrame:
    """tier ごとの件数・1位馬勝率・複勝率をまとめる。"""
    df = pd.DataFrame(races)
    g = df.groupby("tier")
    out = pd.DataFrame({
        "n": g.size(),
        "share": g.size() / len(df),
        "top1_win": g["top1_win"].mean(),
        "top1_place": g["top1_place"].mean(),
        "score_mean": g["score"].mean(),
    })
    out = out.reindex(["S", "A", "B", "C"]).fillna(0.0)
    out.insert(0, "variant", label)
    return out


def build_races(te: pd.DataFrame, p_t3: np.ndarray, p_win: np.ndarray,
                scaler, gap_full: float, disp_full: float) -> list[dict]:
    """レース単位に composite/win_prob を組み、信頼度 tier を求める。"""
    d = te[["race_id", "head_count", "finish_position"]].copy()
    d["p_t3"] = p_t3
    d["p_win"] = p_win
    out: list[dict] = []
    for rid, g in d.groupby("race_id", sort=False):
        p = g["p_t3"].to_numpy(dtype=float)
        comp = scaler(p)
        wp = g["p_win"].to_numpy(dtype=float).tolist()
        hc = int(g["head_count"].iloc[0]) if pd.notna(g["head_count"].iloc[0]) else len(g)
        conf = calculate_race_confidence(comp.tolist(), hc, wp,
                                        gap_full_score=gap_full,
                                        dispersion_full_score=disp_full)
        fp = g["finish_position"].to_numpy()
        top_fp = fp[int(np.argmax(comp))]
        out.append({
            "race_id": rid, "tier": conf["rank"], "score": conf["score"],
            "sd": float(np.std(comp, ddof=1)) if len(comp) > 1 else 0.0,
            "gap_1_2": conf["gap_1_2"],
            "top1_win": float(top_fp == 1), "top1_place": float(top_fp <= 3),
        })
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--json-out", default=str(MODELS_DIR / "chihou_composite_scale_review.json"))
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
    logger.info(f"train {len(tr):,} / valid {len(va):,} / test {len(te):,}"
                f"（{te['race_id'].nunique():,}R）")

    feats = list(ALL_FEATURES)
    logger.info("is_top3 学習")
    p_t3 = train_binary(tr, va, te, (tr["finish_position"] <= 3).astype(int).values,
                        (va["finish_position"] <= 3).astype(int).values, feats, seeds)
    logger.info("is_win 学習")
    p_win = train_binary(tr, va, te, (tr["finish_position"] == 1).astype(int).values,
                         (va["finish_position"] == 1).astype(int).values, feats, seeds)

    # ── 1. C の掃引（sd / gap の飽和率）──
    print("\n" + "=" * 110)
    print("スケール係数 C の掃引（新方式 composite = 50 + C*(p − レース内平均)）")
    print("=" * 110)
    print(f"{'variant':<22}{'平均sd':>10}{'sd>=8(満点)':>14}{'平均gap12':>12}"
          f"{'gap>=10(満点)':>15}{'平均幅':>10}")
    sweep = []
    for label, scaler in [("現行 min-max 15-85", minmax_15_85)] + \
                         [(f"新 C={c:g}", (lambda p, c=c: centered_scale(p, c))) for c in C_GRID]:
        sds, gaps, rngs = [], [], []
        for _, g in te.groupby("race_id", sort=False):
            p = p_t3[g.index.to_numpy()]
            comp = scaler(p)
            if len(comp) < 2:
                continue
            s = np.sort(comp)[::-1]
            sds.append(float(np.std(comp, ddof=1)))
            gaps.append(float(s[0] - s[1]))
            rngs.append(float(s[0] - s[-1]))
        rec = {
            "variant": label, "sd_mean": float(np.mean(sds)),
            "sd_saturated": float(np.mean(np.array(sds) >= 8.0)),
            "gap_mean": float(np.mean(gaps)),
            "gap_saturated": float(np.mean(np.array(gaps) >= 10.0)),
            "range_mean": float(np.mean(rngs)),
        }
        sweep.append(rec)
        print(f"{label:<22}{rec['sd_mean']:>10.2f}{rec['sd_saturated']:>13.1%}"
              f"{rec['gap_mean']:>12.2f}{rec['gap_saturated']:>14.1%}{rec['range_mean']:>10.2f}")
    print("\n参考: JRA v27 は 平均sd=9.24 / 分散満点77.0% / 平均gap12=3.46 / gap満点(>=6)18.4%")

    # ── 2. tier の分離（新旧比較）──
    print("\n" + "=" * 110)
    print("信頼度 tier の分離（tier ごとの1位馬 勝率/複勝率）")
    print("=" * 110)
    tables = []
    # (ラベル, スケーラ, gap_full_score, dispersion_full_score)
    variants: list[tuple] = [("現行 min-max/gap10", minmax_15_85, 10.0, 8.0)]
    for c in C_GRID:
        for gf in GAP_GRID:
            variants.append((f"新 C={c:g}/gap{gf:g}",
                             (lambda p, c=c: centered_scale(p, c)), gf, 8.0))
    # 本番採用値（v13）。C=40 は JRA v27 と同程度の表示幅にするための値で、
    # tier 較正は gap/dispersion 側を比例させて吸収する（C=20/gap6/disp8 と数学的に同値）。
    variants.append((
        "★本番v13 C=40/gap12/disp16",
        (lambda p: centered_scale(p, CHIHOU_INDEX_SCALE)),
        CHIHOU_GAP_FULL_SCORE, CHIHOU_DISPERSION_FULL_SCORE,
    ))
    for label, scaler, gf, df_ in variants:
        races = build_races(te, p_t3, p_win, scaler, gf, df_)
        tables.append(tier_table(races, label))

    for t in tables:
        label = t["variant"].iloc[0]
        s_win = t.loc["S", "top1_win"]
        c_win = t.loc["C", "top1_win"]
        spread = s_win - c_win
        mono = all(t.loc[a, "top1_win"] >= t.loc[b, "top1_win"]
                   for a, b in [("S", "A"), ("A", "B"), ("B", "C")])
        dist = "/".join(f"{t.loc[r, 'share']:.0%}" for r in ["S", "A", "B", "C"])
        print(f"{label:<22} 分布S/A/B/C={dist:<20} S勝率={s_win:.3f} C勝率={c_win:.3f} "
              f"分離={spread:+.3f} 単調={'○' if mono else '×'}")

    print("\n※ min-max も線形スケールもレース内の順位は同一。変わるのは tier のみ。")

    out = {
        "test_start": test_start, "test_end": test_end,
        "n_races": int(te["race_id"].nunique()), "seeds": seeds,
        "c_sweep": sweep,
        "tier_tables": {t["variant"].iloc[0]: t.drop(columns=["variant"]).to_dict()
                        for t in tables},
    }
    Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    logger.info(f"保存: {args.json_out}")


if __name__ == "__main__":
    main()
