"""地方モデルに コーナー位置/脚質・調教師成績・乗替 を入力特徴として統合する A/B。

確立した検証規律: cutoff再学習→held-out真OOS・複数seed平均・deterministic=True 必須
(マルチスレッド揺れ ±0.25pt で判定を誤るため)。判定は的中精度(top1勝率/複勝率)。

追加特徴（全て point-in-time・現走前の履歴のみ・リークなし）:
  CORNER(5): 馬の脚質プロファイル
    c_early_n     : 過去走の序盤コーナー位置/頭数の平均 (0=先頭, 欠損→0.5)
    c_late_gain_n : 過去走の (最終コーナー位置−着順)/頭数 の平均 (+=末脚, 欠損→0)
    c_makuri_n    : 過去走の (序盤−最終コーナー)/頭数 の平均 (+=まくり, 欠損→0)
    c_runs        : コーナー有効走数 min(n,20)/20
    front_density : レース内の先行型(c_early_n≤0.3 かつ 経験あり)割合 = ペース構造
  TRAINER(3): 調教師 point-in-time 成績（前日までの累積・当日レース間リーク回避）
    tr_win_rate  : 平滑化勝率 (wins+0.08*30)/(runs+30)
    tr_top3_rate : 平滑化複勝率 (top3+0.25*30)/(runs+30)
    tr_runs_n    : min(runs,1000)/1000
  JKCHG(1):
    jk_change : 前走騎手と異なる=1 (初出走→0)

feat_sets: baseline(本番30) / +corner / +trainer / +all(corner+trainer+jkchg)

使い方: cd backend && PYTHONPATH=. .venv/bin/python scripts/ab_chihou_corner_trainer.py
"""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.ab_chihou_external_features import MY_QUERY, _add_ext, _ece  # noqa: E402
from scripts.train_chihou_prod_lgb import (  # noqa: E402
    FEATURES,
    NUM_ROUNDS,
    PARAMS,
    fetch_hist,
    prep,
)

SEEDS = [0, 1, 2, 3, 4]
TRAIN_START = "20240101"
CUTOFFS = [
    ("20250630", "20250701", "20251231"),
    ("20251231", "20260101", "20260706"),
]

CORNER_FEATURES = ["c_early_n", "c_late_gain_n", "c_makuri_n", "c_runs", "front_density"]
TRAINER_FEATURES = ["tr_win_rate", "tr_top3_rate", "tr_runs_n"]
JKCHG_FEATURES = ["jk_change"]

HIST_FULL_QUERY = """
SELECT rr.horse_id, r.id AS race_id, r.date, r.head_count, rr.finish_position,
       rr.passing_1, rr.passing_2, rr.passing_3, rr.passing_4,
       rr.jockey_id, re.trainer_id
FROM chihou.race_results rr
JOIN chihou.races r ON r.id = rr.race_id
LEFT JOIN chihou.race_entries re
  ON re.race_id = rr.race_id AND re.horse_id = rr.horse_id
WHERE r.course != '83'
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
ORDER BY rr.horse_id, r.date, r.id
"""


def _conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"))


def _my_fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(MY_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def _fetch_hist_full(conn) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(HIST_FULL_QUERY)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def compute_corner_table(hist: pd.DataFrame) -> pd.DataFrame:
    """(horse_id, race_id) → コーナー特徴 + jk_change。現走前の累積のみ使用。"""
    h = hist.copy()
    for c in ("passing_1", "passing_2", "passing_3", "passing_4",
              "finish_position", "head_count"):
        h[c] = pd.to_numeric(h[c], errors="coerce")
    early = h["passing_2"].fillna(h["passing_1"]).fillna(h["passing_3"])
    late = h["passing_4"].fillna(h["passing_3"])
    hc = h["head_count"].clip(lower=2)
    h["early_n"] = ((early - 1) / (hc - 1)).clip(0, 1)
    h["late_gain"] = ((late - h["finish_position"]) / hc).clip(-1, 1)
    h["makuri"] = ((early - late) / hc).clip(-1, 1)
    h["valid"] = (early.notna() & late.notna()).astype(float)

    h = h.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)
    g = h.groupby("horse_id")
    for src, dst in (("early_n", "c_early_n"), ("late_gain", "c_late_gain_n"),
                     ("makuri", "c_makuri_n")):
        v = h[src].fillna(0.0) * h["valid"]
        cnt = g["valid"].cumsum() - h["valid"]
        s = v.groupby(h["horse_id"]).cumsum() - v
        h[dst] = s / cnt.clip(lower=1)
        h.loc[cnt < 1, dst] = np.nan
    cnt = g["valid"].cumsum() - h["valid"]
    h["c_runs"] = cnt.clip(upper=20) / 20.0

    # 乗替: 前走騎手と異なるか（初出走→0）
    prev_jk = g["jockey_id"].shift(1)
    h["jk_change"] = ((prev_jk.notna()) & (prev_jk != h["jockey_id"])).astype(float)

    return h.set_index(["horse_id", "race_id"])[
        ["c_early_n", "c_late_gain_n", "c_makuri_n", "c_runs", "jk_change"]]


