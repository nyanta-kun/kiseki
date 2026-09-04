"""v28 の「レース単位のフィールドをどう定義するか」を VAL で修正前後比較する。

PR #462 のレビュー指摘1+2 への検証。`docs/jra_winplace_structure_plan_2026_09_04.md`
§18 / §20 の続き。

## 何が変わったのか

v28 の最初の実装は、レース単位の量（`place_slots` と `pace_handicap_pit` の
`pace_type`）を **`jra_prob_scoring.build_population` 後の完走馬**で決めていた。
配信 `composite.calculate_and_save` は **`race_entries` の全馬**で決める。
配信は取消を物理的に知り得ない（`abnormality_code` は `race_results` ＝ 確定後にしか
無い）ので、**学習を配信に合わせた**（`train_jra_iswin_head.attach_serving_field`）。

    old（修正前）: prod_featurize → build_population → attach_past_form
    new（修正後）: prod_featurize → attach_serving_field → attach_past_form
                                 → build_population

実測: エントリー数 ≠ 完走数 が **11.15%**（1,293/11,592R）。
そのうち `place_slots` まで変わるのが **0.863%**（100R）。

## 🔴 なぜ VAL で測るのか

修正はモデルの出力を変える。§18 の 2026Q3 は**修正前のパイプラインで消費済み**なので
再測定に使えない（`scripts/JRA_TEST_USAGE_LEDGER.md`）。
本スクリプトは **`--eval-end` が `jra_protocol.TEST_START` 以降なら起動を拒否する**。

    学習    ≤ 20250630（`jra_protocol.TRAIN_END`）
    早期停止  20250701〜20251231（VAL 前半・評価窓と重ねない）
    評価     20260101〜20260630（VAL 後半 = 2026Q1+Q2）

## 判定に使う枠数（🔴 ここを取り違えると比較が無意味になる）

**採点は必ず払戻規則の枠数**（= 完走頭数から決まる `place_slots_finishers`）で行う。
これが真のラベルであり、両腕で同一。`new` が 0.863% のレースで払戻規則とずれた枠数へ
正規化することの**コスト込み**で測るのが目的なので、採点側を new に合わせてはいけない。

`place_ll` / `coverage@k` の母集団は §12.3 に倣い **払戻規則の枠数が 3** のレース。

使い方:
    cd backend
    .venv/bin/python scripts/jra_v28_field_definition_ab.py
    .venv/bin/python scripts/jra_v28_field_definition_ab.py --n-boot 500   # 試走
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
import psycopg2  # noqa: E402

from scripts.jra_calibration_ab import race_normalize  # noqa: E402
from scripts.jra_place_head_ab import _place_ll_per_race  # noqa: E402
from scripts.jra_prob_scoring import (  # noqa: E402
    build_population,
    harville_place,
    place_scores,
)
from scripts.train_jra_iswin_head import (  # noqa: E402
    V28_FETCH_SQL,
    _query,
    attach_past_form,
    attach_serving_field,
    build_v28_frame,
    dsn,
    is_win_label,
    train_model,
)
from scripts.train_jra_out_rate import featurize as prod_featurize  # noqa: E402
from src import jra_protocol  # noqa: E402
from src.indices.composite import (  # noqa: E402
    V28_FEATURE_NAMES,
    normalize_place_to_slots,
    place_slots_for_field,
)
from src.indices.past_form import load_course_features, load_past_run_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("v28_field_ab")

OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_v28_field_definition_ab.json"
EPS = 1e-9
ARMS = ("old", "new")


def is_placed_label_for(df: pd.DataFrame, slots_col: str) -> np.ndarray:
    """`finish_position <= <slots_col>` のラベル（腕ごとに枠数が違う）。"""
    fp = pd.to_numeric(df["finish_position"], errors="coerce")
    return (fp <= pd.to_numeric(df[slots_col], errors="coerce")).astype(int).to_numpy()


def build_old_frame(raw: pd.DataFrame, conn: object, *, end: str, store, course_features
                    ) -> pd.DataFrame:
    """🔴 **修正前**のパイプライン（完走馬でフィールドを決める）を再現する。

    `build_v28_frame` と違い `build_population` を先に掛けるので、
    `pace_type` も `place_slots` も完走馬だけから決まる。
    """
    df = prod_featurize(raw.copy())
    df = build_population(df)          # 🔴 ここで先に絞るのが旧実装
    df = attach_past_form(df, conn, end=end, store=store, course_features=course_features)
    return df.reset_index(drop=True)


def fit_arm(frame: pd.DataFrame, slots_col: str, *, train_end: str, valid_end: str,
            eval_start: str, seeds: list[int]) -> pd.DataFrame:
    """1腕ぶんの学習と評価窓での予測。

    Returns:
        評価窓の行に `p_win` / `p_placed_raw` を付けた DataFrame。
    """
    tr = frame[frame["date"] <= train_end]
    va = frame[(frame["date"] > train_end) & (frame["date"] <= valid_end)]
    ev = frame[frame["date"] >= eval_start].reset_index(drop=True)
    if not len(tr) or not len(va) or not len(ev):
        raise SystemExit(f"train/valid/eval のいずれかが空: {len(tr)}/{len(va)}/{len(ev)}")

    Xev = ev[V28_FEATURE_NAMES].to_numpy(dtype=float)

    # --- 単勝ヘッド ---
    w_preds = []
    for seed in seeds:
        m = train_model(tr, is_win_label, seed=seed, valid_df=va)
        w_preds.append(m.predict(Xev, num_iteration=m.best_iteration))
    ev["p_win"] = race_normalize(np.mean(w_preds, axis=0), ev["race_id"])

    # --- 複勝の独立ヘッド（🔴 その腕の枠数でラベルを作る。slots=0 は学習から除外） ---
    def _label(d: pd.DataFrame) -> np.ndarray:
        return is_placed_label_for(d, slots_col)

    tr_p = tr[pd.to_numeric(tr[slots_col], errors="coerce") > 0]
    va_p = va[pd.to_numeric(va[slots_col], errors="coerce") > 0]
    p_preds = []
    for seed in seeds:
        m = train_model(tr_p, _label, seed=seed, valid_df=va_p)
        p_preds.append(m.predict(Xev, num_iteration=m.best_iteration))
    ev["p_placed_raw"] = np.clip(np.mean(p_preds, axis=0), EPS, 1.0 - EPS)
    return ev


def normalize_arm(ev: pd.DataFrame, slots_col: str) -> tuple[np.ndarray, int, float]:
    """🔴 本番関数で `Σp = <その腕の枠数>` に正規化する。

    Returns:
        (複勝確率, クリップされた頭数, Σ の最大乖離)。
    """
    raw = ev["p_placed_raw"].to_numpy(dtype=float)
    out = np.full(len(ev), np.nan)
    n_clipped, max_dev = 0, 0.0
    for _, idx in ev.groupby("race_id", sort=False).indices.items():
        slots = int(pd.to_numeric(ev[slots_col], errors="coerce").to_numpy()[idx[0]])
        if slots <= 0:
            out[idx] = np.nan
            continue
        vals = np.asarray(normalize_place_to_slots(raw[idx], slots), dtype=float)
        scaled = raw[idx] * slots / float(raw[idx].sum())
        n_clipped += int((scaled > 1.0 - EPS).sum())
        max_dev = max(max_dev, abs(float(vals.sum()) - slots))
        out[idx] = vals
    return out, n_clipped, max_dev


def paired_ci(a: pd.Series, b: pd.Series, n_boot: int, seed: int = 20260904) -> dict:
    """レースクラスタ bootstrap で `a - b` の 95%CI。"""
    common = a.index.intersection(b.index)
    d = (a.loc[common] - b.loc[common]).to_numpy()
    if not len(d):
        return {}
    rng = np.random.default_rng(seed)
    boots = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n_boot)])
    return {
        "n_races": int(len(d)),
        "delta": round(float(d.mean()), 5),
        "ci95": [round(float(np.percentile(boots, 2.5)), 5),
                 round(float(np.percentile(boots, 97.5)), 5)],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data-start", default="20230501")
    p.add_argument("--train-end", default=jra_protocol.TRAIN_END)
    p.add_argument("--valid-end", default="20251231",
                   help="早期停止に使う窓の終端。評価窓と重ねないこと")
    p.add_argument("--eval-start", default="20260101")
    p.add_argument("--eval-end", default="20260630")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()

    # 🔴 TEST 窓（2026Q3）は §18 で消費済み。ここでは絶対に触らせない
    if args.eval_end >= jra_protocol.TEST_START:
        raise SystemExit(
            f"🔴 --eval-end={args.eval_end} が TEST_START={jra_protocol.TEST_START} 以降。"
            "2026Q3 は §18 で消費済みなので再測定に使ってはいけない"
        )
    seeds = [int(s) for s in args.seeds.split(",")]
    logger.info("protocol: %s", jra_protocol.describe())

    conn = psycopg2.connect(dsn())
    logger.info("対象レース取得 %s〜%s ...", args.data_start, args.eval_end)
    raw = _query(conn, V28_FETCH_SQL, {"start": args.data_start, "end": args.eval_end})
    raw["date"] = raw["date"].astype(str)
    logger.info("  %d行 / %dレース", len(raw), raw["race_id"].nunique())

    # 過去走の索引は両腕で共有（同じ PIT フィルタ・同じ上限）
    store = load_past_run_store(conn, end_date=args.eval_end)
    course_features = load_course_features(conn)

    logger.info("new（修正後・フィールド=エントリー全馬）を構築 ...")
    new = build_v28_frame(raw, conn, end=args.eval_end,
                          store=store, course_features=course_features)
    logger.info("old（修正前・フィールド=完走馬）を構築 ...")
    old = build_old_frame(raw, conn, end=args.eval_end,
                          store=store, course_features=course_features)
    conn.close()

    # --- 行が完全に同じであること（違えば以降の比較が無意味） ---
    kn = list(zip(new["race_id"], new["horse_id"]))
    ko = list(zip(old["race_id"], old["horse_id"]))
    if kn != ko:
        raise SystemExit(f"🔴 両腕の行集合が違う: new={len(kn)} old={len(ko)}")

    # 🔴 採点に使うのは払戻規則の枠数（完走頭数ベース）。両腕で同一
    payout_slots = old["place_slots"].to_numpy()
    np.testing.assert_array_equal(payout_slots, new["place_slots_finishers"].to_numpy())
    new["place_slots_payout"] = payout_slots
    old["place_slots_payout"] = payout_slots
    old["place_slots_arm"] = old["place_slots"]            # 完走頭数ベース
    new["place_slots_arm"] = new["place_slots"]            # エントリー数ベース

    per_race = pd.DataFrame({
        "race_id": new["race_id"], "date": new["date"],
        "n_entries": new["n_entries"], "n_runners": new["n_runners"],
        "slots_new": new["place_slots_arm"], "slots_old": old["place_slots_arm"],
    }).drop_duplicates("race_id")
    field_info = {
        "n_races": int(len(per_race)),
        "races_n_entries_ne_n_runners": int(
            (per_race["n_entries"] != per_race["n_runners"]).sum()),
        "races_slots_differ": int((per_race["slots_new"] != per_race["slots_old"]).sum()),
    }
    field_info["races_n_entries_ne_n_runners_pct"] = round(
        100 * field_info["races_n_entries_ne_n_runners"] / max(1, field_info["n_races"]), 3)
    field_info["races_slots_differ_pct"] = round(
        100 * field_info["races_slots_differ"] / max(1, field_info["n_races"]), 3)
    logger.info("フィールドの差: %s", field_info)

    # --- 新特徴が実際に動いたか（動いていなければ比較の意味が無い） ---
    feat_diff = {}
    for c in ("runner_type_ord", "finish_var5", "win_place_ratio5", "pace_handicap_pit"):
        a, b = old[c].to_numpy(dtype=float), new[c].to_numpy(dtype=float)
        both_nan = np.isnan(a) & np.isnan(b)
        differ = ~both_nan & ~(a == b)
        feat_diff[c] = {"rows_differ": int(differ.sum()),
                        "pct": round(100 * float(differ.mean()), 3)}
    # 🔴 `pace_handicap_pit` はレース単位の `pace_type` 経由で**そのレース全馬**が動く。
    #    「フィールドがずれた 11%」ではなく「実際に pace_type が反転した割合」を測る
    pace_differ_races = int(pd.Series(
        old["pace_handicap_pit"].to_numpy(dtype=float)
        != new["pace_handicap_pit"].to_numpy(dtype=float)
    ).groupby(new["race_id"].to_numpy()).any().sum())
    feat_diff["races_where_pace_type_flipped"] = {
        "n": pace_differ_races,
        "pct": round(100 * pace_differ_races / max(1, new["race_id"].nunique()), 3),
    }
    logger.info("新特徴の差（全期間・行単位）: %s", feat_diff)

    # --- 学習と予測 ---
    ev: dict[str, pd.DataFrame] = {}
    for arm, frame in (("old", old), ("new", new)):
        logger.info("── %s 腕を学習 ...", arm)
        ev[arm] = fit_arm(frame, "place_slots_arm", train_end=args.train_end,
                          valid_end=args.valid_end, eval_start=args.eval_start, seeds=seeds)

    if list(zip(ev["old"]["race_id"], ev["old"]["horse_id"])) != \
            list(zip(ev["new"]["race_id"], ev["new"]["horse_id"])):
        raise SystemExit("🔴 評価窓の行順が両腕で違う")

    checks: dict = {}
    for arm in ARMS:
        d = ev[arm]
        pp, n_clip, max_dev = normalize_arm(d, "place_slots_arm")
        d["p_place"] = pp
        d["p_place_harville"] = harville_place(d, "p_win")
        checks[arm] = {
            "clipped_horses": n_clip,
            "max_abs_dev_sum_from_arm_slots": round(max_dev, 6),
            "max_abs_dev_p_win_sum_from_1": round(float(
                (d.groupby("race_id")["p_win"].sum() - 1.0).abs().max()), 9),
        }
    logger.info("自己検査: %s", checks)

    # --- 採点（🔴 払戻規則の枠数が 3 のレース） ---
    base = ev["old"][["race_id", "horse_id", "date", "finish_position",
                      "place_slots_payout"]].copy()
    base["place_slots"] = base["place_slots_payout"]      # place_scores / _place_ll_per_race 用
    for arm in ARMS:
        base[f"p_place__{arm}"] = ev[arm]["p_place"].to_numpy()
        base[f"p_win__{arm}"] = ev[arm]["p_win"].to_numpy()
        base[f"p_harv__{arm}"] = ev[arm]["p_place_harville"].to_numpy()

    d3 = base[base["place_slots"] == 3].reset_index(drop=True)
    scores = {}
    for arm in ARMS:
        scores[f"{arm}_head"] = place_scores(d3, f"p_place__{arm}", f"p_win__{arm}")
        scores[f"{arm}_harville"] = place_scores(d3, f"p_harv__{arm}", f"p_win__{arm}")

    per = {k: _place_ll_per_race(d3, col) for k, col in {
        "old_head": "p_place__old", "new_head": "p_place__new",
        "old_harville": "p_harv__old", "new_harville": "p_harv__new"}.items()}
    cis = {
        "new_head_minus_old_head": paired_ci(per["new_head"], per["old_head"], args.n_boot),
        "new_head_minus_new_harville": paired_ci(per["new_head"], per["new_harville"],
                                                 args.n_boot),
        "old_head_minus_old_harville": paired_ci(per["old_head"], per["old_harville"],
                                                 args.n_boot),
        "new_head_minus_old_harville": paired_ci(per["new_head"], per["old_harville"],
                                                 args.n_boot),
    }

    # --- 🔴 コストの所在: 枠数が食い違うレースは払戻規則で 2 になるので slots=3 母集団に
    #     入らない（判定母集団からこぼれる）。payout=2 の母集団でも必ず測る ---
    d2 = base[base["place_slots"] == 2].reset_index(drop=True)
    scores_slots2 = {}
    ci_slots2 = {}
    if len(d2):
        for arm in ARMS:
            scores_slots2[f"{arm}_head"] = place_scores(d2, f"p_place__{arm}",
                                                        f"p_win__{arm}")
        ci_slots2["new_head_minus_old_head"] = paired_ci(
            _place_ll_per_race(d2, "p_place__new"),
            _place_ll_per_race(d2, "p_place__old"), args.n_boot)

    diff_races = set(per_race.loc[per_race["slots_new"] != per_race["slots_old"], "race_id"])
    slots_cost: dict = {
        "note": "🔴 枠数が食い違うレースは払戻規則で place_slots=2 になるため "
                "slots=3 の判定母集団には**入らない**。new はそこで Σp=3 に正規化する",
        "n_races_total": len(diff_races),
    }
    for name, dd in (("slots3", d3), ("slots2", d2)):
        sub = dd[dd["race_id"].isin(diff_races)]
        entry = {"n_races_in_eval": int(sub["race_id"].nunique())}
        if len(sub):
            a = _place_ll_per_race(sub, "p_place__new")
            b = _place_ll_per_race(sub, "p_place__old")
            entry.update({
                "place_ll_old": round(float(b.mean()), 5),
                "place_ll_new": round(float(a.mean()), 5),
                "delta_new_minus_old": round(float((a - b).mean()), 5),
                "sum_p_place_new_mean": round(float(
                    sub.groupby("race_id")["p_place__new"].sum().mean()), 4),
                "sum_p_place_old_mean": round(float(
                    sub.groupby("race_id")["p_place__old"].sum().mean()), 4),
            })
        slots_cost[name] = entry

    result = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "purpose": "PR #462 レビュー指摘1+2（学習と配信でフィールドの定義が違う）の"
                   "修正前後を VAL で比較する。🔴 2026Q3 は §18 で消費済みなので使わない",
        "protocol": jra_protocol.describe(),
        "config": {
            "data_start": args.data_start, "train_end": args.train_end,
            "early_stopping_valid": [args.train_end, args.valid_end],
            "eval": [args.eval_start, args.eval_end],
            "seeds": seeds, "n_boot": args.n_boot,
            "scoring_slots": "🔴 払戻規則の枠数（完走頭数ベース）。両腕で同一",
            "arms": {
                "old": "prod_featurize → build_population → attach_past_form"
                       "（フィールド=完走馬・修正前）",
                "new": "prod_featurize → attach_serving_field → attach_past_form"
                       " → build_population（フィールド=エントリー全馬・修正後）",
            },
        },
        "field_definition_diff": field_info,
        "feature_diff_rows": feat_diff,
        "self_checks": checks,
        "eval_population": {
            "n_rows": int(len(base)), "n_races": int(base["race_id"].nunique()),
            "n_rows_slots3": int(len(d3)), "n_races_slots3": int(d3["race_id"].nunique()),
            "date_min": str(base["date"].min()), "date_max": str(base["date"].max()),
        },
        "place_scores_slots3": scores,
        "paired_ci_place_ll": cis,
        "place_scores_slots2": scores_slots2,
        "paired_ci_place_ll_slots2": ci_slots2,
        "races_where_slots_differ": slots_cost,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    # --- 画面出力 ---
    print("\n" + "=" * 108)
    print(f"  v28 フィールド定義 A/B（VAL {args.eval_start}〜{args.eval_end}・"
          f"place_slots(払戻規則)=3 の {d3['race_id'].nunique()}R / {len(d3)}頭）")
    print("=" * 108)
    print(f"{'系列':<18}{'place_ll':>11}{'coverage@3':>13}{'Spearman':>11}"
          f"{'交差R':>9}{'交差ペア':>11}")
    for k in ("old_harville", "old_head", "new_harville", "new_head"):
        m = scores[k]
        print(f"{k:<18}{m['place_logloss']:>11.5f}{m['coverage_at_k']:>13.4f}"
              f"{(m['spearman_in_race'] or 0):>11.4f}{m['cross_races']:>9}"
              f"{m['cross_pairs']:>11}")
    print("\n対応差（レースクラスタ bootstrap 95%CI・負なら前者が良い）:")
    for k, v in cis.items():
        if v:
            print(f"  {k:<34} Δ={v['delta']:+.5f}  CI[{v['ci95'][0]:+.5f}, {v['ci95'][1]:+.5f}]"
                  f"  n={v['n_races']}R")
    if scores_slots2:
        print(f"\n【払戻規則 place_slots=2 の母集団】"
              f"{d2['race_id'].nunique()}R / {len(d2)}頭"
              f"（🔴 枠数が食い違うレースはここに落ちる）")
        for k in ("old_head", "new_head"):
            m = scores_slots2[k]
            print(f"{k:<18}{m['place_logloss']:>11.5f}{m['coverage_at_k']:>13.4f}")
        v = ci_slots2.get("new_head_minus_old_head") or {}
        if v:
            print(f"  new-old  Δ={v['delta']:+.5f}  "
                  f"CI[{v['ci95'][0]:+.5f}, {v['ci95'][1]:+.5f}]  n={v['n_races']}R")

    print(f"\nΣ の乖離 / クリップ: {checks}")
    print(f"フィールドの差: {field_info}")
    print(f"枠数が食い違ったレース（評価窓内）: {slots_cost}")
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
