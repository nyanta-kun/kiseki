"""調教を「レース内の他馬との絶対水準比較」で入れる walk-forward A/B（JRA・検証②）。

## 仮説（2026-08-16 ユーザー提示）

> 評価されていなかった馬が突然高評価の馬と同水準のタイムの調教をできた場合、
> 激走する可能性があります。

検証②は自己比（[[jra_chokyo_interaction_2026_08_16]]）ではなく
**レース内の他馬との比較**で調教を見る。

## 事前の記述統計（モデル不使用・3,173R・2025-09〜2026-08）— 仮説は逆向き

`peer_gap` = 期待水準の正規化順位 − 調教 z の正規化順位（正 = 評価は低いのに調教は上位）

| 5-8番人気 | 3着内率 |
|---|---|
| 評価 > 調教 | **20.78%** |
| 評価 < 調教（格上と並んだ） | **17.49%** |

9番人気以下でも同じ向き（5.62% → 4.51%）。**「格上と並んだ」馬はむしろ走らない。**
弱い馬ほど調教で目一杯追われる（強い馬は流す）ため、`peer_gap` が正であること自体が
能力の裏返しになっていると読める。実際 `peer_gap` は人気順位と **+0.337** 相関し、
自己比の +0.042 と違って**市場にも見えている**。

一方 `ck_gap_to_best`（レース内最速との差）は弱いながら順方向
（5-8番人気で 最速に近い 20.52% vs 遠い 18.20%）。

逆向きでも情報は情報なのでモデルに渡して A/B する。

## 腕

  - `base`    : 現行 34 特徴
  - `ck_x`    : 検証②のベース（34 + 坂路8 + expect_level/ck_upside/ck_downside）
  - `ck_peer` : ck_x + レース内比較4本

⚠️ [[jra_race_relative_features_rejected_2026_08_16]] で「入力のレース内相対化」は
効かないと分かっている。本検証が違うのは、**同じ特徴を z に置き換えるのではなく
「調教順位 vs 期待順位」という 2 特徴間の比較**を作っている点。

使い方:
    cd backend
    .venv/bin/python scripts/jra_chokyo_peer_walkforward.py --cache /tmp/ck_ds.pkl
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

import pandas as pd  # noqa: E402

from scripts.anagusa_top3_walkforward import FEATURES  # noqa: E402
from scripts.jra_chokyo_walkforward import (  # noqa: E402
    ARMS as CK_ARMS,
    build_dataset,
    paired_selection_diff,
    run_arm,
)
from scripts.jra_darkhorse_walkforward import POP_BANDS, band_rank, show  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ck_peer_wf")

PEER_FEATURES = [
    "ck_rank_n",       # レース内の調教 z の正規化順位（0=最速 … 1=最遅）
    "expect_rank_n",   # レース内の期待水準の正規化順位（0=強い … 1=弱い）
    "peer_gap",        # expect_rank_n − ck_rank_n（正 = 評価は低いのに調教は上位）
    "ck_gap_to_best",  # レース内最速との z 差（0 = 最速馬と同水準）
]


def _nrank(s: pd.Series, g: pd.Series) -> pd.Series:
    """レース内の正規化順位（0 = 最小 … 1 = 最大）。NaN はそのまま残す。"""
    r = s.groupby(g).rank(method="average")
    n = s.groupby(g).transform("count")
    return (r - 1) / (n - 1).clip(lower=1)


def add_peer(df: pd.DataFrame) -> pd.DataFrame:
    """調教のレース内比較特徴を付ける。"""
    df = df.copy()
    ck = pd.to_numeric(df["chokyo_4f_z"], errors="coerce")
    exp = pd.to_numeric(df["expect_level"], errors="coerce")
    df["ck_rank_n"] = _nrank(ck, df["race_id"])
    df["expect_rank_n"] = _nrank(exp, df["race_id"])
    df["peer_gap"] = df["expect_rank_n"] - df["ck_rank_n"]
    df["ck_gap_to_best"] = ck - ck.groupby(df["race_id"]).transform("min")
    return df


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-start", default="20230506")
    p.add_argument("--eval-start", default="20251001")
    p.add_argument("--eval-end", default="20260815")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cache", default=None)
    p.add_argument("--pred-cache", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info(f"キャッシュから読込: {cache}")
    else:
        df = build_dataset(args)
        if cache:
            df.to_pickle(cache)
    df = add_peer(df)
    ev_win = df[df["date"] >= args.eval_start]
    logger.info(f"{len(df):,}行 / 評価窓の peer_gap 充足率 "
                f"{ev_win['peer_gap'].notna().mean() * 100:.1f}%")

    arms = {
        "base": list(FEATURES),
        "ck_x": list(CK_ARMS["ck_x"]),
        "ck_peer": list(CK_ARMS["ck_x"]) + PEER_FEATURES,
    }

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs = pd.read_pickle(pred_cache)
        logger.info(f"予測をキャッシュから読込: {pred_cache}")
    else:
        evs = {}
        for arm, feats in arms.items():
            logger.info(f"--- arm={arm} ({len(feats)}特徴) ---")
            evs[arm] = run_arm(df, feats, args, args.eval_start)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)

    ref = evs["base"]
    print(f"\n評価対象: {ref['race_id'].nunique():,}レース / {len(ref):,}頭 "
          f"({ref['date'].min()}〜{ref['date'].max()})")
    print("腕: " + " / ".join(f"{k}({len(v)}特徴)" for k, v in arms.items()))

    print("\n" + "=" * 100)
    print("  【主指標】帯内 指数1位馬の3着内率差（対応比較・レース単位CI）")
    print("=" * 100)
    pair: dict[str, dict] = {}
    for name, lo, hi in POP_BANDS:
        print(f"\n  [{name}]")
        for a, b in (("ck_x", "base"), ("ck_peer", "base"), ("ck_peer", "ck_x")):
            pt, l, h, agree = paired_selection_diff(evs[a], evs[b], lo, hi)
            sig = "有意" if (l > 0 or h < 0) else "  — "
            print(f"    {a:8s} − {b:5s}: {pt:+6.2f}pt [{l:+6.2f}, {h:+6.2f}] {sig}"
                  f"   選出一致 {agree:.1f}%")
            pair.setdefault(name, {})[f"{a}-{b}"] = [round(pt, 2), round(l, 2), round(h, 2)]

    print("\n" + "=" * 100)
    print("  【仮説の場所】「評価は低いが調教はレース内上位」の馬を拾えているか")
    print("=" * 100)
    for arm in arms:
        ev = evs[arm]
        pg = pd.to_numeric(ev["peer_gap"], errors="coerce")
        hit = (pg >= pg.quantile(0.75))  # 評価<調教（格上と並んだ）側
        m = ev["win_popularity"].between(5, 99) & ev["is_finisher"]
        br = band_rank(ev, m, "wf_score")
        show([
            ("5番人気以下 全体", ev[m]),
            ("　うち 評価<調教（格上と並んだ）", ev[m & hit]),
            ("　　うち帯内 指数1位", ev[m & hit & (br == 1)]),
            ("（対照）それ以外 × 帯内1位", ev[m & ~hit & (br == 1)]),
        ], f"{arm}")

    print("\n" + "=" * 100)
    print("  【安定性】四半期別・5-8番人気 帯内指数1位の3着内率")
    print("=" * 100)
    for arm in arms:
        ev = evs[arm]
        m = ev["win_popularity"].between(5, 8)
        br = band_rank(ev, m, "wf_score")
        s = ev[m & (br == 1) & ev["is_finisher"]].groupby("quarter")["place_hit"].agg(
            ["size", "mean"])
        print(f"  {arm:8s} " + " / ".join(
            f"{q} {r['mean'] * 100:.1f}%" for q, r in s.iterrows()))

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"arms": {k: len(v) for k, v in arms.items()},
             "n_races": int(ref["race_id"].nunique()),
             "paired": pair}, ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
