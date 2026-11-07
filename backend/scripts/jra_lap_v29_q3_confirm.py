"""JRA ラップ7列（v29 候補）の 2026Q3 確認（v28 38列 vs 38列 + ラップ7列）。

事前登録: `docs/jra_lap_v29_q3_confirm_plan_2026_11_07.md`。
**本スクリプトは事前登録の仕様を実装するだけで、腕を増やさず・判定基準を動かさない。**

## 独立実装をしない

- データセット・母集団・変換 … `jra_winplace_feature_ab.build_dataset`
- ラップ特徴 … `jra_lap_feature_ab.attach_lap_features`（本番 `src.indices.lap_form` を通す）
- 学習（ハイパラ・early stopping・seed 平均）… `jra_winplace_feature_ab.fit_predict`
- 単勝の正規化 … `jra_prob_scoring.race_normalize`（本番 `composite.py` と同じ L1）
- 主指標の CI … `jra_prob_scoring.paired_logloss_ci`（レース単位 bootstrap）
- 複勝の独立ヘッド（副指標）… `jra_winplace_final_confirm.relabel_is_placed` /
  `normalize_to_slots`、CI は `jra_place_head_ab.quarter_paired_place`

## 窓（§3）

学習 〜2026-06-30 / 評価 2026-07-01〜2026-09-30。
🔴 **2026-10-01 以降（2026Q4 = 現在の TEST）の行が1行でも読み込まれたら止める。**

## 使い方

    cd backend
    ~/bin/kiseki-ro .venv/bin/python scripts/jra_lap_v29_q3_confirm.py \\
        --cache /tmp/v29_q3_dataset.pkl --pred-cache /tmp/v29_q3_pred.pkl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import scripts.jra_lap_feature_ab as lab  # noqa: E402
import scripts.jra_place_head_ab as PH  # noqa: E402
import scripts.jra_winplace_feature_ab as ab  # noqa: E402
from scripts.jra_prob_scoring import (  # noqa: E402
    harville_place,
    paired_logloss_ci,
    place_scores,
    race_normalize,
    win_scores,
)
from scripts.jra_winplace_final_confirm import (  # noqa: E402
    normalize_to_slots,
    relabel_is_placed,
)
from src.indices.lap_form import LAP_FORM_FEATURE_NAMES  # noqa: E402

logger = logging.getLogger("lap_v29_q3")

PREREG = "docs/jra_lap_v29_q3_confirm_plan_2026_11_07.md"
OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_lap_v29_q3_confirm.json"

EVAL_START = "20260701"
EVAL_END = "20260930"
HARD_STOP = "20261001"  # 2026Q4 = 現在の TEST。ここ以降を読んだら止める
SPLIT_MONTH = "20260901"  # v28 の確認に使われた 7〜8月 / 使われていない 9月（§5.3）
BOOT_SEED = 20261107

ARMS = {"v28": lab.V28_COLS, "lap": lab.LAP_COLS}
BASE, NEW = "v28", "lap"
GD0_COLS = ["is_good", "is_yaya", "is_heavy", "is_bad"]


def assert_no_test_rows(df: pd.DataFrame, hard_stop: str = HARD_STOP) -> None:
    """🔴 評価窓より後（既定は 2026Q4）の行が混ざっていたら止める（§3）。"""
    bad = int((df["date"].astype(str) >= hard_stop).sum())
    if bad:
        raise SystemExit(f"🔴 {hard_stop} 以降の行が {bad} 行ある。評価窓より後のデータは使わない")


def split_train_valid(
    train: pd.DataFrame, valid_days: int, eval_start: str = EVAL_START
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """`jra_winplace_feature_ab.run_arm` と同じ train / valid 分割。"""
    cut = (pd.to_datetime(eval_start) - pd.Timedelta(days=valid_days)).strftime("%Y%m%d")
    tr, va = train[train["date"] <= cut], train[train["date"] > cut]
    if len(va) < 2000:
        i = int(len(train) * 0.8)
        tr, va = train.iloc[:i], train.iloc[i:]
    return tr, va, cut


def fit_condition(
    df: pd.DataFrame,
    seeds: list[int],
    valid_days: int,
    with_place_head: bool,
    *,
    eval_start: str = EVAL_START,
    eval_end: str = EVAL_END,
    quarter: str = "2026Q3",
) -> tuple[dict[str, pd.DataFrame], dict]:
    """1条件（主 or gd0）で2腕を同じ分割で学習し、評価行に確率を付けて返す。

    窓の引数は 2026Q4 の再確認（`jra_lap_v29_q4_recheck.py`・§9）が同じ学習を
    別の窓で回すためにある。既定値は §3 の 2026Q3。
    """
    train = df[df["date"] < eval_start]
    te0 = df[(df["date"] >= eval_start) & (df["date"] <= eval_end)].reset_index(drop=True)
    tr, va, cut = split_train_valid(train, valid_days, eval_start)
    info: dict = {
        "train_rows": int(len(tr)),
        "valid_rows": int(len(va)),
        "valid_cut": cut,
        "train_date_max": str(train["date"].max()),
        "eval_rows": int(len(te0)),
        "eval_races": int(te0["race_id"].nunique()),
    }
    evs: dict[str, pd.DataFrame] = {}
    for arm, cols in ARMS.items():
        te = te0.copy()
        t0 = time.time()
        raw_w, it_w = ab.fit_predict(tr, va, te, cols, cols, seeds)
        te["p_win"] = race_normalize(raw_w, te["race_id"])
        te["p_place"] = harville_place(te, "p_win")
        te["quarter"] = quarter
        info[f"best_iters_win__{arm}"] = it_w
        logger.info("  [%s] is_win: %d列 iters=%s (%.1fs)", arm, len(cols), it_w, time.time() - t0)
        if with_place_head:
            tr_p, va_p = relabel_is_placed(tr), relabel_is_placed(va)
            t0 = time.time()
            raw_p, it_p = ab.fit_predict(tr_p, va_p, te, cols, cols, seeds)
            te["p_placed_raw"] = np.clip(raw_p, PH.EPS, 1.0 - PH.EPS)
            te["p_place_head"], n_clip = normalize_to_slots(te, "p_placed_raw")
            info[f"best_iters_place__{arm}"] = it_p
            info[f"place_head_clipped__{arm}"] = n_clip
            logger.info("  [%s] is_placed: iters=%s clip=%d (%.1fs)", arm, it_p, n_clip, time.time() - t0)
        evs[arm] = te
    return evs, info


def win_delta(evs: dict[str, pd.DataFrame], n_boot: int, mask_fn=None) -> dict:
    """`lap − v28` の単勝多項対数損失差と 95%CI（レース単位 bootstrap）。"""
    a, b = evs[NEW], evs[BASE]
    if mask_fn is not None:
        a, b = a[mask_fn(a)], b[mask_fn(b)]
    sa, sb = win_scores(a, "p_win"), win_scores(b, "p_win")
    ci = paired_logloss_ci(sa, sb, n_boot=n_boot, seed=BOOT_SEED)
    return {"lap_logloss": sa["mnl_logloss"], "v28_logloss": sb["mnl_logloss"], **ci}


def verdict(c: dict) -> str:
    """§4 の判定（CI だけで決める）。"""
    if c["ci95"][1] < 0:
        return "確認成功"
    if c["delta_logloss"] < 0:
        return "判断保留"
    return "確認失敗"


def place_head_delta(evs: dict[str, pd.DataFrame], n_boot: int) -> dict:
    """副指標: 複勝の独立ヘッドにラップ7列を入れた場合の `place_ll`（slots=3）差。"""
    ev = evs[BASE][["race_id", "horse_number", "place_slots", "finish_position", "quarter"]].copy()
    ev["p_place__v28"] = evs[BASE]["p_place_head"].to_numpy()
    ev["p_place__lap"] = evs[NEW]["p_place_head"].to_numpy()
    PH.PLACE_COL = {"v28": "p_place__v28", "lap": "p_place__lap"}
    PH.BASE_ARM = "v28"
    r = PH.quarter_paired_place(ev, "lap", n_boot, min_improved=1, seed=BOOT_SEED)
    return {"place_slots_3": r["race_pooled"], "by_quarter": r["by_quarter"]}


def secondary(evs: dict[str, pd.DataFrame]) -> dict:
    out = {}
    for a, ev in evs.items():
        w = win_scores(ev, "p_win")
        m = place_scores(ev[ev["place_slots"] == 3], "p_place", "p_win")
        out[a] = {"win": {k: v for k, v in w.items() if not k.startswith("_")}, "place_slots_3_harville": m}
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--data-start", default="20230101")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--cache", default=None, help="データセット pickle（ラップ特徴込み）")
    p.add_argument("--pred-cache", default=None, help="条件×腕ごとの評価行 pickle")
    p.add_argument("--check-only", action="store_true", help="データを作って目視確認だけ出す（主指標は計算しない）")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    cache = Path(args.cache) if args.cache else None
    lap_meta: dict = {}
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = ab.build_dataset(args.data_start, EVAL_END)
        assert_no_test_rows(df)
        df, lap_meta = lab.attach_lap_features(df, EVAL_END)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)
    assert_no_test_rows(df)
    df = df.reset_index(drop=True)

    ev_rows = df[(df["date"] >= EVAL_START) & (df["date"] <= EVAL_END)]
    cov_eval = lab.coverage(ev_rows)
    logger.info(
        "評価行 %d / %dR (%s〜%s) ラップ充足(%%) %s",
        len(ev_rows),
        ev_rows["race_id"].nunique(),
        ev_rows["date"].min(),
        ev_rows["date"].max(),
        cov_eval,
    )

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        allev = pd.read_pickle(pred_cache)
        logger.info("予測をキャッシュから読込: %s", pred_cache)
    else:
        allev = {}
        logger.info("=== 主条件（確定馬場あり） ===")
        allev["main"] = fit_condition(df, seeds, args.valid_days, with_place_head=True)
        if not args.check_only:
            logger.info("=== gd0（馬場4列を学習・評価とも 0） ===")
            d0 = df.copy()
            d0[GD0_COLS] = 0
            allev["gd0"] = fit_condition(d0, seeds, args.valid_days, with_place_head=False)
            if pred_cache:
                pd.to_pickle(allev, pred_cache)

    evs, info = allev["main"]
    # 目視確認は lap_ab の関数を使う（腕名 v28 / lap が同じ）
    vis = lab.visual_check(ev_rows, evs)
    print("\n".join(vis))
    sums = {a: float(ev.groupby("race_id")["p_win"].sum().sub(1).abs().max()) for a, ev in evs.items()}
    print(f"\nΣp_win の 1 からの最大ずれ: {sums}")
    if args.check_only:
        return

    primary = win_delta(evs, args.bootstrap)
    v = verdict(primary)
    by_month = {
        "jul_aug_used_by_v28": win_delta(evs, args.bootstrap, lambda e: e["date"] < SPLIT_MONTH),
        "sep_unused": win_delta(evs, args.bootstrap, lambda e: e["date"] >= SPLIT_MONTH),
    }
    gd0 = win_delta(allev["gd0"][0], args.bootstrap)
    ph = place_head_delta(evs, args.bootstrap)
    sec = secondary(evs)

    print("\n" + "=" * 90)
    print("  【主指標】単勝 多項対数損失  lap − v28（負＝改善）  2026Q3")
    print("=" * 90)
    print(f"  n={primary['n_races']}R  v28={primary['v28_logloss']:.5f}  lap={primary['lap_logloss']:.5f}")
    print(
        f"  Δ={primary['delta_logloss']:+.5f}  95%CI=[{primary['ci95'][0]:+.5f}, {primary['ci95'][1]:+.5f}]  → **{v}**"
    )
    print("\n  （以下は判定に使わない）")
    for k, c in by_month.items():
        print(
            f"  月内訳 {k:<20} n={c['n_races']:>4}R Δ={c['delta_logloss']:+.5f} "
            f"CI=[{c['ci95'][0]:+.5f}, {c['ci95'][1]:+.5f}]"
        )
    print(
        f"  gd0                       n={gd0['n_races']:>4}R Δ={gd0['delta_logloss']:+.5f} "
        f"CI=[{gd0['ci95'][0]:+.5f}, {gd0['ci95'][1]:+.5f}]"
    )
    pc = ph["place_slots_3"]
    print(
        f"  複勝独立ヘッド place_ll   n={pc['n_races']:>4}R Δ={pc['delta']:+.5f} "
        f"CI=[{pc['ci95'][0]:+.5f}, {pc['ci95'][1]:+.5f}]"
    )
    for a in ARMS:
        w = sec[a]["win"]
        print(f"  {a:<4} top1勝率={w['top1_win_rate']:.4f} top1複勝率={w['top1_place_rate']:.4f}")

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "preregistration": PREREG,
        "window": {"train_end": "20260630", "eval_start": EVAL_START, "eval_end": EVAL_END, "hard_stop": HARD_STOP},
        "arms": {a: {"n_features": len(c), "features": c} for a, c in ARMS.items()},
        "seeds": seeds,
        "bootstrap": args.bootstrap,
        "boot_seed": BOOT_SEED,
        "valid_days": args.valid_days,
        "fit_info": info,
        "fit_info_gd0": allev["gd0"][1],
        "lap_meta": lap_meta,
        "lap_feature_coverage_pct_eval": cov_eval,
        "lap_feature_names": list(LAP_FORM_FEATURE_NAMES),
        "visual_check": vis,
        "sum_p_win_max_abs_dev": sums,
        "criteria": "95%CI 上端<0 → 確認成功 / 点推定<0 → 判断保留 / それ以外 → 確認失敗（§4）",
        "results": {
            "primary": {**primary, "verdict": v},
            "report_only": {"by_month": by_month, "gd0": gd0, "place_head": ph, "secondary": sec},
        },
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {outp}")


if __name__ == "__main__":
    main()
