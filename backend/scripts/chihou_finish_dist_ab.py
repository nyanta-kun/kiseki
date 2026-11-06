"""地方の能力分布モデル（R1）を Harville（B0）と比べる（予想フロー再設計・地方 Phase 2）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` **§5.6（事前登録）**。
本スクリプトは §5.6 を実装するだけで、判定基準を動かさない。

## 母集団（前向き記録 `chihou.place_picks`・Phase 1 と同じ）

- 発走前（T−6）に保存した全出走馬の `win_probability` / `place_probability`（生の値）
- 確定済み・`abnormality_code` 1, 2（取消・除外）を除いたフィールド全体
- 🔴 **VAL だけ**（`chihou_protocol.VAL_END` まで）。TEST の行は SQL の段で読まない

## 腕

| 腕 | T3 |
|---|---|
| B0 | Harville(勝率) |
| R1 | `services/chihou_finish_dist.build_race_finish_dist`（k = min(3, 頭数−1)・ok でなければ B0） |

## 判定（§5.6）

主指標: 三連複の実現組の対数損失 Δ(R1 − B0)、レース単位の bootstrap 95%CI。
上限 < 0 で採用（改善）・< +0.005 で採用（非劣性）・それ以外は不採用（B0 のまま）。

## 使い方（本番 DB は読み取り専用）

    ~/bin/kiseki-ro .venv/bin/python scripts/chihou_finish_dist_ab.py \\
        --out ../docs/model_verification/chihou_finish_dist_ab.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.chihou_protocol import VAL_END, VAL_START  # noqa: E402
from src.indices.composite import place_slots_for_field  # noqa: E402
from src.indices.finish_dist import METHOD_VERSION  # noqa: E402
from src.indices.finish_dist_scoring import (  # noqa: E402
    METRICS,
    harville_top3_tensor,
    paired_bootstrap_ci,
    score_race,
)
from src.services.chihou_finish_dist import SOURCE_R1, build_race_finish_dist  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_finish_dist_ab")

NONINFERIOR_MARGIN = 0.005
FIT_FAILURE_LIMIT = 0.01

SQL = """
SELECT r.race_id, r.date, p.horse_number, p.win_probability, p.place_probability,
       p.finish_position, p.abnormality_code
FROM chihou.place_pick_races r
JOIN chihou.place_picks p ON p.pick_race_id = r.id
WHERE r.settled_at IS NOT NULL
  AND r.date >= %(start)s AND r.date <= %(end)s
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


def evaluate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """レースごとに R1 と B0 を作って採点する。(当てはめの状態, 採点) を返す。"""
    d = df[~df["abnormality_code"].isin([1, 2])]
    fits: list[dict] = []
    rows: list[dict] = []
    for rid, g in d.groupby("race_id", sort=False):
        n = len(g)
        fd = build_race_finish_dist(
            g["horse_number"].astype(int).tolist(),
            g["win_probability"].astype(float).to_numpy(),
            g["place_probability"].astype(float).to_numpy(),
        )
        if fd is None:
            continue
        top3_raw = g["place_probability"].astype(float).to_numpy()
        scaled = top3_raw * (fd.k / top3_raw.sum()) if fd.k > 0 and top3_raw.sum() > 0 else top3_raw
        fits.append(
            {
                "race_id": int(rid),
                "date": str(g["date"].iloc[0]),
                "n": n,
                "k": fd.k,
                "fit_status": fd.fit_status,
                "fallback_b0": fd.source != SOURCE_R1 and fd.k > 0,
                "n_clipped": fd.n_clipped,
                "n_target_over_1": int((scaled >= 1.0).sum()),
                "sigma_min": float(fd.sigma.min()) if fd.sigma is not None else np.nan,
                "sigma_max": float(fd.sigma.max()) if fd.sigma is not None else np.nan,
                "sigma_sd_log": float(np.log(fd.sigma).std()) if fd.sigma is not None else np.nan,
            }
        )
        fp = pd.to_numeric(g["finish_position"], errors="coerce").to_numpy(dtype=float)
        slots = place_slots_for_field(n)
        for arm, t3 in (("B0", harville_top3_tensor(fd.win)), ("R1", fd.t3)):
            rows.append(
                {
                    "race_id": int(rid),
                    "date": str(g["date"].iloc[0]),
                    "arm": arm,
                    "n": n,
                    "place_slots": slots,
                    **score_race(t3, fp, slots),
                }
            )
    return pd.DataFrame(fits), pd.DataFrame(rows)


