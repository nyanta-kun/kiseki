"""着順分布の採点を walk-forward の予測に掛ける（予想フロー再設計 Wave 0c）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §3 Wave 0c。
採点の本体は `src/indices/finish_dist_scoring.py`（純関数）。本スクリプトは
入力の用意と集計だけを行い、指標を再実装しない。

## 入力

`scripts/jra_place_head_ab.py --pred-cache <path>` が保存する walk-forward の予測
（`p_win` = is_win ヘッド・`p_top3_raw` = 独立 is_top3 ヘッド）。
腕の確率の作り方は `jra_place_head_ab.attach_place_probs` を import して使う。

## 腕

| 腕 | 上位3着の分布 T3 | 複勝確率 |
|---|---|---|
| `harville`   | Harville(`p_win`)（基準・B0） | T3 から（= 本番の v27 複勝） |
| `henery_fit` | Harville(`p_win^λ` を再正規化)・λ は探索窓で当てた値 | T3 から |
| `place_head` | Harville(`p_win`) | 独立複勝ヘッドをレース内で Σ=place_slots に正規化（`top3_norm`・v28 の形） |

`place_head` は組合せの確率が `harville` と同一なので、差が出るのは `place_ll` だけになる。

## 合格基準（Wave 0c）

1. 既知値の再現: 本スクリプトの `place_ll` が、同じ予測の上で
   `jra_place_head_ab._place_ll_per_race` と一致する（3腕とも）。
   保存済みの `jra_place_head_ab.json` とも四半期ごとに比べ、ずれを出力する
   （DB のデータが当時から変わっていれば、ここだけがずれる）。
2. 全指標で四半期別の値と、`harville` に対する対応差の 95%CI が出ること。
3. シミュレーションの収束は `tests/test_finish_dist_scoring.py` が固定している。

## 使い方

    cd backend
    ~/bin/kiseki-ro .venv/bin/python scripts/jra_place_head_ab.py \\
        --pred-cache <dir>/place_head_preds.pkl --cache <dir>/place_head_ds.pkl \\
        --out <dir>/place_head_ab_rerun.json
    .venv/bin/python scripts/jra_finish_dist_scoring.py \\
        --pred-cache <dir>/place_head_preds.pkl \\
        --out ../docs/model_verification/jra_finish_dist_scoring.json
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

from scripts.jra_place_head_ab import (  # noqa: E402
    PLACE_COL,
    _place_ll_per_race,
    attach_place_probs,
)
from src.indices.finish_dist_scoring import (  # noqa: E402
    EPS,
    METRICS,
    STATUS_OK,
    harville_top3_tensor,
    paired_bootstrap_ci,
    score_race,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("finish_dist_scoring")

KNOWN_JSON = _root.parent / "docs" / "model_verification" / "jra_place_head_ab.json"
OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_finish_dist_scoring.json"

BASE_ARM = "harville"
ARMS = ("harville", "henery_fit", "place_head")
# 既知値の照合先（jra_place_head_ab の腕名）
KNOWN_ARM = {"harville": "harville", "henery_fit": "henery_fit", "place_head": "top3_norm"}


def _henery_p(p_win: np.ndarray, lam: float) -> np.ndarray:
    q = np.power(np.clip(p_win, EPS, 1.0), lam)
    return q / q.sum()


def score_all(ev: pd.DataFrame, lam: float) -> pd.DataFrame:
    """全レースを3腕で採点し、レース × 腕 1 行の表を返す。"""
    rows = []
    for rid, g in ev.groupby("race_id", sort=False):
        n = len(g)
        if n < 3:
            continue
        fp = pd.to_numeric(g["finish_position"], errors="coerce").to_numpy(dtype=float)
        slots = int(g["place_slots"].iloc[0])
        p_win = g["p_win"].to_numpy(dtype=float)
        t3_h = harville_top3_tensor(p_win)
        t3_l = harville_top3_tensor(_henery_p(p_win, lam))
        base = {"race_id": rid, "quarter": g["quarter"].iloc[0], "place_slots": slots}
        for arm, t3, pp in (
            ("harville", t3_h, None),
            ("henery_fit", t3_l, None),
            ("place_head", t3_h, g[PLACE_COL["top3_norm"]].to_numpy(dtype=float)),
        ):
            r = score_race(t3, fp, slots, place_probs=pp)
            rows.append({**base, "arm": arm, **r})
    return pd.DataFrame(rows)


def reproduce_place_ll(ev: pd.DataFrame, sc: pd.DataFrame) -> dict:
    """同じ予測の上で `jra_place_head_ab._place_ll_per_race` と一致するかを見る。"""
    d = ev[ev["place_slots"] == 3]
    out = {}
    for arm, known in KNOWN_ARM.items():
        ref = _place_ll_per_race(d, PLACE_COL[known])
        ours = sc[(sc["arm"] == arm) & (sc["place_slots"] == 3)].set_index("race_id")["place_ll"]
        common = ref.index.intersection(ours.index)
        dev = float(np.abs(ref.loc[common].to_numpy() - ours.loc[common].to_numpy()).max())
        out[arm] = {"n_races": int(len(common)), "max_abs_dev": dev, "match": bool(dev < 1e-9)}
    return out


def compare_known_json(sc: pd.DataFrame, known_path: Path) -> dict:
    """保存済み `jra_place_head_ab.json` の四半期別 place_ll と比べる（ずれを報告するだけ）。"""
    if not known_path.exists():
        return {"available": False}
    known = json.loads(known_path.read_text())
    ref: dict[tuple[str, str], float] = {}
    n_ref: dict[str, int] = {}
    for arm in ("top3_raw", "top3_norm", "henery_fit"):
        for win in ("explore", "confirm"):
            for r in known["primary"][arm][win]["by_quarter"]:
                ref[(arm, r["quarter"])] = r["arm_place_ll"]
                ref[("harville", r["quarter"])] = r["base_place_ll"]
                n_ref[r["quarter"]] = r["n_races"]
    rows = []
    d = sc[sc["place_slots"] == 3]
    for arm, karm in KNOWN_ARM.items():
        for q, g in d[d["arm"] == arm].groupby("quarter"):
            if (karm, q) not in ref:
                continue
            ours = float(g["place_ll"].mean())
            rows.append(
                {
                    "arm": arm,
                    "quarter": q,
                    "ours": round(ours, 5),
                    "known": ref[(karm, q)],
                    "abs_dev": round(abs(round(ours, 5) - ref[(karm, q)]), 5),
                    "n_races_ours": int(len(g)),
                    "n_races_known": n_ref.get(q),
                }
            )
    max_dev = max((r["abs_dev"] for r in rows), default=None)
    return {
        "available": True,
        "rows": rows,
        "max_abs_dev": max_dev,
        "exact_to_printed_precision": bool(max_dev is not None and max_dev <= 1e-5),
    }


def summarize(
    sc: pd.DataFrame,
    quarters: dict[str, list[str]],
    n_boot: int,
    arms: tuple[str, ...] = ARMS,
    base_arm: str = BASE_ARM,
) -> dict:
    """窓ごと・腕ごと・指標ごとの平均と、基準腕との対応差の 95%CI。"""
    out: dict = {}
    wide = sc.pivot_table(index="race_id", columns="arm", values=list(METRICS), aggfunc="first")
    qmap = sc.drop_duplicates("race_id").set_index("race_id")["quarter"]
    slots = sc.drop_duplicates("race_id").set_index("race_id")["place_slots"]
    for win, qs in quarters.items():
        rids = qmap.index[qmap.isin(qs)]
        w = wide.loc[wide.index.intersection(rids)]
        res: dict = {}
        for metric in METRICS:
            sub = w[metric]
            if metric == "place_ll":
                # 判定は place_slots=3（jra_place_head_ab と同じ）
                sub = sub.loc[sub.index.intersection(slots.index[slots == 3])]
            m: dict = {"mean": {a: _r(sub[a].mean()) for a in arms}}
            m["n_races"] = {a: int(sub[a].notna().sum()) for a in arms}
            m["delta_vs_base"] = {}
            for a in arms:
                if a == base_arm:
                    continue
                d = (sub[a] - sub[base_arm]).to_numpy(dtype=float)
                if np.all(np.isnan(d) | (d == 0.0)):
                    m["delta_vs_base"][a] = {"identical_to_base": True}
                    continue
                ci = paired_bootstrap_ci(d, n_boot=n_boot)
                m["delta_vs_base"][a] = {
                    "mean": _r(ci["mean"]),
                    "ci95": [_r(x) for x in ci["ci"]],
                    "n_races": ci["n_races"],
                }
            res[metric] = m
        out[win] = res
    return out


def _r(x: float | None) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 5)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--pred-cache", required=True, help="jra_place_head_ab.py の --pred-cache")
    p.add_argument("--known-json", default=str(KNOWN_JSON))
    p.add_argument("--lambda", dest="lam", type=float, default=None, help="省略時は known-json の λ")
    p.add_argument("--explore-quarters", default="2024Q1,2024Q2,2024Q3,2024Q4,2025Q1,2025Q2")
    p.add_argument("--confirm-quarters", default="2025Q3,2025Q4,2026Q1,2026Q2")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()

    known_path = Path(args.known_json)
    lam = args.lam
    if lam is None:
        lam = float(json.loads(known_path.read_text())["lambda_fit"]["lambda"])
    logger.info("λ = %.4f", lam)

    ev = pd.read_pickle(args.pred_cache).reset_index(drop=True)
    ev, norm_info = attach_place_probs(ev, lam)
    logger.info("予測: %d行 / %dレース", len(ev), ev["race_id"].nunique())

    t0 = time.time()
    sc = score_all(ev, lam)
    logger.info("採点: %dレース × %d腕 (%.1fs)", sc["race_id"].nunique(), len(ARMS), time.time() - t0)

    status = sc[sc["arm"] == BASE_ARM]["status"].value_counts().to_dict()
    repro = reproduce_place_ll(ev, sc)
    known = compare_known_json(sc, known_path)
    quarters = {
        "explore": args.explore_quarters.split(","),
        "confirm": args.confirm_quarters.split(","),
    }
    summary = summarize(sc, quarters, args.bootstrap)

    out = {
        "generated_at": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(timespec="seconds"),
        "plan": "docs/prediction_flow_redesign_plan_2026_11_06.md §3 Wave 0c",
        "input": {"pred_cache": str(args.pred_cache), "lambda": lam, **norm_info},
        "arms": list(ARMS),
        "base_arm": BASE_ARM,
        "population": {
            "n_races": int(sc["race_id"].nunique()),
            "status_counts": {str(k): int(v) for k, v in status.items()},
            "note": "1〜3着を使う指標は status=ok のレースだけで採点。place_ll は place_slots=3 で集計",
        },
        "acceptance": {
            "reproduce_place_ll_same_predictions": repro,
            "compare_saved_json": known,
        },
        "summary": summary,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    logger.info("書き出し: %s", args.out)
    logger.info("同一予測での place_ll 一致: %s", {a: v["match"] for a, v in repro.items()})
    logger.info("保存済み JSON とのずれ（最大）: %s", known.get("max_abs_dev"))
    n_ok = int(status.get(STATUS_OK, 0))
    logger.info("1〜3着が確定した採点対象: %d / %d レース", n_ok, sum(status.values()))


if __name__ == "__main__":
    main()
