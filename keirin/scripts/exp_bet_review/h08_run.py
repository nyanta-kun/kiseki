#!/usr/bin/env python3
"""H08 本体: レースへの商品の割り当て表（事前登録どおり・セルの決め方は1通り）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h08_run.py
前提: alloc_common.py build が alloc_base.pkl を作っていること。

事前に固定したこと（データを見る前・README「事前登録 — 二巡目」H08 の解釈を補ったもの）:
  1. レース集合 = ①（現行ラインナップ）が売るレースに固定。②は各レースの商品を {現行, その型の _sign, L_lead, 見送り} から
     選び直すだけ。①が売らないレースを②に足さない（足すと「本数を保つ」対照が成立しない）。
  2. 三分位の境界 = 2025 上期の母集団（7車 全レース）の `axis_sum` と `mdl_ent`(=shape.pw_ent) の p33/p67 を1回だけ引く
     （全体で1組。感度として型別の組も併記）。
  3. キー = 型 × axis_sum 三分位 × mdl_ent 三分位 × L_lead 条件の成立（shape.lead_legs が空でない）。L_lead なし版は最後の要素を外す。
  4. セルの決め方（1通り）: 上期の ①売りレース のうち、そのセルで候補が組める(入稿ゲート通過・L_lead は除外条件に当たらない)レース上の
     合計回収率が最大の候補。候補ごとの下限件数 N_MIN=30（上期）。セル全体が 30 未満、または現行が 30 未満なら現行のまま。
     見送りの回収率 = 上期の①全体の合計回収率（見送りは総回収率に中立）。最大の候補の回収率がこれを下回るセルは見送り。
  5. 組めない商品が割り当てられたレースは現行に戻す。`_sign`（非F）は1日5本まで（超えたら計画払戻の低い方から現行へ戻す）。
  6. ③ = 日ごとに②の割り当て本数（現行以外）を保ち、割り当て先を①売りレースの中から無作為に入れ替える（組める所だけから引く）。20 seed。
"""
from __future__ import annotations
import json, sys
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import alloc_common as C   # noqa: E402

D = C.D
N_MIN = 30
N_SEED, N_BOOT = 20, 2000
H1_END = C.H1_END
SIGN_SLOTS = C.HIGHPAY_SLOTS_PER_DAY


def tertile_cuts(recs, per_type=False):
    pop = [r for r in recs.values() if r["day"] <= H1_END]
    if not per_type:
        ax = np.array([r["axis"] for r in pop]); en = np.array([r["ent"] for r in pop])
        c = dict(axis=(np.percentile(ax, 100 / 3), np.percentile(ax, 200 / 3)),
                 ent=(np.percentile(en, 100 / 3), np.percentile(en, 200 / 3)))
        return {"*": c}
    out = {}
    for t in sorted({r["tl"] for r in pop}):
        p = [r for r in pop if r["tl"] == t]
        ax = np.array([r["axis"] for r in p]); en = np.array([r["ent"] for r in p])
        out[t] = dict(axis=(np.percentile(ax, 100 / 3), np.percentile(ax, 200 / 3)),
                      ent=(np.percentile(en, 100 / 3), np.percentile(en, 200 / 3)))
    return out


def tert(v, cuts):
    return 0 if v < cuts[0] else (1 if v < cuts[1] else 2)


def key_of(r, cuts, with_lead, per_type):
    c = cuts[r["tl"]] if per_type else cuts["*"]
    k = (r["tl"], tert(r["axis"], c["axis"]), tert(r["ent"], c["ent"]))
    return k + ((bool(r["lead_legs"]),) if with_lead else ())


def rows_with_cands(sold, recs_by_key, morning, with_lead):
    """①の売り行に候補（現行・sign・lead）を付ける。"""
    out = []
    for s in sold:
        r = recs_by_key[s["key"]]
        cur = dict(inv=s["inv"], pay=s["pay"], mean=s["mean"], plan=s["plan"])
        sg = r["sign"] if (r["sign"] and r["sign"]["gate"]) else None
        sign = None if sg is None else dict(inv=sg["inv"], pay=sg["pay"], mean=sg["mean"], plan=sg["plan"])
        ld = None
        if with_lead and C.lead_avail(r, morning):
            l = r["lead"]; ld = dict(inv=l["inv"], pay=l["pay"], mean=l["mean"], plan="L_lead")
        out.append(dict(key=s["key"], day=r["day"], rec=r, cur=cur, sign=sign, lead=ld,
                        cur_is_sign=s["plan"].endswith("_sign"), cur_is_lead=s["plan"] == "L_lead"))
    return out


