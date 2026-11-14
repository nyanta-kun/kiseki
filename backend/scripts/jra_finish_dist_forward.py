"""発走10分前の記録から能力分布モデル（R1）を作り直し、前向きで採点する（予想フロー再設計 Phase 3）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` **§5.5（事前登録）**。
本スクリプトは §5.5 を実装するだけで、判定基準を動かさない。

## 入力

`keiba.hit_tier_picks`（発走10分前・全出走馬の勝率と複勝率）と確定着順。
R1 と B0 は配信と同じ `services.jra_finish_dist.build_race_finish_dist` で作る。

## 窓

| 区分 | 期間 | 扱い |
|---|---|---|
| 判定 | 2026-09-01〜09-30（`--score-start/--score-end`） | 着順で採点する |
| 監視のみ | それ以外の v28 記録（2026-10 以降は TEST 2026Q4） | **着順を読まない**。当てはめの状態だけ数える |

## ⚠️ 入力のモデルの境目

v28 の勝率・複勝ヘッドは四半期ごとに学習し直す。2026Q3 の再学習（学習終端 2026-09-27）は
**2026-11 にデプロイした**（それまでは学習終端 2026-06-28 のモデル）。版（v28）は変わらないので
`index_version` では区別できない。前向き記録を期間で比べるときはこの境目をまたがないこと。

## 使い方（本番 DB は読み取り専用）

    ~/bin/kiseki-ro .venv/bin/python scripts/jra_finish_dist_forward.py \\
        --out ../docs/model_verification/jra_finish_dist_forward_202609.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.indices.finish_dist import METHOD_VERSION  # noqa: E402
from src.indices.finish_dist_scoring import (  # noqa: E402
    METRICS,
    harville_top3_tensor,
    paired_bootstrap_ci,
    score_race,
)
from src.services.jra_finish_dist import SOURCE_R1, build_race_finish_dist  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("finish_dist_forward")

INDEX_VERSION = 28
SCORE_START = "20260901"
SCORE_END = "20260930"
PHASE2_CONFIRM_CI = (-0.07252, -0.04536)  # §5.4.1 の確認窓 trio_ll Δ(R1−B0)
FIT_FAILURE_LIMIT = 0.01

# 着順は判定窓の分だけ読む（TEST の着順を SQL の段で取り込まない）
SQL = """
SELECT r.date, r.race_id, p.horse_number, p.win_probability, p.place_probability,
       CASE WHEN r.date BETWEEN %(score_start)s AND %(score_end)s
            THEN p.finish_position END AS finish_position
FROM keiba.hit_tier_races r
JOIN keiba.hit_tier_picks p ON p.pick_race_id = r.id
WHERE r.index_version = %(ver)s
  AND p.win_probability IS NOT NULL
  AND p.place_probability IS NOT NULL