def compute_trainer_table(hist: pd.DataFrame) -> pd.DataFrame:
    """(trainer_id, date) → 前日までの累積成績（当日レース間リーク回避）。"""
    h = hist[hist["trainer_id"].notna()].copy()
    h["fp"] = pd.to_numeric(h["finish_position"], errors="coerce")
    h["win"] = (h["fp"] == 1).astype(float)
    h["top3"] = (h["fp"] <= 3).astype(float)
    day = (h.groupby(["trainer_id", "date"])
             .agg(runs=("fp", "size"), wins=("win", "sum"), top3s=("top3", "sum"))
             .reset_index()
             .sort_values(["trainer_id", "date"]))
    g = day.groupby("trainer_id")
    day["cum_runs"] = g["runs"].cumsum() - day["runs"]
    day["cum_wins"] = g["wins"].cumsum() - day["wins"]
    day["cum_top3"] = g["top3s"].cumsum() - day["top3s"]
    k = 30.0
    day["tr_win_rate"] = (day["cum_wins"] + 0.08 * k) / (day["cum_runs"] + k)
    day["tr_top3_rate"] = (day["cum_top3"] + 0.25 * k) / (day["cum_runs"] + k)
    day["tr_runs_n"] = day["cum_runs"].clip(upper=1000) / 1000.0
    return day.set_index(["trainer_id", "date"])[
        ["tr_win_rate", "tr_top3_rate", "tr_runs_n"]]


