"""JRA v28 複勝の独立ヘッド（is_placed・38特徴）の学習スクリプト。

計画 `docs/jra_winplace_structure_plan_2026_09_04.md` §16.1 / §13.1 / §18。

## なにを変えるのか

v27 まで `place_probability` は **単勝確率からの Harville 変換**
（`composite.CompositeIndexCalculator._harville_place_probs`）だった。これは
`p_place` が `p_win` の単調関数になるため、**単勝順位と複勝順位が絶対に交差しない**。
実測でも全窓で交差 0件だった（§9.2）。

v28 は複勝を**独立の binary ヘッド**にして、レース内で `Σp = place_slots` に
正規化する。2026Q3 の一度きり評価（648R）で

    複勝 place_ll (place_slots=3・625R) 0.47118 → 0.45934   Δ −0.01184
                                          95%CI [−0.01515, −0.00832]
    交差 0R → 610R / 3,533ペア

となり「確認成功」した（§18.1）。

## 🔴 ラベルは `place_slots` ごとに変える（§13.1 罠1 / §18.3-2）

    place_slots = 3 (n >= 8) → finish_position <= 3
    place_slots = 2 (5 <= n <= 7) → finish_position <= 2
    place_slots = 0 (n < 5) → 払戻対象着順が無い ⇒ **学習から除外する**

Phase D-1 は一律 `<= 3` でラベルを作っていたため、5〜7頭立てを
「3着以内ヘッドを2着以内で採点」していた。本スクリプトと本番 `composite.py` は
`place_slots` に揃える。

🔴 `place_slots` は **`races.head_count` から作らない**。`head_count` は発走前 NULL で
配信時にだけ壊れる（地方で train/serve skew の前例あり）。**実際のフィールドの
馬数**から作る — 学習は `jra_prob_scoring.build_population` が数えたレース内行数、
配信は `composite.place_slots_for_field(len(results))`。**同じ関数の同じ規則**である。

## 正規化とクリップ

`composite.normalize_place_to_slots`（本番関数）をそのまま呼ぶ。
**クリップのみ・再正規化なし**。根拠はその docstring を参照（検証実装と同一）。

## 特徴・境界・データセット

`train_jra_iswin_head.py` の `load_v28_dataset` / `train_model` / `PARAMS` /
`MAX_ROUND` を **import して共有する**。2本のヘッドで母集団・特徴・ハイパラ・
early stopping が食い違わないようにするため（検証も `fit_predict` を共有していた）。

出力:
  models/v28_placed_head.txt
  models/v28_placed_head_metrics.json

使い方:
  cd backend
  .venv/bin/python scripts/train_jra_placed_head.py
"""
from __future__ import annotations

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

