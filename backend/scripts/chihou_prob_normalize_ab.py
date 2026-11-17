"""地方の勝率・複勝率をレース内で正規化すると当たり具合が悪化しないかを測る（予想フロー再設計 Phase 1）。

計画: `docs/prediction_flow_redesign_plan_2026_11_06.md` §1.2・§3 Phase 1。

## 背景

`chihou_calculator.py` は is_win / is_top3 ヘッドの較正済み生確率をそのまま保存する。
レース内の合計は揃っておらず、2026-10 実測で勝率の合計は p10–p90 で 0.70〜1.38、
3着内率の合計は 2.51〜3.66 だった。Phase 1 は**保存値と版を変えず**、読み出し側で
勝率 Σ=1・3着内率 Σ=3 に揃える。その前に、揃えても当たり具合が悪化しないことを確かめる。

## 母集団（前向き記録 `chihou.place_picks`）

- 発走前（T−6）に保存した全出走馬の `win_probability` / `place_probability`。
  画面が実際に出していた値そのもの（`calculated_indices` は当日夜に上書きされる）
- 確定済み（`place_pick_races.settled_at IS NOT NULL`）・`abnormality_code` 1, 2（取消・除外）を除く
- 🔴 **VAL だけ**: `chihou_protocol.VAL_END`（= TEST_START の前日）まで。TEST は使わない
- 正規化は、取消・除外を除いた**フィールド全体**で行う（本番の読み出しと同じ）

## 指標と判定（事前に固定・計画 §3 Phase 1）

| 指標 | 生 | 正規化 |
|---|---|---|
| 単勝の多項対数損失 `win_mll` | −log(p_1着) | −log(p_1着 / Σp) |
| 3着内の二値対数損失 `top3_ll` | 生 `place_probability` | Σ=3 に揃えた値（1 を超えたらクリップ） |

参考に単勝の二値対数損失 `win_bll`（馬ごと）も出す。
**判定: `win_mll` と `top3_ll` の両方で「正規化 − 生」の 95%CI 上限 ≤ 0**（悪化しない）。
`top3_ll` は 8頭以上（3着まで払戻）のレースで判定する。

⚠️ 生の勝率で多項対数損失を取ると、合計が 1 を超えるレースほど有利になる
（確率になっていないため）。それでも計画の判定どおり両方を出す。

## 使い方

    ~/bin/kiseki-ro -C <repo>/backend .venv/bin/python scripts/chihou_prob_normalize_ab.py \\
        --out ../docs/model_verification/chihou_prob_normalize_ab.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.chihou_protocol import VAL_END, VAL_START  # noqa: E402

EPS = 1e-9
OUT_PATH = _root.parent / "docs" / "model_verification" / "chihou_prob_normalize_ab.json"

SQL = """
SELECT r.race_id, r.date, p.horse_number, p.win_probability, p.place_probability,
       p.finish_position, p.abnormality_code
FROM chihou.place_pick_races r
JOIN chihou.place_picks p ON p.pick_race_id = r.id
WHERE r.settled_at IS NOT NULL
  AND r.date >= %(start)s AND r.date <= %(end)s
