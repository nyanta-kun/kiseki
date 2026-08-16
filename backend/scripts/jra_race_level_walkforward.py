"""レース単位の「本命崩れ」を全馬に配ると人気薄を拾えるか（JRA・検証①）。

## 仮説（2026-08-16 ユーザー提示）

> 人気馬が悪くなることで市場人気に対しギャップが生まれ、下位の馬が馬券に
> 絡みやすくなると思います。

## 事前の記述統計（モデル不使用・2,947R・2025-09〜2026-08）

レース単位で「1-3番人気の調教自己比」を作り四分位で比べた:

| 本命の自己比 | 1番人気 複勝率 | 3着内の人気薄頭数 | 高配当決着率 |
|---|---|---|---|
| 上昇 | 67.70% | 1.035 | 25.81% |
| 悪化 | **61.68%** | 1.071 | 29.35% |
| 差 | **−6.02pt [−11.03, −1.14] 有意** | +0.036 [−0.04, +0.11] | +3.54pt [−0.95, +8.14] |

🔴 **仮説の前半（本命が崩れる）は成立、後半（人気薄に回る）は成立しない。**
1番人気が飛んだ席は 2〜4番人気が埋めており、勝馬の平均人気は 3.30→3.51 しか動かない。

ただし上表は「全人気薄への平均的な恩恵」しか見ていない。
**「本命が崩れるとき、どのタイプの人気薄が伸びるか」という交互作用**は
記述統計では見えないので、ここでモデルに探させる。

## 腕

  - `base`  : 現行 34 特徴
  - `ck_x`  : 検証②のベスト（34 + 坂路8 + expect_level/ck_upside/ck_downside）
  - `race`  : ck_x + レース単位4本（下記）

⚠️ **レース内で定数の特徴は、それ単体では順位を動かせない**（最終スコアが
レース内 z 合成のため。[[jra_race_relative_features_rejected_2026_08_16]]）。
効くとすれば木の中で馬単位特徴と交互作用したときだけなので、
**交互作用を明示した列を必ず一緒に入れる**。

⚠️ 期待水準の上位馬を **人気ではなく `expect_level`（odds-free）で決める**。
composite は発走前オッズを使わない設計なので、人気で定義した特徴は本番に載せられない。

使い方:
    cd backend
    .venv/bin/python scripts/jra_race_level_walkforward.py --cache /tmp/ck_ds.pkl
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.anagusa_top3_walkforward import FEATURES, _race_z  # noqa: E402
from scripts.jra_chokyo_walkforward import (  # noqa: E402
    ARMS as CK_ARMS,
    build_dataset,
    paired_selection_diff,
    run_arm,
)
from scripts.jra_darkhorse_walkforward import POP_BANDS, band_rank, show  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("race_level_wf")

# レース単位で作り、全馬に配る列 + それを馬単位へ落とす交互作用列
RACE_LEVEL_FEATURES = [
    "race_top_ck_dev",    # 期待上位3頭の調教自己比の平均（正=本命が悪化）
    "race_top_ck_worst",  # うち最も悪化している値
    "field_open",         # race_top_ck_dev × expect_level（期待が低い馬ほど恩恵）
    "ck_rel_to_top",      # 自馬の自己比 − 本命の自己比（本命より上向きか）
]

TOP_N = 3  # 「本命」とみなす期待水準上位の頭数


def add_race_level(df: pd.DataFrame) -> pd.DataFrame:
    """レース単位の本命状態と、その馬単位への落とし込みを付ける。

    `expect_level` は 0=強い…1=弱い（`jra_chokyo_walkforward.add_expect_level`）。
    走歴が無い馬は NaN なので本命の選定から自然に外れる。
    """
    df = df.copy()
    dev = pd.to_numeric(df["chokyo_self_4f_dev"], errors="coerce")
    exp = pd.to_numeric(df["expect_level"], errors="coerce")
    df["_dev"] = dev

    # レース内で expect_level が小さい順に TOP_N 頭 = 「強いと思われている馬」
    rank_exp = exp.groupby(df["race_id"]).rank(method="first")
    is_top = (rank_exp <= TOP_N) & exp.notna()
    top = df[is_top].groupby("race_id")["_dev"]
    df["race_top_ck_dev"] = df["race_id"].map(top.mean())
    df["race_top_ck_worst"] = df["race_id"].map(top.max())

    df["field_open"] = df["race_top_ck_dev"] * exp
    df["ck_rel_to_top"] = dev - df["race_top_ck_dev"]
    return df.drop(columns=["_dev"])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-start", default="20230506")
    p.add_argument("--eval-start", default="20251001", help="調教が乗る最初の四半期")
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
    df = add_race_level(df)
    cov = df["race_top_ck_dev"].notna().mean()
    logger.info(f"{len(df):,}行 / レース単位特徴の充足率 {cov * 100:.1f}%")

    arms = {
        "base": list(FEATURES),
        "ck_x": list(CK_ARMS["ck_x"]),
        "race": list(CK_ARMS["ck_x"]) + RACE_LEVEL_FEATURES,
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
        for a, b in (("ck_x", "base"), ("race", "base"), ("race", "ck_x")):
            pt, l, h, agree = paired_selection_diff(evs[a], evs[b], lo, hi)
            sig = "有意" if (l > 0 or h < 0) else "  — "
            print(f"    {a:5s} − {b:5s}: {pt:+6.2f}pt [{l:+6.2f}, {h:+6.2f}] {sig}"
                  f"   選出一致 {agree:.1f}%")
            pair.setdefault(name, {})[f"{a}-{b}"] = [round(pt, 2), round(l, 2), round(h, 2)]

    # 仮説どおりの場所で見る: 本命が悪化しているレースに限定して人気薄の選出精度を測る
    print("\n" + "=" * 100)
    print("  【仮説の場所】本命(期待上位3頭)の調教が悪化しているレースに限定")
    print("=" * 100)
    for arm in arms:
        ev = evs[arm]
        d = pd.to_numeric(ev["race_top_ck_dev"], errors="coerce")
        thr = d.quantile(0.75)
        worse = d >= thr
        m = ev["win_popularity"].between(5, 99) & ev["is_finisher"]
        br = band_rank(ev, m, "wf_score")
        show([
            ("本命悪化レース × 5番人気以下 全体", ev[m & worse]),
            ("　うち帯内 指数1位", ev[m & worse & (br == 1)]),
            ("（対照）本命が悪化していないレース × 帯内1位", ev[m & ~worse & (br == 1)]),
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
        print(f"  {arm:5s} " + " / ".join(
            f"{q} {r['mean'] * 100:.1f}%" for q, r in s.iterrows()))

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"arms": {k: len(v) for k, v in arms.items()},
             "n_races": int(ref["race_id"].nunique()),
             "paired": pair}, ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
