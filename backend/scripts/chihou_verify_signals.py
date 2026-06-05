"""地方 表示シグナル(バッジ/信頼度/期待度)の妥当性検証

現行の表示シグナルが「実際に成績順に並ぶか(=妥当か)」をOOSで検証する。
特に Phase2 で win_probability を較正is_win・place を較正is_top3 に変えたため、
旧確率分布前提でチューニングされた以下が今も妥当か確認する:
  - confidence_score/rank (勝率集中スコア成分)
  - recommend_rank       (EV 閾値 EV>2.0 / 1.0-2.0 …)
  - race_concentration   (top2_share > 0.873 / 0.715)

検証軸: 各ランク/レベルの 1位(composite)馬の 勝率/複勝率/単ROI が
単調に並ぶか。並ばなければ「表示の妥当性なし=再較正 or 廃止」。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_verify_signals.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg2

from scripts.chihou_model_compare import scale_to_index  # noqa: E402
from scripts.train_chihou_prod_lgb import (  # noqa: E402
    FEATURES, PARAMS, BASE_QUERY, CHIHOU_V9_VERSION, prep,
)
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.indices.confidence import (  # noqa: E402
    calculate_race_confidence, calculate_recommend_rank,
)
from src.services.chihou_recommender import calc_race_concentration  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_signals")


def fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": CHIHOU_V9_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def tier_table(rows, key, order, title):
    """ランク/レベル別に 1位馬の勝率/複勝/単ROI/n を集計して表示。"""
    print(f"\n--- {title} (1位=composite最上位馬の成績) ---")
    print(f"  {'tier':<10}{'n':>6}{'勝率':>8}{'複勝率':>8}{'単ROI':>8}")
    df = pd.DataFrame(rows)
    for t in order:
        s = df[df[key] == t]
        if len(s) == 0:
            print(f"  {str(t):<10}{0:>6}{'-':>8}{'-':>8}{'-':>8}")
            continue
        win = (s["fp"] == 1).mean() * 100
        plc = (s["fp"] <= 3).mean() * 100
        roi = s.loc[s["fp"] == 1, "odds"].sum() / len(s)
        print(f"  {str(t):<10}{len(s):>6}{win:>7.1f}%{plc:>7.1f}%{roi:>8.3f}")


def main():
    train_s, train_e = "20230101", "20250630"
    test_s, test_e = "20250701", "20260605"

    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} dbname={os.getenv('DB_NAME')} "
           f"user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df_hist = fetch_hist(conn)
    tr = prep(conn, fetch(conn, train_s, train_e), df_hist)
    te = prep(conn, fetch(conn, test_s, test_e), df_hist)
    conn.close()
    logger.info("train=%d test=%d", len(tr), len(te))

    Xtr = tr[FEATURES].values.astype(np.float64)
    Xte = te[FEATURES].values.astype(np.float64)
    fp_tr = pd.to_numeric(tr["finish_position"], errors="coerce")
    m_t3 = lgb.train(dict(PARAMS, seed=0), lgb.Dataset(Xtr, (fp_tr <= 3).astype(int).values, feature_name=FEATURES), num_boost_round=400)
    m_win = lgb.train(dict(PARAMS, seed=0), lgb.Dataset(Xtr, (fp_tr == 1).astype(int).values, feature_name=FEATURES), num_boost_round=400)

    te = te.copy()
    raw_t3 = m_t3.predict(Xte)
    te["place_prob"] = np.clip(raw_t3, 0, 1)              # is_top3 較正(複勝確率)
    te["win_prob"] = np.clip(m_win.predict(Xte), 0, 1)   # is_win 較正(単勝確率)
    te["composite"] = scale_to_index(raw_t3, te["race_id"])
    te["fp"] = pd.to_numeric(te["finish_position"], errors="coerce")
    te["odds"] = pd.to_numeric(te["win_odds"], errors="coerce")

    # レースごとにシグナルを算出
    rows = []
    for rid, g in te.groupby("race_id"):
        g = g.sort_values("composite", ascending=False)
        comp = g["composite"].tolist()
        wprob = g["win_prob"].tolist()
        pprob = g["place_prob"].tolist()
        hc = len(g)
        conf = calculate_race_confidence(comp, hc, wprob)
        top = g.iloc[0]
        rr = calculate_recommend_rank(conf["score"], float(top["win_prob"]), float(top["odds"]) if pd.notna(top["odds"]) else None)
        conc = calc_race_concentration(pprob)
        rows.append({
            "race_id": rid,
            "fp": int(top["fp"]) if pd.notna(top["fp"]) else 99,
            "odds": float(top["odds"]) if pd.notna(top["odds"]) else 0.0,
            "top_is_fav": bool(pd.notna(top["odds"]) and float(top["odds"]) < 1.5),
            "conf_rank": conf["rank"],
            "conf_label": conf["label"],
            "recommend_rank": rr,
            "concentration": conc["confidence_level"],
            "top2_share": conc["top2_share"],
            "gap_1_2": conf["gap_1_2"],
            "win_prob_top": conf["win_prob_top"],
            "ev_top": float(top["win_prob"]) * float(top["odds"]) if pd.notna(top["odds"]) else None,
        })

    print("\n" + "=" * 70)
    print(f"表示シグナル妥当性検証  OOS test {test_s}-{test_e}  {len(rows)}レース")
    print("各シグナルの『1位馬の成績』が tier 順に単調なら妥当。新較正確率で算出。")
    print("=" * 70)

    tier_table(rows, "conf_rank", ["S", "A", "B", "C"], "① confidence_rank (レース信頼度)")
    tier_table(rows, "conf_label", ["HIGH", "MID", "LOW"], "② confidence_label")
    tier_table(rows, "recommend_rank", ["S", "A", "B", "C"], "③ recommend_rank (推奨度・EV連動)")
    tier_table(rows, "concentration", ["high", "medium", "low"], "④ race_concentration (複勝集中度)")

    # EV分布の確認（recommend_rank が壊れていないか）
    df = pd.DataFrame(rows)
    ev = df["ev_top"].dropna()
    print("\n--- ⑤ 1位馬の較正EV分布（recommend_rank閾値の妥当性確認） ---")
    print(f"  EV: 平均{ev.mean():.3f} 中央{ev.median():.3f} p90={ev.quantile(0.9):.3f} EV>1.0={((ev>1.0).mean()*100):.1f}% EV>2.0={((ev>2.0).mean()*100):.1f}%")
    print(f"  recommend_rank 分布: " + " ".join(f"{r}={(df['recommend_rank']==r).mean()*100:.0f}%" for r in ['S','A','B','C']))
    print(f"  win_prob_top: 平均{df['win_prob_top'].dropna().mean():.3f} 中央{df['win_prob_top'].dropna().median():.3f}")

    # race_concentration 再較正: top2_share 五分位 × 1位複勝率（旧基準 high≈76%/low≈57%復元用）
    print("\n--- ⑥ top2_share 分布と再較正（新較正place確率） ---")
    ts = df["top2_share"].dropna()
    print(f"  top2_share: min={ts.min():.3f} Q1={ts.quantile(.25):.3f} 中央{ts.median():.3f} Q3={ts.quantile(.75):.3f} max={ts.max():.3f}")
    dfx = df.dropna(subset=["top2_share"]).copy()
    dfx["q"] = pd.qcut(dfx["top2_share"], 5, labels=["Q1低","Q2","Q3","Q4","Q5高"])
    print(f"  {'五分位':<8}{'top2_share範囲':>18}{'1位複勝率':>10}{'1位勝率':>9}{'n':>6}")
    for q, gg in dfx.groupby("q", observed=True):
        print(f"  {str(q):<8}{gg['top2_share'].min():>8.3f}-{gg['top2_share'].max():.3f}{(gg['fp']<=3).mean()*100:>9.1f}%{(gg['fp']==1).mean()*100:>8.1f}%{len(gg):>6}")

    # 統一案セグメントの実績
    print("\n--- ⑦ 統一推奨セグメント（優先順）の1位馬実績 ---")
    print(f"  {'セグメント':<24}{'n':>6}{'勝率':>8}{'複勝率':>8}{'単ROI':>8}")
    sweet = df[(df["odds"] >= 10) & (df["odds"] < 30)]  # 近似(割安場条件は別途)
    fav = df[df["top_is_fav"]]
    confS = df[(df["conf_rank"] == "S") & (~df["top_is_fav"]) & ~((df["odds"] >= 10) & (df["odds"] < 30))]
    for name, s in [("断然本命(単<1.5)", fav), ("信頼軸(confS・非本命)", confS), ("中オッズ1位(10-30倍)", sweet)]:
        if len(s):
            print(f"  {name:<22}{len(s):>6}{(s['fp']==1).mean()*100:>7.1f}%{(s['fp']<=3).mean()*100:>7.1f}%{s.loc[s['fp']==1,'odds'].sum()/len(s):>8.3f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
