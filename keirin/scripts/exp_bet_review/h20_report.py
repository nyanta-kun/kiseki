#!/usr/bin/env python3
"""H20 レポート用の表を作る（h20_run.py / h20_sales.py の結果を読む）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h20_report.py > data/exp_bet_review/h20/h20_tables.md
"""
from __future__ import annotations
import pickle, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alloc_common as C    # noqa: E402
import h20_common as H      # noqa: E402

D = C.D
res = pickle.load(open(D / "h20" / "h20_result.pkl", "rb"))
sal = pickle.load(open(D / "h20" / "h20_sales.pkl", "rb"))
P = print


def strata_table(r):
    P("| 次元 | 値 | 件数 | 開催日 | 回収率% | 95%CI | 表示的中% | 候補 |")
    P("|---|---|---|---|---|---|---|---|")
    for t in r["tab"]:
        P(f"| {t['dim']} | {t['val']} | {t['n']} | {t['days']} | {t['roi']:.1f} | [{t['lo']:.1f}, {t['hi']:.1f}] | {t['shown']:.1f} | {'**候補**' if t['cand'] else ''} |")


def main():
    P("## 付録1: 上期の全層\n")
    for nm in ("A", "B"):
        r = res[nm]
        P(f"### 版{nm}（{'L_lead あり' if nm == 'A' else 'L_lead なし'}）: 上期 売った行 {r['n_all']} 件・全体 {r['overall']:.2f}% "
          f"[{r['ov_ci'][0]:.2f}, {r['ov_ci'][1]:.2f}]・表示的中 {r['hit_all']:.2f}%・層 {len(r['tab'])} 個・候補 {len(r['cands'])} 個\n")
        P(f"五分位の切点: axis_sum {np.round(r['cut_axis'], 4).tolist()} / 合成オッズ {np.round(r['cuts_prod'][0], 3).tolist()} / 平均想定払戻(円) {np.round(r['cuts_prod'][1], 0).tolist()}\n")
        strata_table(r)
        P("")
    # 版B の最近接
    r = res["B"]
    P("## 付録2: 版B で候補に最も近かった層（n>=200 ∧ 開催日>=60・CI 上限 − 全体 の小さい順）\n")
    P("| 層 | 件数 | 回収率% | 95%CI | CI上限 − 全体(pt) |")
    P("|---|---|---|---|---|")
    near = sorted([t for t in r["tab"] if t["n"] >= 200 and t["days"] >= 60], key=lambda t: t["hi"] - r["overall"])[:6]
    for t in near:
        P(f"| {t['dim']}={t['val']} | {t['n']} | {t['roi']:.1f} | [{t['lo']:.1f}, {t['hi']:.4f}] | {t['hi'] - r['overall']:+.3f} |")
    P(f"\n版B 全体(上期) = {r['overall']:.4f}%\n")

    # 版A 下期
    A = res["A"]; e = A["ev"]
    P("## 付録3: 版A 下期の比較\n")
    m0, m2, m3 = e["m0"], e["m2"], e["m3"]
    med = lambda k: float(np.median([m[k] for m in m3]))
    rng_ = lambda k: f"{min(m[k] for m in m3):.2f}–{max(m[k] for m in m3):.2f}"
    P("| 指標 | ① 除外なし | ② 除外集合 | ③ 無作為除外(20 seed 中央 / 範囲) |")
    P("|---|---|---|---|")
    P(f"| 件/日 | {m0['n_day']:.1f} | {m2['n_day']:.1f} | {med('n_day'):.1f} / {rng_('n_day')} |")
    P(f"| 回収率(合計)% | {m0['roi']:.2f} | {m2['roi']:.2f} | {med('roi'):.2f} / {rng_('roi')} |")
    P(f"| 表示的中% | {m0['shown']:.2f} | {m2['shown']:.2f} | {med('shown'):.2f} / {rng_('shown')} |")
    P(f"| 10万+/日 | {m0['big']:.3f} | {m2['big']:.3f} | {med('big'):.3f} / {rng_('big')} |")
    P(f"| 日次回収率 p10 % | {m0['p10']:.1f} | {m2['p10']:.1f} | {med('p10'):.1f} / {rng_('p10')} |")
    P(f"| 高額枠 本/日 | {e['hp0']:.2f} | {e['hp2']:.2f} | {e['hp3']:.2f} |")
    P(f"| L_lead 本/日 | {e['ld0']:.2f} | {e['ld2']:.2f} | {e['ld3']:.2f} |")
    P(f"\n除外したレース/日: ①で売ったレース {e['nsold_day']:.1f} + 売らなかったレース {e['nunsold_day']:.1f}（合計 {e['nex_races']} レース / 184 日）。")
    P(f"\nΔROI(② − ③中央) = {e['d_ctrl_pt']:+.2f}pt CI [{e['d_ctrl_ci'][0]:+.2f}, {e['d_ctrl_ci'][1]:+.2f}]・②が個々の seed を上回る {e['ctrl_wins']}/20")
    P(f"\n② − ① = {e['d_base_pt']:+.2f}pt CI [{e['d_base_ci'][0]:+.2f}, {e['d_base_ci'][1]:+.2f}]")
    P(f"\n表示的中 ② − ① = {e['d_shown_pt']:+.2f}pt CI [{e['d_shown_ci'][0]:+.2f}, {e['d_shown_ci'][1]:+.2f}]")
    P(f"\n10万+/日 ② vs ① = {e['big_rel']:+.1f}%（③中央 {e['big_ctrl_med']:.3f}）")
    P(f"\n判定: ΔROI の CI 下限>0 = {'はい' if e['pass_roi'] else 'いいえ'} / 表示的中の低下<=1pt = {'はい' if e['pass_shown'] else 'いいえ'} / 10万+ の減少<=20% = {'はい' if e['pass_big'] else 'いいえ'} → **{'満たす' if e['passed'] else '満たさない'}**\n")
    P("候補層の下期（① の売った行）での回収率:\n")
    P(f"下期全体 {A['h2_overall']:.2f}%\n")
    P("| 層 | 上期 回収率 | 下期 件数 | 下期 回収率 | 95%CI | 下期 表示的中% |")
    P("|---|---|---|---|---|---|")
    up = {(t['dim'], t['val']): t for t in A['cands']}
    for c in A["h2_cand"]:
        t = up[(c['dim'], c['val'])]
        if c['n']:
            P(f"| {c['dim']}={c['val']} | {t['roi']:.1f} | {c['n']} | {c['roi']:.1f} | [{c['lo']:.1f}, {c['hi']:.1f}] | {c['shown']:.1f} |")
        else:
            P(f"| {c['dim']}={c['val']} | {t['roi']:.1f} | 0 | - | - | - |")

    # 目視: 下期のある1日
    P("\n## 付録4: 目視（版A・下期の1日の ① と ②）\n")
    L0, L2, E = A["L0"], A["L2"], A["E"]
    best = None
    for d in sorted(L0):
        hp0 = sum(s["slot"] == "highpay" for s in L0[d][0]); hp2 = sum(s["slot"] == "highpay" for s in L2[d][0])
        if hp0 != hp2 and 25 <= len(L0[d][0]) <= 45:
            best = d; break
    d = best or sorted(L0)[0]
    s0 = {s["key"]: s for s in L0[d][0]}; s2 = {s["key"]: s for s in L2[d][0]}
    P(f"{d}: ① {len(s0)} 件（高額枠 {sum(s['slot']=='highpay' for s in s0.values())}・L_lead {sum(s['slot']=='lead' for s in s0.values())}）→ ② {len(s2)} 件"
      f"（高額枠 {sum(s['slot']=='highpay' for s in s2.values())}・L_lead {sum(s['slot']=='lead' for s in s2.values())}）。除外レース {len(E[d])}。\n")
    P("| race_key | ① 商品(枠) | ② 商品(枠) | 除外 | ①払戻/投資 |")
    P("|---|---|---|---|---|")
    for k in sorted(set(s0) | set(s2)):
        a, b = s0.get(k), s2.get(k)
        P(f"| {k} | {a['plan'] + '(' + a['slot'] + ')' if a else '-'} | {b['plan'] + '(' + b['slot'] + ')' if b else '-'} | {'除外' if k in E[d] else ''} | "
          f"{(str(round(a['pay'])) + '/' + str(round(a['inv']))) if a else '-'} |")

    P("\n## 付録5: 実売（2026-08-29〜10-05・38 開催日・7車 live・採点済み）\n")
    for nm in ("A", "B"):
        o = sal[nm]
        al = o["all"]
        P(f"### 版{nm}: 全体 n={al['n']} ・回収率 {al['roi']:.1f}% [{al['lo']:.1f}, {al['hi']:.1f}]・表示的中 {al['shown']:.1f}%\n")
        P("| 層 | 上期(2025) 回収率 [CI] | 実売 件数 | 実売 開催日 | 実売 回収率 | 95%CI | 実売 表示的中% |")
        P("|---|---|---|---|---|---|---|")
        for dim, val, t, s in o["focus"]:
            if s:
                P(f"| {dim}={val} | {t['roi']:.1f} [{t['lo']:.1f}, {t['hi']:.1f}] | {s['n']} | {s['days']} | {s['roi']:.1f} | [{s['lo']:.1f}, {s['hi']:.1f}] | {s['shown']:.1f} |")
        if o["union"]:
            u, rr = o["union"], o["rest"]
            P(f"\n候補の和集合に当たる実売: n={u['n']} 回収率 {u['roi']:.1f}% [{u['lo']:.1f}, {u['hi']:.1f}] 表示的中 {u['shown']:.1f}% / 当たらない: n={rr['n']} {rr['roi']:.1f}% [{rr['lo']:.1f}, {rr['hi']:.1f}] 表示的中 {rr['shown']:.1f}%")
        P("")


if __name__ == "__main__":
    main()
