"""特徴量を「レース内相対」に作り替えると効くかの walk-forward A/B（JRA・検証③）。

## 仮説（2026-08-16 ユーザー提示）

> レースはあくまで同一レースに出走している相対評価のため、これまでの指数が
> 馬単体の絶対評価であれば根本的にその見直しが必要。枠番×競馬場×距離×馬場も
> レース内での相対評価として関連するデータと思います。

## 事前の実測が仮説を支持している

34 特徴の分散を「レース間 / レース内」に分解すると（2025-06 以降）:

| 性格 | 特徴 | レース間分散の割合 |
|---|---|---|
| レース識別子 | distance / head_count / is_turf / 馬場 / grade | 100% |
| **絶対評価寄り** | distance_change 82% / horse_age 71% / course_aptitude 70% / speed_index 47% | 高い |
| **既に相対** | jvan_battle_dm 1.0% / jvan_time_dm 0.6% / jockey_index 0.0% | ほぼ 0 |

🔴 **gain の 71.8% を占める DM 2列は、もともとレース内相対の指数**。
効いている特徴が相対で、効いていない特徴が絶対、という並びになっている。

## 腕

  - `base`     : 現行 34 特徴
  - `rel_add`  : 34 + 馬単位の連続特徴をレース内 z 化したものを**追加**（併存）
  - `rel_only` : 馬単位の連続特徴を**レース内 z へ置換**（絶対値を落とす。特徴数は同じ）
  - `frame`    : 34 + 枠の相対位置（`frame_rel` / `horse_no_rel`）だけ追加

`frame` を分けている理由。**枠番は JRA では常に 1〜8** で、頭数が変わっても
8枠は必ず最も外——つまり枠番自体は既に相対量になっている。
モデルに入っていないのは **馬番（内から何番目か）** の方で、たとえば 16頭立ての
15番と16番はどちらも8枠だが外への出方が違う。そこで
`horse_no_rel = (馬番-1)/(頭数-1)` を明示的に与えて効くかを見る。

（枠番 × 競馬場 × 距離 × 馬場 の長期統計は `position_advantage`
＝ `indices/frame_bias.py` として既に入っている。ただし
「平均50・σ10 の絶対スコア」なので `rel_add` / `rel_only` の z 化対象に含める。）

主指標は [[jra_darkhorse_discrimination_limit_2026_08_16]] と同じ
**人気を完全に揃えた層化リフト**と、腕どうしの**対応比較**。

⚠️ 調教を使わないので評価窓を 11 四半期（2024Q1〜2026Q3）取れる。
検証①②（調教）の 4 四半期より統計的な力が強い。

使い方:
    cd backend
    .venv/bin/python scripts/jra_relative_walkforward.py --cache /tmp/ck_ds.pkl
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

from scripts.anagusa_top3_walkforward import (  # noqa: E402
    FEATURES,
    _race_z,
    add_rank,
    fit_vintage,
    load_all,
    normalized_rank,
    prepare,
    quarters,
)
from scripts.jra_chokyo_walkforward import paired_selection_diff  # noqa: E402
from scripts.jra_darkhorse_walkforward import POP_BANDS, band_rank, show  # noqa: E402
from src.indices.composite import V27_OUT_WEIGHT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("relative_wf")

# レース内 z 化の対象＝**馬ごとに値が変わる連続特徴**。
# レース属性（distance / head_count / is_turf / 馬場 / grade）は
# レース内で定数なので z が定義できない。除外する。
HORSE_LEVEL_COLS: list[str] = [
    # サブ指数 17
    "speed_index", "last_3f_index", "course_aptitude", "position_advantage",
    "rotation_index", "jockey_index", "pace_index", "pedigree_index",
    "training_index", "anagusa_index", "paddock_index", "rebound_index",
    "rivals_growth_index", "career_phase_index", "distance_change_index",
    "jockey_trainer_combo_index", "going_pedigree_index",
    # 馬メタ 7
    "frame_number", "horse_age", "weight_carried", "horse_weight",
    "weight_change", "jvan_time_dm", "jvan_battle_dm",
]
REL_SUFFIX = "_rz"
REL_COLS = [c + REL_SUFFIX for c in HORSE_LEVEL_COLS]

# 枠の相対位置。生の枠番は 16頭立ての8枠と8頭立ての8枠を区別できない。
FRAME_COLS = ["frame_rel", "horse_no_rel"]

RACE_LEVEL_COLS = [c for c in FEATURES if c not in HORSE_LEVEL_COLS]

ARMS: dict[str, list[str]] = {
    "base": list(FEATURES),
    "rel_add": list(FEATURES) + REL_COLS,
    "rel_only": RACE_LEVEL_COLS + REL_COLS,
    "frame": list(FEATURES) + FRAME_COLS,
}


def add_relative(df: pd.DataFrame) -> pd.DataFrame:
    """馬単位の連続特徴のレース内 z と、枠の相対位置を付ける。

    sd=0（全馬同値。死んだサブ指数で普通に起きる）のレースは 0 に潰す——
    NaN にすると「値が無い」と混ざり、定数であることを木に伝えられない。
    """
    df = df.copy()
    for c in HORSE_LEVEL_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df[c + REL_SUFFIX] = _race_z(df, c)

    # 内から何割の位置か。枠番(1-8)は頭数によらず同じ意味なので生値のままでよいが、
    # 馬番は頭数で意味が変わる（16頭の16番と8頭の8番は別物）ので割合に直す。
    hn = pd.to_numeric(df.get("horse_number"), errors="coerce")
    if hn is None or hn.isna().all():
        raise SystemExit(
            "horse_number が入っていません。--cache を作り直してください"
            "（anagusa_top3_walkforward.FETCH_SQL に re.horse_number を追加済み）"
        )
    hn = hn.where(hn > 0)  # 枠順未確定は 0 で入る。欠損として扱う
    g_hn = hn.groupby(df["race_id"])
    span = g_hn.transform("max") - g_hn.transform("min")
    df["horse_no_rel"] = (hn - g_hn.transform("min")) / span.where(span > 0, np.nan)
    # 枠は 1-8 固定なので割合ではなく「その枠に何頭いるか」＝混み具合を持たせる
    df["frame_rel"] = df.groupby(["race_id", "frame_number"])["frame_number"].transform("size")
    return df


def run_arm(df: pd.DataFrame, feats: list[str], args: argparse.Namespace) -> pd.DataFrame:
    fin = df[df["is_finisher"]].copy()
    fin["y_rank"] = normalized_rank(fin)
    fin["y_out"] = (fin["finish_position"] >= 6).astype(int)

    out = []
    for label, qstart, qend in quarters(args.eval_start, args.eval_end):
        train = fin[fin["date"] < qstart]
        if train.empty:
            continue
        span = (pd.to_datetime(qstart) - pd.to_datetime(train["date"].min())).days
        if span < args.min_train_days:
            continue
        target = df[(df["date"] >= qstart) & (df["date"] <= qend)]
        if target.empty:
            continue
        reg_m, out_m = fit_vintage(train, args.seed, args.valid_days, features=feats)
        res = target.copy()
        res["_reg"] = reg_m.predict(target[feats].values)
        res["_out"] = np.clip(out_m.predict(target[feats].values), 0.0, 1.0)
        res["wf_score"] = _race_z(res, "_reg") * -1.0 - V27_OUT_WEIGHT * _race_z(res, "_out")
        res["quarter"] = label
        out.append(res)
    if not out:
        raise SystemExit("評価対象の四半期がありません")
    ev = pd.concat(out, ignore_index=True)
    add_rank(ev, "wf_score", "wf_rank", ascending=False)
    return ev


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-start", default="20230506")
    p.add_argument("--eval-start", default="20240101")
    p.add_argument("--eval-end", default="20260815")
    p.add_argument("--min-train-days", type=int, default=200)
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cache", default=None, help="特徴付きデータセットの pickle（他検証と共用可）")
    p.add_argument("--pred-cache", default=None, help="腕ごとの予測 pickle")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info(f"キャッシュから読込: {cache}")
    else:
        df = prepare(load_all(args.data_start, args.eval_end))
        df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")
        if cache:
            df.to_pickle(cache)
    df = add_relative(df)
    logger.info(f"{len(df):,}行 / {df['race_id'].nunique():,}レース")

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs = pd.read_pickle(pred_cache)
        logger.info(f"予測をキャッシュから読込: {pred_cache}")
    else:
        evs = {}
        for arm, feats in ARMS.items():
            logger.info(f"--- arm={arm} ({len(feats)}特徴) ---")
            evs[arm] = run_arm(df, feats, args)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)

    ref = evs["base"]
    print(f"\n評価対象: {ref['race_id'].nunique():,}レース / {len(ref):,}頭 "
          f"({ref['date'].min()}〜{ref['date'].max()}) / "
          f"四半期 {len(set(ref['quarter']))} 本")
    print("腕: " + " / ".join(f"{k}({len(v)}特徴)" for k, v in ARMS.items()))

    print("\n" + "=" * 100)
    print("  【主指標】帯内 指数1位馬の3着内率差（base との対応比較・レース単位CI）")
    print("=" * 100)
    pair: dict[str, dict] = {}
    for name, lo, hi in POP_BANDS:
        print(f"\n  [{name}]")
        for arm in ARMS:
            if arm == "base":
                continue
            pt, l, h, agree = paired_selection_diff(evs[arm], evs["base"], lo, hi)
            sig = "有意" if (l > 0 or h < 0) else "  — "
            print(f"    {arm:9s} − base: {pt:+6.2f}pt [{l:+6.2f}, {h:+6.2f}] {sig}"
                  f"   選出一致 {agree:.1f}%")
            pair.setdefault(name, {})[arm] = [round(pt, 2), round(l, 2), round(h, 2),
                                              round(agree, 1)]

    print("\n" + "=" * 100)
    print("  【参考】帯内 指数1位馬の素の成績")
    print("=" * 100)
    for name, lo, hi in [("1-4番人気", 1, 4), ("5-8番人気", 5, 8), ("9番人気以下", 9, 99)]:
        rows = []
        for arm in ARMS:
            ev = evs[arm]
            m = ev["win_popularity"].between(lo, hi)
            br = band_rank(ev, m, "wf_score")
            rows.append((f"{arm} 帯内1位", ev[m & (br == 1)]))
        show(rows, f"{name}")

    print("\n" + "=" * 100)
    print("  【安定性】四半期別・5-8番人気 帯内指数1位の3着内率")
    print("=" * 100)
    for arm in ARMS:
        ev = evs[arm]
        m = ev["win_popularity"].between(5, 8)
        br = band_rank(ev, m, "wf_score")
        s = ev[m & (br == 1) & ev["is_finisher"]].groupby("quarter")["place_hit"].agg(
            ["size", "mean"])
        print(f"  {arm:9s} " + " / ".join(
            f"{q} {r['mean'] * 100:.1f}%" for q, r in s.iterrows()))

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"arms": {k: len(v) for k, v in ARMS.items()},
             "quarters": sorted(set(ref["quarter"])),
             "n_races": int(ref["race_id"].nunique()),
             "paired_vs_base": pair}, ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