from scripts.jra_calibration_ab import race_normalize  # noqa: E402
from scripts.jra_prob_scoring import harville_place, place_scores  # noqa: E402
from scripts.train_jra_iswin_head import (  # noqa: E402
    MAX_ROUND,
    NEW_FEATURE_NAMES,
    PARAMS,
    is_win_label,
    load_v28_dataset,
    train_model,
)
from src import jra_protocol  # noqa: E402
from src.indices.composite import (  # noqa: E402
    V28_FEATURE_NAMES,
    normalize_place_to_slots,
    place_slots_for_field,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_placed")

MODELS_DIR = _root / "models"
MODEL_PATH = MODELS_DIR / "v28_placed_head.txt"
METRICS_PATH = MODELS_DIR / "v28_placed_head_metrics.json"

EPS = 1e-9


def is_placed_label(df: pd.DataFrame) -> np.ndarray:
    """🔴 `finish_position <= place_slots` を 1 とするラベル。

    ⚠️ **`place_slots == 0` の行が混ざっていてはいけない**（払戻対象着順が無く
    ラベルが定義できない）。呼び出し側で `drop_no_place_slots` を通すこと。
    ここでも念のため検査して落ちる。
    """
    slots = pd.to_numeric(df["place_slots"], errors="coerce")
    if bool((slots <= 0).any()):
        raise ValueError(
            "place_slots <= 0 の行が学習に混ざっている（§18.3-2: 5頭未満は除外する）"
        )
    fp = pd.to_numeric(df["finish_position"], errors="coerce")
    return (fp <= slots).astype(int).to_numpy()


def drop_no_place_slots(df: pd.DataFrame) -> pd.DataFrame:
    """`place_slots == 0`（5頭未満・複勝の発売なし）の行を落とす（§18.3-2）。"""
    return df[pd.to_numeric(df["place_slots"], errors="coerce") > 0].copy()


def sanity_check_place_slots(df: pd.DataFrame) -> dict:
    """🔴 `place_slots` が **フィールドの馬数**から作られていることを実測で検査する。

    `races.head_count` から作ると発走前 NULL で壊れるため、
    `build_population` が数えた `n_runners` と `composite.place_slots_for_field` の
    規則が一致していることをここで固定する。ずれていれば即座に落ちる。
    """
    n = pd.to_numeric(df["n_runners"], errors="coerce").to_numpy()
    slots = pd.to_numeric(df["place_slots"], errors="coerce").to_numpy()
    expect = np.asarray([place_slots_for_field(int(v)) for v in n])
    bad = int((expect != slots).sum())
    if bad:
        raise SystemExit(
            f"place_slots が place_slots_for_field(n_runners) と {bad} 行で不一致"
        )
    hc = pd.to_numeric(df["head_count"], errors="coerce").to_numpy()
    hc_mismatch = int(np.nansum(hc != n))
    return {
        "rule": "place_slots_for_field(n_runners): n>=8→3 / 5<=n<=7→2 / n<5→0",
        "n_runners_source": "jra_prob_scoring.build_population（レース内の実行数）",
        "rows_where_head_count_differs_from_n_runners": hc_mismatch,
        "slots_distribution": {
            str(int(k)): int(v) for k, v in pd.Series(slots).value_counts().items()
        },
    }


def place_metrics(te: pd.DataFrame, place_col: str, win_col: str) -> dict:
    """`place_slots` ごとの複勝指標（`jra_prob_scoring.place_scores` をそのまま使う）。"""
    out = {}
    for slots in (3, 2):
        d = te[te["place_slots"] == slots]
        if len(d):
            out[f"slots_{slots}"] = place_scores(d, place_col, win_col)
    return out


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20991231")
    p.add_argument("--train-end", default=jra_protocol.TRAIN_END)
    p.add_argument("--valid-end", default=jra_protocol.VAL_END)
    p.add_argument("--refit-end", default=jra_protocol.TRAIN_DATA_END)
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--no-harville-baseline", action="store_true",
                   help="honest test の Harville ベースライン（is_win ヘッドの学習が要る）を省く")
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    logger.info("protocol: %s", jra_protocol.describe())

    df = load_v28_dataset(args.start, args.end)
    slots_info = sanity_check_place_slots(df)
    logger.info("place_slots の検査: %s", slots_info)

    tr_all = df[df["date"] <= args.train_end]
    va_all = df[(df["date"] > args.train_end) & (df["date"] <= args.valid_end)]
    te = df[df["date"] > args.valid_end].reset_index(drop=True)

    # 🔴 place_slots=0 は学習からのみ除外する（評価対象にも入らない）
    tr, va = drop_no_place_slots(tr_all), drop_no_place_slots(va_all)
    dropped = {"train": int(len(tr_all) - len(tr)), "valid": int(len(va_all) - len(va)),
               "reason": "place_slots=0（5頭未満）は払戻対象着順が無くラベルが定義できない"}
    logger.info("train=%d valid=%d test=%d / 除外 %s", len(tr), len(va), len(te), dropped)
    if not len(tr) or not len(va):
        raise SystemExit("train / valid が空。境界を確認すること")

    Xte = te[V28_FEATURE_NAMES].to_numpy(dtype=float) if len(te) else None

    # ── ラウンド数を VAL で選ぶ ──
    best_iters, te_preds = [], []
    for seed in seeds:
        m = train_model(tr, is_placed_label, seed=seed, valid_df=va)
        best_iters.append(int(m.best_iteration))
        if Xte is not None:
            te_preds.append(m.predict(Xte, num_iteration=m.best_iteration))
    n_rounds = int(np.median(best_iters))
    logger.info("best_iter=%s → refit rounds=%d", best_iters, n_rounds)

    metrics: dict = {
        "head": "is_placed (v28 / 38特徴・独立ヘッド)",
        "label": "finish_position <= place_slots（🔴 place_slots ごとに変える・§13.1 罠1）",
        "place_slots": slots_info,
        "dropped_rows": dropped,
        "normalization": "composite.normalize_place_to_slots（Σp=place_slots・クリップのみ／再正規化なし）",
        "train_period": [df["date"].min(), args.train_end],
        "valid_period": [args.train_end, args.valid_end],
        "seeds": seeds,
        "best_iters": best_iters,
        "refit_rounds": n_rounds,
        "features": list(V28_FEATURE_NAMES),
        "n_features": len(V28_FEATURE_NAMES),
        "params": PARAMS,
        "max_round": MAX_ROUND,
        "new_feature_missing_pct": {
            c: round(float(df[c].isna().mean() * 100), 2) for c in NEW_FEATURE_NAMES
        },
    }

    # ── honest test: TRAIN のみで学習したモデルを TEST に一度だけ当てる ──
    if len(te):
        raw_te = np.clip(np.mean(te_preds, axis=0), EPS, 1.0 - EPS)
        te["p_placed_raw"] = raw_te

        # 🔴 本番と同じ関数で正規化する（レース単位・Σ=place_slots・クリップのみ）
        norm = np.full(len(te), np.nan)
        n_clipped = 0
        for _, idx in te.groupby("race_id", sort=False).indices.items():
            slots = place_slots_for_field(len(idx))
            if slots <= 0:
                continue
            vals = normalize_place_to_slots(raw_te[idx], slots)
            scaled = raw_te[idx] * slots / float(raw_te[idx].sum())
            n_clipped += int((scaled > 1.0 - EPS).sum())
            norm[idx] = vals
        te["p_place_new"] = norm
        metrics["test_clipped_horses"] = n_clipped

        # 単勝確率（交差件数・Harville ベースライン用）。TRAIN のみで学習する。
        if not args.no_harville_baseline:
            w_preds = []
            for seed in seeds:
                mw = train_model(tr_all, is_win_label, seed=seed, valid_df=va_all)
                w_preds.append(mw.predict(Xte, num_iteration=mw.best_iteration))
            te["p_win"] = race_normalize(np.mean(w_preds, axis=0), te["race_id"])
            te["p_place_harville"] = harville_place(te, "p_win")
            metrics["test_harville"] = place_metrics(te, "p_place_harville", "p_win")
        else:
            te["p_win"] = np.nan

        metrics["test"] = {
            "period": [te["date"].min(), te["date"].max()],
            "n": int(len(te)), "n_races": int(te["race_id"].nunique()),
            **place_metrics(te, "p_place_new", "p_win"),
        }

        # Σp = place_slots の実測（クリップの分だけ崩れうる）
        s = te[te["place_slots"] > 0].groupby("race_id").agg(
            total=("p_place_new", "sum"), slots=("place_slots", "first"))
        metrics["test_sum_deviation_max"] = round(
            float((s["total"] - s["slots"]).abs().max()), 6)
        logger.info("honest test: Σp−place_slots の最大乖離 = %.6f / クリップ %d頭",
                    metrics["test_sum_deviation_max"], n_clipped)

        visual_check(te)
    else:
        logger.warning("test 期間にデータなし（TEST_START=%s）", jra_protocol.TEST_START)

    # ── 本番モデル: TEST_START の前日まで で refit ──
    fit = drop_no_place_slots(df[df["date"] <= args.refit_end])
    logger.info("refit: %d行 (%s〜%s)", len(fit), fit["date"].min(), fit["date"].max())
    final = train_model(fit, is_placed_label, seed=seeds[0], num_round=n_rounds)
    MODELS_DIR.mkdir(exist_ok=True)
    final.save_model(str(MODEL_PATH))
    metrics["model_path"] = str(MODEL_PATH)
    metrics["refit_period"] = [fit["date"].min(), fit["date"].max()]
    metrics["n_rows"] = int(len(fit))
    metrics["n_races"] = int(fit["race_id"].nunique())
    metrics["protocol"] = jra_protocol.describe()
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
    logger.info("保存: %s / %s", MODEL_PATH, METRICS_PATH)


