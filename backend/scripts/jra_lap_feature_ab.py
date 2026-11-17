"""JRA ラップ由来の過去走特徴 A/B（v28 38列 vs 38列 + ラップ7列）。

事前登録: `docs/jra_lap_feature_plan_2026_09_16.md`。
**本スクリプトは事前登録の仕様を実装するだけで、判定基準を動かさない。**

## 独立実装をしない

- データセット・母集団・34列変換・v28 の PIT 特徴 … `jra_winplace_feature_ab.build_dataset`
- 学習手順（ハイパラ・seed・early stopping）… 同 `run_arm` / `fit_predict`
- 主指標と判定 … 同 `quarter_paired`（§10.4 と同一）
- ラップ特徴 … 本番モジュール `src.indices.lap_form`

`v28` 腕は `jra_winplace_feature_ab` の `feat` 腕と同一の列なので、同スクリプトの
保存済み結果があれば四半期別の対数損失を並べて出す（基盤が再現しているかの目視確認）。

## 使い方

    cd backend
    .venv/bin/python scripts/jra_lap_feature_ab.py \
        --quarters 2024Q1..2026Q2 --seeds 42,123,456 --bootstrap 2000 \
        --cache /tmp/lap_ab_dataset.pkl --pred-cache /tmp/lap_ab_pred.pkl
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
import psycopg2  # noqa: E402

import scripts.jra_winplace_feature_ab as ab  # noqa: E402
from scripts.jra_place_residual_diag import _dsn  # noqa: E402
from scripts.jra_prob_scoring import place_scores, win_scores  # noqa: E402
from src.indices.lap_form import (  # noqa: E402
    LAP_FORM_FEATURE_NAMES,
    compute_lap_form_features,
    load_lap_context,
)

logger = logging.getLogger("lap_ab")

OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_lap_feature_ab.json"
PRIOR_AB_PATH = _root.parent / "docs" / "model_verification" / "jra_winplace_feature_ab.json"

V28_COLS: list[str] = list(ab.FEATURES) + list(ab.FEAT_EXTRA)
LAP_COLS: list[str] = V28_COLS + list(LAP_FORM_FEATURE_NAMES)
ARMS = {"v28": V28_COLS, "lap": LAP_COLS}


def attach_lap_features(df: pd.DataFrame, end: str) -> tuple[pd.DataFrame, dict]:
    """`df` の各行に `LAP_FORM_FEATURE_NAMES` を付ける（本番モジュールの純関数を通す）。

    組み立ては本番 `lap_form.load_lap_context`（配信の `build_lap_form_features_for_race` と
    同じ `build_lap_context`）に任せる。2026-11-07 にここから移した（中身は同一）。
    """
    conn = psycopg2.connect(_dsn())
    t0 = time.time()
    par, store, meta_info = load_lap_context(conn, end_date=end)
    conn.close()
    logger.info(
        "ラップ要約 %d レース（区間数不一致で除外 %d）/ 有効走 %d (%.1fs)",
        meta_info["lap_races"],
        meta_info["lap_races_rejected"],
        meta_info["lap_runs"],
        time.time() - t0,
    )

    t0 = time.time()
    cols: dict[str, list] = {name: [] for name in LAP_FORM_FEATURE_NAMES}
    for hid, date in zip(df["horse_id"].to_numpy(), df["date"].astype(str).to_numpy()):
        f = compute_lap_form_features(store.before(int(hid), date), par, date)
        for name in LAP_FORM_FEATURE_NAMES:
            v = f[name]
            cols[name].append(np.nan if v is None else float(v))
    out = df.copy()
    for name in LAP_FORM_FEATURE_NAMES:
        out[name] = cols[name]
    logger.info("ラップ特徴を生成 (%.1fs)", time.time() - t0)
    return out, dict(meta_info)


def coverage(df: pd.DataFrame) -> dict[str, float]:
    return {c: round(float(df[c].notna().mean() * 100), 2) for c in LAP_FORM_FEATURE_NAMES}


def visual_check(df: pd.DataFrame, evs: dict[str, pd.DataFrame]) -> list[str]:
    """ラップ特徴の有無が混在するレースを1つ出す。Σp_win が両腕とも 1 であること。"""
    ev = evs["v28"]  # 評価行は df の行を複製したものなのでラップ列を既に持っている
    mixed = ev.groupby("race_id")["lap_l3_par_l1"].agg(lambda s: s.isna().any() and s.notna().any())
    rid = int(mixed[mixed].index[0]) if mixed.any() else int(ev["race_id"].iloc[0])
    src = df[df["race_id"] == rid].sort_values("horse_number")
    r0 = src.iloc[0]
    L = [
        "",
        "=" * 130,
        f"目視確認 race_id={rid} {r0['date']} {r0['course_name']}{int(r0['race_number'])}R "
        f"{r0['race_name']} {r0['surface']}{int(r0['distance'])}m n={len(src)}",
        "=" * 130,
    ]
    short = {
        "lap_front_par_l1": "F1",
        "lap_l3_par_l1": "L1",
        "lap_race_front_par_l1": "RF1",
        "lap_race_shape_l1": "SH1",
        "lap_front_par_m3": "Fm3",
        "lap_l3_par_m3": "Lm3",
        "lap_l3_par_best3": "Lb3",
    }
    hdr = f"{'馬番':>4}{'着':>4}  {'馬名':<18}" + "".join(f"{short[c]:>7}" for c in LAP_FORM_FEATURE_NAMES)
    hdr += "".join(f"{'p_win:' + a:>13}" for a in ARMS)
    L.append(hdr)
    pw = {a: evs[a].set_index(["race_id", "horse_number"])["p_win"] for a in ARMS}
    for _, r in src.iterrows():
        hn = int(r["horse_number"])
        line = f"{hn:>4}{int(r['finish_position']):>4}  {str(r['horse_name'])[:17]:<18}"
        for c in LAP_FORM_FEATURE_NAMES:
            v = r[c]
            line += f"{'NaN':>7}" if pd.isna(v) else f"{float(v):>7.2f}"
        for a in ARMS:
            line += f"{pw[a].get((rid, hn), float('nan')):>13.5f}"
        L.append(line)
    L.append(
        f"{'Σ':>4}"
        + " " * (4 + 20 + 7 * len(LAP_FORM_FEATURE_NAMES))
        + "".join(f"{evs[a][evs[a]['race_id'] == rid]['p_win'].sum():>13.5f}" for a in ARMS)
    )
    L.append(
        "（F=前半 par 比 秒/200m・L=上がり3F par 比 秒・RF=そのレースの前半ペース・"
        "SH=ラップ形状(負=加速)。いずれも対象レースより前の走から作る）"
    )
    return L


def prior_feat_arm(qs: list[tuple[str, str, str]]) -> dict[str, float]:
    """保存済み `jra_winplace_feature_ab.json` の `feat` 腕の四半期別対数損失。"""
    if not PRIOR_AB_PATH.exists():
        return {}
    try:
        prior = json.loads(PRIOR_AB_PATH.read_text())
        rows = prior["results"]["primary"]["feat"]["by_quarter"]
    except (KeyError, ValueError):
        return {}
    labels = {q[0] for q in qs}
    return {r["quarter"]: float(r["arm_logloss"]) for r in rows if r["quarter"] in labels}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--quarters", default="2024Q1..2026Q2")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--data-start", default="20230101")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--cache", default=None, help="データセット pickle（ラップ特徴込み）")
    p.add_argument("--pred-cache", default=None, help="腕ごとの評価行 pickle")
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    qs = ab.parse_quarters(args.quarters)
    eval_end = qs[-1][2]
    if eval_end >= ab.TEST_START:
        raise SystemExit(
            f"🔴 評価終端 {eval_end} が TEST_START={ab.TEST_START} 以降。" "事前登録 §3 により 2026Q3 は使わない"
        )

    cache = Path(args.cache) if args.cache else None
    lap_meta: dict = {}
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = ab.build_dataset(args.data_start, eval_end)
        df, lap_meta = attach_lap_features(df, eval_end)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)
    df = df[df["date"] < ab.TEST_START].reset_index(drop=True)
    cov_all = coverage(df)
    logger.info("母集団 %d行 / %dレース / ラップ特徴の充足率(%%) %s", len(df), df["race_id"].nunique(), cov_all)

    # 🔴 学習手順は ab.run_arm をそのまま使う（腕の列定義だけを登録する）
    for arm, cols in ARMS.items():
        ab.ARMS[arm] = {"names": cols, "cols": cols}

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs = pd.read_pickle(pred_cache)
        logger.info("予測をキャッシュから読込: %s", pred_cache)
    else:
        evs = {}
        for arm in ARMS:
            logger.info("=== arm=%s (%d特徴) ===", arm, len(ARMS[arm]))
            evs[arm] = ab.run_arm(df, arm, qs, seeds, args.valid_days)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)

    vis = visual_check(df, evs)
    print("\n".join(vis))

    base_ev = evs["v28"]
    eval_rows = df[df["race_id"].isin(base_ev["race_id"].unique())]
    cov_eval = coverage(eval_rows)
    print(
        f"\n評価対象: {base_ev['race_id'].nunique():,}レース / {len(base_ev):,}頭 "
        f"({base_ev['date'].min()}〜{base_ev['date'].max()}) / 四半期 {len(qs)}本"
    )
    print("ラップ特徴の充足率(%, 評価行): " + " / ".join(f"{k}={v}" for k, v in cov_eval.items()))

    paired = ab.quarter_paired(evs["lap"], base_ev, args.bootstrap)
    prior = prior_feat_arm(qs)
    print("\n" + "=" * 100)
    print("  【主指標】レース単位 多項対数損失  lap − v28（負＝改善）")
    print("=" * 100)
    print(f"{'四半期':<9}{'nR':>6}{'v28':>10}{'lap':>10}{'Δ':>10}{'top1勝率 v28→lap':>22}" f"{'(参考)前回feat':>16}")
    for r in paired["by_quarter"]:
        pr = prior.get(r["quarter"])
        print(
            f"{r['quarter']:<9}{r['n_races']:>6}{r['base_logloss']:>10.4f}{r['arm_logloss']:>10.4f}"
            f"{r['delta']:>+10.4f}{r['base_top1_win_rate']:>12.4f} →{r['arm_top1_win_rate']:>7.4f}"
            f"{(f'{pr:.4f}' if pr is not None else '-'):>16}"
        )
    eq, po = paired["quarter_equal_weight"], paired["race_pooled"]
    print(f"\n  四半期等重み Δ={eq['delta']:+.5f} 95%CI=[{eq['ci95'][0]:+.5f}, {eq['ci95'][1]:+.5f}]")
    print(
        f"  レース重み   Δ={po['delta']:+.5f} 95%CI=[{po['ci95'][0]:+.5f}, {po['ci95'][1]:+.5f}]"
        f" (n={po['n_races']}R)"
    )
    print(f"  改善四半期 {paired['n_improved_quarters']}/{paired['n_quarters']} → **{paired['verdict']}**")

    print("\n" + "=" * 100)
    print("  【副指標】全期間プール（採否には使わない）")
    print("=" * 100)
    sec: dict = {}
    print(
        f"{'腕':<6}{'nR':>7}{'MNL logloss':>13}{'info gain%':>12}{'top1勝率':>10}{'top1複勝率':>12}"
        f"{'cov@3':>9}{'spearman':>10}"
    )
    for a in ARMS:
        w = win_scores(evs[a], "p_win")
        s3 = evs[a][evs[a]["place_slots"] == 3]
        m = place_scores(s3, "p_place", "p_win")
        sec[a] = {"win": {k: v for k, v in w.items() if not k.startswith("_")}, "place_slots_3": m}
        sp = m["spearman_in_race"] if m["spearman_in_race"] is not None else float("nan")
        print(
            f"{a:<6}{w['n_races']:>7}{w['mnl_logloss']:>13.5f}{w['info_gain_pct']:>12.2f}"
            f"{w['top1_win_rate']:>10.4f}{w['top1_place_rate']:>12.4f}"
            f"{m['coverage_at_k']:>9.4f}{sp:>10.4f}"
        )

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "preregistration": "docs/jra_lap_feature_plan_2026_09_16.md",
        "quarters": [{"label": q[0], "start": q[1], "end": q[2]} for q in qs],
        "test_window_excluded": ab.TEST_START,
        "arms": {a: {"n_features": len(c), "features": c} for a, c in ARMS.items()},
        "seeds": seeds,
        "bootstrap": args.bootstrap,
        "valid_days": args.valid_days,
        "n_rows": int(len(df)),
        "n_races": int(df["race_id"].nunique()),
        "lap_meta": lap_meta,
        "lap_feature_coverage_pct": {"all_rows": cov_all, "eval_rows": cov_eval},
        "criteria": "四半期等重み Δ の 95%CI が 0 を跨がず改善側、かつ改善 6/10 以上（§4）",
        "prior_feat_arm_logloss": prior,
        "visual_check": vis,
        "results": {"primary": paired, "secondary": sec},
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {outp}")


if __name__ == "__main__":
    main()