def build_table(rows_h1, cuts, with_lead, per_type, roi_base, skip_never=False):
    cells = defaultdict(list)
    for x in rows_h1:
        cells[key_of(x["rec"], cuts, with_lead, per_type)].append(x)
    table, info = {}, {}
    for k, xs in cells.items():
        n = len(xs)
        def roi(sel):
            inv = sum(x[sel]["inv"] for x in xs if x[sel]); pay = sum(x[sel]["pay"] for x in xs if x[sel])
            return (pay / inv * 100 if inv else float("nan")), sum(1 for x in xs if x[sel])
        rc, nc = roi("cur"); rs, ns = roi("sign"); rl, nl = (roi("lead") if with_lead else (float("nan"), 0))
        if n < N_MIN:
            table[k] = "cur"; info[k] = (n, rc, nc, rs, ns, rl, nl, "cur(件数<N_MIN)"); continue
        elig = [("cur", rc)]
        if ns >= N_MIN: elig.append(("sign", rs))
        if with_lead and nl >= N_MIN: elig.append(("lead", rl))
        best = max(elig, key=lambda e: (e[1], e[0] == "cur"))      # 同率は現行
        # 同率で cur を優先するため (ROI, is_cur) の最大
        label = best[0]
        if not skip_never and best[1] < roi_base:
            label = "skip"
        table[k] = label; info[k] = (n, rc, nc, rs, ns, rl, nl, label)
    return table, info


def effective_labels(rows_day, table, cuts, with_lead, per_type):
    """表 → 日内の実効ラベル。組めない商品は現行へ戻す。_sign(非F) は 1 日 5 本まで。"""
    lab = {}
    for x in rows_day:
        t = table.get(key_of(x["rec"], cuts, with_lead, per_type), "cur")
        if t == "sign" and (x["sign"] is None or x["cur_is_sign"]):
            t = "cur"
        if t == "lead" and (x["lead"] is None or x["cur_is_lead"]):
            t = "cur"
        lab[x["key"]] = t
    return lab


def slot_trim(rows_day, lab):
    """非F の _sign が 1 日 5 本を超えたら、割り当てで増えた分を計画払戻の低い方から現行へ戻す。F_sign は数えない。戻した本数を返す。"""
    def nonf_sign(x, t):
        if t == "sign":
            return x["rec"]["tl"] != "F"
        if t == "cur":
            return x["cur_is_sign"] and x["cur"]["plan"][0] != "F"
        return False
    cur_n = sum(nonf_sign(x, lab[x["key"]]) for x in rows_day)
    reverted = 0
    if cur_n > SIGN_SLOTS:
        cand = sorted([x for x in rows_day if lab[x["key"]] == "sign" and x["rec"]["tl"] != "F"], key=lambda x: (x["sign"]["mean"], x["key"]))
        for x in cand:
            if cur_n <= SIGN_SLOTS:
                break
            lab[x["key"]] = "cur"; cur_n -= 1; reverted += 1
    return reverted


def tally(rows_by_day, labs, days):
    A = C.DayArr(days)
    for day, rows in rows_by_day.items():
        lab = labs[day]
        for x in rows:
            t = lab[x["key"]]
            if t == "skip":
                continue
            p = x["cur"] if t == "cur" else x[t]
            A.add(day, p["inv"], p["pay"], p["plan"])
    return A


def random_labels(rows_day, lab_tbl, rng):
    """②の実効ラベルの本数（現行以外）を保ち、組める所から無作為に引く。"""
    n_lead = sum(1 for v in lab_tbl.values() if v == "lead")
    n_sign = sum(1 for v in lab_tbl.values() if v == "sign")
    n_skip = sum(1 for v in lab_tbl.values() if v == "skip")
    lab = {x["key"]: "cur" for x in rows_day}
    short = Counter()
    def pick(cands, n, name):
        cands = [x for x in cands if lab[x["key"]] == "cur"]
        if len(cands) < n:
            short[name] += n - len(cands); n = len(cands)
        if n:
            for j in rng.choice(len(cands), n, replace=False):
                lab[cands[j]["key"]] = name
    pick([x for x in rows_day if x["lead"] is not None and not x["cur_is_lead"]], n_lead, "lead")
    pick([x for x in rows_day if x["sign"] is not None and not x["cur_is_sign"]], n_sign, "sign")
    pick(rows_day, n_skip, "skip")
    rev = slot_trim(rows_day, lab)
    return lab, short, rev


