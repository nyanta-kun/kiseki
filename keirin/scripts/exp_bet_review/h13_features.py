#!/usr/bin/env python3
"""H13 特徴量: 全出走者の発走前に得られる量だけで作る（point-in-time）。

 - 選手の S/B 傾向: 本番 add_sb_dyn_features_wt(b_rate_90, s_rate_90 …) をそのまま import（closed="left"）。
   追加で自前の 365 日 B 率・90 日の確定走数（同じ closed="left"）。
 - 🔴 s_count/h_count/b_count/決まり手累計は**使わない**: 直近窓の累計で、取得時点（そのレースの結果を含むか）が
   確認できない（Δb_count は 当該レースの res_back とも前走の res_back とも相関する）。リーク疑いのため落とす。
 - 隊列推定 formation_* は本番 add_formation_features_wt をそのまま呼ぶ（比較対象 (iii)）。
 - 同じレースの相手との相対量（B 率順位・逃の人数・他ラインの先頭の B 率の最大 …）。
 - 結果列（finish_order / res_* / final_half）は「履歴の集計」にだけ使い、特徴行には残さない。
出力: data/exp_bet_review/h13/feat.pkl
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from src.preprocessing.feature_wt import add_sb_dyn_features_wt, add_formation_features_wt  # noqa

D = REPO / "data/exp_bet_review/h13"


def own_rates(h: pd.DataFrame) -> pd.DataFrame:
    """b_rate_365 / n_conf_90（確定走数）。closed="left"（当該日を含まない）。"""
    H = h[["race_key", "player_id", "race_date", "res_back", "finish_order"]].copy()
    H["_dt"] = pd.to_datetime(H["race_date"])
    conf = pd.to_numeric(H["finish_order"], errors="coerce") >= 1
    H["_b"] = pd.to_numeric(H["res_back"], errors="coerce").where(conf)
    H["_one"] = H["_b"].notna().astype(float)
    H = H.sort_values(["player_id", "_dt"]).reset_index(drop=True)
    g = H.set_index("_dt").groupby("player_id")
    H["b_rate_365"] = g["_b"].rolling("365D", closed="left").mean().reset_index(level=0, drop=True).values
    H["n_conf_90"] = g["_one"].rolling("90D", closed="left").sum().reset_index(level=0, drop=True).values
    H["n_conf_365"] = g["_one"].rolling("365D", closed="left").sum().reset_index(level=0, drop=True).values
    return H[["race_key", "player_id", "b_rate_365", "n_conf_90", "n_conf_365"]]


def race_relative(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    g = df.groupby("race_key", sort=False)
    df["n_start"] = g["player_id"].transform("size")
    df["style_nige"] = (df["style"] == "逃").astype(int)
    df["style_ryo"] = (df["style"] == "両").astype(int)
    df["style_oi"] = (df["style"] == "追").astype(int)
    rp = df["race_point"].fillna(0.0)
    df["rp"] = rp
    df["rp_rank"] = rp.groupby(df["race_key"]).rank(ascending=False, method="average")
    df["rp_rel"] = rp - rp.groupby(df["race_key"]).transform("mean")
    df["is_solo"] = (df["line_size"].fillna(1) <= 1).astype(int)
    b = df["b_rate_90"]
    df["b90_rank"] = b.groupby(df["race_key"]).rank(ascending=False, method="average")
    df["b90_sum"] = b.groupby(df["race_key"]).transform("sum")
    df["b90_share"] = np.where(df["b90_sum"] > 0, b / df["b90_sum"].where(df["b90_sum"] > 0), 1.0 / df["n_start"])
    df["b90_max"] = b.groupby(df["race_key"]).transform("max")
    # 自分を除く最大（最大が2人いれば自分を除いても最大のまま）
    b2 = b.groupby(df["race_key"]).transform(lambda s: np.sort(s.values)[-2] if len(s) > 1 else 0.0)
    df["b90_max_other"] = np.where(b >= df["b90_max"], b2, df["b90_max"])
    df["b90_gap_other"] = b - df["b90_max_other"]
    df["n_nige"] = df.groupby("race_key")["style_nige"].transform("sum")
    df["n_nige_other"] = df["n_nige"] - df["style_nige"]
    df["n_b90_gt"] = g["b_rate_90"].transform(lambda s: s.values.size) * 0  # placeholder (下で埋める)
    # ライン単位
    key = ["race_key", "line_group"]
    lg = df.groupby(key, sort=False)
    df["line_b_max"] = lg["b_rate_90"].transform("max")
    df["line_b_sum"] = lg["b_rate_90"].transform("sum")
    df["line_rp_sum"] = lg["rp"].transform("sum")
    df["line_n_nige"] = lg["style_nige"].transform("sum")
    df["rank_in_line_b"] = df.groupby(key)["b_rate_90"].rank(ascending=False, method="min")
    df["is_line_b_top"] = (df["b_rate_90"] >= df["line_b_max"]).astype(int)
    df["line_rp_rank"] = df.groupby("race_key")["line_rp_sum"].rank(ascending=False, method="dense")
    df["line_b_rank"] = df.groupby("race_key")["line_b_max"].rank(ascending=False, method="dense")
    # 他ラインの先頭（line_pos==1）の B 率の最大 / 他ライン全員の B 率の最大
    lead_b = df["b_rate_90"].where(df["line_pos"] == 1)
    df["_lead_b"] = lead_b
    out_lead, out_any = [], []
    for _, s in df.groupby("race_key", sort=False):
        for idx, lgid in zip(s.index, s["line_group"].values):
            o = s[s["line_group"].values != lgid]
            out_lead.append((idx, o["_lead_b"].max() if len(o) else np.nan, o["b_rate_90"].max() if len(o) else np.nan))
    t = pd.DataFrame(out_lead, columns=["idx", "other_lead_b_max", "other_line_b_max"]).set_index("idx")
    df["other_lead_b_max"] = t["other_lead_b_max"].fillna(0.0)
    df["other_line_b_max"] = t["other_line_b_max"].fillna(0.0)
    df["line_vs_other_b"] = df["line_b_max"] - df["other_line_b_max"]
    # 自ラインで自分より前（line_pos が小さい）の僚機の B 率最大
    front_mate = []
    for _, s in df.groupby(key, sort=False):
        for idx, lp in zip(s.index, s["line_pos"].values):
            o = s[s["line_pos"].values < lp]
            front_mate.append((idx, o["b_rate_90"].max() if len(o) else np.nan))
    fm = pd.DataFrame(front_mate, columns=["idx", "front_mate_b_max"]).set_index("idx")
    df["front_mate_b_max"] = fm["front_mate_b_max"].fillna(-1.0)
    df["b_minus_front_mate"] = np.where(df["front_mate_b_max"] >= 0, b - df["front_mate_b_max"], 0.0)
    df["n_b90_gt"] = df.groupby("race_key")["b_rate_90"].transform(lambda s: s.rank(ascending=False, method="min") - 1)
    return df.drop(columns=["_lead_b"])


FEATURES = [
    "b_rate_90", "s_rate_90", "b_rate_365", "n_conf_90", "n_conf_365", "fh_rel_90", "fh_best_rate_90",
    "style_nige", "style_ryo", "style_oi", "prediction_mark",
    "line_pos", "line_size", "is_line_leader", "n_lines", "is_solo", "n_start",
    "rp", "rp_rank", "rp_rel",
    "b90_rank", "b90_share", "b90_gap_other", "n_nige", "n_nige_other", "n_b90_gt",
    "line_b_max", "line_b_sum", "line_rp_sum", "line_n_nige", "rank_in_line_b", "is_line_b_top", "line_rp_rank", "line_b_rank",
    "other_lead_b_max", "other_line_b_max", "line_vs_other_b", "front_mate_b_max", "b_minus_front_mate",
]


def main():
    h = pd.read_pickle(D / "all.pkl")
    h = h[h["cancel"] == 0]
    # 履歴は全行（確定行だけが集計に入る）。特徴を付ける対象は 2024-01 以降。
    base = add_sb_dyn_features_wt(h, history=h)
    base = base.merge(own_rates(h), on=["race_key", "player_id"], how="left")
    base[["b_rate_365", "n_conf_90", "n_conf_365"]] = base[["b_rate_365", "n_conf_90", "n_conf_365"]].fillna(0.0)
    base = base[base["race_date"] >= "2024-01-01"].reset_index(drop=True)
    base = add_formation_features_wt(base)          # 本番の隊列推定
    base = race_relative(base)
    base["prediction_mark"] = base["prediction_mark"].fillna(0)
    base.to_pickle(D / "feat.pkl")
    print(base.shape, base["race_key"].nunique())


if __name__ == "__main__":
    main()
