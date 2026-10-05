#!/usr/bin/env python3
"""H09 本体: 穴商品の日次本数の上限 K（事前登録どおり K ∈ {6, 8, 10, 12, 上限なし}・掃引はこれだけ）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h09_run.py
前提: alloc_common.py build が data/exp_bet_review/alloc_base.pkl を作っていること。

- 穴商品 = 設計上の表示的中 10% 未満（`_sign`・`_big`・A_ana・F_pay・L_lead）。7車の台で実在するのは
  F_sign / B,C,D_sign / A_ana / L_lead（F_pay は 7車の決勝が F_sign になるため出ない・_big は HIGHPAY_BIG_SLOTS=∅）
- 優先順位 = 計画払戻（`mean`）の降順で K 本残す。超過分はそのレースの型の hit 商品へ差し替え、
  入稿ゲート（平均2万超・1点2倍以上）を通らなければ見送り（他の穴商品へは回さない）。
- 対照: 日ごとに同じ本数を無作為に選んで同じ差し替えを当てる（20 seed）。
- 版: L_lead あり（①に L_lead 段・穴商品に含む） / L_lead なし（①に L_lead 段なし）。
- 感度: strict = 差し替えた hit にも軸信頼ゲートを掛け、日次上限に当たって落ちたレースへは戻さない。
"""
from __future__ import annotations
import json, sys
from collections import Counter
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alloc_common as C   # noqa: E402

D = C.D
KS = [6, 8, 10, 12, None]            # None = 上限なし
N_SEED, N_BOOT = 20, 2000


def replace_row(rec, s, strict):
    """超過した穴商品 s を hit へ差し替える。戻り値 (inv, pay, plan) or None（見送り）と理由。"""
    h = rec["hit"]
    if not h or not h["gate"]:
        return None, "hit組めず/入稿ゲート落ち"
    if strict:
        if not C._G.passes_axis_gate(h["plan"], rec["axis"], 7):
            return None, "軸ゲート落ち(strict)"
        if s["src"] == "cap":
            return None, "日次上限落ち(strict)"
    return (h["inv"], h["pay"], h["plan"]), "ok"


def apply_k(recs, lineup_days, days, K, *, strict=False, rng=None, stats=None):
    """K 本を超えた穴商品を差し替えた日配列を返す。rng があれば無作為対照（優先順位の代わりに無作為）。"""
    A = C.DayArr(days)
    for day, (sold, status) in lineup_days.items():
        ana = [s for s in sold if C.is_ana(s["plan"])]
        n_ex = 0 if K is None else max(0, len(ana) - K)
        drop = set()
        if n_ex:
            if rng is None:
                order = sorted(ana, key=lambda s: (-s["mean"], s["key"]))
                drop = {s["key"] for s in order[K:]}
            else:
                idx = rng.choice(len(ana), n_ex, replace=False)
                drop = {ana[j]["key"] for j in idx}
        for s in sold:
            if s["key"] in drop:
                got, why = replace_row(REC[s["key"]], s, strict)
                if stats is not None:
                    stats["demoted"] += 1
                    stats["src_" + s["src"]] += 1
                    stats["from_" + s["plan"]] += 1
                    stats["why_" + why] += 1
                    if got:
                        stats["replaced_ok"] += 1
                        stats["ok_src_" + s["src"]] += 1
                if got:
                    A.add(day, got[0], got[1], got[2])
                continue
            A.add(day, s["inv"], s["pay"], s["plan"])
    return A


def fmt(m):
    return (f"{m['n_day']:5.1f} {m['shown']:6.2f} {m['roi']:6.2f} {m['mean_daily']:6.2f} {m['p10']:6.2f} {m['lt50']:6.2f} "
            f"{m['big']:6.3f} {m['ana']:5.2f}")


