#!/usr/bin/env python3
"""再検証① 三連単の**順序構造** — 「軸2を2点」と「順位の入れ替えを何点買うか」
（2026-08-27・ユーザー訂正）。

> 軸2を2点は三連複ではオッズ的にも成り立たず、三連単によるもの。
> 順位の入れ替えまでを何点買うかも検証対象。

軸1 = vintage p3 1位 / 軸2 = p3 2位 / 相手 c = p3 3位以下から m 車。
順序パターン:
  12       a1-a2-c            （1着a1・2着a2 固定・3着流し）      m点
  12+21    a1-a2-c, a2-a1-c   （**1着と2着の入れ替え**）          2m点
  12+13    a1-a2-c, a1-c-a2   （**2着と3着の入れ替え**）          2m点
  12+21+13+31 4順列                                          4m点
  all6     {a1,a2,c} の6順列すべて（＝三連複1点と同じ集合）      6m点
  ax1_2nd2 1着=a1固定・2着∈{p3 2位,3位}・3着流し（**ユーザーの元案**）2m点
配分は 信頼度傾斜（床1.3）と ダッチ。**既存ゲートは掛けない**。
⚠️ 確認窓(2026)が本番相当。
"""
from __future__ import annotations

import sys
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C            # noqa: E402
import recheck_free as F      # noqa: E402


def combos(i, pattern, m):
    z = F.cache()
    order = [int(x) for x in np.argsort(-z["P3"][i]) + 1]
    a1, a2, a3 = order[0], order[1], order[2]
    rest = [c for c in order[2:]][:m]
    out = []
    if pattern == "12":
        out = [(a1, a2, c) for c in rest]
    elif pattern == "12+21":
        out = [(a1, a2, c) for c in rest] + [(a2, a1, c) for c in rest]
    elif pattern == "12+13":
        out = [(a1, a2, c) for c in rest] + [(a1, c, a2) for c in rest]
    elif pattern == "4perm":
        out = ([(a1, a2, c) for c in rest] + [(a2, a1, c) for c in rest]
               + [(a1, c, a2) for c in rest] + [(a2, c, a1) for c in rest])
    elif pattern == "all6":
        for c in rest:
            for p in ((a1, a2, c), (a2, a1, c), (a1, c, a2),
                      (a2, c, a1), (c, a1, a2), (c, a2, a1)):
                out.append(p)
    elif pattern == "ax1_2nd2":
        seconds = [a2, a3]
        rest2 = [c for c in order[2:] if c != a3][:m]
        out = [(a1, s, c) for s in seconds for c in rest2 if c != s]
    return [c for c in out if len(set(c)) == 3]


def run(t, w, pattern, m, mode, fm=1.3):
    z = F.cache()
    idx = C.select(t, w)
    nd = len(set(z["DATE"][idx]))
    inv = pay = 0.0
    hits, ratios, ks = [], [], []
    n = 0
    ok2 = 0
    for i in idx:
        cs = combos(i, pattern, m)
        if not cs:
            continue
        sel = np.array([C.CIDX[c] for c in cs])
        po = z["PO"][i][sel]; pr = z["PROB"][i][sel]
        if not np.isfinite(po).all() or (po <= 0).any():
            continue
        st = F.alloc(po, pr, mode, fm)
        if st is None:
            continue
        n += 1; ks.append(len(cs))
        iv = float(st.sum()); inv += iv
        win = int(z["WIN"][i])
        hp = np.flatnonzero(sel == win)
        p = float(st[int(hp[0])] / 100.0 * z["PAY"][i]) if len(hp) else 0.0
        pay += p
        if p > 0:
            hits.append(p); ratios.append(p / iv)
    if not n:
        return None
    q = lambda a, x: sorted(a)[int(len(a) * x)] if a else 0
    gami = sum(1 for r in ratios if r < 1)
    return dict(perday=n / nd, k=np.mean(ks), hit=len(hits) / n * 100,
                gami=gami / max(len(hits), 1) * 100,
                shown=(len(hits) - gami) / n * 100,
                q25=q(hits, .25), med=median(hits) if hits else 0, q90=q(hits, .9),
                two=sum(1 for r in ratios if r >= 2) / nd,
                big=sum(1 for h in hits if h >= 100_000) / nd, roi=pay / inv * 100)


HDR = ("  {:24s} {:>5s} {:>6s} {:>7s} {:>6s} {:>8s} {:>26s} {:>7s} {:>7s} {:>6s}"
       .format("順序パターン", "点数", "件/日", "的中%", "ガミ%", "表示的中",
               "払戻 q25/中央/q90", "2倍+/日", "10万+/日", "ROI%"))


def show(lbl, s):
    if not s:
        print(f"  {lbl:24s} (該当なし)"); return
    print(f"  {lbl:24s} {s['k']:5.1f} {s['perday']:6.2f} {s['hit']:7.2f} {s['gami']:6.2f}"
          f" {s['shown']:8.2f} {s['q25']:7,.0f}/{s['med']:8,.0f}/{s['q90']:8,.0f}"
          f" {s['two']:7.2f} {s['big']:7.3f} {s['roi']:6.1f}")


if __name__ == "__main__":
    types = sys.argv[1:] or list("ABCDEF")
    mode, fm = "conf", 1.3
    for t in types:
        print(f"\n=== 型{t}  三連単の順序構造（信頼度傾斜 床1.3・ゲートなし）===")
        for w, wl in (("explore", "探索"), ("confirm", "確認(本番相当)")):
            print(f"[{wl}]"); print(HDR)
            for pat in ("12", "12+21", "12+13", "4perm", "all6", "ax1_2nd2"):
                for m in (2, 3, 5):
                    show(f"{pat} × 相手{m}車", run(t, w, pat, m, mode, fm))
