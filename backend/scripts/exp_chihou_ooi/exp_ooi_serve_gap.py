"""大井: 配信時(condition不明) vs バックフィル時(condition既知) の指数差を測る。

本番 calculate_and_save は race_date 00:01 に走るが、その時点で
races.condition / head_count / weather は NULL（レース確定後にしか入らない）。
一方 学習・バックフィルは確定後の condition を読む。
→ 「馬場込みで学習して馬場なしで配信」という v14 が市場特徴で直したのと同じ構造。

ここでは同一モデル・同一母集団で condition を伏せた場合の順位品質を比較する。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import lightgbm as lgb  # noqa: E402

from scripts.chihou_rank_quality_review import connect  # noqa: E402
from scripts.train_chihou_market_lgb import PROD_FEATURES, prep  # noqa: E402
from scripts.train_chihou_prod_lgb import CHIHOU_V9_VERSION  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from scripts.inference_chihou_v14 import INFER_QUERY, PROD_LGB_T3, PROD_LGB_WIN  # noqa: E402
from src.indices.chihou_calculator import _scale_to_index_local  # noqa: E402


def fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(INFER_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    for c in ["finish_position", "win_odds", "win_popularity", "head_count"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop_duplicates(subset=["race_id", "horse_id"]).reset_index(drop=True)


def score(conn, df_raw, df_hist, m_t3, m_win, mask_condition: bool):
    d = df_raw.copy()
    if mask_condition:
        # 配信時の状態を再現: condition 不明（is_good/heavy/bad=0, wetness=1.0=稍）
        d["condition"] = None
    df = prep(conn, d, df_hist)
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
    X = df[list(PROD_FEATURES)].to_numpy(dtype=np.float64)
    df["p_t3"] = m_t3.predict(X)
    df["p_win"] = m_win.predict(X)
    comp = []
    for _, g in df.groupby("race_id", sort=False):
        comp.extend(_scale_to_index_local(list(g["p_t3"])))
    df["composite"] = comp
    return df


def summarize(df, label):
    df = df.copy()
    df["idx_rank"] = df.groupby("race_id")["composite"].rank(ascending=False, method="first")
    df["win_rank"] = df.groupby("race_id")["p_win"].rank(ascending=False, method="first")
    fin = df["finish_position"]
    top1 = df[df["idx_rank"] == 1]
    wtop1 = df[df["win_rank"] == 1]
    ran = df[fin.notna()]
    # レース内 Spearman（完走馬のみ）
    sp = []
    for _, g in ran.groupby("race_id"):
        if len(g) >= 4:
            sp.append(g["composite"].corr(g["finish_position"], method="spearman"))
    print(f"\n--- {label} ---")
    print(f"  races={df['race_id'].nunique():,}  rows={len(df):,}")
    print(f"  指数1位 勝率  : {100*(top1['finish_position']==1).mean():.1f}%")
    print(f"  指数1位 複勝率: {100*(top1['finish_position']<=3).mean():.1f}%")
    print(f"  win確率1位 勝率: {100*(wtop1['finish_position']==1).mean():.1f}%")
    print(f"  レース内Spearman(vs着順, 負が良): {np.nanmean(sp):+.4f}")
    return {
        "top1_win": (top1["finish_position"] == 1).mean(),
        "top1_place": (top1["finish_position"] <= 3).mean(),
        "wintop1_win": (wtop1["finish_position"] == 1).mean(),
        "spearman": float(np.nanmean(sp)),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--course", default="大井")
    args = p.parse_args()

    m_t3 = lgb.Booster(model_file=str(PROD_LGB_T3))
    m_win = lgb.Booster(model_file=str(PROD_LGB_WIN))
    conn = connect()
    try:
        df_raw = fetch(conn, args.start, args.end)
        df_hist = fetch_hist(conn)
        if args.course != "ALL":
            df_raw = df_raw[df_raw["course_name"] == args.course].reset_index(drop=True)
        print(f"対象: {args.course} {args.start}-{args.end}  "
              f"{df_raw['race_id'].nunique():,}R / {len(df_raw):,}行")
        a = score(conn, df_raw, df_hist, m_t3, m_win, mask_condition=False)
        b = score(conn, df_raw, df_hist, m_t3, m_win, mask_condition=True)
    finally:
        conn.close()

    ra = summarize(a, "condition 既知（＝DBのバックフィル値・過去の検証はこれ）")
    rb = summarize(b, "condition 不明（＝本番配信 00:01 の実態）")
    print("\n=== 差（配信 − バックフィル）===")
    for k in ra:
        print(f"  {k:12s}: {rb[k]-ra[k]:+.4f}")

    # 順位がどれだけ動くか
    a2 = a.set_index(["race_id", "horse_id"])["composite"].rank(ascending=False)
    m = a[["race_id", "horse_id"]].copy()
    m["ra"] = a.groupby("race_id")["composite"].rank(ascending=False, method="first").values
    m["rb"] = b.groupby("race_id")["composite"].rank(ascending=False, method="first").values
    print(f"\n1位馬が入れ替わったレース: "
          f"{100*(m[m.ra==1].set_index(['race_id','horse_id']).index != m[m.rb==1].set_index(['race_id','horse_id']).index).mean():.1f}%")
    print(f"全馬の平均順位変動: {(m.ra-m.rb).abs().mean():.2f} 着分")


if __name__ == "__main__":
    main()