def deltas(sc: pd.DataFrame, n_boot: int) -> dict:
    """指標ごとの Δ(R1 − B0)。1〜3着がそろったレースだけ。place_ll は 8頭以上（3着払い）。"""
    ok = sc[sc["status"] == "ok"]
    out: dict = {}
    for m in METRICS:
        sub = ok if m != "place_ll" else ok[ok["place_slots"] == 3]
        w = sub.pivot_table(index="race_id", columns="arm", values=m).dropna()
        if w.empty:
            continue
        ci = paired_bootstrap_ci((w["R1"] - w["B0"]).to_numpy(), n_boot)
        out[m] = {"n_races": len(w), "B0": round(float(w["B0"].mean()), 5), "R1": round(float(w["R1"].mean()), 5), **ci}
    return out


def verdict(ci_hi: float | None) -> str:
    """§5.6 の判定（動かさない）。"""
    if ci_hi is None:
        return "判定不能"
    if ci_hi < 0.0:
        return "採用（改善）"
    if ci_hi < NONINFERIOR_MARGIN:
        return "採用（非劣性）"
    return "不採用（B0 のまま）"


def main() -> None:
    """§5.6 の手順で採点し JSON を書き出す。"""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start", default=VAL_START)
    p.add_argument("--end", default=VAL_END)
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--limit-races", type=int, default=0, help="試運転用（0 = 全レース）")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    if a.end > VAL_END:
        raise SystemExit(f"--end {a.end} は VAL_END {VAL_END} より後（TEST は読まない）")

    with _connect() as conn:
        df = pd.read_sql(SQL, conn, params={"start": a.start, "end": a.end})
    if a.limit_races:
        keep = df["race_id"].drop_duplicates().head(a.limit_races)
        df = df[df["race_id"].isin(keep)]
    logger.info("記録: %d行 / %dレース（%s〜%s）", len(df), df["race_id"].nunique(), df["date"].min(), df["date"].max())
    t0 = time.time()
    fi, sc = evaluate(df)
    logger.info("採点: %dレース (%.0fs)", len(fi), time.time() - t0)

    fit_pos = fi[fi["k"] > 0]
    fail = float(fit_pos["fallback_b0"].mean())
    d = deltas(sc, a.bootstrap)
    primary = d.get("trio_ll", {})
    ci_hi = primary["ci"][1] if primary.get("ci") else None
    out = {
        "generated_at": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(timespec="seconds"),
        "plan": "docs/prediction_flow_redesign_plan_2026_11_06.md §5.6（事前登録）",
        "method_version": METHOD_VERSION,
        "window": [str(df["date"].min()), str(df["date"].max())],
        "population": {
            "n_races": int(fi.shape[0]),
            "status_counts": {str(k): int(v) for k, v in sc[sc["arm"] == "B0"]["status"].value_counts().items()},
        },
        "fit": {
            "fit_failure_rate": round(fail, 5),
            "fit_failure_below_1pct": fail < FIT_FAILURE_LIMIT,
            "status_counts": fi["fit_status"].value_counts().to_dict(),
            "by_k": {int(k): {"n": len(g), "fallback_b0": int(g["fallback_b0"].sum())} for k, g in fi.groupby("k")},
            "by_field": {
                lab: {"n": len(g), "fallback_b0": int(g["fallback_b0"].sum())}
                for lab, g in fi.groupby(
                    pd.cut(fi["n"], [0, 4, 7, 11, 99], labels=["<=4", "5-7", "8-11", "12+"]), observed=True
                )
            },
            "races_with_target_over_1": int((fi["n_target_over_1"] > 0).sum()),
            "horses_target_over_1": int(fi["n_target_over_1"].sum()),
            "sigma_sd_log_median": round(float(fi["sigma_sd_log"].median()), 4),
            "sigma_min_p01": round(float(fi["sigma_min"].quantile(0.01)), 4),
            "sigma_max_p99": round(float(fi["sigma_max"].quantile(0.99)), 4),
        },
        "primary": {
            "metric": "trio_ll Δ(R1 − B0)",
            **primary,
            "noninferior_margin": NONINFERIOR_MARGIN,
            "verdict": verdict(ci_hi),
        },
        "deltas": d,
    }
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float) + "\n")
    logger.info("書き出し: %s", a.out)
    logger.info("当てはめ失敗率 %.4f / 主指標 %s → %s", fail, primary, out["primary"]["verdict"])


if __name__ == "__main__":
    main()
