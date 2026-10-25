"""シュウのAI指数極(482) — どんなレースを選び、どこで当てているか（2026-10-25）。

台: netkeirin 一覧API（scripts/exp_hot/month2.jsonl = 08-01〜09-05 /
month_0906_1024.jsonl = 09-06〜10-24）を DB のレース（shu482_dump.py の出力）へ
date+場名+R で突き合わせる。買い目は使わない（一覧の 購入・払戻 だけ）。

使い方:
    .venv/bin/python scripts/exp_bet_review/shu482_profile.py DUMPDIR
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HOT = Path(__file__).resolve().parent.parent / "exp_hot"


def load_shu() -> pd.DataFrame:
    """482/428 の全商品（採点済み）。"""
    rows = []
    for f in ("month2.jsonl", "month_0906_1024.jsonl"):
        rows += [json.loads(x) for x in open(HOT / f, encoding="utf-8")]
    df = pd.DataFrame(rows)
    df = df[df.yid.isin([482, 428]) & df.payout.notna()].copy()
    df["venue"] = df.venue.replace({"伊東温泉": "伊東"})
    df["race_date"] = pd.to_datetime(df.date).dt.date
    return df.drop_duplicates(["yid", "gid"])


def race_features(d: str) -> pd.DataFrame:
    """レース単位の特徴（市場の集中度・当たり目の人気・モデルの軸の堅さ）。"""
    races = pd.read_pickle(f"{d}/races.pkl")
    races["race_date"] = pd.to_datetime(races.race_date).dt.date
    ent = pd.read_pickle(f"{d}/entries.pkl")
    tf = pd.read_pickle(f"{d}/tf_odds.pkl")
    tf = tf[tf.odds_value > 0].copy()
    tf["inv"] = 1.0 / tf.odds_value
    tf["rk"] = tf.groupby("race_key").odds_value.rank(method="first")
    tot = tf.groupby("race_key").inv.sum()
    s5 = tf[tf.rk <= 5].groupby("race_key").inv.sum() / tot
    s20 = tf[tf.rk <= 20].groupby("race_key").inv.sum() / tot

    e = ent[ent.finish_order.between(1, 3)].sort_values(["race_key", "finish_order", "frame_no"])
    top3 = e.groupby("race_key").frame_no.apply(lambda s: "-".join(map(str, s.iloc[:3])) if len(s) >= 3 else None)
    win = tf.merge(top3.rename("combination").reset_index(), on=["race_key", "combination"])
    win = win.set_index("race_key")[["odds_value", "rk"]].rename(columns={"odds_value": "win_odds", "rk": "win_pop"})

    ent = ent.copy()
    ent["p3"] = ent.pred_top3_pct.astype(float) / 100
    ent["pw"] = ent.pred_win_pct.astype(float) / 100
    g = ent.sort_values(["race_key", "p3"], ascending=[True, False]).groupby("race_key")
    axis_sum = g.p3.apply(lambda s: s.iloc[:2].sum())
    pw1 = ent.groupby("race_key").pw.max()

    def mark_agree(s):
        top2 = set(s.sort_values("p3", ascending=False).frame_no.iloc[:2])
        mk = set(s[s.prediction_mark.isin([1, 2])].frame_no)
        return len(top2 & mk)
    ov = ent.groupby("race_key")[["frame_no", "p3", "prediction_mark"]].apply(mark_agree)

    out = races.set_index("race_key").join(
        pd.DataFrame({"s5": s5, "s20": s20, "axis_sum": axis_sum, "pw1": pw1, "mark_ov": ov})
    ).join(win)
    return out.reset_index()


def main(d: str) -> None:
    """集計を表で出す。"""
    rf = race_features(d)
    shu = load_shu()
    m = shu.merge(rf, left_on=["race_date", "venue", "race_no"],
                  right_on=["race_date", "venue_name", "race_no"], how="left")
    print("突合率", m.groupby("yid").race_key.apply(lambda s: s.notna().mean()).round(3).to_dict())
    m = m[m.race_key.notna()]
    days = set(m[m.yid == 482].race_date)
    uni = rf[rf.race_date.isin(days) & rf.win_odds.notna()].copy()
    sel = set(m[m.yid == 482].race_key)
    uni["sel482"] = uni.race_key.isin(sel)
    s428 = set(m[m.yid == 428].race_key)
    uni["sel428"] = uni.race_key.isin(s428)
    print(f"\n482 の日の全レース {len(uni)} / 428 が出した {uni.sel428.sum()} / 482 が出した {uni.sel482.sum()}")

    def desc(x):
        return pd.Series({
            "n": len(x), "車数7": (x.n_entries == 7).mean(), "車数9": (x.n_entries == 9).mean(),
            "上位5目の市場シェア": x.s5.median(), "最低三連単": x.tf_min.median(),
            "当たり目倍率中央": x.win_odds.median(), "当たり目100倍+": (x.win_odds >= 100).mean(),
            "当たり目人気中央": x.win_pop.median(), "軸2車3着内率和": x.axis_sum.median(),
            "1着率最大": x.pw1.median(), "モデル上位2=◎○の重なり": x.mark_ov.mean()})
    print("\n## 選んだレース vs 選ばなかったレース（482 の日・428 も出したレース内）")
    u = uni[uni.sel428]
    print(pd.DataFrame({"482選": desc(u[u.sel482]), "482非選": desc(u[~u.sel482])}).round(3).to_string())

    print("\n## 482 の採用率（上位5目の市場シェア 5分位・428 出走レース内）")
    u = u.copy()
    u["q"] = pd.qcut(u.s5, 5, labels=False)
    print(u.groupby("q").agg(n=("sel482", "size"), 採用率=("sel482", "mean"),
                             s5下=("s5", "min"), s5上=("s5", "max"),
                             当たり100倍=("win_odds", lambda s: (s >= 100).mean())).round(3).to_string())
    print("\n## 482 の採用率（車数別）")
    print(u.groupby("n_entries").agg(n=("sel482", "size"), 採用率=("sel482", "mean")).round(3).to_string())
    print("\n## 482 の採用率（種別）")
    print(u.groupby("race_type").agg(n=("sel482", "size"), 採用率=("sel482", "mean")).query("n>=40")
          .sort_values("採用率").round(3).to_string())

    p = m[m.yid == 482].copy()
    p["hit"] = p.payout > 0
    p["stake_hit"] = np.where(p.hit, p.payout / p.win_odds / 100 * 100, np.nan)  # 円/点
    print("\n## 482 の成績（市場シェア 5分位）")
    p["q"] = pd.cut(p.s5, [0] + list(u.s5.quantile([.2, .4, .6, .8])) + [1], labels=False)
    print(p.groupby("q").agg(n=("hit", "size"), 的中=("hit", "mean"),
                             ROI=("payout", lambda s: s.sum() / p.loc[s.index, "bet"].sum()),
                             十万=("payout", lambda s: (s >= 1e5).sum()),
                             五十万=("payout", lambda s: (s >= 5e5).sum())).round(3).to_string())
    h = p[p.hit]
    print("\n## 482 の当たり（n=%d）: 当たり目の倍率と1点賭け金" % len(h))
    print(h.win_odds.describe(percentiles=[.1, .25, .5, .75, .9]).round(1).to_string())
    print("1点賭け金(=払戻/倍率)", h.stake_hit.describe(percentiles=[.1, .5, .9]).round(0).to_string())
    hh = h[h.payout >= 1e5]
    print("\n## 10万+ (n=%d) 当たり目倍率 / 人気 / 1点" % len(hh))
    print(hh[["race_date", "venue", "race_no", "race_name", "n_entries", "payout", "win_odds", "win_pop",
              "stake_hit", "s5", "axis_sum"]].sort_values("payout", ascending=False).head(30).to_string())
    print("\n## 期間別")
    p["half"] = np.where(p.race_date < pd.Timestamp("2026-09-06").date(), "08-01〜09-05", "09-06〜10-24")
    print(p.groupby("half").agg(n=("hit", "size"), 的中=("hit", "mean"),
                                ROI=("payout", lambda s: s.sum() / p.loc[s.index, "bet"].sum()),
                                十万=("payout", lambda s: (s >= 1e5).sum()),
                                五十万=("payout", lambda s: (s >= 5e5).sum()),
                                百万=("payout", lambda s: (s >= 1e6).sum())).round(3).to_string())
    m.to_pickle(f"{d}/shu_merged.pkl")
    uni.to_pickle(f"{d}/uni.pkl")


if __name__ == "__main__":
    main(sys.argv[1])
