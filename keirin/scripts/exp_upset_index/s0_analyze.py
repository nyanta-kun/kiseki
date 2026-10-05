#!/usr/bin/env python3
"""段0: 選手単位の市場の歪み（事前登録 docs/upset_index/PREREG_2026_10_02.md）。

三連複の最終オッズから選手ごとの市場3着内率 m3 を逆算し、
十分位ごとに「実3着内率 ÷ m3」を測る。開催日単位ブートストラップで 95%CI。

    p_combo = (1/odds) / Σ(1/odds)        （控除を除いた市場確率）
    m3_i    = Σ_{combo ∋ i} p_combo       （Σ_i m3_i = 3）

候補指標:
    mkt   : m3 自身（人気-穴バイアスの確認）
    ratio : p3n / m3   （p3n = vintage pred_top3_pct をレース内で Σ=3 に正規化）

母集団: 3着までが確定したレース（finish_order 1,2,3 が揃う）で、三連複オッズに
現れる選手（＝欠車でない）。finish_order=0 でオッズがある選手（失格・落車）は着外。

補足（中止判定には使わない）: 組単位の較正。各レースの全組を
モデル PL 確率 / 市場確率 の比で十分位に分け、実的中 ÷ 市場確率 を出す。
組の ROI ≈ 0.743 × (実/市場) なので、BOX が壁を越えるには組で 1.0 超、
回収率100% には約 1.35 超が要る。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_upset_index/s0_analyze.py
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(__file__).resolve().parents[2] / "data" / "exp_upset"
WINDOWS = {"explore_2025": ("2025-01-01", "2025-12-31"), "confirm_2026": ("2026-01-01", "2026-09-30")}
N_BOOT = 2000
RNG = np.random.default_rng(20261002)


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    """抽出済みの月次 pickle を全部読む。"""
    e = pd.concat([pd.read_pickle(f) for f in sorted(D.glob("s0_entries_*.pkl"))], ignore_index=True)
    t = pd.concat([pd.read_pickle(f) for f in sorted(D.glob("s0_trio_*.pkl"))], ignore_index=True)
    # 2026-06 末に組の表記が "1=2=3" → "1-2-3" へ変わった。両表記が併存する
    # 12レースは値が食い違う（片方が古いまま残っている）ので除外する。
    t["sep"] = t.combination.str.contains("-")
    mixed = t.groupby("race_key").sep.nunique()
    t = t[~t.race_key.isin(mixed[mixed > 1].index)].drop(columns="sep")
    t["combination"] = t.combination.str.replace("-", "=")
    return e, t


def market_m3(t: pd.DataFrame) -> pd.DataFrame:
    """三連複オッズ → (race_key, frame_no, m3)。"""
    t = t[t.odds_value > 0].copy()
    t["q"] = 1.0 / t.odds_value
    t["q"] /= t.groupby("race_key").q.transform("sum")
    parts = t.combination.str.split("=", expand=True).astype(int)
    rows = pd.concat(
        [pd.DataFrame({"race_key": t.race_key, "frame_no": parts[i], "q": t.q}) for i in range(3)]
    )
    return rows.groupby(["race_key", "frame_no"], as_index=False).q.sum().rename(columns={"q": "m3"})


def build(e: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    """選手単位の分析表。"""
    placed = e[e.finish_order.between(1, 3)].groupby("race_key").finish_order.nunique()
    ok = placed[placed == 3].index
    df = e[e.race_key.isin(ok)].merge(market_m3(t), on=["race_key", "frame_no"], how="inner")
    df["hit3"] = df.finish_order.between(1, 3).astype(int)
    df["p3"] = df.pred_top3_pct.astype(float) / 100.0
    df = df[df.p3.notna()]
    df["p3n"] = 3.0 * df.p3 / df.groupby("race_key").p3.transform("sum")
    df["ratio"] = df.p3n / df.m3
    return df


def boot_ratio(sub: pd.DataFrame) -> tuple[float, float, float]:
    """Σhit / Σm3 と開催日単位ブートストラップ 95%CI。"""
    g = sub.groupby("race_date").agg(h=("hit3", "sum"), m=("m3", "sum"))
    h, m = g.h.to_numpy(), g.m.to_numpy()
    idx = RNG.integers(0, len(g), size=(N_BOOT, len(g)))
    bs = h[idx].sum(1) / m[idx].sum(1)
    return h.sum() / m.sum(), *np.percentile(bs, [2.5, 97.5])


def decile_table(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """col の十分位（全体で切る）ごとの 実/m3。"""
    out = []
    df = df.copy()
    df["dec"] = pd.qcut(df[col].rank(method="first"), 10, labels=False) + 1
    for d, sub in df.groupby("dec"):
        r, lo, hi = boot_ratio(sub)
        out.append(dict(dec=d, n=len(sub), col_min=sub[col].min(), col_max=sub[col].max(),
                        m3=sub.m3.mean(), hit=sub.hit3.mean(), ratio=r, lo=lo, hi=hi))
    return pd.DataFrame(out)


def pl_trio(p: np.ndarray) -> dict[tuple[int, int, int], float]:
    """1着率ベクトルから PL の三連複確率（順不同で合算）。"""
    n = len(p)
    out: dict[tuple[int, int, int], float] = {}
    for a, b, c in itertools.permutations(range(n), 3):
        pr = p[a] * p[b] / (1 - p[a]) * p[c] / (1 - p[a] - p[b])
        k = tuple(sorted((a, b, c)))
        out[k] = out.get(k, 0.0) + pr
    return out


def combo_table(e: pd.DataFrame, t: pd.DataFrame, races: set[str]) -> pd.DataFrame:
    """組単位: モデル(PL・vintage 1着率)/市場 の十分位ごとの 実的中/市場確率。"""
    t = t[(t.odds_value > 0) & t.race_key.isin(races)].copy()
    t["q"] = 1.0 / t.odds_value
    t["q"] /= t.groupby("race_key").q.transform("sum")
    rows = []
    for rk, ee in e[e.race_key.isin(races)].groupby("race_key"):
        ee = ee.sort_values("frame_no")
        pw = ee.pred_win_pct.astype(float).to_numpy()
        if np.isnan(pw).any() or pw.sum() <= 0:
            continue
        pw = pw / pw.sum()
        fr = ee.frame_no.to_numpy()
        top = tuple(sorted(ee[ee.finish_order.between(1, 3)].frame_no))
        pm = pl_trio(pw)
        rows.append(pd.DataFrame({
            "race_key": rk, "race_date": ee.race_date.iloc[0],
            "combination": ["=".join(str(fr[i]) for i in k) for k in pm],
            "pm": list(pm.values()),
            "hit": [int(tuple(sorted(fr[i] for i in k)) == top) for k in pm],
        }))
    c = pd.concat(rows).merge(t[["race_key", "combination", "q"]], on=["race_key", "combination"])
    c["pm"] /= c.groupby("race_key").pm.transform("sum")
    c["r"] = c.pm / c.q
    c["dec"] = pd.qcut(c.r.rank(method="first"), 10, labels=False) + 1
    out = []
    for d, sub in c.groupby("dec"):
        g = sub.groupby("race_date").agg(h=("hit", "sum"), m=("q", "sum"))
        h, m = g.h.to_numpy(), g.m.to_numpy()
        idx = RNG.integers(0, len(g), size=(N_BOOT, len(g)))
        bs = h[idx].sum(1) / m[idx].sum(1)
        out.append(dict(dec=d, n=len(sub), r_min=sub.r.min(), r_max=sub.r.max(),
                        odds_med=float(np.median(1 / sub.q)), ratio=h.sum() / m.sum(),
                        lo=np.percentile(bs, 2.5), hi=np.percentile(bs, 97.5)))
    return pd.DataFrame(out)


def main() -> None:
    """全表を出力する。"""
    e, t = load()
    df = build(e, t)
    pd.set_option("display.width", 200)
    for n in (7, 9):
        for w, (a, b) in WINDOWS.items():
            sub = df[(df.n_entries == n) & df.race_date.between(a, b)]
            print(f"\n===== {n}車 {w}  races={sub.race_key.nunique()} riders={len(sub)} =====")
            print("-- 市場 m3 の十分位（人気-穴バイアス）")
            print(decile_table(sub, "m3").round(3).to_string(index=False))
            print("-- ratio = p3n/m3 の十分位（候補指標）")
            print(decile_table(sub, "ratio").round(3).to_string(index=False))
    for n in (7, 9):
        for w, (a, b) in WINDOWS.items():
            races = set(df[(df.n_entries == n) & df.race_date.between(a, b)].race_key)
            print(f"\n===== 組単位 {n}車 {w} =====")
            print(combo_table(e, t, races).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