ORDER BY r.race_id, p.horse_number
"""


def _connect():
    import psycopg2

    url = os.getenv("KEIRIN_DB_URL")
    if url:
        return psycopg2.connect(
            url.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
        )
    from dotenv import load_dotenv

    load_dotenv(_root.parent / ".env")
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def load(score_start: str, score_end: str) -> pd.DataFrame:
    """発走前記録を読む。判定窓の外の着順は NULL で返る。"""
    with _connect() as conn:
        return pd.read_sql(SQL, conn, params={"ver": INDEX_VERSION, "score_start": score_start, "score_end": score_end})


def evaluate(df: pd.DataFrame, score_start: str, score_end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """レースごとに R1 と B0 を作り、(当てはめの状態, 採点) を返す。"""
    fits: list[dict] = []
    rows: list[dict] = []
    for rid, g in df.groupby("race_id", sort=False):
        date = str(g["date"].iloc[0])
        fd = build_race_finish_dist(
            g["horse_number"].astype(int).tolist(),
            g["win_probability"].astype(float).to_numpy(),
            g["place_probability"].astype(float).to_numpy(),
        )
        if fd is None:
            continue
        in_window = score_start <= date <= score_end
        fits.append(
            {
                "race_id": int(rid),
                "date": date,
                "month": date[:6],
                "n": len(g),
                "place_slots": fd.place_slots,
                "fit_status": fd.fit_status,
                "fallback_b0": fd.source != SOURCE_R1 and fd.place_slots > 0,
                "scored": in_window,
            }
        )
        if not in_window:
            continue
        fp = pd.to_numeric(g["finish_position"], errors="coerce").to_numpy(dtype=float)
        t_b0 = harville_top3_tensor(fd.win)
        for arm, t3 in (("B0", t_b0), ("R1", fd.t3)):
            rows.append(
                {
                    "race_id": int(rid),
                    "date": date,
                    "arm": arm,
                    "place_slots": fd.place_slots,
                    **score_race(t3, fp, fd.place_slots),
                }
            )
    return pd.DataFrame(fits), pd.DataFrame(rows)


def deltas(sc: pd.DataFrame, n_boot: int) -> dict:
    """指標ごとに Δ(R1 − B0) の平均と 95%CI。1〜3着が揃ったレースだけ。"""
    ok = sc[sc["status"] == "ok"]
    out: dict = {}
    for m in METRICS:
        sub = ok if m != "place_ll" else ok[ok["place_slots"] == 3]
        w = sub.pivot_table(index="race_id", columns="arm", values=m)
        if w.empty or "R1" not in w or "B0" not in w:
            continue
        w = w.dropna()
        ci = paired_bootstrap_ci((w["R1"] - w["B0"]).to_numpy(), n_boot)
        out[m] = {"n_races": len(w), "B0": round(float(w["B0"].mean()), 5), "R1": round(float(w["R1"].mean()), 5), **ci}
    return out


def verdict(primary: dict, fit_failure_rate: float) -> dict:
    """§5.5 の合格条件（動かさない）。"""
    lo, hi = primary["ci"]
    c1 = primary["mean"] < 0
    c2 = lo <= PHASE2_CONFIRM_CI[1] and hi >= PHASE2_CONFIRM_CI[0]
    c3 = fit_failure_rate < FIT_FAILURE_LIMIT
    return {
        "point_below_zero": c1,
        "ci_overlaps_phase2": c2,
        "fit_failure_below_1pct": c3,
        "ci_upper_below_zero": hi < 0,  # 参考（合格条件ではない）
        "pass": bool(c1 and c2 and c3),
    }


def main() -> None:
    """§5.5 の手順で採点し JSON を書き出す。"""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--score-start", default=SCORE_START)
    p.add_argument("--score-end", default=SCORE_END)
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--csv", help="DB の代わりに SQL と同じ列の CSV を読む（試運転用）")
    p.add_argument("--out", required=True)
    a = p.parse_args()

    df = pd.read_csv(a.csv) if a.csv else load(a.score_start, a.score_end)
    df["date"] = df["date"].astype(str)
    logger.info("記録: %d行 / %dレース", len(df), df["race_id"].nunique())
    fi, sc = evaluate(df, a.score_start, a.score_end)

    k_pos = fi[fi["place_slots"] > 0]
    fail_all = float(k_pos["fallback_b0"].mean()) if len(k_pos) else float("nan")
    fit = {
        "n_races": len(fi),
        "fit_failure_rate": round(fail_all, 5),
        "by_month": {m: {"n": len(g), "fallback_b0": int(g["fallback_b0"].sum())} for m, g in fi.groupby("month")},
        "by_place_slots": {
            int(k): {"n": len(g), "fallback_b0": int(g["fallback_b0"].sum())} for k, g in fi.groupby("place_slots")
        },
        "status_counts": fi["fit_status"].value_counts().to_dict(),
    }
    out: dict = {
        "generated_at": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(timespec="seconds"),
        "plan": "docs/prediction_flow_redesign_plan_2026_11_06.md §5.5（事前登録）",
        "method_version": METHOD_VERSION,
        "index_version": INDEX_VERSION,
        "score_window": [a.score_start, a.score_end],
        "fit": fit,
    }
    if not sc.empty:
        d = deltas(sc, a.bootstrap)
        st = sc[sc["arm"] == "B0"]["status"].value_counts().to_dict()
        win_fit = fi[fi["scored"] & (fi["place_slots"] > 0)]
        out["scored"] = {
            "n_races": int(sc["race_id"].nunique()),
            "status_counts": {str(k): int(v) for k, v in st.items()},
            "fit_failure_rate": round(float(win_fit["fallback_b0"].mean()), 5),
        }
        out["deltas"] = d
        if "trio_ll" in d:
            out["primary"] = {
                "metric": "trio_ll Δ(R1 − B0)",
                **d["trio_ll"],
                "phase2_confirm_ci": list(PHASE2_CONFIRM_CI),
                "verdict": verdict(d["trio_ll"], fail_all),
            }
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float) + "\n")
    logger.info("書き出し: %s", a.out)
    logger.info("当てはめ失敗率 %.4f / 主指標 %s", fail_all, out.get("primary"))


if __name__ == "__main__":
    main()
