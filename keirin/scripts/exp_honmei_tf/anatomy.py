#!/usr/bin/env python3
"""E（指数1位3着内 × 高配当）の記述分析 — どんなレース / どんな組み合わせか（2026-08-26）。"""
from __future__ import annotations
import itertools, sys
import numpy as np

z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY = z["PROB"], z["PO"], z["WIN"], z["PAY"]
DATE, RTYPE, P3, PW = z["DATE"], z["RTYPE"], z["P3"], z["PW"]
LG, ST, PC = z["LG"], z["ST"], z["PC"]
LSZ, LPOS, LEAD, MARK, RP, NL = (z["A_line_size"], z["A_line_pos"], z["A_is_line_leader"],
                                 z["A_prediction_mark"], z["A_race_point"], z["A_n_lines"])
GRADE, CUPG, DAYI = z["GRADE2"], z["CUPG2"], z["DAYI"]
CANON = np.array(list(itertools.permutations(range(1, 8), 3)))
N = len(WIN)
OK = WIN >= 0
EXP = (DATE < "2026-01-01") & OK
CNF = (DATE >= "2026-01-01") & OK

TOP3 = CANON[np.clip(WIN, 0, None)]                 # 実1-3着の車番
H = P3.argmax(1) + 1                                # 指数1位
RANKP3 = np.argsort(np.argsort(-P3, 1), 1) + 1
RANKRP = np.argsort(np.argsort(-np.nan_to_num(RP, nan=-1), 1), 1) + 1
IN3 = (TOP3 == H[:, None]).any(1) & OK
HI50 = (PAY >= 5000) & OK
HI100 = (PAY >= 10000) & OK
r = np.arange(N)
hi = H - 1                                          # 指数1位の列index

def frac(mask_base, cond):
    """base の中で cond が成り立つ割合を 探索/確認 で。"""
    o = []
    for w in (EXP, CNF):
        m = mask_base & w
        o.append(cond[m].mean() if m.sum() else np.nan)
    return o

def table(title, groups, base):
    print(f"\n--- {title}（母数 = 指数1位が3着以内のレース）---")
    print(f"{'区分':<20}{'n(探索/確認)':>18}{'50倍+ 探索/確認':>20}{'100倍+ 探索/確認':>20}")
    for lbl, g in groups:
        b = base & g
        if (b & EXP).sum() < 150 or (b & CNF).sum() < 60:
            continue
        a50 = frac(b, HI50); a100 = frac(b, HI100)
        print(f"{lbl:<20}{(b&EXP).sum():>8,}/{(b&CNF).sum():<8,}"
              f"{a50[0]*100:>10.1f}%/{a50[1]*100:<8.1f}%{a100[0]*100:>10.1f}%/{a100[1]*100:<8.1f}%")

print("=" * 90)
print("■ 基準値")
a50, a100 = frac(IN3, HI50), frac(IN3, HI100)
print(f"指数1位が3着以内のレース  n={IN3.sum():,}  "
      f"うち50倍+ {a50[0]*100:.1f}%/{a50[1]*100:.1f}%  100倍+ {a100[0]*100:.1f}%/{a100[1]*100:.1f}%")
print("=" * 90)

# ── A. レース側 ──
table("レース種別", [(t, RTYPE == t) for t in sorted(set(RTYPE[OK]))], IN3)
table("ライン数", [(f"{k}ライン", NL[r, hi] == k) for k in (2, 3, 4, 5)], IN3)
table("指数1位のライン内位置",
      [("単騎(ライン1車)", LSZ[r, hi] == 1), ("ライン先頭", (LEAD[r, hi] == 1) & (LSZ[r, hi] > 1)),
       ("番手(2番手)", LPOS[r, hi] == 2), ("3番手以降", LPOS[r, hi] >= 3)], IN3)
table("指数1位の脚質", [(s, ST[r, hi] == s) for s in sorted(set(ST[OK][:, 0])) if s], IN3)
table("指数1位のWT公式印",
      [("◎(1)", MARK[r, hi] == 1), ("○(2)", MARK[r, hi] == 2), ("▲(3)", MARK[r, hi] == 3),
       ("△(4)以下", MARK[r, hi] >= 4), ("無印", ~np.isfinite(MARK[r, hi]))], IN3)
table("指数1位の競走得点順位",
      [(f"得点{k}位", RANKRP[r, hi] == k) for k in (1, 2, 3, 4)] +
      [("得点5位以下", RANKRP[r, hi] >= 5)], IN3)
table("指数1位の3着内率 p3",
      [(f"p3 {lo}〜{hi_}%", (P3[r, hi] >= lo / 100) & (P3[r, hi] < hi_ / 100))
       for lo, hi_ in ((0, 45), (45, 55), (55, 65), (65, 75), (75, 101))], IN3)
table("グレード", [(g if g else "(なし)", GRADE == g) for g in sorted(set(GRADE[OK]))], IN3)
table("開催日目", [(f"{d}日目", DAYI == d) for d in (1, 2, 3, 4)], IN3)
