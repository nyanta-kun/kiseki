#!/usr/bin/env python3
"""p3 モデルはどこで弱いか — 印の有無で切って測る（2026-09-14）。

発端: 本番 `lgbm_wt` の gain は `prediction_mark` が 32.17% で単独最大。だが印は
7車中4車にしか付かず、**42.9% が「印なし」で一括り**（3着内率 22.96%）。
モデル最大の入力がフィールドの4割に何も言っていない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/model_headroom.py auc|axis|cal
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402


def _auc(x, y):
    x = np.asarray(x, float); y = np.asarray(y, bool)
    if y.all() or not y.any():
        return float("nan")
    r = np.argsort(np.argsort(x)) + 1
    n1, n0 = y.sum(), (~y).sum()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def rows(window):
    """1行=1車。board の vintage P3（OOS）で測る。"""
    z = C.board()
    idx = C.select(None, window)
    P3, PW, MK = z["P3"], z["PW"], z["A_prediction_mark"]
    WIN = z["WIN"].astype(int)
    p3, pw, mk, y, y1, race = [], [], [], [], [], []
    for i in idx:
        top3 = set(C.CANON[WIN[i]])
        first = C.CANON[WIN[i]][0]
        for c in range(1, 8):
            p3.append(float(P3[i][c - 1])); pw.append(float(PW[i][c - 1]))
            mk.append(int(MK[i][c - 1])); y.append(c in top3); y1.append(c == first)
            race.append(int(i))
    return (np.array(p3), np.array(pw), np.array(mk), np.array(y),
            np.array(y1), np.array(race))


def auc():
    print("§1 p3 / pw モデルの AUC を印の有無で切る（board の vintage 予測＝OOS）\n")
    for w in ("confirm", "explore"):
        p3, pw, mk, y, y1, _ = rows(w)
        print(f"--- {w} (車 n={len(p3):,}) ---")
        print(f"{'母集団':22s}{'n':>9}{'3着内率':>9}{'p3 AUC':>9}{'pw AUC':>9}{'印単独AUC':>10}")
        for lab, m in (("全体", np.ones(len(mk), bool)),
                       ("印あり（◎○▲△）", mk > 0),
                       ("**印なし**", mk == 0)):
            # 印は 1=◎ 2=○ 3=▲ 4=△ 0=なし → 強さ順へ直してから AUC
            strength = np.where(mk == 0, 0, 5 - mk)
            print(f"{lab:22s}{m.sum():9,d}{y[m].mean()*100:8.2f}%"
                  f"{_auc(p3[m], y[m]):9.4f}{_auc(pw[m], y1[m]):9.4f}"
                  f"{_auc(strength[m], y[m]):10.4f}")
        print()


def axis():
    """軸2車（p3上位2）に印なしの車がどれだけ入り、そのときそろうか。"""
    print("§2 軸2車に「印なし」が入るか、入るとどうなるか\n")
    z = C.board()
    for w in ("confirm", "explore"):
        idx = C.select(None, w)
        P3, MK, WIN = z["P3"], z["A_prediction_mark"], z["WIN"].astype(int)
        n = {0: [0, 0], 1: [0, 0], 2: [0, 0]}
        for i in idx:
            order = sorted(range(1, 8), key=lambda c: (-float(P3[i][c - 1]), c))
            a1, a2 = order[0], order[1]
            k = sum(1 for c in (a1, a2) if int(MK[i][c - 1]) == 0)
            top3 = set(C.CANON[WIN[i]])
            n[k][0] += 1
            n[k][1] += (a1 in top3 and a2 in top3)
        tot = sum(v[0] for v in n.values())
        print(f"--- {w} (n={tot:,}レース) ---")
        print(f"{'軸2車のうち印なし':22s}{'レース':>9}{'割合':>8}{'軸2車そろい率':>13}")
        for k in (0, 1, 2):
            if not n[k][0]:
                continue
            print(f"{k}車{'':19s}{n[k][0]:9,d}{n[k][0]/tot*100:7.1f}%"
                  f"{n[k][1]/n[k][0]*100:12.2f}%")
        print()


def cal():
    """印グループごとの較正（予測 p3 ↔ 実測3着内率）。"""
    print("§3 印グループ別の較正（実測 ÷ 予測）\n")
    for w in ("confirm", "explore"):
        p3, pw, mk, y, y1, _ = rows(w)
        print(f"--- {w} ---")
        print(f"{'印':>8}{'n':>9}{'予測平均':>10}{'実測':>9}{'実測/予測':>10}")
        for m, lab in ((1, "◎本命"), (2, "○対抗"), (3, "▲単穴"), (4, "△連下"), (0, "なし")):
            s = mk == m
            if not s.sum():
                continue
            print(f"{lab:>8}{s.sum():9,d}{p3[s].mean():10.4f}{y[s].mean():9.4f}"
                  f"{y[s].mean()/max(p3[s].mean(),1e-9):10.3f}")
        print()


if __name__ == "__main__":
    {"auc": auc, "axis": axis, "cal": cal}[sys.argv[1]]()