def add_new_features(df: pd.DataFrame, corner_tbl: pd.DataFrame,
                     trainer_tbl: pd.DataFrame, trainer_map: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.join(corner_tbl, on=["horse_id", "race_id"], how="left")
    df["c_early_n"] = df["c_early_n"].fillna(0.5)
    for c in ("c_late_gain_n", "c_makuri_n", "c_runs", "jk_change"):
        df[c] = df[c].fillna(0.0)
    # front_density: レース内の先行型割合（経験ありのみカウント）
    is_front = ((df["c_early_n"] <= 0.3) & (df["c_runs"] > 0)).astype(float)
    df["front_density"] = is_front.groupby(df["race_id"]).transform("mean")

    df = df.merge(trainer_map, on=["horse_id", "race_id"], how="left")
    df = df.join(trainer_tbl, on=["trainer_id", "date"], how="left")
    df["tr_win_rate"] = df["tr_win_rate"].fillna(0.08)
    df["tr_top3_rate"] = df["tr_top3_rate"].fillna(0.25)
    df["tr_runs_n"] = df["tr_runs_n"].fillna(0.0)
    return df


def _eval(te: pd.DataFrame, win_score, top3_score) -> dict:
    d = te.copy()
    d["sw"] = win_score
    d["s3"] = top3_score
    d["fp"] = pd.to_numeric(d["finish_position"], errors="coerce")
    d["wodds"] = pd.to_numeric(d["win_odds"], errors="coerce")
    r1w = d.loc[d.groupby("race_id")["sw"].idxmax()]
    r1p = d.loc[d.groupby("race_id")["s3"].idxmax()]
    win_pct = (r1w["fp"] == 1).mean() * 100
    place_pct = (r1p["fp"] <= 3).mean() * 100
    ret = np.where(r1w["fp"] == 1, r1w["wodds"], 0.0)
    win_roi = float(np.nanmean(ret))
    ece = _ece((d["fp"] == 1).astype(int).to_numpy(), np.clip(d["sw"].to_numpy(), 0, 1))
    return dict(win_pct=win_pct, place_pct=place_pct, win_roi=win_roi, ece=ece)


ALL_NEW = CORNER_FEATURES + TRAINER_FEATURES + JKCHG_FEATURES
KEEP_COLS = ["race_id", "finish_position", "win_odds"]


def _slim(df: pd.DataFrame) -> pd.DataFrame:
    """特徴列+評価列のみ残し float32 化してメモリを最小化する。"""
    cols = KEEP_COLS + list(dict.fromkeys(FEATURES + ALL_NEW))
    out = df[cols].copy()
    for c in cols:
        if c not in ("race_id",):
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("float32")
    return out


def main() -> None:
    conn = _conn()
    df_hist = fetch_hist(conn)
    hist_full = _fetch_hist_full(conn)
    print(f"履歴 {len(hist_full)} 行取得。特徴テーブル構築中...", flush=True)
    corner_tbl = compute_corner_table(hist_full)
    trainer_tbl = compute_trainer_table(hist_full)
    trainer_map = hist_full[["horse_id", "race_id", "trainer_id"]].drop_duplicates()
    del hist_full
    gc.collect()

    feat_sets = {
        "baseline": FEATURES,
        "+corner": FEATURES + CORNER_FEATURES,
        "+trainer": FEATURES + TRAINER_FEATURES,
        "+all": FEATURES + ALL_NEW,
    }

    only = os.getenv("AB_ONLY_CUTOFF")  # "0"/"1" 指定でそのcutoffのみ実行（省メモリ）
    cutoffs = [CUTOFFS[int(only)]] if only is not None else CUTOFFS

    for cutoff, test_start, test_end in cutoffs:
        print(f"\n{'#'*70}\n# cutoff={cutoff} test={test_start}〜{test_end}\n{'#'*70}", flush=True)
        tr_raw = _slim(add_new_features(
            _add_ext(prep(conn, _my_fetch(conn, TRAIN_START, cutoff), df_hist)),
            corner_tbl, trainer_tbl, trainer_map))
        gc.collect()
        te_raw = _slim(add_new_features(
            _add_ext(prep(conn, _my_fetch(conn, test_start, test_end), df_hist)),
            corner_tbl, trainer_tbl, trainer_map))
        gc.collect()
        cov = (te_raw["c_runs"] > 0).mean()
        print(f"train {tr_raw['race_id'].nunique()}R / test {te_raw['race_id'].nunique()}R "
              f"| corner経験カバレッジ test={cov:.1%}", flush=True)

        fp_tr = pd.to_numeric(tr_raw["finish_position"], errors="coerce")
        y_win = (fp_tr == 1).astype(int).values
        y_top3 = (fp_tr <= 3).astype(int).values

        results: dict[str, list[dict]] = {k: [] for k in feat_sets}
        for seed in SEEDS:
            params = dict(PARAMS, seed=seed, bagging_seed=seed,
                          feature_fraction_seed=seed, deterministic=True,
                          force_row_wise=True)
            for name, feats in feat_sets.items():
                Xtr = tr_raw[feats].to_numpy(dtype=np.float32)
                Xte = te_raw[feats].to_numpy(dtype=np.float32)
                mw = lgb.train(params, lgb.Dataset(Xtr, y_win, feature_name=feats),
                               num_boost_round=NUM_ROUNDS)
                m3 = lgb.train(params, lgb.Dataset(Xtr, y_top3, feature_name=feats),
                               num_boost_round=NUM_ROUNDS)
                results[name].append(_eval(te_raw, mw.predict(Xte), m3.predict(Xte)))
                del Xtr, Xte, mw, m3
                gc.collect()
            print(f"  seed {seed} 完了", flush=True)

        metrics = [("top1勝率%", "win_pct", "+"), ("top1複勝率%", "place_pct", "+"),
                   ("単勝ROI", "win_roi", "+"), ("ECE(is_win)", "ece", "-")]
        b_all = results["baseline"]
        for name in feat_sets:
            if name == "baseline":
                continue
            print(f"\n--- {name} vs baseline (cutoff {cutoff}) ---")
            for label, key, good in metrics:
                b = np.array([r[key] for r in b_all])
                a = np.array([r[key] for r in results[name]])
                delta = a.mean() - b.mean()
                improved = (delta > 0) if good == "+" else (delta < 0)
                per_seed = [(a[i] - b[i]) if good == "+" else (b[i] - a[i])
                            for i in range(len(SEEDS))]
                n_better = sum(1 for x in per_seed if x > 0)
                sig = "★std超" if abs(delta) > max(b.std(), a.std()) and improved \
                    else ("◯" if improved else "✗")
                print(f"  {label:13} base {b.mean():.3f}±{b.std():.3f} → {a.mean():.3f}±{a.std():.3f} "
                      f"| Δ={delta:+.3f} ({n_better}/{len(SEEDS)}seed改善) {sig}")

    conn.close()
    print("\n判定基準: top1勝率/複勝率が両cutoffで全seed改善かつΔ>std → 本採用候補。")


if __name__ == "__main__":
    main()
