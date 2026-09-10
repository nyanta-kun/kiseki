#!/usr/bin/env python3
"""相手（3着候補）の選定を見直せるか（2026-09-10・ユーザー依頼）。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

- 売る1商品は `src.type_lab.sell_plans_for`（看板枠 → 型Aの3分割 → 型Fの種別）。
- **「相手」が明示的に存在するのは三連複の2プランだけ**:
    `A_trio` = `axis2_flow`   … 軸2車＋相手を p3 降順に 2点
    `D_hit`  = `axis2_drop_fav` … 軸2車＋相手5点から最人気1点を落とし、確率上位3点
  三連単の主力（A_hit/B_hit/C_hit/E_hit/F_hit）は `prob_top`＝**軸の概念が無く**、
  210点を確率降順に帯・Σ・点数の枠まで積むだけ。相手のカバレッジは結果として決まる。
  `F_pay`/`A_pay` は `axis1_second2`（1着=軸1固定・2着2車・3着 pool を p3 順に n 点）。
- 確率は `src.strategy_wt.rank_7t3_blend_probs`（PL に λ=2.0 / μ=1.5 の同ライン隣接
  ボーナス）。**板の PROB はボーナス前**。
- 既に入っている「相手・並びの操作」: `apply_line_swap`（B/C/E/F_hit・確率下位 m 点を
  同一ライン3車の目へ差し替え・10倍未満は採らない）、`_insert_underband`（C_hit / F_hit の
  ゲート代替）、`axis2_drop_fav`（D_hit の最人気1点落とし）。
- ゲートは3段: 軸信頼（`AXIS_GATE_MIN`）→ 買い目を組む → 入稿ゲート
  （平均想定払戻 > 20,000円・全点の予測オッズ >= 2.0倍）。

台: /tmp/race_type_board.npz（7車 36,427R・vintage walk-forward）。
窓: 探索 2024-07〜2025-12 / 確認 2026-01〜08。
"""
from __future__ import annotations

import itertools
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402

PERMS, C3 = C.CANON, C.CANON3
C3IDX = C.C3IDX


def _mask(win: str) -> np.ndarray:
    return C.select(None, win)


# ═════════════════════════ §1 相手は誰か（記述） ═════════════════════════

KEYS = ("p3", "trio_pr", "pw", "mkt", "line_axis", "mark", "lpos", "st_nige",
        "rp", "pr_mkt", "pr_line", "mkt_line")


def describe() -> None:
    """軸2車がそろった回の3着車を、5つの候補からどの量が当てるか。"""
    z = C.board()
    P3, PW, LG, LPOS, MARK, RP, ST = (z["P3"], z["PW"], z["LG"], z["A_line_pos"],
                                      z["A_prediction_mark"], z["A_race_point"], z["ST"])
    TPO = z["TRIO_PO"]
    from src.strategy_wt import rank_7t3_blend_probs
    cars = list(range(1, 8))
    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        idx = _mask(win)
        hit = {k: [0, 0, 0] for k in KEYS}     # top1 / top2 / top3
        n = 0
        for i in idx:
            i = int(i)
            p3 = P3[i]
            order = list(np.argsort(-p3) + 1)
            a1, a2 = order[0], order[1]
            fin = set(PERMS[int(z["WIN"][i])])
            if a1 not in fin or a2 not in fin:
                continue
            third = next(c for c in fin if c not in (a1, a2))
            cand = [c for c in order[2:]]
            n += 1
            lg = {c: str(LG[i][c - 1]) for c in range(1, 8)}
            axis_g = {lg[a1], lg[a2]} - {"", "0"}
            pr = rank_7t3_blend_probs(
                cars, {c: float(PW[i][c - 1]) for c in cars},
                {c: float(p3[c - 1]) for c in cars},
                line_group={c: LG[i][c - 1] for c in cars},
                line_pos={c: LPOS[i][c - 1] for c in cars})
            t3pr = {}
            for c in cand:
                t3pr[c] = sum(pr.get(q, 0.0)
                              for q in itertools.permutations((a1, a2, c)))
            sc = {}
            sc["p3"] = {c: float(p3[c - 1]) for c in cand}
            sc["trio_pr"] = t3pr
            sc["pw"] = {c: float(PW[i][c - 1]) for c in cand}
            sc["mkt"] = {c: -float(TPO[i][C3IDX[frozenset({a1, a2, c})]]) for c in cand}
            sc["line_axis"] = {c: (1.0 if lg[c] in axis_g else 0.0) + float(p3[c - 1]) * 1e-3
                               for c in cand}
            sc["line_any"] = {c: (0.0 if lg[c] in ("", "0") else 1.0)
                              + float(p3[c - 1]) * 1e-3 for c in cand}
            sc["mark"] = {c: (-float(MARK[i][c - 1]) if MARK[i][c - 1] > 0 else -9.0)
                          + float(p3[c - 1]) * 1e-3 for c in cand}
            sc["lpos"] = {c: (-float(LPOS[i][c - 1]) if LPOS[i][c - 1] > 0 else -9.0)
                          + float(p3[c - 1]) * 1e-3 for c in cand}
            sc["st_nige"] = {c: (1.0 if str(ST[i][c - 1]) in ("逃", "捲") else 0.0)
                             + float(p3[c - 1]) * 1e-3 for c in cand}
            sc["rp"] = {c: float(RP[i][c - 1]) for c in cand}
            # 合成（順位の平均・小さいほど良い → 符号反転）
            def _rk(d):
                o = sorted(cand, key=lambda c: -d[c])
                return {c: j for j, c in enumerate(o)}
            r_pr, r_mkt = _rk(t3pr), _rk(sc["mkt"])
            r_ln = _rk(sc["line_axis"])
            sc["pr_mkt"] = {c: -(r_pr[c] + r_mkt[c]) for c in cand}
            sc["pr_line"] = {c: -(r_pr[c] + r_ln[c]) for c in cand}
            sc["mkt_line"] = {c: -(r_mkt[c] + r_ln[c]) for c in cand}
            for k in KEYS:
                rk = sorted(cand, key=lambda c: -sc[k][c])
                for j in range(3):
                    if third in rk[:j + 1]:
                        hit[k][j] += 1
        print(f"\n=== {label}  軸2車そろい n={n:,} ===")
        print(f"  {'量':12s} {'top1%':>7s} {'top2%':>7s} {'top3%':>7s}")
        for k in KEYS:
            h = hit[k]
            print(f"  {k:12s} {h[0]/n*100:7.2f} {h[1]/n*100:7.2f} {h[2]/n*100:7.2f}")
        print(f"  {'無作為':12s} {1/5*100:7.2f} {2/5*100:7.2f} {3/5*100:7.2f}")


