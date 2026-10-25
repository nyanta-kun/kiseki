"""シュウ極(482) と型ラボの高額系を同じ物差しで並べる（2026-10-25）。

物差し（10/21 レビューと同じ作法）:
- 表示的中 = 払戻 > 購入（ガミを除く。netkeirin の表示と同じ）
- 回収率の 95%CI は開催日単位ブートストラップ 2,000回
- 「最大3本を除く」回収率を必ず併記する
- 高額の頻度は 1商品あたり（％）と 1日あたりの両方

使い方:
    .venv/bin/python scripts/exp_bet_review/shu482_compare.py DUMPDIR
（先に shu482_dump.py / shu482_dump_paper.py / shu482_profile.py を回す）
"""
import sys

import numpy as np
import pandas as pd

RNG = np.random.default_rng(20261025)


def summarize(df: pd.DataFrame, label: str) -> dict:
    """1商品=1行（bet, payout, day）から指標を出す。"""
    df = df[df.bet > 0]
    n, days = len(df), df.day.nunique()
    bet, pay = df.bet.sum(), df.payout.sum()
    top3 = np.sort(df.payout.values)[-3:].sum()
    g = df.groupby("day")[["bet", "payout"]].sum()
    idx = RNG.integers(0, len(g), size=(2000, len(g)))
    bs = g.payout.values[idx].sum(1) / g.bet.values[idx].sum(1)
    hit = df.payout > df.bet
    return {
        "商品": label, "件数": n, "日数": days, "件/日": round(n / days, 1),
        "表示的中%": round(100 * hit.mean(), 2),
        "回収率%": round(100 * pay / bet, 1),
        "CI下": round(100 * np.percentile(bs, 2.5), 1), "CI上": round(100 * np.percentile(bs, 97.5), 1),
        "最大3本除く%": round(100 * (pay - top3) / bet, 1),
        "的中時中央": int(df.payout[hit].median()) if hit.any() else 0,
        "10万+%": round(100 * (df.payout >= 1e5).mean(), 2),
        "50万+%": round(100 * (df.payout >= 5e5).mean(), 3),
        "50万+/日": round((df.payout >= 5e5).sum() / days, 3),
        "100万+": int((df.payout >= 1e6).sum()),
        "最大": int(df.payout.max()),
    }


def main(d: str) -> None:
    """表を出す。"""
    m = pd.read_pickle(f"{d}/shu_merged.pkl")
    s = m[m.yid == 482].copy()
    s["day"] = s.race_date
    rows = [summarize(s, "シュウ極 482（08-01〜10-24）"),
            summarize(s[s.race_date < pd.Timestamp("2026-09-06").date()], "  482 08-01〜09-05"),
            summarize(s[s.race_date >= pd.Timestamp("2026-09-06").date()], "  482 09-06〜10-24")]
    s4 = m[m.yid == 428].copy()
    s4["day"] = s4.race_date
    rows.append(summarize(s4, "シュウ本体 428（同）"))

    p = pd.read_pickle(f"{d}/paper.pkl")
    for col in ("budget", "payout", "void_refund"):
        p[col] = p[col].astype(float).fillna(0)
    p["bet"] = p.budget - p.void_refund
    p["day"] = p.race_date
    p["fam"] = p.plan_key.str.extract(r"_(big|sign|hit)$")[0].fillna(p.plan_key)
    p["win"] = np.where(pd.to_datetime(p.race_date) < "2026-01-01", "2025", "2026-01〜08")
    print("paper rule_version 種類:", p.groupby("fam").rule_version.nunique().to_dict())
    for fam in ("big", "sign", "A_ana", "hit"):
        for w in ("2025", "2026-01〜08"):
            x = p[(p.fam == fam) & (p.win == w)]
            if len(x):
                rows.append(summarize(x, f"型ラボ {fam} paper {w}"))

    t = pd.read_pickle(f"{d}/tl.pkl")
    t = t[t.settled].copy()
    for col in ("budget", "payout", "void_refund"):
        t[col] = t[col].astype(float).fillna(0)
    t["bet"] = t.budget - t.void_refund
    t["day"] = t.race_date
    t["fam"] = t.plan_key.str.extract(r"_(big|sign|hit)$")[0].fillna(t.plan_key)
    for fam in ("big", "sign", "A_ana", "hit", "T_upset"):
        x = t[t.fam == fam]
        if len(x):
            rows.append(summarize(x, f"型ラボ {fam} live 09-06〜10-24"))
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    print(out.to_string(index=False))
    out.to_csv(f"{d}/compare.csv", index=False)

    print("\n## 当たり目の市場人気順位（確定三連単オッズで何番人気の目が当たったか）")
    s["hit"] = s.payout > s.bet
    bins = [0, 5, 10, 20, 50, 100, 1000]
    lab = ["1-5", "6-10", "11-20", "21-50", "51-100", "101+"]

    def popdist(x, label):
        c = pd.cut(x, bins, labels=lab).value_counts(normalize=True).reindex(lab).round(3)
        return c.rename(f"{label}(n={len(x)})")
    tab = [popdist(s[s.hit].win_pop, "482 的中"),
           popdist(s[s.payout >= 1e5].win_pop, "482 10万+")]
    pb = p[(p.payout > p.bet)]
    for fam in ("big", "sign", "A_ana"):
        tab.append(popdist(pb[pb.fam == fam].win_pop, f"型ラボ{fam} 的中"))
        tab.append(popdist(p[(p.fam == fam) & (p.payout >= 1e5)].win_pop, f"型ラボ{fam} 10万+"))
    print(pd.concat(tab, axis=1).to_string())

    print("\n## 当たり目倍率と1点賭け金（的中のみ）")
    s["stake"] = s.payout / s.win_odds
    pb = pb.assign(stake=pb.payout / pb.win_tf_odds.astype(float))
    for lab2, x in [("482", s[s.hit]), *[(f"型ラボ{f}", pb[pb.fam == f]) for f in ("big", "sign", "A_ana")]]:
        odds = x.win_odds if "win_odds" in x else x.win_tf_odds.astype(float)
        print(f"{lab2:10s} 倍率 中央 {odds.median():7.1f} p90 {odds.quantile(.9):7.1f} / "
              f"1点 中央 {x.stake.median():7.0f} / 点数中央 "
              f"{x.n_legs.median() if 'n_legs' in x else float('nan')}")


if __name__ == "__main__":
    main(sys.argv[1])
