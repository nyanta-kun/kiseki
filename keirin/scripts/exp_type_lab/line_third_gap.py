#!/usr/bin/env python3
"""3車ラインの3番手が「1車離れている」とき、それは個人の力か・ライン内の落差か
（2026-09-14・ユーザー質問）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/line_third_gap.py <sec>

sec: base | twoway | resid | pos
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict

import numpy as np

_R = None


def load():
    global _R
    if _R is None:
        with open("/tmp/ratebranch/rows.pkl", "rb") as f:
            _R = pickle.load(f)
    return _R


def lines_of(r):
    """{ライン記号: [(lpos, car), ...] を lpos 昇順}。単騎も含む。"""
    d = defaultdict(list)
    for c in range(1, 8):
        d[str(r["lg"][c - 1])].append((float(r["lpos"][c - 1]), c))
    return {k: sorted(v) for k, v in d.items()}


def units(size=3, pos=3):
    """size 車ラインの pos 番手について 1行/1車 を吐く。"""
    out = []
    for r in load():
        fin = set(r["fin"])
        for k, mem in lines_of(r).items():
            if len(mem) != size:
                continue
            cars = [c for _, c in mem]
            tgt = cars[pos - 1]
            rest = [c for c in cars if c != tgt]
            rp = r["rp"]
            out.append(dict(
                win=r["win"], race_key=r["race_key"], car=tgt,
                in3=tgt in fin,
                # 個人の力（絶対）
                rp_abs=float(rp[tgt - 1]),
                rp_rank=float(np.sum(rp > rp[tgt - 1]) + 1),       # レース内順位(1=最上位)
                t3_abs=float(r["rate"][tgt - 1, 2]),               # 3着内率
                # ライン内の落差（相対）
                gap=float(np.mean([rp[c - 1] for c in rest]) - rp[tgt - 1]),
                gap_prev=float(rp[cars[pos - 2] - 1] - rp[tgt - 1]) if pos >= 2 else 0.0,
                # モデルの見立て
                p3=float(r["p3vec"][tgt - 1]),
                p3_rank=float(np.sum(r["p3vec"] > r["p3vec"][tgt - 1]) + 1),
            ))
    return out


def _q(v, n=5):
    qs = np.quantile(v, np.linspace(0, 1, n + 1))
    qs[0] -= 1e-9
    return qs


def _cell(rows, key, qs):
    idx = np.clip(np.searchsorted(qs, [x[key] for x in rows], side="left") - 1, 0, len(qs) - 2)
    return idx


def base():
    u = units()
    print("§1 3車ラインの3番手が3着以内に入る率（1行=1車）\n")
    for w in ("confirm", "explore"):
        rs = [x for x in u if x["win"] == w]
        print(f"{w}: n={len(rs):,}  3着以内 {np.mean([x['in3'] for x in rs])*100:.2f}%")
    print("\n参考: 同じラインの先頭・番手")
    for pos in (1, 2, 3):
        line = f"  {pos}番手" if pos > 1 else "  先頭  "
        for w in ("confirm", "explore"):
            rs = [x for x in units(3, pos) if x["win"] == w]
            line += f"   {w} {np.mean([x['in3'] for x in rs])*100:5.2f}% (n={len(rs):,})"
        print(line)


def twoway():
    """個人の力（絶対）×ライン内の落差（相対）の2元表。片方を止めてもう片方を動かす。"""
    u = units()
    print("§2 3車ラインの3番手 — 個人の力 × ライン内の落差（セル＝3着以内率%）\n")
    for w in ("confirm", "explore"):
        rs = [x for x in u if x["win"] == w]
        qa = _q([x["rp_rank"] for x in rs], 3)      # 個人の力（レース内得点順位）
        qg = _q([x["gap"] for x in rs], 3)          # ライン内の落差
        ia, ig = _cell(rs, "rp_rank", qa), _cell(rs, "gap", qg)
        M = np.zeros((3, 3)); N = np.zeros((3, 3))
        for k, x in enumerate(rs):
            M[ia[k], ig[k]] += x["in3"]; N[ia[k], ig[k]] += 1
        print(f"--- {w} (n={len(rs):,}) ---")
        hdr = "個人の力 / 落差"
        print(f"{hdr:>16s}{'落差 小':>12s}{'中':>12s}{'落差 大':>12s}{'行計':>10s}")
        lab = ["強い(上位)", "中", "弱い(下位)"]
        for i in range(3):
            row = "".join(f"{M[i,j]/N[i,j]*100:8.2f}%({int(N[i,j]):4d})" for j in range(3))
            print(f"{lab[i]:>16s}{row}{M[i].sum()/N[i].sum()*100:9.2f}%")
        print(f"{'列計':>16s}" + "".join(f"{M[:,j].sum()/N[:,j].sum()*100:8.2f}%      " for j in range(3)))
        # 周辺効果
        d_abs = M[0].sum()/N[0].sum()*100 - M[2].sum()/N[2].sum()*100
        d_gap = M[:,0].sum()/N[:,0].sum()*100 - M[:,2].sum()/N[:,2].sum()*100
        # 相手を固定したときの効き
        d_abs_in = np.mean([M[0,j]/N[0,j]*100 - M[2,j]/N[2,j]*100 for j in range(3)])
        d_gap_in = np.mean([M[i,0]/N[i,0]*100 - M[i,2]/N[i,2]*100 for i in range(3)])
        print(f"  個人の力の効き  素 {d_abs:+.2f}pt → 落差を止めると {d_abs_in:+.2f}pt")
        print(f"  落差の効き      素 {d_gap:+.2f}pt → 個人の力を止めると {d_gap_in:+.2f}pt")
        print(f"  相関 r(個人の力順位, 落差) = {np.corrcoef([x['rp_rank'] for x in rs], [x['gap'] for x in rs])[0,1]:+.3f}\n")


def resid():
    """モデル p3 で層別しても、落差／個人の力が残るか。"""
    u = units()
    print("§3 モデル p3 の順位で層別したときの残差（セル＝3着以内率%）\n")
    for w in ("confirm", "explore"):
        rs = [x for x in u if x["win"] == w]
        print(f"--- {w} (n={len(rs):,}) ---")
        print(f"{'p3順位':>8s}{'n':>7s}{'層の母数':>10s}{'落差 小':>10s}{'落差 大':>10s}"
              f"{'力 強':>9s}{'力 弱':>9s}")
        for lo, hi, lab in ((1, 2, "1-2位"), (3, 4, "3-4位"), (5, 7, "5-7位")):
            g = [x for x in rs if lo <= x["p3_rank"] <= hi]
            if len(g) < 50:
                continue
            mg = np.median([x["gap"] for x in g]); ma = np.median([x["rp_rank"] for x in g])
            f = lambda s: np.mean([x["in3"] for x in g if s(x)]) * 100 if any(s(x) for x in g) else 0
            print(f"{lab:>8s}{len(g):7d}{np.mean([x['in3'] for x in g])*100:9.2f}%"
                  f"{f(lambda x: x['gap'] <= mg):9.2f}%{f(lambda x: x['gap'] > mg):9.2f}%"
                  f"{f(lambda x: x['rp_rank'] <= ma):8.2f}%{f(lambda x: x['rp_rank'] > ma):8.2f}%")
        print()


def pos():
    """4車ラインの3・4番手でも同じ形か。"""
    print("§4 ライン規模別（各 pos の3着以内率・落差 小/大）\n")
    for size in (2, 3, 4):
        for p in range(1, size + 1):
            u = units(size, p)
            line = f"  {size}車ライン {p}番手"
            for w in ("confirm", "explore"):
                rs = [x for x in u if x["win"] == w]
                if len(rs) < 50:
                    line += f"   {w} n={len(rs)}"
                    continue
                m = np.median([x["gap"] for x in rs])
                lo = np.mean([x["in3"] for x in rs if x["gap"] <= m]) * 100
                hi = np.mean([x["in3"] for x in rs if x["gap"] > m]) * 100
                line += f"   {w} {np.mean([x['in3'] for x in rs])*100:5.2f}% (落差小 {lo:5.2f}→大 {hi:5.2f}) n={len(rs):,}"
            print(line)
        print()




def resid2():
    """層内の p3 ばらつきを潰してから残差を見る。
    🔴 粗い p3 順位で層別すると、層の中でも p3 が大きく動く。『力が強い半分』が
       ただの『p3 が高い半分』でないかを、①層内 p3 の平均 ②p3 を細かく揃えた層
       の2通りで確認する。"""
    u = units()
    print("§3-2 残差が本物か — 層内の p3 を揃えて見る\n")
    for w in ("confirm", "explore"):
        rs = [x for x in u if x["win"] == w]
        print(f"--- {w} (n={len(rs):,}) ---")
        print(f"{'p3順位':>8s}{'n':>7s}{'力強 の3着内':>12s}{'力弱 の3着内':>12s}{'Δ':>8s}"
              f"{'力強 の平均p3':>13s}{'力弱 の平均p3':>13s}")
        for lo, hi, lab in ((3, 4, "3-4位"), (5, 7, "5-7位")):
            g = [x for x in rs if lo <= x["p3_rank"] <= hi]
            ma = np.median([x["rp_rank"] for x in g])
            a = [x for x in g if x["rp_rank"] <= ma]; b = [x for x in g if x["rp_rank"] > ma]
            print(f"{lab:>8s}{len(g):7d}{np.mean([x['in3'] for x in a])*100:11.2f}%"
                  f"{np.mean([x['in3'] for x in b])*100:11.2f}%"
                  f"{(np.mean([x['in3'] for x in a])-np.mean([x['in3'] for x in b]))*100:+7.2f}"
                  f"{np.mean([x['p3'] for x in a]):13.4f}{np.mean([x['p3'] for x in b]):13.4f}")
        # p3 の値そのものを細かい十分位で揃えてから、力と落差の残差を見る
        print(f"\n  p3 の値を十分位で揃えたあとの残差（全層をプールした加重平均）")
        qp = _q([x["p3"] for x in rs], 10)
        ip = _cell(rs, "p3", qp)
        for key, name in (("rp_rank", "個人の力"), ("gap", "ライン内の落差")):
            num = den = 0.0
            for d in range(10):
                g = [x for k, x in enumerate(rs) if ip[k] == d]
                if len(g) < 100:
                    continue
                m = np.median([x[key] for x in g])
                a = [x["in3"] for x in g if x[key] <= m]; b = [x["in3"] for x in g if x[key] > m]
                if not a or not b:
                    continue
                sign = 1 if key == "gap" else 1
                num += (np.mean(a) - np.mean(b)) * len(g) * sign
                den += len(g)
            print(f"    {name:16s} Δ(小さい半分 − 大きい半分) = {num/den*100:+.2f}pt")
        print()


if __name__ == "__main__":
    {"base": base, "twoway": twoway, "resid": resid, "pos": pos, "resid2": resid2}[sys.argv[1]]()