# ═════════════════════ §2 集合（三連複35点）のカバレッジ ═════════════════════

def _blend_rank(a: dict, b: dict, w: float) -> dict:
    """順位の加重平均（小さいほど上位）。w=0 で a のみ、1 で b のみ。"""
    ra = {c: j for j, c in enumerate(sorted(a, key=lambda x: -a[x]))}
    rb = {c: j for j, c in enumerate(sorted(b, key=lambda x: -b[x]))}
    return {c: -((1 - w) * ra[c] + w * rb[c]) for c in a}


def coverage() -> None:
    """決着の三連複集合が上位 k 点に入る率。モデル確率 ↔ 市場（予測オッズ）。"""
    z = C.board()
    P3, PW, LG, LPOS, TPO = (z["P3"], z["PW"], z["LG"], z["A_line_pos"], z["TRIO_PO"])
    from src.strategy_wt import rank_7t3_blend_probs
    cars = list(range(1, 8))
    KS = (2, 3, 4, 5, 6, 8, 10, 14)
    arms = ["model", "market", "w.25", "w.50", "w.75"]
    for label, win in (("探索", "explore"), ("確認", "confirm")):
        idx = _mask(win)
        cov = {a: {k: 0 for k in KS} for a in arms}
        cov_both = {a: {k: 0 for k in KS} for a in arms}
        n = nb = 0
        for i in idx:
            i = int(i)
            p3 = P3[i]
            order = list(np.argsort(-p3) + 1)
            a1, a2 = order[0], order[1]
            po = {frozenset(c): float(TPO[i][j]) for j, c in enumerate(C3)
                  if np.isfinite(TPO[i][j]) and TPO[i][j] > 0}
            if len(po) < 30:
                continue
            pr = rank_7t3_blend_probs(
                cars, {c: float(PW[i][c - 1]) for c in cars},
                {c: float(p3[c - 1]) for c in cars},
                line_group={c: LG[i][c - 1] for c in cars},
                line_pos={c: LPOS[i][c - 1] for c in cars})
            mdl = {k: sum(pr.get(q, 0.0) for q in itertools.permutations(sorted(k)))
                   for k in po}
            mkt = {k: 1.0 / po[k] for k in po}
            wt3 = frozenset(C3[int(z["TRIO_WIN"][i])])
            if wt3 not in po:
                continue
            n += 1
            fin = set(PERMS[int(z["WIN"][i])])
            both = a1 in fin and a2 in fin
            nb += both
            for a in arms:
                if a == "model":
                    s = mdl
                elif a == "market":
                    s = mkt
                else:
                    s = _blend_rank(mdl, mkt, float(a[1:]))
                rk = sorted(s, key=lambda x: -s[x])
                pos = rk.index(wt3)
                for k in KS:
                    if pos < k:
                        cov[a][k] += 1
                        if both:
                            cov_both[a][k] += 1
        print(f"\n=== {label}  n={n:,}（うち軸2車そろい {nb:,}）===")
        for tag, d, den in (("全体", cov, n), ("軸2車そろい時", cov_both, nb)):
            print(f"  -- {tag} --")
            print("  {:8s}".format("腕") + "".join(f"{k:>8d}" for k in KS))
            for a in arms:
                print(f"  {a:8s}" + "".join(f"{d[a][k]/den*100:8.2f}" for k in KS))




