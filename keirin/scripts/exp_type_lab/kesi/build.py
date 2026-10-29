#!/usr/bin/env python3
"""消しモデルの特徴表を作る（事前登録 docs/type_lab/kesi_model_2026_10_28.md §2）。

入力（DB から psql で書き出した CSV）:
  rows_*.csv  選手1行（出走表の項目・最終三連単オッズから作った m3/mw・着順）
  form.csv    全レースの選手の着順（調子の特徴用・2024-01〜）
  iv.csv      選手コメントのタグ
出力: <dir>/kesi_feats.pkl

    .venv/bin/python scripts/exp_type_lab/kesi/build.py /tmp/claude-501/kesi
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CAT = ["race_type", "grade", "venue_id", "player_class", "style"]
DNF = 8  # 失格・途中棄権（finish_order=0）は最下位扱い


def load_rows(d: Path) -> pd.DataFrame:
    fs = sorted(glob.glob(str(d / "rows_*.csv")))
    df = pd.concat([pd.read_csv(f, dtype={"race_key": str, "cup_id": str, "venue_id": str}) for f in fs],
                   ignore_index=True)
    df = df.drop_duplicates(["race_key", "frame_no"])
    df["date"] = pd.to_datetime(df["race_date"])
    df["top3"] = df["finish_order"].between(1, 3).astype(int)
    return df


def add_relative(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("race_key")
    df["rp_rank"] = g["race_point"].rank(ascending=False, method="min")
    df["rp_minus_max"] = df["race_point"] - g["race_point"].transform("max")
    df["rp_minus_mean"] = df["race_point"] - g["race_point"].transform("mean")
    m3c = df["m3"].clip(0.005, 0.995)
    df["logit_m3"] = np.log(m3c / (1 - m3c))
    df["m3_rank"] = g["m3"].rank(ascending=False, method="min")
    df["logit_mw"] = np.log(df["mw"].clip(1e-4, 0.9999) / (1 - df["mw"].clip(1e-4, 0.9999)))
    # ライン
    lk = df["race_key"] + "_" + df["line_group"].astype(str)
    df["line_rp_sum"] = df.groupby(lk)["race_point"].transform("sum")
    lead_rp = df[df["line_pos"] == 1].set_index(lk[df["line_pos"] == 1])["race_point"]
    df["line_leader_rp"] = lk.map(lead_rp)
    lines = df.assign(lk=lk).drop_duplicates("lk")[["race_key", "lk", "line_rp_sum", "line_leader_rp"]]
    lines["line_rank_sum"] = lines.groupby("race_key")["line_rp_sum"].rank(ascending=False, method="min")
    lines["line_rank_lead"] = lines.groupby("race_key")["line_leader_rp"].rank(ascending=False, method="min")
    df = df.merge(lines[["lk", "line_rank_sum", "line_rank_lead"]], left_on=lk, right_on="lk", how="left").drop(columns=["lk"])
    best_other = []
    mx = df.groupby("race_key")["line_rp_sum"].transform("max")
    df["line_sum_minus_max"] = df["line_rp_sum"] - mx
    lmx = df.groupby("race_key")["line_leader_rp"].transform("max")
    df["line_lead_minus_max"] = df["line_leader_rp"] - lmx
    df["is_tanki"] = (df["line_size"] == 1).astype(int)
    del best_other
    return df


def add_form(df: pd.DataFrame, d: Path) -> pd.DataFrame:
    f = pd.read_csv(d / "form.csv", dtype={"race_key": str, "cup_id": str})
    f = f.drop_duplicates(["player_id", "race_key"])
    f["date"] = pd.to_datetime(f["race_date"])
    f["fin"] = f["finish_order"].where(f["finish_order"] > 0, DNF)
    f["t3"] = f["finish_order"].between(1, 3).astype(float)
    f = f.sort_values(["player_id", "date", "race_key"]).reset_index(drop=True)
    g = f.groupby("player_id")
    # その日より前の走だけ（shift で1つ前まで。同日の別レースは1日1走なので実質起きないが、念のため日付で落とす）
    f["prev_date"] = g["date"].shift(1)
    f["prev_fin"] = g["fin"].shift(1)
    for k in (3, 5):
        f[f"fin_mean{k}"] = g["fin"].transform(lambda s: s.shift(1).rolling(k, min_periods=1).mean())
        f[f"t3_rate{k}"] = g["t3"].transform(lambda s: s.shift(1).rolling(k, min_periods=1).mean())
    same_day = f["prev_date"] == f["date"]
    for c in ("prev_fin", "fin_mean3", "fin_mean5", "t3_rate3", "t3_rate5"):
        f.loc[same_day, c] = np.nan
    f["days_since"] = (f["date"] - f["prev_date"]).dt.days
    # 同じ開催の前走
    f["cup_prev_fin"] = f.groupby(["player_id", "cup_id"])["fin"].shift(1)
    f["cup_n_prev"] = f.groupby(["player_id", "cup_id"]).cumcount()
    cols = ["player_id", "race_key", "prev_fin", "fin_mean3", "fin_mean5", "t3_rate3", "t3_rate5",
            "days_since", "cup_prev_fin", "cup_n_prev"]
    return df.merge(f[cols], on=["player_id", "race_key"], how="left")


def add_iv(df: pd.DataFrame, d: Path) -> pd.DataFrame:
    iv = pd.read_csv(d / "iv.csv", dtype={"race_key": str, "cup_id": str})
    tagc = ["condition", "fatigue", "equipment", "confidence"]
    strc = ["trouble", "self_eval", "trend", "tactic"]
    for c in strc:
        iv[c] = iv[c].astype("category").cat.codes.replace(-1, np.nan)
    pre = iv[iv["kind"] == "pre"].drop_duplicates(["cup_id", "player_id"], keep="last")
    pre = pre[["cup_id", "player_id"] + tagc + strc].rename(columns={c: f"pre_{c}" for c in tagc + strc})
    df = df.merge(pre, on=["cup_id", "player_id"], how="left")
    post = iv[iv["kind"] == "post"].copy()
    post["date"] = pd.to_datetime(post["race_date"])
    post = post.sort_values("date")[["player_id", "date"] + tagc + strc]
    post = post.rename(columns={c: f"post_{c}" for c in tagc + strc})
    df = df.sort_values("date")
    # その日より前（allow_exact_matches=False）の直近のレース後コメント
    df = pd.merge_asof(df, post, on="date", by="player_id", allow_exact_matches=False, direction="backward")
    return df


def main():
    d = Path(sys.argv[1])
    df = load_rows(d)
    print("rows", len(df), "races", df.race_key.nunique(), df.race_date.min(), df.race_date.max(), flush=True)
    df = add_relative(df)
    df = add_form(df, d)
    df = add_iv(df, d)
    for c in CAT:
        df[c] = df[c].astype(str).astype("category")
    df = df.sort_values(["race_key", "frame_no"]).reset_index(drop=True)
    df.to_pickle(d / "kesi_feats.pkl")
    print("saved", len(df), "cols", len(df.columns))
    print(df[df.race_key == df.race_key.iloc[0]][["race_key", "frame_no", "line_group", "line_pos", "race_point",
                                                   "rp_rank", "line_rank_sum", "m3", "prev_fin", "fin_mean5", "top3"]])


if __name__ == "__main__":
    main()
