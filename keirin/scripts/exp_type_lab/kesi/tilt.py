#!/usr/bin/env python3
"""賭け金の傾けの歴史的検査（事前登録 docs/type_lab/edge_tilt_2026_10_28.md）。

    .venv/bin/python scripts/exp_type_lab/kesi/tilt.py /tmp/claude-501/kesi

入力: kesi_feats.pkl / morning.csv / legs.csv / snap_time.csv / payoffs.csv（psql で書き出し・git 管理外）
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as R  # noqa: E402

LO, HI = 0.5, 2.0
N_BOOT = 2000


def rider_g(d: Path) -> pd.DataFrame:
    df = pd.read_pickle(d / "kesi_feats.pkl")
    df = df[df.race_date >= "2024-07-01"].reset_index(drop=True)
    am = pd.read_csv(d / "morning.csv", dtype={"race_key": str})
    m, _ = R.fit(df, R.BASE_FEATS, "2024-07-01", "2025-12-31", "2025-10-01")
    t = df[df.race_date >= "2026-06-01"].merge(am, on=["race_key", "frame_no"], how="inner")
    u = t.copy()
    u["m3"] = u["m3_am"]; u["logit_m3"] = R.logit(u.m3)
    u["m3_rank"] = u.groupby("race_key")["m3"].rank(ascending=False, method="min")
    t["g"] = R.predict(m, u, R.BASE_FEATS) / t.m3_am.clip(0.005, 0.995)
    return t[["race_key", "frame_no", "g", "m3_am", "m3", "race_date"]]


def tilt(stakes: list[int], edges: list[float]) -> list[int]:
    """stake×edge を元の合計へ比例配分。各目を元の [LO, HI] 倍に収め、100円単位・各目100円以上・合計不変。"""
    total = sum(stakes)
    lo = [max(100, math.ceil(s * LO / 100) * 100) for s in stakes]
    hi = [max(lo[i], math.floor(s * HI / 100) * 100) for i, s in enumerate(stakes)]
    w = [s * e for s, e in zip(stakes, edges)]
    free = list(range(len(stakes)))
    x = [0.0] * len(stakes)
    budget = float(total)
    fixed = set()
    for _ in range(len(stakes) + 1):           # 水位合わせ: はみ出しを端に止めて残りを再配分
        ws = sum(w[i] for i in free)
        if ws <= 0:
            break
        changed = False
        for i in free:
            x[i] = budget * w[i] / ws
        for i in list(free):
            if x[i] < lo[i]:
                x[i] = lo[i]; fixed.add(i); changed = True
            elif x[i] > hi[i]:
                x[i] = hi[i]; fixed.add(i); changed = True
        if not changed:
            break
        free = [i for i in range(len(stakes)) if i not in fixed]
        budget = total - sum(x[i] for i in fixed)
    units = [int(v // 100) for v in x]
    rem = total // 100 - sum(units)
    order = sorted(range(len(x)), key=lambda i: -(x[i] / 100 - units[i]))
    k = 0
    while rem != 0 and k < 10 * len(x) + 10:
        i = order[k % len(order)]
        if rem > 0 and (units[i] + 1) * 100 <= hi[i]:
            units[i] += 1; rem -= 1
        elif rem < 0 and (units[i] - 1) * 100 >= lo[i]:
            units[i] -= 1; rem += 1
        k += 1
    out = [u * 100 for u in units]
    if sum(out) != total:                      # 端に詰まったら元に戻す（記録する）
        return stakes
    return out


def cars(combo: str) -> list[int]:
    return [int(c) for c in combo.replace("=", "-").split("-")]


def main():
    d = Path(sys.argv[1])
    g = rider_g(d)
    G = {(r.race_key, int(r.frame_no)): float(r.g) for r in g.itertuples()}
    legs = pd.read_csv(d / "legs.csv", dtype={"race_key": str})
    st = pd.read_csv(d / "snap_time.csv", dtype={"race_key": str})
    pay = pd.read_csv(d / "payoffs.csv", dtype={"race_key": str})
    P = defaultdict(float)
    for r in pay.itertuples():
        P[(r.race_key, r.bet_type, r.combination)] += float(r.payoff)
    n0 = legs.groupby(["race_key", "plan_key"]).ngroups
    legs = legs.merge(st, on="race_key", how="left")
    ok_time = legs.t1.notna() & (legs.t1 < legs.generated_at)
    legs = legs[ok_time]
    settled = set(pay.race_key)
    legs = legs[legs.race_key.isin(settled)]
    print(f"商品 {n0} → 朝のオッズが生成より前 かつ 払戻あり {legs.groupby(['race_key', 'plan_key']).ngroups}", flush=True)

    recs, leg_rows, miss = [], [], 0
    for (rk, pk), grp in legs.groupby(["race_key", "plan_key"]):
        cs = [cars(c) for c in grp.combo]
        try:
            edges = [G[(rk, a)] * G[(rk, b)] * G[(rk, c)] for a, b, c in cs]
        except KeyError:
            miss += 1
            continue
        bt = grp.bet_type.iloc[0]
        stakes = [int(s) for s in grp.stake]
        new = tilt(stakes, edges)
        keys = []
        for a, b, c in cs:
            if bt == "trio":
                keys.append((rk, "trio", "-".join(map(str, sorted((a, b, c))))))
            else:
                keys.append((rk, "trifecta", f"{a}-{b}-{c}"))
        po = [P.get(k, 0.0) for k in keys]
        inv = sum(stakes)
        p0 = sum(s * p / 100 for s, p in zip(stakes, po))
        p1 = sum(s * p / 100 for s, p in zip(new, po))
        recs.append(dict(race_key=rk, day=rk[:8], plan=pk, pub=grp.published_at.notna().any(), inv=inv,
                         p0=p0, p1=p1, hit=any(p > 0 for p in po), moved=sum(abs(a - b) for a, b in zip(stakes, new)) / 2,
                         pedge=sum(s * e for s, e in zip(stakes, edges)) / inv))
        for k, (s0, s1, e, p) in enumerate(zip(stakes, new, edges, po)):
            leg_rows.append(dict(edge=e, s0=s0, s1=s1, pay100=p, pub=recs[-1]["pub"]))
    df = pd.DataFrame(recs); L = pd.DataFrame(leg_rows)
    print(f"選手の係数が欠けて除外 {miss} 商品。傾けで動いた賭け金 平均 {df.moved.mean():,.0f}円/1万", flush=True)
    r0 = df[df.pub].iloc[0]
    print("目視:", r0.race_key, r0.plan, legs[(legs.race_key == r0.race_key) & (legs.plan_key == r0.plan)][["combo", "stake"]].values.tolist())

    rng = np.random.default_rng(20261028)
    for lab, s in (("公開（判定）", df[df.pub]), ("全 live（併記）", df)):
        days = sorted(s.day.unique())
        a = s.groupby("day").agg(inv=("inv", "sum"), p0=("p0", "sum"), p1=("p1", "sum"),
                                 d0=("p0", lambda x: 0), n=("inv", "size")).reindex(days)
        s = s.assign(disp0=s.p0 > s.inv, disp1=s.p1 > s.inv)
        dd = s.groupby("day").agg(disp0=("disp0", "sum"), disp1=("disp1", "sum")).reindex(days)
        roi0 = s.p0.sum() / s.inv.sum() * 100; roi1 = s.p1.sum() / s.inv.sum() * 100
        ex0 = (s.p0.sum() - s.p0.nlargest(5).sum()) / s.inv.sum() * 100
        ex1 = (s.p1.sum() - s.p1.nlargest(5).sum()) / s.inv.sum() * 100
        dh = (s.disp1.mean() - s.disp0.mean()) * 100
        bd, bh = [], []
        for _ in range(N_BOOT):
            ix = rng.integers(0, len(days), len(days))
            bd.append((a.p1.values[ix].sum() - a.p0.values[ix].sum()) / a.inv.values[ix].sum() * 100)
            bh.append((dd.disp1.values[ix].sum() - dd.disp0.values[ix].sum()) / a.n.values[ix].sum() * 100)
        print(f"[{lab}] {len(s)}件 {len(days)}日  生の的中 {s.hit.mean() * 100:.2f}%（両方同じ）")
        print(f"   回収 本番 {roi0:.2f}% → 傾け {roi1:.2f}%  差 {roi1 - roi0:+.2f}pt [{np.percentile(bd, 2.5):+.2f}, {np.percentile(bd, 97.5):+.2f}]")
        print(f"   上位5除き 本番 {ex0:.2f}% → 傾け {ex1:.2f}%  差 {ex1 - ex0:+.2f}pt")
        print(f"   表示的中 本番 {s.disp0.mean() * 100:.2f}% → 傾け {s.disp1.mean() * 100:.2f}%  差 {dh:+.2f}pt "
              f"[{np.percentile(bh, 2.5):+.2f}, {np.percentile(bh, 97.5):+.2f}]  ガミ 本番 {(s.hit & ~s.disp0).sum()} → 傾け {(s.hit & ~s.disp1).sum()}")
        if lab.startswith("公開"):
            ok = (roi1 - roi0 >= 2.0) and (ex1 - ex0 > 0) and (dh >= -0.5)
            print(f"   → 判定 {'前向きへ進める' if ok else '不通過'}（回収差≥+2.0 / 上位5除き差>0 / 表示的中差≥−0.5）")
            for pk, gp in s.groupby("plan"):
                print(f"     {pk:<8} {len(gp):>5}件  回収 {gp.p0.sum() / gp.inv.sum() * 100:6.1f} → {gp.p1.sum() / gp.inv.sum() * 100:6.1f}  "
                      f"表示的中 {gp.disp0.mean() * 100:5.1f} → {gp.disp1.mean() * 100:5.1f}")
    # 探索（判定外）: 商品ごとの賭け金加重 edge の五分位で回収が分かれるか（レース・商品の間の差）
    for lab, s in (("公開", df[df.pub]), ("全 live", df)):
        s = s.assign(q=pd.qcut(s.pedge.rank(method="first"), 5, labels=False))
        t = s.groupby("q").agg(edge=("pedge", "mean"), n=("inv", "size"), hit=("hit", "mean"),
                               roi=("p0", "sum"), inv=("inv", "sum"))
        t["roi"] = t.roi / t.inv * 100; t["hit"] *= 100
        print(f"商品の edge 五分位（{lab}・本番の賭け金のまま）:")
        print(t.drop(columns="inv").round(3).to_string())
    # 併記: 目の edge 五分位ごとの 1目あたり回収（最終オッズ＝公式払戻）
    L["q"] = pd.qcut(L.edge.rank(method="first"), 5, labels=False)
    t = L.groupby("q").agg(edge=("edge", "mean"), n=("edge", "size"), roi=("pay100", lambda x: x.sum() / (100 * len(x)) * 100),
                           hit=("pay100", lambda x: (x > 0).mean() * 100))
    print("目の edge 五分位（全 live の目・1目100円あたり）:")
    print(t.round(3).to_string())
    g["gq"] = pd.qcut(g.g.rank(method="first"), 5, labels=False)
    print("選手の g 五分位: 朝m3 → 最終m3 → 実際は feats に無いので m3 の動きだけ")
    print(g.groupby("gq").agg(g=("g", "mean"), m3_am=("m3_am", "mean"), m3_fin=("m3", "mean")).round(3).to_string())


if __name__ == "__main__":
    main()
