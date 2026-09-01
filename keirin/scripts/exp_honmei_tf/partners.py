#!/usr/bin/env python3
"""E のとき指数1位と一緒に来る2車は何者か（2026-08-26）。"""
from __future__ import annotations
import itertools, sys
import numpy as np

z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY = z["PROB"], z["PO"], z["WIN"], z["PAY"]
DATE, RTYPE, P3 = z["DATE"], z["RTYPE"], z["P3"]
LG, ST = z["LG"], z["ST"]
LSZ, LPOS, LEAD, MARK, RP, NL = (z["A_line_size"], z["A_line_pos"], z["A_is_line_leader"],
                                 z["A_prediction_mark"], z["A_race_point"], z["A_n_lines"])
CANON = np.array(list(itertools.permutations(range(1, 8), 3)))
N = len(WIN); OK = WIN >= 0
EXP = (DATE < "2026-01-01") & OK; CNF = (DATE >= "2026-01-01") & OK
TOP3 = CANON[np.clip(WIN, 0, None)]
H = P3.argmax(1) + 1
RANKP3 = np.argsort(np.argsort(-P3, 1), 1) + 1
IN3 = (TOP3 == H[:, None]).any(1) & OK
r = np.arange(N); hi = H - 1

# 相手2車（指数1位以外の入着2車）の列index
oth = np.full((N, 2), -1, int)
for i in range(N):
    if not IN3[i]:
        continue
    v = [c - 1 for c in TOP3[i] if c != H[i]]
    oth[i] = v[:2]
has = IN3 & (oth[:, 0] >= 0)

BAND = {"50倍未満": (PAY < 5000) & has, "50〜100倍": (PAY >= 5000) & (PAY < 10000) & has,
        "100倍以上": (PAY >= 10000) & has}

def dist(vals_fn, labels, title):
    """相手2車を1車ずつ数えた分布を配当帯別に。"""
    print(f"\n--- {title}（相手2車を1車ずつ数える・探索/確認）---")
    print(f"{'':<16}" + "".join(f"{l:>18}" for l in labels))
    for bn, bm in BAND.items():
        cells = []
        for w in (EXP, CNF):
            m = bm & w
            v = np.concatenate([vals_fn(oth[m, 0], m), vals_fn(oth[m, 1], m)])
            cnt = np.array([np.mean(v == l) for l in labels])
            cells.append(cnt)
        s = f"{bn:<16}"
        for j in range(len(labels)):
            s += f"{cells[0][j]*100:>8.1f}/{cells[1][j]*100:<9.1f}"
        print(s)

# 同ライン / 別ライン
def same_line(idxs, m):
    return np.where(LG[m][np.arange(m.sum()), idxs] == LG[m][np.arange(m.sum()), hi[m]],
                    "同ライン", "別ライン")
dist(same_line, ["同ライン", "別ライン"], "指数1位と同じラインか")

def role(idxs, m):
    a = np.arange(m.sum())
    sz = LSZ[m][a, idxs]; ld = LEAD[m][a, idxs]
    out = np.where(sz == 1, "単騎", np.where(ld == 1, "先頭", "番手以降"))
    return out
dist(role, ["単騎", "先頭", "番手以降"], "相手のライン内での立場")

def style(idxs, m):
    return ST[m][np.arange(m.sum()), idxs]
dist(style, ["逃", "追", "両"], "相手の脚質")

def prank(idxs, m):
    return RANKP3[m][np.arange(m.sum()), idxs].astype(str)
dist(prank, [str(k) for k in range(2, 8)], "相手の指数順位")

def mk(idxs, m):
    a = np.arange(m.sum()); v = MARK[m][a, idxs]
    return np.where(np.isfinite(v), np.nan_to_num(v, nan=9).astype(int).astype(str), "無印")
dist(mk, ["1", "2", "3", "4", "5", "6", "無印"], "相手のWT公式印")

print("\n" + "=" * 104)
print("■ 2車まとめて見る — 相手2車のライン構成")
print("=" * 104)
a = np.arange(N)
s0 = LG[a, oth[:, 0]] == LG[a, hi]
s1 = LG[a, oth[:, 1]] == LG[a, hi]
pat = np.where(s0 & s1, "2車とも同ライン", np.where(s0 | s1, "1車が同ライン", "2車とも別ライン"))
print(f"{'':<18}" + "".join(f"{l:>22}" for l in ["2車とも同ライン", "1車が同ライン", "2車とも別ライン"]))
for bn, bm in BAND.items():
    s = f"{bn:<18}"
    for l in ["2車とも同ライン", "1車が同ライン", "2車とも別ライン"]:
        e = np.mean(pat[bm & EXP] == l) * 100; c = np.mean(pat[bm & CNF] == l) * 100
        s += f"{e:>10.1f}/{c:<11.1f}"
    print(s)

print("\n" + "=" * 104)
print("■ 全レース基準の E 率（＝実際に狙えるか）")
print("=" * 104)
E50 = IN3 & (PAY >= 5000); E100 = IN3 & (PAY >= 10000)
def show(lbl, g):
    if (g & EXP).sum() < 200 or (g & CNF).sum() < 80: return
    print(f"{lbl:<22}{(g&OK).sum():>8,}件  指数1位3着内 {IN3[g&EXP].mean()*100:5.1f}/{IN3[g&CNF].mean()*100:5.1f}%"
          f"   E(50倍+) {E50[g&EXP].mean()*100:5.1f}/{E50[g&CNF].mean()*100:5.1f}%"
          f"   E(100倍+) {E100[g&EXP].mean()*100:5.1f}/{E100[g&CNF].mean()*100:5.1f}%")
print(f"{'全レース':<22}{OK.sum():>8,}件  指数1位3着内 {IN3[EXP].mean()*100:5.1f}/{IN3[CNF].mean()*100:5.1f}%"
      f"   E(50倍+) {E50[EXP].mean()*100:5.1f}/{E50[CNF].mean()*100:5.1f}%"
      f"   E(100倍+) {E100[EXP].mean()*100:5.1f}/{E100[CNF].mean()*100:5.1f}%")
for k in (2, 3, 4, 5):
    show(f"{k}ライン", (NL[r, hi] == k) & OK)
show("指数1位が単騎", (LSZ[r, hi] == 1) & OK)
show("指数1位がライン先頭", ((LEAD[r, hi] == 1) & (LSZ[r, hi] > 1)) & OK)
show("指数1位が番手", (LPOS[r, hi] == 2) & OK)
show("指数1位の脚質=逃", (ST[r, hi] == "逃") & OK)
show("指数1位の脚質=追", (ST[r, hi] == "追") & OK)
for t in ("初特選", "決勝", "一般", "選抜", "特選", "準決勝", "予選", "チャレンジ予選"):
    show(f"種別={t}", (RTYPE == t) & OK)