def fmt_m(m):
    return (f"{m['n_day']:.1f} | {m['shown']:.2f} | {m['roi']:.2f} | {m['mean_daily']:.2f} | {m['p10']:.2f} | {m['lt50']:.2f} | "
            f"{m['big']:.3f} | {m['ana']:.2f}")


def run_variant(name, recs, recs_by_key, morning, with_lead, per_type, skip_never, out, cells_out=None):
    L = C.lineup(recs, morning, legacy=False, with_lead=with_lead)
    days_all = sorted(L)
    h1_days = [d for d in days_all if d <= H1_END]; h2_days = [d for d in days_all if d > H1_END]
    rows_by_day = {d: rows_with_cands(L[d][0], recs_by_key, morning, with_lead) for d in days_all}
    cuts = tertile_cuts(recs, per_type)
    rows_h1 = [x for d in h1_days for x in rows_by_day[d]]
    inv1 = sum(x["cur"]["inv"] for x in rows_h1); pay1 = sum(x["cur"]["pay"] for x in rows_h1)
    roi_base = pay1 / inv1 * 100
    table, info = build_table(rows_h1, cuts, with_lead, per_type, roi_base, skip_never)
    # ラベル
    labs, rev_tot = {}, 0
    for d in days_all:
        lab = effective_labels(rows_by_day[d], table, cuts, with_lead, per_type)
        rev_tot += slot_trim(rows_by_day[d], lab)
        labs[d] = lab
    labs_cur = {d: {x["key"]: "cur" for x in rows_by_day[d]} for d in days_all}
    A1 = tally(rows_by_day, labs_cur, days_all)        # ①
    A2 = tally(rows_by_day, labs, days_all)            # ②
    seeds, short_tot, rev3 = [], Counter(), 0
    for s in range(N_SEED):
        rng = np.random.default_rng(5000 + s)
        labs3 = {}
        for d in days_all:
            lab, sh, rv = random_labels(rows_by_day[d], labs[d], rng)
            labs3[d] = lab; short_tot.update(sh); rev3 += rv
        seeds.append(tally(rows_by_day, labs3, days_all))
    # 集計
    ix_h1 = np.array([i for i, d in enumerate(days_all) if d <= H1_END]); ix_h2 = np.array([i for i, d in enumerate(days_all) if d > H1_END])
    rng_b = np.random.default_rng(20261007)
    lines = [f"### {name}\n"]
    lines.append(f"- 三分位の境界（2025 上期・7車 {sum(1 for r in recs.values() if r['day'] <= H1_END)}R）: " +
                 ("; ".join(f"{t}: axis_sum {c['axis'][0]:.3f}/{c['axis'][1]:.3f} mdl_ent {c['ent'][0]:.3f}/{c['ent'][1]:.3f}" for t, c in cuts.items())))
    lines.append(f"- 上期の①売りレース {len(rows_h1)}R・回収率 {roi_base:.2f}%（見送りの回収率とみなす値）・セル数 {len(info)}")
    dec = Counter(v[7] for v in info.values())
    nrace = Counter()
    for k, v in info.items():
        nrace[v[7]] += v[0]
    lines.append("- セルの決定（セル数 / 上期レース数）: " + ", ".join(f"{k}: {dec[k]}セル / {nrace[k]}R" for k in sorted(dec)))
    lab_cnt = Counter(v for d in days_all for v in labs[d].values())
    for nm, ixs, ds in (("上期", ix_h1, h1_days), ("下期", ix_h2, h2_days)):
        c = Counter(v for d in ds for v in labs[d].values())
        lines.append(f"- {nm}の実効ラベル（②）: " + ", ".join(f"{k} {c[k]}" for k in ("cur", "sign", "lead", "skip")) +
                     f"（計 {sum(c.values())}R）")
    lines.append(f"- `_sign` 5本超でトリム（②）: {rev_tot}R（1年合計）／③のトリム {rev3 / N_SEED:.1f}R/seed・組める候補が足りず割り当てられなかった本数 {dict(short_tot)}（20 seed 合計）\n")
    res = {}
    for nm, ix in (("2025 上期（表を決めた半期・in-sample）", ix_h1), ("2025 下期（表を決めていない半期・判定窓）", ix_h2)):
        m1 = C.metrics(A1, ix); m2 = C.metrics(A2, ix)
        m3 = [C.metrics(a, ix) for a in seeds]
        med = lambda k: float(np.median([m[k] for m in m3]))
        lines.append(f"**{nm}**（{len(ix)}日）\n")
        lines.append("| 腕 | 件/日 | 表示的中% | 回収率(合計)% | 日次回収率の平均% | 日次 p10 % | 50%割れの日% | 10万+/日 | 穴アイコン/日 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        lines.append(f"| ①現行 | {fmt_m(m1)} |")
        lines.append(f"| ②割り当て表 | {fmt_m(m2)} |")
        lines.append("| ③無作為(20 seed 中央) | " + " | ".join([
            f"{med('n_day'):.1f}", f"{med('shown'):.2f}", f"{med('roi'):.2f}", f"{med('mean_daily'):.2f}", f"{med('p10'):.2f}",
            f"{med('lt50'):.2f}", f"{med('big'):.3f}", f"{med('ana'):.2f}"]) + " |")
        lines.append(f"| ③ 日次平均の min–max | | | | {min(m['mean_daily'] for m in m3):.2f}–{max(m['mean_daily'] for m in m3):.2f} | "
                     f"{min(m['p10'] for m in m3):.2f}–{max(m['p10'] for m in m3):.2f} | | | |")
        # ブートストラップ（日の再抽出・同一再抽出）
        sub = lambda A: (A.inv[ix], A.pay[ix])
        B = C.boot_idx(len(ix), rng_b, N_BOOT)
        i1, p1 = sub(A1); i2, p2 = sub(A2)
        d21_md = C.mean_daily_of(i2, p2, B) - C.mean_daily_of(i1, p1, B)
        d21_p10 = C.p10_of(i2, p2, B) - C.p10_of(i1, p1, B)
        d21_roi = C.pooled_of(i2, p2, B) - C.pooled_of(i1, p1, B)
        md3 = np.array([C.mean_daily_of(*sub(a), B) for a in seeds]); p3 = np.array([C.p10_of(*sub(a), B) for a in seeds])
        d23_md = C.mean_daily_of(i2, p2, B) - np.median(md3, axis=0)
        d23_p10 = C.p10_of(i2, p2, B) - np.median(p3, axis=0)
        pt = lambda x, y: f"{x:+.2f} [{y[0]:+.2f}, {y[1]:+.2f}]"
        lines.append("")
        lines.append("| 差 | 日次回収率の平均 | 日次 p10 | 回収率(合計) |")
        lines.append("|---|---|---|---|")
        lines.append(f"| ②−① | {pt(m2['mean_daily'] - m1['mean_daily'], C.ci(d21_md))} | {pt(m2['p10'] - m1['p10'], C.ci(d21_p10))} | "
                     f"{pt(m2['roi'] - m1['roi'], C.ci(d21_roi))} |")
        lines.append(f"| ②−③(seed中央) | {pt(m2['mean_daily'] - med('mean_daily'), C.ci(d23_md))} | {pt(m2['p10'] - med('p10'), C.ci(d23_p10))} | |")
        wins = sum(1 for m in m3 if m2["mean_daily"] > m["mean_daily"])
        lines.append(f"\n②の日次平均が③の個々の seed を上回る数: {wins}/{N_SEED}\n")
        res["H1" if "上期" in nm else "H2"] = dict(m1=m1, m2=m2, d21_md=(m2["mean_daily"] - m1["mean_daily"], C.ci(d21_md)),
                           d23_md=(m2["mean_daily"] - med("mean_daily"), C.ci(d23_md)),
                           p10_21=(m2["p10"] - m1["p10"], C.ci(d21_p10)), wins=wins)
    h1, h2 = res["H1"], res["H2"]
    crit = [
        ("下期 日次回収率の平均 ②−③中央値 の CI 下限 > 0", h2["d23_md"][1][0] > 0, f"{h2['d23_md'][0]:+.2f} [{h2['d23_md'][1][0]:+.2f}, {h2['d23_md'][1][1]:+.2f}]"),
        ("下期 日次 p10 が ② ≥ ①", h2["m2"]["p10"] >= h2["m1"]["p10"], f"②{h2['m2']['p10']:.2f} / ①{h2['m1']['p10']:.2f}"),
        ("②−①（日次平均）が上期・下期で同符号", (h1["d21_md"][0] > 0) == (h2["d21_md"][0] > 0) and h1["d21_md"][0] != 0,
         f"上期 {h1['d21_md'][0]:+.2f} / 下期 {h2['d21_md'][0]:+.2f}"),
    ]
    lines.append("事前登録の基準の機械判定（採否は書かない）:\n")
    lines.append("| 基準 | 値 | 判定 |")
    lines.append("|---|---|---|")
    for a, b, c in crit:
        lines.append(f"| {a} | {c} | {'満たす' if b else '満たさない'} |")
    lines.append(f"\n全基準: **{'満たす' if all(b for _, b, _ in crit) else '満たさない'}**\n")
    # L_flat（紙上の参考・表の候補には入れない）: ①売りレース上と、母集団全体で
    fl = []
    for nm, ds in (("上期", h1_days), ("下期", h2_days)):
        xs = [recs_by_key[x["key"]] for d in ds for x in rows_by_day[d]]
        pop = [r for r in recs.values() if (r["day"] <= H1_END) == (nm == "上期")]
        def rr(rs):
            rs = [r["flat"] for r in rs if r["flat"] and r["flat"]["gate"]]
            inv = sum(f["inv"] for f in rs); pay = sum(f["pay"] for f in rs)
            return len(rs), (pay / inv * 100 if inv else float("nan")), sum(1 for f in rs if f["pay"] > f["inv"]), sum(1 for f in rs if f["pay"] >= 100000)
        a, b = rr(xs), rr(pop)
        fl.append(f"| {nm} | {a[0]} | {a[1]:.1f} | {a[2]} | {a[3]} | {b[0]} | {b[1]:.1f} | {b[2]} | {b[3]} |")
    lines.append("L_flat（紙上の参考・表の候補には入れない・条件は 2025 で in-sample）:\n")
    lines.append("| 窓 | ①売りレース上で組める R | 回収率% | 表示的中 | 10万+ | 母集団全体で組める R | 回収率% | 表示的中 | 10万+ |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    lines += fl
    lines.append("")
    out.append("\n".join(lines))
    if cells_out is not None:
        cl = [f"#### セル表（{name}）\n", "| キー(型, axis三分位, ent三分位" + (", L条件" if with_lead else "") + ") | 上期R | 現行回収率(n) | sign回収率(n) | lead回収率(n) | 決定 |", "|---|---|---|---|---|---|"]
        for k, v in sorted(info.items(), key=lambda kv: kv[0]):
            n, rc, nc, rs, ns, rl, nl, lb = v
            f = lambda r, c: ("-" if c == 0 else f"{r:.0f}% ({c})")
            cl.append(f"| {k} | {n} | {f(rc, nc)} | {f(rs, ns)} | {f(rl, nl)} | {lb} |")
        cells_out.append("\n".join(cl))
    return res


def main():
    recs, start = C.load_base()
    morning = C.morning_set(start)
    recs_by_key = {r["key"]: r for r in recs.values()}
    out, cells = [], []
    runs = [
        ("版A: L_lead あり（候補に L_lead・①に L_lead 段・キーに L 条件）— 主", True, False, False),
        ("版B: L_lead なし（候補は現行/sign/見送りのみ・①に L_lead 段なし・キーに L 条件なし）", False, False, False),
        ("感度1 版A + 型別の三分位", True, True, False),
        ("感度2 版A + 見送りを選ばない（見送りの回収率を 0 とみなす）", True, False, True),
    ]
    summ = {}
    for name, wl, pt, sn in runs:
        print("run", name, flush=True)
        summ[name] = run_variant(name, recs, recs_by_key, morning, wl, pt, sn, out, cells if name.startswith(("版A", "版B")) else None)
    (D / "h08_tables.md").write_text("\n\n".join(out) + "\n\n" + "\n\n".join(cells))
    print("\n\n".join(out))


if __name__ == "__main__":
    main()