"""


def score_races(df: pd.DataFrame) -> pd.DataFrame:
    """レースごとに生と正規化の対数損失を出す（DB に依存しない純関数）。

    Args:
        df: 1 行 1 馬。``race_id`` / ``win_probability`` / ``place_probability`` /
            ``finish_position`` / ``abnormality_code`` を持つ。

    Returns:
        1 行 1 レース。採点できない指標は NaN。
    """
    d = df[~df["abnormality_code"].isin([1, 2])].copy()
    d = d[d["win_probability"].notna() & d["place_probability"].notna()]
    rows = []
    for rid, g in d.groupby("race_id", sort=False):
        n = len(g)
        if n < 2:
            continue
        fp = pd.to_numeric(g["finish_position"], errors="coerce").fillna(0).to_numpy(dtype=float)
        pw = np.clip(g["win_probability"].to_numpy(dtype=float), EPS, 1 - EPS)
        pp = np.clip(g["place_probability"].to_numpy(dtype=float), EPS, 1 - EPS)
        pw_n = pw / pw.sum()
        pp_n = np.clip(pp * 3.0 / pp.sum(), EPS, 1 - EPS)

        y_win = (fp == 1).astype(float)
        y_t3 = ((fp >= 1) & (fp <= 3)).astype(float)
        one_winner = int(y_win.sum()) == 1
        w = int(np.argmax(y_win)) if one_winner else None

        def bll(p: np.ndarray, y: np.ndarray) -> float:
            q = np.clip(p, EPS, 1 - EPS)
            return float(np.mean(-(y * np.log(q) + (1 - y) * np.log(1 - q))))

        rows.append(
            {
                "race_id": rid,
                "date": g["date"].iloc[0],
                "n": n,
                "win_sum": float(pw.sum()),
                "place_sum": float(pp.sum()),
                "n_place_clipped": int((pp * 3.0 / pp.sum() > 1 - EPS).sum()),
                "win_mll_raw": float(-np.log(pw[w])) if w is not None else np.nan,
                "win_mll_norm": float(-np.log(pw_n[w])) if w is not None else np.nan,
                "win_bll_raw": bll(pw, y_win),
                "win_bll_norm": bll(pw_n, y_win),
                "top3_ll_raw": bll(pp, y_t3),
                "top3_ll_norm": bll(pp_n, y_t3),
            }
        )
    return pd.DataFrame(rows)


def paired_ci(delta: np.ndarray, n_boot: int, seed: int = 20261106) -> dict:
    """レース単位の bootstrap（1 レース 1 値なのでレースの復元抽出）。"""
    d = delta[~np.isnan(delta)]
    rng = np.random.default_rng(seed)
    boots = np.array([d[rng.integers(0, d.size, d.size)].mean() for _ in range(n_boot)])
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "n_races": int(d.size),
        "mean": round(float(d.mean()), 5),
        "ci95": [round(float(lo), 5), round(float(hi), 5)],
    }


def summarize(sc: pd.DataFrame, n_boot: int) -> dict:
    out: dict = {}
    big = sc[sc["n"] >= 8]
    for metric, pop in (("win_mll", sc), ("win_bll", sc), ("top3_ll", big), ("top3_ll_all_fields", sc)):
        base = metric.replace("_all_fields", "")
        delta = (pop[f"{base}_norm"] - pop[f"{base}_raw"]).to_numpy(dtype=float)
        ci = paired_ci(delta, n_boot)
        months = {}
        for m, g in pop.assign(month=pop["date"].str[:6]).groupby("month"):
            dd = (g[f"{base}_norm"] - g[f"{base}_raw"]).dropna()
            months[m] = {"n_races": int(len(dd)), "delta": round(float(dd.mean()), 5)}
        out[metric] = {
            "raw": round(float(pop[f"{base}_raw"].mean()), 5),
            "norm": round(float(pop[f"{base}_norm"].mean()), 5),
            "delta_norm_minus_raw": ci,
            "not_worse": bool(ci["ci95"][1] <= 0.0),
            "by_month": months,
        }
    # 🔴 単勝の多項対数損失の差は、1着が1頭のレースでは log(Σp) と恒等的に等しい
    #   （−log(p_w/Σp) = −log p_w + log Σp）。結果に依存しないので判定に使えない。
    one = sc[sc["win_mll_raw"].notna()]
    out["win_mll_identity_check"] = {
        "mean_log_win_sum": round(float(np.log(one["win_sum"]).mean()), 5),
        "delta_win_mll": round(float((one["win_mll_norm"] - one["win_mll_raw"]).mean()), 5),
    }
    # 正規化は Σ<目標のレースで確率を上げ、Σ>目標で下げる。両方の群で改善するかを見る
    splits: dict = {}
    for metric, sum_col, target, pop in (
        ("win_bll", "win_sum", 1.0, sc),
        ("top3_ll", "place_sum", 3.0, big),
    ):
        for name, g in (("below", pop[pop[sum_col] < target]), ("above", pop[pop[sum_col] >= target])):
            delta = (g[f"{metric}_norm"] - g[f"{metric}_raw"]).to_numpy(dtype=float)
            splits[f"{metric}__{sum_col}_{name}_{target:g}"] = paired_ci(delta, n_boot)
    out["split_by_sum"] = splits
    out["sum_vs_truth"] = {
        "mean_win_sum": round(float(sc["win_sum"].mean()), 4),
        "mean_place_sum_8plus": round(float(big["place_sum"].mean()), 4),
        "note": "1着は1頭・3着以内は3頭（8頭以上）",
    }
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start", default=VAL_START)
    p.add_argument("--end", default=VAL_END)
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()
    if args.end > VAL_END:
        raise SystemExit(f"🔴 終端 {args.end} が VAL_END={VAL_END} を超える（TEST は使わない）")

    import psycopg2

    url = os.getenv("KEIRIN_DB_URL")
    if url:
        conn = psycopg2.connect(url)
    else:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
        )
    with conn:
        df = pd.read_sql(SQL, conn, params={"start": args.start, "end": args.end})
    conn.close()

    sc = score_races(df)
    summary = summarize(sc, args.bootstrap)
    q = lambda s: [round(float(x), 3) for x in np.percentile(s, [10, 50, 90])]  # noqa: E731
    out = {
        "plan": "docs/prediction_flow_redesign_plan_2026_11_06.md §3 Phase 1",
        "window": {"start": args.start, "end": args.end, "val_end": VAL_END},
        "population": {
            "n_races": int(len(sc)),
            "n_races_8plus": int((sc["n"] >= 8).sum()),
            "date_min": str(sc["date"].min()),
            "date_max": str(sc["date"].max()),
            "win_sum_p10_p50_p90": q(sc["win_sum"]),
            "place_sum_p10_p50_p90": q(sc["place_sum"]),
            "place_clipped_horses": int(sc["n_place_clipped"].sum()),
        },
        "criterion": "win_mll と top3_ll（8頭以上）の両方で 正規化−生 の 95%CI 上限 ≤ 0",
        "verdict_pass_original_criterion": bool(summary["win_mll"]["not_worse"] and summary["top3_ll"]["not_worse"]),
        "criterion_amendment": (
            "2026-11-06: 生の勝率はレース内で合計が1でない（分布でない）ため、単勝の多項対数損失の差は "
            "1着が1頭のレースで log(Σp) と恒等的に等しく、結果に依存しない（win_mll_identity_check）。"
            "判定は馬ごとの二値対数損失（win_bll・top3_ll）に置き換える。ユーザー確認待ち"
        ),
        "verdict_pass_amended": bool(summary["win_bll"]["not_worse"] and summary["top3_ll"]["not_worse"]),
        "summary": summary,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    keys = ("population", "verdict_pass_original_criterion", "verdict_pass_amended")
    print(json.dumps({k: out[k] for k in keys}, ensure_ascii=False, indent=1))
    for k, v in summary.items():
        if isinstance(v, dict) and "raw" in v:
            print(k, v["raw"], "->", v["norm"], v["delta_norm_minus_raw"], "not_worse=", v["not_worse"])
    for k in ("win_mll_identity_check", "split_by_sum", "sum_vs_truth"):
        print(k, json.dumps(summary[k], ensure_ascii=False))


if __name__ == "__main__":
    main()
