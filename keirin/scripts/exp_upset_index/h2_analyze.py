#!/usr/bin/env python3
"""仮説H2: 100倍超えになりそうなレースだけを 10点以内で買う（事前登録 追記 2026-10-03）。

レース選定量:
    mkt_p100 : 最終オッズの市場確率で「確定配当 >= 100倍」となる確率（直前購入）
    mdl_p100 : モデル PL 確率で同じ量（帯の定義には最終オッズを使う）
    mdl_ent  : vintage 1着率のエントロピー（オッズ完全不使用・朝入稿の代理）
買い目（券種ごとに オッズ>=100 の組から k 点）:
    cheap / model / edge、参考 top（帯制限なしのモデル上位 k 点）
配分: equal（均等）/ dutch（1/odds 比例）。1レース 10,000円・100円単位。

同着は `finish_order, frame_no` の順で代表1通りだけを当たりとする（同着は 0.2〜0.5%）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_upset_index/h2_analyze.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(__file__).resolve().parents[2] / "data" / "exp_upset"
WINDOWS = {"explore_2025": ("2025-01-01", "2025-12-31"), "confirm_2026": ("2026-01-01", "2026-09-30")}
BUDGET = 10_000
BAND = 100.0
KS = (5, 10)
RULES = ("cheap", "model", "edge", "top")
STAKINGS = ("equal", "dutch")
QUANTS = (0.10, 0.20, 0.30, 0.50)
N_BOOT = 2000
N_SEED = 20
RNG = np.random.default_rng(20261003)


def load_entries() -> pd.DataFrame:
    """出走表（段0 の抽出物）。"""
    e = pd.concat([pd.read_pickle(f) for f in sorted(D.glob("s0_entries_*.pkl"))], ignore_index=True)
    return e


def load_odds(kind: str) -> pd.DataFrame:
    """オッズ。表記の揺れ（'=' / '-'）を正規化し、両表記が併存するレースを除く。"""
    pat = "s0_trio_*.pkl" if kind == "trio" else "h2_tf_*.pkl"
    t = pd.concat([pd.read_pickle(f) for f in sorted(D.glob(pat))], ignore_index=True)
    sep = "=" if kind == "trio" else "-"
    other = "-" if kind == "trio" else "="
    t["alt"] = t.combination.str.contains(other, regex=False)
    mixed = t.groupby("race_key").alt.nunique()
    t = t[~t.race_key.isin(mixed[mixed > 1].index)]
    t["combination"] = t.combination.str.replace(other, sep, regex=False)
    t = t[t.odds_value > 0].drop(columns="alt")
    parts = t.combination.str.split(sep, expand=True).astype("int8")
    t["a"], t["b"], t["c"] = parts[0], parts[1], parts[2]
    return t


def race_table(e: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """レース単位の結果と、選手の正規化前1着率。"""
    top = e[e.finish_order.between(1, 3)].sort_values(["race_key", "finish_order", "frame_no"])
    cnt = top.groupby("race_key").size()
    top = top[top.race_key.isin(cnt[cnt >= 3].index)].groupby("race_key").head(3)
    res = top.groupby("race_key").frame_no.apply(list).rename("top3").reset_index()
    meta = e.groupby("race_key").agg(race_date=("race_date", "first"), n=("n_entries", "first")).reset_index()
    res = res.merge(meta, on="race_key")
    pw = e[["race_key", "frame_no", "pred_win_pct"]].copy()
    pw["pw"] = pw.pred_win_pct.astype(float)
    return res, pw[["race_key", "frame_no", "pw"]]


def combos(kind: str, res: pd.DataFrame, pw: pd.DataFrame) -> pd.DataFrame:
    """組ごとの 市場確率 q・モデル PL 確率 pm・当たり。"""
    t = load_odds(kind)
    t = t[t.race_key.isin(res.race_key)]
    t["q"] = 1.0 / t.odds_value
    t["q"] = t.q / t.groupby("race_key").q.transform("sum")
    # 盤面に載っている選手だけで 1着率を正規化（欠車を除く）
    on_board = pd.concat([t[["race_key", c]].rename(columns={c: "frame_no"}) for c in "abc"]).drop_duplicates()
    p = on_board.merge(pw, on=["race_key", "frame_no"], how="left")
    bad = p[p.pw.isna()].race_key.unique()
    p = p[~p.race_key.isin(bad)]
    p["pw"] = p.pw / p.groupby("race_key").pw.transform("sum")
    t = t[~t.race_key.isin(bad)]
    key = p.set_index(["race_key", "frame_no"]).pw
    for c in "abc":
        t["p" + c] = key.reindex(pd.MultiIndex.from_arrays([t.race_key, t[c].astype(int)])).to_numpy()

    def pl(x: pd.Series, y: pd.Series, z: pd.Series) -> pd.Series:
        return x * y / (1 - x) * z / (1 - x - y)

    pa, pb, pc = t.pa, t.pb, t.pc
    if kind == "trifecta":
        t["pm"] = pl(pa, pb, pc)
    else:
        t["pm"] = pl(pa, pb, pc) + pl(pa, pc, pb) + pl(pb, pa, pc) + pl(pb, pc, pa) + pl(pc, pa, pb) + pl(pc, pb, pa)
    t["pm"] = t.pm / t.groupby("race_key").pm.transform("sum")
    r = res.set_index("race_key")
    tops = r.top3.reindex(t.race_key)
    t1 = np.array([x[0] for x in tops]); t2 = np.array([x[1] for x in tops]); t3 = np.array([x[2] for x in tops])
    a, b, c = t.a.to_numpy(), t.b.to_numpy(), t.c.to_numpy()
    if kind == "trifecta":
        t["hit"] = (a == t1) & (b == t2) & (c == t3)
    else:
        s1 = np.sort(np.stack([t1, t2, t3], 1), 1)
        s2 = np.sort(np.stack([a, b, c], 1), 1)
        t["hit"] = (s1 == s2).all(1)
    return t.drop(columns=["pa", "pb", "pc", "combination"])


def race_signals(t: pd.DataFrame, pw: pd.DataFrame) -> pd.DataFrame:
    """レース選定量。"""
    band = t.odds_value >= BAND
    g = pd.DataFrame({
        "mkt_p100": t.q.where(band, 0).groupby(t.race_key).sum(),
        "mdl_p100": t.pm.where(band, 0).groupby(t.race_key).sum(),
    })
    p = pw.dropna().copy()
    p["pw"] = p.pw / p.groupby("race_key").pw.transform("sum")
    ent = (-(p.pw * np.log(p.pw.clip(1e-9)))).groupby(p.race_key).sum()
    g["mdl_ent"] = ent.reindex(g.index)
    g["actual100"] = t[t.hit].set_index("race_key").odds_value.reindex(g.index) >= BAND
    return g


def race_results(t: pd.DataFrame) -> pd.DataFrame:
    """全レース × (rule, k, staking) の 投資・払戻。"""
    t = t.copy()
    t["edge"] = t.pm / t.q
    out = []
    for rule in RULES:
        cand = t if rule == "top" else t[t.odds_value >= BAND]
        col = {"cheap": "q", "model": "pm", "edge": "edge", "top": "pm"}[rule]
        cand = cand.sort_values(["race_key", col], ascending=[True, False])
        cand = cand.assign(rk=cand.groupby("race_key").cumcount())
        for k in KS:
            sub = cand[cand.rk < k]
            sub = sub.assign(inv=1.0 / sub.odds_value)
            npts = sub.groupby("race_key").odds_value.transform("size")
            for st in STAKINGS:
                if st == "equal":
                    w = 1.0 / npts
                else:
                    w = sub.inv / sub.groupby("race_key").inv.transform("sum")
                stake = np.maximum(np.floor(BUDGET * w / 100) * 100, 100)
                pay = stake * sub.odds_value * sub.hit
                df = pd.DataFrame({"race_key": sub.race_key, "bet": stake, "pay": pay, "hit": sub.hit.astype(int)})
                df = df.groupby("race_key", sort=False).agg(bet=("bet", "sum"), pay=("pay", "sum"),
                                                            hit=("hit", "max")).reset_index()
                df["rule"], df["k"], df["staking"] = rule, k, st
                out.append(df)
    return pd.concat(out, ignore_index=True)


def summarize(sel: pd.DataFrame, rand_sets: list[pd.DataFrame] | None) -> dict:
    """回収率・最高配当除き・日次ブートストラップ CI・無作為対照との差。"""
    g = sel.groupby("race_date").agg(b=("bet", "sum"), p=("pay", "sum"))
    days = g.index
    b, p = g.b.to_numpy(), g.p.to_numpy()
    top_day = sel.loc[sel.pay.idxmax(), "race_date"] if sel.pay.max() > 0 else None
    p_ex = p.copy()
    if top_day is not None:
        p_ex[list(days).index(top_day)] -= sel.pay.max()
    idx = RNG.integers(0, len(days), size=(N_BOOT, len(days)))
    roi_bs = p[idx].sum(1) / b[idx].sum(1)
    roi_ex_bs = p_ex[idx].sum(1) / b[idx].sum(1)
    d = dict(races=len(sel), per_day=len(sel) / len(days), hit=sel.hit.mean(),
             roi=p.sum() / b.sum(), roi_ex=p_ex.sum() / b.sum(),
             roi_ex_lo=np.percentile(roi_ex_bs, 2.5),
             roi_lo=np.percentile(roi_bs, 2.5), roi_hi=np.percentile(roi_bs, 97.5),
             pay_med=float(np.median(sel.pay[sel.hit == 1])) if sel.hit.any() else 0.0)
    if rand_sets:
        rb = np.stack([r.groupby("race_date").bet.sum().reindex(days, fill_value=0).to_numpy() for r in rand_sets])
        rp = np.stack([r.groupby("race_date").pay.sum().reindex(days, fill_value=0).to_numpy() for r in rand_sets])
        # 無作為側は選定側と同じ日の集合で比べる（同じ日をリサンプル）
        r_roi = np.median(rp[:, idx].sum(2) / np.maximum(rb[:, idx].sum(2), 1), axis=0)
        diff = roi_bs - r_roi
        d.update(rand_roi=float(np.median(rp.sum(1) / rb.sum(1))),
                 diff_lo=np.percentile(diff, 2.5), diff_hi=np.percentile(diff, 97.5))
    return d


def main() -> None:
    """全構成を集計して表で出す。"""
    e = load_entries()
    res, pw = race_table(e)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    for kind in (sys.argv[1:] or ["trio", "trifecta"]):
        t = combos(kind, res, pw)
        sig = race_signals(t, pw)
        rr = race_results(t).merge(res[["race_key", "race_date", "n"]], on="race_key")
        rr = rr.merge(sig, left_on="race_key", right_index=True)
        rows = []
        for n in (7, 9):
            base = rr[rr.n == n]
            ex = base[base.race_date.between(*WINDOWS["explore_2025"])]
            ex1 = ex.drop_duplicates("race_key")
            print(f"\n##### {kind} {n}車: 実際に100倍以上で決着した率 "
                  f"2025={ex1.actual100.mean():.3f} / "
                  f"2026={base[base.race_date.between(*WINDOWS['confirm_2026'])].drop_duplicates('race_key').actual100.mean():.3f}")
            for metric in ("none", "mkt_p100", "mdl_p100", "mdl_ent"):
                for qn in (QUANTS if metric != "none" else (1.0,)):
                    thr = None if metric == "none" else ex1[metric].quantile(1 - qn)
                    for w, (a, b_) in WINDOWS.items():
                        win = base[base.race_date.between(a, b_)]
                        races = win.drop_duplicates("race_key")
                        chosen = races if thr is None else races[races[metric] >= thr]
                        sel_keys = set(chosen.race_key)
                        n_sel = len(sel_keys)
                        if n_sel == 0:
                            continue
                        all_keys = races.race_key.to_numpy()
                        rand_keys = [set(RNG.choice(all_keys, n_sel, replace=False)) for _ in range(N_SEED)] \
                            if thr is not None else None
                        # 選定で見ても実際に100倍決着が増えているか
                        hit100 = chosen.actual100.mean()
                        for (rule, k, st), g in win.groupby(["rule", "k", "staking"]):
                            sel = g[g.race_key.isin(sel_keys)]
                            rs = [g[g.race_key.isin(s)] for s in rand_keys] if rand_keys else None
                            d = summarize(sel, rs)
                            rows.append(dict(kind=kind, cars=n, metric=metric, q=qn, win=w, rule=rule, k=k,
                                             staking=st, act100=hit100, **d))
        df = pd.DataFrame(rows)
        df.to_pickle(D / f"h2_{kind}.pkl")
        show = df[df.staking == "dutch"]
        cols = ["cars", "metric", "q", "win", "rule", "k", "act100", "per_day", "hit", "roi", "roi_ex",
                "roi_ex_lo", "rand_roi", "diff_lo", "diff_hi", "pay_med"]
        print(show[cols].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