def visual_check(te: pd.DataFrame) -> None:
    """🔴 実データを1レース表示して目視確認する（`CLAUDE.md` 検証の作法）。

    見るのは3点: `Σp_place = place_slots` / 新特徴が **NaN のまま**渡っていること /
    複勝順位が単勝順位と**交差しうる**こと（Harville では原理的に起きない）。
    """
    d = te[te["place_slots"] == 3]
    if not len(d):
        return
    rid = int(d["race_id"].iloc[0])
    g = te[te["race_id"] == rid].sort_values("horse_number")
    print("\n" + "=" * 120)
    print(f"🔴 目視確認 race_id={rid} {g.iloc[0]['date']} n={len(g)} "
          f"place_slots={int(g.iloc[0]['place_slots'])}")
    print("=" * 120)
    print(f"{'馬番':>4}{'着':>4}{'脚質ord':>9}{'着順分散5':>11}{'勝複比5':>10}"
          f"{'pace_pit':>10}{'p_win':>10}{'raw_placed':>12}{'p_place new':>13}"
          f"{'p_place harv':>14}")

    def _f(v: object, w: int, dg: int = 3) -> str:
        try:
            fv = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return f"{'-':>{w}}"
        return f"{'NaN':>{w}}" if np.isnan(fv) else f"{fv:>{w}.{dg}f}"

    for _, r in g.iterrows():
        print(f"{int(r['horse_number']):>4}{int(r['finish_position']):>4}"
              f"{_f(r['runner_type_ord'], 9)}{_f(r['finish_var5'], 11)}"
              f"{_f(r['win_place_ratio5'], 10)}{_f(r['pace_handicap_pit'], 10)}"
              f"{_f(r.get('p_win'), 10, 5)}{_f(r['p_placed_raw'], 12, 5)}"
              f"{_f(r['p_place_new'], 13, 5)}{_f(r.get('p_place_harville'), 14, 5)}")
    print(f"{'Σ':>4}{'':>4}{'':>9}{'':>11}{'':>10}{'':>10}"
          f"{_f(g['p_win'].sum(), 10, 5)}{_f(g['p_placed_raw'].sum(), 12, 5)}"
          f"{_f(g['p_place_new'].sum(), 13, 5)}{_f(g['p_place_harville'].sum() if 'p_place_harville' in g else None, 14, 5)}")
    print(f"（期待: Σp_win=1.00000 / Σp_place new = {int(g.iloc[0]['place_slots'])}.00000 "
          f"/ raw_placed だけはずれてよい）")


if __name__ == "__main__":
    main()
