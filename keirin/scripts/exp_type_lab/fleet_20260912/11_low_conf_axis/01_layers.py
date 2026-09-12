#!/usr/bin/env python3
"""層の定義と相関 — 「軸1が薄い」をどの既存量で表すか。

🔴 閾値は **探索窓の分位**で作る（確認窓を見て決めない）。
出力: /tmp/11_layers.pkl（各レースの層フラグ）
"""
from __future__ import annotations
import itertools, pickle, sys
from pathlib import Path
import numpy as np

REPO = Path("/Users/ysuzuki/GitHub/kiseki/keirin")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from src.type_lab import win_entropy

PERMS = C.CANON
CIDX = C.CIDX


def main():
    z = C.board()
    A = {k: z[k] for k in ("P3", "PW", "PO", "DATE", "TYPE", "AXIS_SUM", "GAP",
                           "A_prediction_mark", "WIN", "PAY", "A_race_point")}
    TYPE = np.array([str(v) for v in A["TYPE"]])
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if TYPE[int(i)] in "ABCDEF"]
        for i in idx:
            p3 = A["P3"][i].astype(float)
            pw = A["PW"][i].astype(float)
            po = A["PO"][i].astype(float)
            ok = np.isfinite(po) & (po > 0)
            if ok.sum() < 60:
                continue
            # 市場（予測オッズ）から 1着シェア・3着内シェアを作る
            inv = np.where(ok, 1.0 / np.maximum(po, 1e-9), 0.0)
            mw = np.zeros(7); mp = np.zeros(7)
            for t, k in enumerate(PERMS):
                if not ok[t]:
                    continue
                mw[k[0] - 1] += inv[t]
                for c in k:
                    mp[c - 1] += inv[t]
            mw = mw / max(mw.sum(), 1e-12)
            mp = mp / max(mp.sum(), 1e-12) * 3.0
            o3 = np.argsort(-p3)            # p3 降順（0-index）
            ow = np.argsort(-pw)
            omw = np.argsort(-mw)
            mk = A["A_prediction_mark"][i].astype(float)
            hons = [c for c in range(1, 8) if int(mk[c - 1]) == 1]
            rp = A["A_race_point"][i].astype(float)
            rows.append(dict(
                win=win, key=int(i), date=str(A["DATE"][i]), type=TYPE[i],
                axis_sum=float(A["AXIS_SUM"][i]), gap=float(A["GAP"][i]),
                pw_ent=float(win_entropy({c: float(pw[c - 1]) for c in range(1, 8)})),
                # 「2、3番手と差がない」
                p3_gap12=float(p3[o3[0]] - p3[o3[1]]),
                p3_gap13=float(p3[o3[0]] - p3[o3[2]]),
                pw_gap12=float(pw[ow[0]] - pw[ow[1]]),
                pw_gap13=float(pw[ow[0]] - pw[ow[2]]),
                # 「レース全体で軸1の占有が少ない」
                pw_max=float(pw[ow[0]]),
                p3_max=float(p3[o3[0]]),
                pw_share=float(pw[ow[0]] / max(pw.sum(), 1e-12)),
                rp_sd=float(np.std(rp)),
                # 市場との食い違い
                a1=int(o3[0] + 1), a2=int(o3[1] + 1), a3=int(o3[2] + 1),
                pw1=int(ow[0] + 1), mkt1=int(omw[0] + 1),
                hon=int(hons[0]) if len(hons) == 1 else 0,
                mkt_w1=float(mw[omw[0]]),
                # 決着
                w1=int(PERMS[int(A["WIN"][i])][0]),
                w2=int(PERMS[int(A["WIN"][i])][1]),
                w3=int(PERMS[int(A["WIN"][i])][2]),
                pay=float(A["PAY"][i]) / 100.0,
            ))
    Path("/tmp/11_layers.pkl").write_bytes(pickle.dumps(rows))
    print(f"{len(rows):,} 行")

    # ── 相関（探索窓・Spearman） ──
    import scipy.stats as st
    ex = [r for r in rows if r["win"] == "explore"]
    QS = ("axis_sum", "p3_gap12", "p3_gap13", "pw_gap12", "pw_gap13",
          "pw_max", "p3_max", "pw_share", "pw_ent", "rp_sd", "gap")
    M = np.array([[r[q] for q in QS] for r in ex])
    print("\n=== Spearman 相関（探索窓 n=%d） ===" % len(ex))
    print("            " + " ".join(f"{q[:8]:>9s}" for q in QS))
    for a, qa in enumerate(QS):
        cells = []
        for b in range(len(QS)):
            cells.append(f"{st.spearmanr(M[:, a], M[:, b]).statistic:9.3f}")
        print(f"{qa:12s}" + " ".join(cells))

    # ── 層の定義（探索窓の分位）と重なり ──
    print("\n=== 層の定義（探索窓の分位）===")
    thr = {}
    for q, side, pct in (("axis_sum", "lo", 25), ("p3_gap12", "lo", 25),
                         ("pw_gap12", "lo", 25), ("pw_max", "lo", 25),
                         ("pw_ent", "hi", 10), ("pw_ent", "hi", 25)):
        v = np.array([r[q] for r in ex])
        t = np.percentile(v, pct if side == "lo" else 100 - pct)
        nm = f"{q}_{side}{pct}"
        thr[nm] = (q, side, float(t))
        print(f"  {nm:18s} {q} {'<=' if side=='lo' else '>='} {t:.4f}")

    def inlay(r, nm):
        q, side, t = thr[nm]
        return (r[q] <= t) if side == "lo" else (r[q] >= t)

    names = list(thr)
    print("\n=== 層の重なり（Jaccard・探索窓）===")
    print("                   " + " ".join(f"{n[:16]:>17s}" for n in names))
    for a in names:
        sa = {r["key"] for r in ex if inlay(r, a)}
        cells = []
        for b in names:
            sb = {r["key"] for r in ex if inlay(r, b)}
            cells.append(f"{len(sa & sb) / max(len(sa | sb), 1):17.3f}")
        print(f"{a:18s}" + " ".join(cells))

    # ── 層ごとの「1着が軸1以外」率・配当 ──
    print("\n=== 層ごとの実態 ===")
    hd = ("  {:18s} {:>7s} {:>8s} {:>9s} {:>9s} {:>9s} {:>9s} {:>8s}".format(
        "層", "割合%", "1着=軸1%", "1着=軸2%", "1着=軸3%", "軸1圏外%", "払戻中央", "100倍+%"))
    for win in ("explore", "confirm"):
        sub = [r for r in rows if r["win"] == win]
        print(f"\n[{win}] n={len(sub):,}")
        print(hd)
        for nm in ["(全体)"] + names:
            s = sub if nm == "(全体)" else [r for r in sub if inlay(r, nm)]
            if not s:
                continue
            n = len(s)
            f1 = sum(1 for r in s if r["w1"] == r["a1"]) / n * 100
            f2 = sum(1 for r in s if r["w1"] == r["a2"]) / n * 100
            f3 = sum(1 for r in s if r["w1"] == r["a3"]) / n * 100
            out = sum(1 for r in s if r["a1"] not in (r["w1"], r["w2"], r["w3"])) / n * 100
            pays = sorted(r["pay"] for r in s)
            big = sum(1 for r in s if r["pay"] >= 100) / n * 100
            print(f"  {nm:18s} {n/len(sub)*100:7.1f} {f1:8.2f} {f2:9.2f} {f3:9.2f}"
                  f" {out:9.2f} {pays[n//2]:9.1f} {big:8.2f}")
    Path("/tmp/11_thr.pkl").write_bytes(pickle.dumps(thr))


if __name__ == "__main__":
    main()
