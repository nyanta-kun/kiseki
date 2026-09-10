#!/usr/bin/env python3
"""第6〜7章 レース選別の腕（2026-09-10）。

日次上限の**残す順**を差し替える腕は、本番の `cap_priority` と**件数が完全に一致する**
（毎日同じ `cap` 件を選ぶだけなので）。したがって比較は件数一定で行える。
それでも `race_filter_2026_08_27.md` の警告どおり**無作為対照20本**を必ず置く。

差は**レース単位ブートストラップ**の 95%CI。ROI で採否は決めない。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_type_lab.split_common import (  # noqa: E402
    apply_cap, daily_stats, load_races, stats, window)
from scripts.exp_type_lab.split_cells import BANK  # noqa: E402


def dnf_races() -> set[str]:
    from src.database import get_connection
    out = set()
    with get_connection() as c:
        for r in c.execute("SELECT DISTINCT race_key FROM wt_entries WHERE finish_order=0"):
            out.add(str(dict(r)["race_key"]))
    return out


def yv(sold):
    st = [r for r in sold if r["settled"]]
    return (np.array([r["inv"] for r in st]), np.array([r["pay"] for r in st]))


def boot(a, b, iters=1000, seed=0):
    """Δ(表示的中, ROI) = a − b。母集団が違うので独立ブートストラップ。"""
    ia, pa = yv(a); ib, pb = yv(b)
    rng = np.random.default_rng(seed)
    ds, dr = [], []
    for _ in range(iters):
        ja = rng.integers(0, len(ia), len(ia)); jb = rng.integers(0, len(ib), len(ib))
        ds.append((pa[ja] >= ia[ja]).mean() * 100 - (pb[jb] >= ib[jb]).mean() * 100)
        dr.append(pa[ja].sum() / ia[ja].sum() * 100 - pb[jb].sum() / ib[jb].sum() * 100)
    f = lambda v: (float(np.mean(v)), float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return f(ds), f(dr)


HDR = ("{:32s} {:>6s} {:>8s} {:>7s} {:>8s} {:>8s} {:>7s} {:>7s} {:>12s} {:>8s}"
       .format("腕", "件/日", "表示的中%", "ROI%", "10万+/日", "日中央%", "日SD", "半減日%",
               "Δ表示 95%CI", "対照20"))


def report(name, sold, base, ctrl_shown, ctrl_roi):
    s, d = stats(sold), daily_stats(sold)
    if sold is base:
        ci = "—"
    else:
        (m, lo, hi), _ = boot(sold, base)
        ci = f"{m:+.2f}[{lo:+.1f},{hi:+.1f}]"
    w = (f"{sum(s['shown'] > c for c in ctrl_shown):2d}/20"
         if ctrl_shown else "—")
    print(f"{name:32s} {s['perday']:6.2f} {s['shown']:7.2f}% {s['roi']:6.1f}% "
          f"{s['big']:8.3f} {d['med_rate']:7.2f}% {d['sd_rate']:6.2f} "
          f"{d['p_half']:6.1f}% {ci:>12s} {w:>8s}")


def main():
    R = load_races()
    DNF = dnf_races()
    for r in R:
        r["_dnf"] = r["race_key"] in DNF
        b, ind = BANK.get(r["race_key"].split("_")[1], (400, 0))
        r["_bank"], r["_indoor"] = b, ind

    # ── 第6章 落車・失格は事前に見分けられるか ──────────────────────────
    print("=" * 120)
    print("■ 第6章 落車・失格(DNF)レースの割合は事前に分かる量で分かれるか")
    print("=" * 120)
    for wn, lab in (("explore", "探索2025"), ("confirm", "確認2026")):
        rs = window(R, wn)
        base = sum(r["_dnf"] for r in rs) / len(rs) * 100
        print(f"  {lab}: 全体 {base:.2f}%（{len(rs):,}レース）")
        for gname, kf in (("バンク", lambda r: f"{r['_bank']}m"),
                          ("開催日目", lambda r: f"{r['dayidx']}日目"),
                          ("決勝系", lambda r: "決勝系" if "決勝" in r["rtype"] else "一般"),
                          ("型", lambda r: r["tl"]),
                          ("実力伯仲 rp_sd 五分位", None)):
            if kf is None:
                v = np.array([r["rp_sd"] if r["rp_sd"] is not None else np.nan for r in rs])
                ok = np.isfinite(v)
                q = np.percentile(v[ok], [20, 40, 60, 80])
                lab2 = np.digitize(v, q)
                cells = defaultdict(list)
                for i, r in enumerate(rs):
                    if ok[i]:
                        cells[f"Q{lab2[i]+1}"].append(r["_dnf"])
            else:
                cells = defaultdict(list)
                for r in rs:
                    cells[kf(r)].append(r["_dnf"])
            txt = "  ".join(f"{k}:{np.mean(v)*100:.2f}%(n={len(v)})"
                            for k, v in sorted(cells.items()) if len(v) >= 200)
            print(f"    {gname:16s} {txt}")

    # ── 第7章 残す順の腕 ────────────────────────────────────────────────
    print("\n" + "=" * 120)
    print("■ 第7章 日次上限の「残す順」を差し替える（件数は完全に同じ）")
    print("=" * 120)
    for wn, lab in (("explore", "探索 2025"), ("confirm", "確認 2026-01〜08")):
        rs = window(R, wn)
        base = apply_cap(rs)
        cs, cr = [], []
        for sd in range(20):
            rng = np.random.default_rng(sd)
            s = apply_cap(rs, prio=lambda r, g=rng: g.random())
            cs.append(stats(s)["shown"]); cr.append(stats(s)["roi"])
        print(f"\n── {lab}  （無作為対照20本の中央 表示的中 {np.median(cs):.2f}% / "
              f"ROI {np.median(cr):.1f}%）")
        print(HDR)
        arms = [
            ("① 現行 cap_priority", lambda r: r["prio"]),
            ("② ＋333/500mバンク優先", lambda r: r["prio"] + (0.15 if r["_bank"] != 400 else 0)),
            ("③ ＋決勝系を後回し", lambda r: r["prio"] - (0.15 if "決勝" in r["rtype"] else 0)),
            ("④ ＋1日目を優先", lambda r: r["prio"] + (0.15 if r["dayidx"] == 1 else 0)),
            ("⑤ 軸信頼だけ（rp_sd 無し）", lambda r: 1.0 if r["exempt"] else
             __import__("scripts.exp_type_lab.split_common", fromlist=["GATE"]).GATE
             .axis_priority(r["plan"], r["axis"])),
            ("⑥ 無作為（対照の代表）", lambda r, g=np.random.default_rng(0): g.random()),
        ]
        for name, fn in arms:
            report(name, apply_cap(rs, prio=fn), base, cs, cr)
        # 会場を散らす（件数一定・会場ごとに上限 K）
        for K in (3, 4, 5):
            def pick(elig, cap, K=K):
                elig = sorted(elig, key=lambda r: (-r["prio"], r["venue"], r["race_no"]))
                used = defaultdict(int); out = []; rest = []
                for r in elig:
                    if used[r["venue"]] < K and len(out) < cap:
                        used[r["venue"]] += 1; out.append(r)
                    else:
                        rest.append(r)
                return out + rest[:max(0, cap - len(out))]
            report(f"⑦ 会場あたり{K}件まで（散らす）",
                   apply_cap(rs, extra_pick=pick), base, cs, cr)


if __name__ == "__main__":
    main()