# ═══════════ §3 型ごとに「3着の出どころ」は違うか（設計との整合）═══════════

TYPES = tuple("ABCDEF")


def bytype() -> None:
    """型 A〜F ごとに、軸2車そろい率／3着車の p3 順位分布／相手を当てる量。

    現行設計の建て付け:
      固い型 A/B … 軸から**絞る**（A_hit 3点・B_hit Σ床3万で 5〜8点）
      C          … 堅いが崩れ筋。帯15倍で**点数を増やす**（12点）＋帯下1点
      D          … 混戦・軸あり。三連複で相手を**3点に絞り最人気を落とす**
      E/F        … 混戦・大混戦。帯30倍14点 / 帯5倍12点で**広げる**
    """
    z = C.board()
    P3, PW, LG, LPOS, MARK, TPO = (z["P3"], z["PW"], z["LG"], z["A_line_pos"],
                                   z["A_prediction_mark"], z["TRIO_PO"])
    TYPE = z["TYPE"]
    from src.strategy_wt import rank_7t3_blend_probs
    cars = list(range(1, 8))
    keys = ("trio_pr", "mkt", "line_axis")
    for label, win in (("探索", "explore"), ("確認", "confirm")):
        idx = _mask(win)
        agg = {t: dict(n=0, both=0, dist=[0] * 5,
                       hit={k: [0, 0, 0] for k in ("p3",) + keys}) for t in TYPES}
        for i in idx:
            i = int(i)
            t = str(TYPE[i])
            if t not in agg:
                continue
            a = agg[t]
            a["n"] += 1
            p3 = P3[i]
            order = list(np.argsort(-p3) + 1)
            a1, a2 = order[0], order[1]
            fin = set(PERMS[int(z["WIN"][i])])
            if a1 not in fin or a2 not in fin:
                continue
            a["both"] += 1
            third = next(c for c in fin if c not in (a1, a2))
            cand = list(order[2:])
            a["dist"][cand.index(third)] += 1
            pr = rank_7t3_blend_probs(
                cars, {c: float(PW[i][c - 1]) for c in cars},
                {c: float(p3[c - 1]) for c in cars},
                line_group={c: LG[i][c - 1] for c in cars},
                line_pos={c: LPOS[i][c - 1] for c in cars})
            lg = {c: str(LG[i][c - 1]) for c in cars}
            axis_g = {lg[a1], lg[a2]} - {"", "0"}
            sc = {
                "p3": {c: float(p3[c - 1]) for c in cand},
                "trio_pr": {c: sum(pr.get(q, 0.0)
                                   for q in itertools.permutations((a1, a2, c)))
                            for c in cand},
                "mkt": {c: -float(TPO[i][C3IDX[frozenset({a1, a2, c})]]) for c in cand},
                "line_axis": {c: (1.0 if lg[c] in axis_g else 0.0)
                              + float(p3[c - 1]) * 1e-3 for c in cand},
            }
            for k in ("p3",) + keys:
                rk = sorted(cand, key=lambda c: -sc[k][c])
                for j in range(3):
                    if third in rk[:j + 1]:
                        a["hit"][k][j] += 1
        print(f"\n=== {label} ===")
        print(f"  {'型':4s} {'n':>6s} {'そろい%':>7s} | 3着車の p3 順位（そろい時の%）"
              f"      | {'相手 top1%':>10s} {'top2%':>7s} {'top3%':>7s}")
        for t in TYPES:
            a = agg[t]
            if not a["both"]:
                continue
            d = [x / a["both"] * 100 for x in a["dist"]]
            h = a["hit"]["p3"]
            print(f"  {t:4s} {a['n']:6d} {a['both']/a['n']*100:7.2f} |"
                  + "".join(f"{v:6.1f}" for v in d)
                  + f"      | {h[0]/a['both']*100:10.2f} {h[1]/a['both']*100:7.2f}"
                    f" {h[2]/a['both']*100:7.2f}")
        print(f"\n  -- 相手を当てる量の比較（top1% / そろい時）--")
        print("  {:4s}".format("型") + "".join(f"{k:>12s}" for k in ("p3",) + keys))
        for t in TYPES:
            a = agg[t]
            if not a["both"]:
                continue
            print(f"  {t:4s}" + "".join(
                f"{a['hit'][k][0]/a['both']*100:12.2f}" for k in ("p3",) + keys))
        print(f"\n  -- 同 top3%（相手を上位3車まで買ったときのカバレッジ）--")
        print("  {:4s}".format("型") + "".join(f"{k:>12s}" for k in ("p3",) + keys))
        for t in TYPES:
            a = agg[t]
            if not a["both"]:
                continue
            print(f"  {t:4s}" + "".join(
                f"{a['hit'][k][2]/a['both']*100:12.2f}" for k in ("p3",) + keys))



if __name__ == "__main__":
    globals()[sys.argv[1] if len(sys.argv) > 1 else "describe"]()
