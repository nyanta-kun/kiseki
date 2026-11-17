"""能力分布モデル（R1）を Harville（B0）・2・3着割引 Harville（B1）と比べる（予想フロー再設計 Phase 2）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §3 Phase 2・**§5.4（事前登録）**。
本スクリプトは §5.4 を実装するだけで、判定基準を動かさない。

## 入力

`scripts/jra_finish_dist_preds.py` が作る walk-forward 予測（2024Q1〜2026Q3）。
`p_win` = is_win ヘッド（レース内 Σ=1）、複勝の目標 = 独立 is_top3 ヘッドを
レース内で Σ=place_slots に正規化したもの（`jra_place_head_ab.attach_place_probs` の
`top3_norm`・v28 と同じ形）。

## 腕（§5.4）

| 腕 | 上位3着の分布 T3 |
|---|---|
| `B0` | Harville(`p_win`)（基準） |
| `B1` | 2着・3着だけ `p_win` を γ2・γ3 乗して割り引く Harville。γ は探索窓で最尤 |
| `R1` | `X_i = μ_i + σ_i·Gumbel`。μ・σ はレースごとに `p_win` と複勝の目標へ当てはめ（学習パラメータなし）。当てはめが status=ok でないレース・place_slots=0 のレースは B0 へ落とす（件数を数える） |

## 判定（§5.4・確認窓 2025Q3〜2026Q3）

主指標: 三連複の実現組の対数損失の差 Δ(R1 − B0)、レース単位の bootstrap 95%CI。

| 条件 | 判定 |
|---|---|
| 上限 < 0 | 採用（改善） |
| 0 ≤ 上限 < +0.005 nat | 採用（非劣性・単勝と複勝に整合する組合せ確率として） |
| それ以外 | 不採用（B0 で配信） |

## 使い方

    .venv/bin/python scripts/jra_finish_dist_phase2.py \\
        --preds <dir>/finish_dist_preds.pkl \\
        --out ../docs/model_verification/jra_finish_dist_phase2.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import minimize_scalar  # noqa: E402

from scripts.jra_finish_dist_scoring import summarize  # noqa: E402
from scripts.jra_place_head_ab import PLACE_COL, attach_place_probs  # noqa: E402
from src.indices.finish_dist import (  # noqa: E402
    FIT_OK,
    METHOD_VERSION,
    discounted_harville_top3_tensor,
    fit_random_utility,
    top3_tensor,
)
from src.indices.finish_dist_scoring import (  # noqa: E402
    EPS,
    STATUS_OK,
    harville_top3_tensor,
    marginals_from_t3,
    paired_bootstrap_ci,
    realized_top3,
    score_race,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("finish_dist_phase2")

OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_finish_dist_phase2.json"
ARMS = ("B0", "B1", "R1")
BASE_ARM = "B0"
EXPLORE = ("2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2")
CONFIRM = ("2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3")
NONINFERIOR_MARGIN = 0.005
PLACE_TARGET = PLACE_COL["top3_norm"]


# ---------------------------------------------------------------------------
# B1 の γ（探索窓で最尤）
# ---------------------------------------------------------------------------


def _realized(ev: pd.DataFrame) -> list[tuple[np.ndarray, int, int, int]]:
    out = []
    for _, g in ev.groupby("race_id", sort=False):
        if len(g) < 3:
            continue
        st, top = realized_top3(pd.to_numeric(g["finish_position"], errors="coerce").to_numpy(dtype=float))
        if st != STATUS_OK or top is None:
            continue
        p = np.clip(g["p_win"].to_numpy(dtype=float), EPS, None)
        out.append((p / p.sum(), *top))
    return out


def fit_gammas(ev_explore: pd.DataFrame) -> dict:
    """2着 | 1着 と 3着 | 1・2着 の条件付き尤度を、それぞれ γ について最大化する。"""
    races = _realized(ev_explore)

    def nll2(g: float) -> float:
        s = 0.0
        for p, a, b, _ in races:
            q = p**g
            s -= np.log(q[b]) - np.log(q.sum() - q[a])
        return s / len(races)

    def nll3(g: float) -> float:
        s = 0.0
        for p, a, b, c in races:
            q = p**g
            s -= np.log(q[c]) - np.log(q.sum() - q[a] - q[b])
        return s / len(races)

    r2 = minimize_scalar(nll2, bounds=(0.2, 2.0), method="bounded", options={"xatol": 1e-5})
    r3 = minimize_scalar(nll3, bounds=(0.2, 2.0), method="bounded", options={"xatol": 1e-5})
    return {
        "gamma2": round(float(r2.x), 5),
        "gamma3": round(float(r3.x), 5),
        "n_races": len(races),
        "nll2_at_1": round(nll2(1.0), 6),
        "nll2_at_fit": round(float(r2.fun), 6),
        "nll3_at_1": round(nll3(1.0), 6),
        "nll3_at_fit": round(float(r3.fun), 6),
    }


# ---------------------------------------------------------------------------
# 1 レースの採点（プロセスごとに並列）
# ---------------------------------------------------------------------------


def _score_one(task: tuple) -> tuple[list[dict], dict]:
    rid, quarter, slots, p_win, place, fp, g2, g3 = task
    base = {"race_id": rid, "quarter": quarter, "place_slots": slots}
    t_b0 = harville_top3_tensor(p_win)
    t_b1 = discounted_harville_top3_tensor(p_win, g2, g3)
    fr = fit_random_utility(p_win, place, slots)
    use_fit = fr.status == FIT_OK and slots > 0
    t_r1 = top3_tensor(fr.mu, fr.sigma) if use_fit else t_b0
    rows = [{**base, "arm": arm, **score_race(t3, fp, slots)} for arm, t3 in (("B0", t_b0), ("B1", t_b1), ("R1", t_r1))]
    info = {
        "race_id": rid,
        "quarter": quarter,
        "n": len(p_win),
        "place_slots": slots,
        "fit_status": fr.status,
        "fallback_b0": not use_fit,
        "n_clipped": fr.n_clipped,
        "max_place_resid": fr.max_place_resid,
        "max_win_resid": fr.max_win_resid,
        "n_iter": fr.n_iter,
        "sigma_min": float(fr.sigma.min()),
        "sigma_max": float(fr.sigma.max()),
        "sigma_sd_log": float(np.log(fr.sigma).std()),
        "t3_sum_r1": float(t_r1.sum()),
    }
    return rows, info


def build_tasks(ev: pd.DataFrame, g2: float, g3: float) -> list[tuple]:
    tasks = []
    for rid, g in ev.groupby("race_id", sort=False):
        if len(g) < 3:
            continue
        p = np.clip(g["p_win"].to_numpy(dtype=float), EPS, None)
        tasks.append(
            (
                rid,
                g["quarter"].iloc[0],
                int(g["place_slots"].iloc[0]),
                p / p.sum(),
                g[PLACE_TARGET].to_numpy(dtype=float),
                pd.to_numeric(g["finish_position"], errors="coerce").to_numpy(dtype=float),
                g2,
                g3,
            )
        )
    return tasks


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def verdict(ci_hi: float | None) -> str:
    """§5.4 の判定（動かさない）。"""
    if ci_hi is None:
        return "判定不能"
    if ci_hi < 0.0:
        return "採用（改善）"
    if ci_hi < NONINFERIOR_MARGIN:
        return "採用（非劣性）"
    return "不採用（B0 で配信）"


def per_quarter_delta(sc: pd.DataFrame, metric: str, arm: str, base: str) -> list[dict]:
    wide = sc.pivot_table(index=["quarter", "race_id"], columns="arm", values=metric, aggfunc="first")
    out = []
    for q, g in wide.groupby(level=0):
        d = (g[arm] - g[base]).dropna()
        out.append({"quarter": q, "n_races": int(len(d)), "delta": round(float(d.mean()), 5)})
    return out


def r1_vs_b1(sc: pd.DataFrame, qs: tuple[str, ...], n_boot: int) -> dict:
    out = {}
    wide = sc[sc["quarter"].isin(qs)].pivot_table(index="race_id", columns="arm", aggfunc="first")
    if wide.empty:
        return out
    for metric in ("trio_ll", "quinella_ll", "wide_ll", "top2_ll", "exacta_ll", "trifecta_ll"):
        d = (wide[(metric, "R1")] - wide[(metric, "B1")]).to_numpy(dtype=float)
        ci = paired_bootstrap_ci(d, n_boot=n_boot)
        out[metric] = {
            "mean": round(ci["mean"], 5) if ci["mean"] is not None else None,
            "ci95": [round(x, 5) for x in ci["ci"]] if ci["ci"] else None,
            "n_races": ci["n_races"],
        }
    return out


def fit_summary(fi: pd.DataFrame) -> dict:
    out = {}
    for name, qs in (("explore", EXPLORE), ("confirm", CONFIRM)):
        d = fi[fi["quarter"].isin(qs)]
        out[name] = {
            "n_races": int(len(d)),
            "status_counts": {str(k): int(v) for k, v in d["fit_status"].value_counts().items()},
            "fallback_to_b0": int(d["fallback_b0"].sum()),
            "fallback_rate": round(float(d["fallback_b0"].mean()), 5),
            "fallback_rate_slots3": round(float(d.loc[d["place_slots"] == 3, "fallback_b0"].mean()), 5),
            "races_with_clipped_target": int((d["n_clipped"] > 0).sum()),
            "max_win_resid_p99": float(d["max_win_resid"].quantile(0.99)),
            "t3_sum_max_abs_dev": float((d["t3_sum_r1"] - 1.0).abs().max()),
            "sigma_sd_log_quantiles": {str(q): round(float(d["sigma_sd_log"].quantile(q)), 4) for q in (0.1, 0.5, 0.9)},
            "sigma_range_quantiles": {
                "min_p05": round(float(d["sigma_min"].quantile(0.05)), 4),
                "max_p95": round(float(d["sigma_max"].quantile(0.95)), 4),
            },
        }
    return out


def show_example(ev: pd.DataFrame, rid: str, g2: float, g3: float) -> None:
    """集計の前に 1 レースを目で見る（CLAUDE.md「baseline は実データを1件表示して目視確認する」）。"""
    g = ev[ev["race_id"] == rid].sort_values("horse_number")
    p = g["p_win"].to_numpy(dtype=float)
    p = p / p.sum()
    place = g[PLACE_TARGET].to_numpy(dtype=float)
    slots = int(g["place_slots"].iloc[0])
    fr = fit_random_utility(p, place, slots)
    fp = pd.to_numeric(g["finish_position"], errors="coerce").to_numpy(dtype=float)
    _, top = realized_top3(fp)
    logger.info("例 race_id=%s place_slots=%d fit=%s", rid, slots, fr.status)
    for i, r in enumerate(g.itertuples()):
        logger.info(
            "  馬番%2d 勝率 %.3f 複勝目標 %.3f σ %.3f 着順 %s",
            int(r.horse_number),
            p[i],
            place[i],
            fr.sigma[i],
            r.finish_position,
        )
    if top is not None:
        for arm, t3 in (
            ("B0", harville_top3_tensor(p)),
            ("B1", discounted_harville_top3_tensor(p, g2, g3)),
            ("R1", top3_tensor(fr.mu, fr.sigma)),
        ):
            m = marginals_from_t3(t3)
            logger.info("  %s 実現した三連複の確率 %.5f", arm, m.trio[top[0], top[1], top[2]])


def main() -> None:
    """§5.4 の手順で採点し、JSON を書き出す。"""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--preds", required=True)
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--workers", type=int, default=1, help="2 以上で並列（サンドボックス外のみ）")
    p.add_argument("--limit-races", type=int, default=0, help="試運転用（0 = 全レース）")
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()

    ev = pd.read_pickle(args.preds).reset_index(drop=True)
    ev = ev[ev["quarter"].isin(EXPLORE + CONFIRM)].reset_index(drop=True)
    ev, norm_info = attach_place_probs(ev, 1.0)
    meta_path = Path(args.preds).with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    logger.info("予測: %d行 / %dレース", len(ev), ev["race_id"].nunique())

    gam = fit_gammas(ev[ev["quarter"].isin(EXPLORE)])
    g2, g3 = gam["gamma2"], gam["gamma3"]
    logger.info("B1 の γ（探索窓 %dR）: γ2=%.4f γ3=%.4f", gam["n_races"], g2, g3)

    first_q3 = ev[ev["quarter"] == CONFIRM[-1]].sort_values(["date", "race_id"])["race_id"].iloc[0]
    show_example(ev, first_q3, g2, g3)

    tasks = build_tasks(ev, g2, g3)
    if args.limit_races:
        tasks = tasks[: args.limit_races]
    t0 = time.time()
    rows: list[dict] = []
    infos: list[dict] = []
    if args.workers > 1:
        ex = ProcessPoolExecutor(max_workers=args.workers)
        it = ex.map(_score_one, tasks, chunksize=16)
    else:  # サンドボックス内ではセマフォが使えず並列にできない
        ex = None
        it = map(_score_one, tasks)
    for i, (r, inf) in enumerate(it, 1):
        rows.extend(r)
        infos.append(inf)
        if i % 1000 == 0:
            logger.info("  %d / %d レース (%.0fs)", i, len(tasks), time.time() - t0)
    if ex is not None:
        ex.shutdown()
    sc = pd.DataFrame(rows)
    fi = pd.DataFrame(infos)
    logger.info("採点: %dレース (%.0fs)", len(fi), time.time() - t0)

    quarters = {"explore": list(EXPLORE), "confirm": list(CONFIRM)}
    summary = summarize(sc, quarters, args.bootstrap, arms=ARMS, base_arm=BASE_ARM)
    primary = summary["confirm"].get("trio_ll", {}).get("delta_vs_base", {}).get("R1", {})
    ci_hi = primary["ci95"][1] if primary.get("ci95") else None
    status = sc[sc["arm"] == BASE_ARM]["status"].value_counts().to_dict()

    out = {
        "generated_at": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(timespec="seconds"),
        "plan": "docs/prediction_flow_redesign_plan_2026_11_06.md §3 Phase 2 / §5.4（事前登録）",
        "method_version": METHOD_VERSION,
        "input": {"preds": str(args.preds), "preds_meta": meta, **norm_info},
        "windows": quarters,
        "arms": {
            "B0": "Harville(p_win)",
            "B1": f"2・3着割引 Harville γ2={g2} γ3={g3}（探索窓で最尤）",
            "R1": "Gumbel μ_i+σ_i·G（p_win と top3_norm に当てはめ・status≠ok は B0）",
        },
        "gamma_fit": gam,
        "population": {
            "n_races": int(sc["race_id"].nunique()),
            "status_counts": {str(k): int(v) for k, v in status.items()},
            "note": "1〜3着を使う指標は status=ok のレースだけ。place_ll は place_slots=3 で集計",
        },
        "fit": fit_summary(fi),
        "primary": {
            "metric": "trio_ll Δ(R1 − B0)・確認窓",
            **primary,
            "noninferior_margin": NONINFERIOR_MARGIN,
            "verdict": verdict(ci_hi),
        },
        "per_quarter_trio_delta_r1": per_quarter_delta(sc, "trio_ll", "R1", "B0"),
        "per_quarter_trio_delta_b1": per_quarter_delta(sc, "trio_ll", "B1", "B0"),
        "r1_vs_b1_confirm": r1_vs_b1(sc, CONFIRM, args.bootstrap),
        "summary": summary,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    logger.info("書き出し: %s", args.out)
    logger.info("主指標 %s → %s", primary, out["primary"]["verdict"])


if __name__ == "__main__":
    main()