def run_variant(name, recs, morning, with_lead, strict, out):
    L = C.lineup(recs, morning, legacy=False, with_lead=with_lead)
    days = sorted(L)
    nd = len(days)
    rng_b = np.random.default_rng(20261006)
    B = C.boot_idx(nd, rng_b, N_BOOT)
    res = {}
    arrs = {}
    stats_all = {}
    for K in KS:
        st = Counter()
        A = apply_k(recs, L, days, K, strict=strict, stats=st)
        arrs[K] = A
        res[K] = C.metrics(A)
        stats_all[K] = dict(st)
    # 無作為対照
    ctrl = {}
    for K in KS[:-1]:
        seeds = []
        for s in range(N_SEED):
            rng = np.random.default_rng(1000 * (K or 0) + s)
            seeds.append(apply_k(recs, L, days, K, strict=strict, rng=rng))
        ctrl[K] = seeds
    base = arrs[None]
    lines = []
    lines.append(f"### {name}\n")
    lines.append("| K | 件/日 | 表示的中% | 回収率(合計)% | 日次回収率の平均% | 日次 p10 % | 50%割れの日% | 10万+/日 | 穴アイコン/日 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for K in KS:
        m = res[K]
        lines.append(f"| {'上限なし' if K is None else K} | {m['n_day']:.1f} | {m['shown']:.2f} | {m['roi']:.2f} | {m['mean_daily']:.2f} | "
                     f"{m['p10']:.2f} | {m['lt50']:.2f} | {m['big']:.3f} | {m['ana']:.2f} |")
    lines.append("")
    lines.append("上限なしとの差（日を単位にした同一再抽出のブートストラップ 2,000 回・95%CI）と、無作為対照（20 seed）との差:\n")
    lines.append("| K | Δp10 [CI] | Δ50%割れ日% [CI] | Δ回収率(合計) [CI] | Δ日次平均 [CI] | 対照 p10 (中央 / min–max) | p10: K − 対照中央 [CI] | 対照より p10 が高い seed |")
    lines.append("|---|---|---|---|---|---|---|---|")
    crit = {}
    for K in KS[:-1]:
        A = arrs[K]
        d_p10 = C.p10_of(A.inv, A.pay, B) - C.p10_of(base.inv, base.pay, B)
        d_lt = C.lt50_of(A.inv, A.pay, B) - C.lt50_of(base.inv, base.pay, B)
        d_roi = C.pooled_of(A.inv, A.pay, B) - C.pooled_of(base.inv, base.pay, B)
        d_md = C.mean_daily_of(A.inv, A.pay, B) - C.mean_daily_of(base.inv, base.pay, B)
        cp = np.array([C.p10_of(a.inv, a.pay, B) for a in ctrl[K]])        # (seed, boot)
        d_ctrl = C.p10_of(A.inv, A.pay, B) - np.median(cp, axis=0)
        p10_pt = [C.metrics(a)["p10"] for a in ctrl[K]]
        wins = sum(1 for p in p10_pt if res[K]["p10"] > p)
        pt = lambda x, y: f"{x:+.2f} [{y[0]:+.2f}, {y[1]:+.2f}]"
        pe = res[K]["p10"] - res[None]["p10"]
        lines.append(f"| {K} | {pt(pe, C.ci(d_p10))} | {pt(res[K]['lt50'] - res[None]['lt50'], C.ci(d_lt))} | "
                     f"{pt(res[K]['roi'] - res[None]['roi'], C.ci(d_roi))} | {pt(res[K]['mean_daily'] - res[None]['mean_daily'], C.ci(d_md))} | "
                     f"{np.median(p10_pt):.2f} / {min(p10_pt):.2f}–{max(p10_pt):.2f} | "
                     f"{pt(res[K]['p10'] - np.median(p10_pt), C.ci(d_ctrl))} | {wins}/{N_SEED} |")
        crit[K] = dict(
            p10_gain=pe, p10_lo=C.ci(d_p10)[0], roi_lo=C.ci(d_roi)[0], roi_diff=res[K]["roi"] - res[None]["roi"],
            md_lo=C.ci(d_md)[0],
            ok=(C.ci(d_p10)[0] > 0 and C.ci(d_roi)[0] > -2.0))
    lines.append("")
    lines.append("事前登録の基準（ある K で p10 が上限なしより改善し差の CI 下限 > 0 ∧ 平均回収率の差の CI 下限 > −2pt）:\n")
    lines.append("| K | p10 差の CI 下限 > 0 | 回収率(合計)差の CI 下限 > −2pt | 日次平均差の CI 下限 > −2pt（参考） | 両方 |")
    lines.append("|---|---|---|---|---|")
    for K in KS[:-1]:
        c = crit[K]
        lines.append(f"| {K} | {'はい' if c['p10_lo'] > 0 else 'いいえ'} ({c['p10_lo']:+.2f}) | "
                     f"{'はい' if c['roi_lo'] > -2 else 'いいえ'} ({c['roi_lo']:+.2f}) | "
                     f"{'はい' if c['md_lo'] > -2 else 'いいえ'} ({c['md_lo']:+.2f}) | **{'はい' if c['ok'] else 'いいえ'}** |")
    lines.append("")
    lines.append("差し替えの内訳（K ごとの 1 年合計）。src = そのレースの本線（現行の hit 商品）が①で売れなかった理由 "
                 "（main=本線で売った（F_sign・A_ana など本線が穴商品）/ axis=軸信頼ゲート落ち / cap=日次上限落ち / "
                 "その他=入稿ゲート落ち・本線が組めない等）:\n")
    lines.append("| K | 差し替え対象 | うち hit で売った | 見送り | src=main | src=axis | src=cap | src=その他(L_lead) | 日次上限落ちのレースへ hit を戻した件数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for K in KS[:-1]:
        s = stats_all[K]
        oth = sum(v for k, v in s.items() if k.startswith("src_") and k not in ("src_main", "src_axis", "src_cap"))
        lines.append(f"| {K} | {s.get('demoted', 0)} | {s.get('replaced_ok', 0)} | {s.get('demoted', 0) - s.get('replaced_ok', 0)} | "
                     f"{s.get('src_main', 0)} | {s.get('src_axis', 0)} | {s.get('src_cap', 0)} | {oth} | {s.get('ok_src_cap', 0)} |")
    lines.append("")
    lines.append("差し替え対象の元プラン: " + "; ".join(
        f"K={K}: " + ", ".join(f"{k[5:]} {v}" for k, v in sorted(stats_all[K].items(), key=lambda x: -x[1]) if k.startswith("from_"))
        for K in KS[:-1]))
    lines.append("")
    # 残った穴商品の内訳（K ごと・件/日。優先順位 = 計画払戻の降順で残した分）
    lines.append("K ごとに**残る**穴商品の内訳（件/日・優先順位どおり K 本残した結果。上限なしの内訳は右端）:\n")
    plans_all = sorted({s["plan"] for sd, _ in L.values() for s in sd if C.is_ana(s["plan"])})
    lines.append("| K | " + " | ".join(plans_all) + " | 合計 |")
    lines.append("|---|" + "---|" * (len(plans_all) + 1))
    for K in KS:
        cc = Counter()
        for day, (sold, _) in L.items():
            ana = sorted([s for s in sold if C.is_ana(s["plan"])], key=lambda s: (-s["mean"], s["key"]))
            for s in (ana if K is None else ana[:K]):
                cc[s["plan"]] += 1
        lines.append(f"| {'上限なし' if K is None else K} | " + " | ".join(f"{cc[p] / nd:.2f}" for p in plans_all) + f" | {sum(cc.values()) / nd:.2f} |")
    lines.append("")
    # 日ごとの穴本数の分布
    cnt = []
    for day, (sold, _) in L.items():
        cnt.append(sum(C.is_ana(s["plan"]) for s in sold))
    cnt = np.array(cnt)
    lines.append(f"（参考）上限なしの穴商品の本数/日: 平均 {cnt.mean():.2f} / 中央 {np.median(cnt):.0f} / p10 {np.percentile(cnt, 10):.0f} / p90 {np.percentile(cnt, 90):.0f} / "
                 f"最大 {cnt.max()}。K を超える日の割合: " + ", ".join(f"K={K}: {(cnt > K).mean() * 100:.1f}%" for K in KS[:-1]) + "\n")
    out.append("\n".join(lines))
    return dict(res=res, crit=crit, stats=stats_all)


REC = None


def main():
    global REC
    recs, start = C.load_base()
    REC = {r["key"]: r for r in recs.values()}
    morning = C.morning_set(start)
    out = []
    summary = {}
    for name, with_lead, strict in (
            ("版A: L_lead あり・差し替えは入稿ゲートのみ（事前登録どおり・主）", True, False),
            ("版B: L_lead なし（①に L_lead 段なし）・差し替えは入稿ゲートのみ", False, False),
            ("感度 strict: L_lead あり・差し替えた hit にも軸信頼ゲートを掛け、日次上限落ちのレースへは戻さない", True, True)):
        print("run", name, flush=True)
        summary[name] = run_variant(name, recs, morning, with_lead, strict, out)
    (D / "h09_tables.md").write_text("\n\n".join(out))
    json.dump({k: {str(K): v for K, v in s["res"].items()} for k, s in summary.items()}, open(D / "h09_res.json", "w"), default=float)
    print("\n\n".join(out))


if __name__ == "__main__":
    main()
