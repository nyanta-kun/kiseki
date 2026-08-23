#!/usr/bin/env python3
"""軸1が飛ぶレースを**別の層として**扱えるか（2026-08-23・ユーザー指摘）。

## 指摘

> 項目2の「改善できない」は全体の単一の学習の影響ではないか。
> 軸1が外れているレースに限定し、1着になっている選手の傾向を見ることで、
> 基本的なレースは既存指数で推奨し、軸1が着外になるレースは別ものとして
> 分析できないか。

🔴 **もっともな指摘。** `exp_axis1_selection.py` は**全レースで1本のモデル**を学習し
argmax を比べただけで、母集団の 79.3%（軸1が当たるレース）に薄められている。
「層別に学習し直す」ことは一度も測っていない
（[[keirin_irregular_layer_screening_2026_08_20]] も**市場との差**を測ったもので、
層別学習は 🟡未検証 として残っている）。

## 測る順序

1. **検出できるか** — 軸1が3着を外すレースを事前に当てられるか（AUC・十分位）
2. **層別学習に意味があるか** — 高リスク層の中で
   ①現行(p3最大) ②全体モデル ③**その層だけで学習したモデル** を比べる
   🔴 ③が①②を上回って初めて「単一の学習だったせい」と言える
3. **飛んだとき誰が来るのか** — 1着車の p3 順位・脚質・ライン位置の分布
4. **純化** — 高リスク層を学習から抜くと、残りの層への識別が鋭くなるか
5. **振り分け** — 高リスク層を見送ると商品の的中/ROI はどうなるか
   （🔴 ROI ではなく KPI の話なので**壁の下でも成立しうる**）

🔴 学習と検定は年をまたぐ独立窓。`--swap` で逆向きも回す。
🔴 層の定義に使う検出器のスコアは、学習窓側では**年で2分割した交差適合**で作る
   （自分自身を in-sample で層別すると層が過度に綺麗に切れる）。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    fit, load_any, load_boards)
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402

PAYOUT_RATE = 0.7485

CAR_COLS = ["player_class", "style", "race_point", "line_group", "line_size",
            "line_pos", "is_line_leader", "n_lines", "s_count", "b_count",
            "first_rate", "second_rate", "third_rate", "gear_ratio",
            "ex_spurt_pct", "ex_thrust_pct", "ex_left_behind_pct",
            "ex_split_line_pct", "ex_snatch_pct"]

CLASS_MAP = {"S": 3, "A": 2, "B": 1}
STYLE_MAP = {"逃": 1, "捲": 2, "追": 3, "両": 4}


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def load_rich(keys):
    con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = con.cursor()
    out = defaultdict(dict)
    q = ("select race_key, frame_no, " + ", ".join(CAR_COLS) +
         " from keirin.wt_entries where race_key = any(%s)")
    for i in range(0, len(keys), 2000):
        cur.execute(q, (keys[i:i + 2000],))
        for row in cur.fetchall():
            rk, fn = row[0], int(row[1])
            d = dict(zip(CAR_COLS, row[2:]))
            cls = str(d["player_class"] or "")[:1]
            sty = str(d["style"] or "")[:1]
            out[rk][fn] = dict(
                cls=CLASS_MAP.get(cls, 0), style=STYLE_MAP.get(sty, 0),
                rp=_f(d["race_point"]), lg=d["line_group"],
                lsize=_f(d["line_size"]), lpos=_f(d["line_pos"]),
                leader=int(bool(d["is_line_leader"])), n_lines=_f(d["n_lines"]),
                s_cnt=_f(d["s_count"]), b_cnt=_f(d["b_count"]),
                r1=_f(d["first_rate"]), r2=_f(d["second_rate"]),
                r3=_f(d["third_rate"]), gear=_f(d["gear_ratio"]),
                ex_spurt=_f(d["ex_spurt_pct"]), ex_thrust=_f(d["ex_thrust_pct"]),
                ex_behind=_f(d["ex_left_behind_pct"]),
                ex_split=_f(d["ex_split_line_pct"]),
                ex_snatch=_f(d["ex_snatch_pct"]))
    con.close()
    return out


CAR_FEATS = [
    "p3", "rank", "p3_share", "gap_up", "gap_dn", "p3_minus_mean", "z_in_race",
    "rp", "rp_gap_top", "cls", "style", "s_cnt", "b_cnt",
    "r1", "r2", "r3", "gear",
    "ex_spurt", "ex_thrust", "ex_behind", "ex_split", "ex_snatch",
    "lsize", "lpos", "leader", "line_p3_sum", "line_p3_rank",
    "same_line_as_top1", "n_lines", "max_lsize", "p3_ent", "p3_std",
]

RACE_FEATS = [
    "p3_1", "p3_2", "p3_3", "gap_12", "gap_13", "gap_23", "p3_ent", "p3_std",
    "p3_sum", "n_lines", "max_lsize", "top1_lsize", "top1_lpos", "top1_leader",
    "top1_cls", "top1_style", "top1_rp", "top1_rp_gap", "top1_r1", "top1_r3",
    "top1_s", "top1_b", "top1_ex_behind", "top1_ex_split",
    "top12_same_line", "top13_same_line", "n_top3_in_top1_line",
    "rp_std", "cls_std", "n_nige", "n_oikomi", "field_r1_mean",
]


def build(races, ent, fins):
    """車行（CAR_FEATS）とレース行（RACE_FEATS・目的＝軸1が3着を外す）。"""
    Xc, yc, mc = [], [], []
    Xr, yr, mr = [], [], []
    for r in races:
        e = ent.get(r["key"]); o3 = fins.get(r["key"])
        if not e or not o3:
            continue
        o, p3 = r["order"], r["p3"]
        if len(o) < 7 or len(e) < 7:
            continue
        top3 = {c for w in winning_trifectas(o3) for c in w}
        winner = winning_trifectas(o3)[0][0]
        vals = np.array([p3[c] for c in o], dtype=float)
        mean, std, tot = vals.mean(), max(vals.std(), 1e-9), max(vals.sum(), 1e-9)
        groups = {e[c]["lg"] for c in o if e[c]["lg"] is not None}
        n_lines = float(len(groups) or 1)
        max_ls = max(e[c]["lsize"] for c in o)
        q = vals / tot
        p3_ent = float(-(q * np.log(q + 1e-12)).sum() / np.log(len(q)))
        line_p3 = defaultdict(float)
        for c in o:
            line_p3[e[c]["lg"]] += p3[c]
        lrank = {g: i + 1 for i, g in enumerate(
            sorted(line_p3, key=lambda g: -line_p3[g]))}
        rp_top = max(e[c]["rp"] for c in o)
        a1 = o[0]
        for i, c in enumerate(o):
            ec = e[c]
            Xc.append([
                p3[c], float(i + 1), p3[c] / tot,
                (p3[o[i - 1]] - p3[c]) if i > 0 else 0.0,
                (p3[c] - p3[o[i + 1]]) if i + 1 < len(o) else 0.0,
                p3[c] - mean, (p3[c] - mean) / std,
                ec["rp"], rp_top - ec["rp"], ec["cls"], ec["style"],
                ec["s_cnt"], ec["b_cnt"], ec["r1"], ec["r2"], ec["r3"],
                ec["gear"], ec["ex_spurt"], ec["ex_thrust"], ec["ex_behind"],
                ec["ex_split"], ec["ex_snatch"],
                ec["lsize"], ec["lpos"], ec["leader"],
                line_p3[ec["lg"]], float(lrank[ec["lg"]]),
                float(ec["lg"] is not None and ec["lg"] == e[a1]["lg"]),
                n_lines, max_ls, p3_ent, float(vals.std()),
            ])
            yc.append(int(c in top3))
            mc.append((r["key"], r["date"], c, i + 1, int(c == winner)))
        ea = e[a1]
        Xr.append([
            p3[o[0]], p3[o[1]], p3[o[2]],
            p3[o[0]] - p3[o[1]], p3[o[0]] - p3[o[2]], p3[o[1]] - p3[o[2]],
            p3_ent, float(vals.std()), float(vals.sum()), n_lines, max_ls,
            ea["lsize"], ea["lpos"], ea["leader"], ea["cls"], ea["style"],
            ea["rp"], rp_top - ea["rp"], ea["r1"], ea["r3"],
            ea["s_cnt"], ea["b_cnt"], ea["ex_behind"], ea["ex_split"],
            float(e[o[1]]["lg"] == ea["lg"]), float(e[o[2]]["lg"] == ea["lg"]),
            float(sum(1 for c in o[:3] if e[c]["lg"] == ea["lg"])),
            float(np.std([e[c]["rp"] for c in o])),
            float(np.std([e[c]["cls"] for c in o])),
            float(sum(1 for c in o if e[c]["style"] == 1)),
            float(sum(1 for c in o if e[c]["style"] == 3)),
            float(np.mean([e[c]["r1"] for c in o])),
        ])
        yr.append(int(a1 not in top3))          # 🔴 1 = 軸1が飛んだ
        mr.append((r["key"], r["date"], a1, winner,
                   o.index(winner) + 1 if winner in o else -1))
    return (np.array(Xc, np.float32), np.array(yc, np.int8), mc,
            np.array(Xr, np.float32), np.array(yr, np.int8), mr)


def auc(y, p):
    y = np.asarray(y); p = np.asarray(p)
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    r = np.argsort(np.argsort(p)) + 1
    return (r[y == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def day_diff_ci(days, B=4000, seed=23):
    """days: {date: [n, base_hit, alt_hit]} → (Δ, lo, hi)"""
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return (v[:, 2].sum() / v[:, 0].sum() - v[:, 1].sum() / v[:, 0].sum(),
            d[int(B * .025)], d[int(B * .975)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--swap", action="store_true")
    ap.add_argument("--top-frac", type=float, default=0.20,
                    help="高リスク層の割合")
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr
    def span(v):
        return f"{min(r['date'] for r in v)}〜{max(r['date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）")

    ent_tr, ent_te = load_rich([r["key"] for r in tr]), load_rich([r["key"] for r in te])
    fin_tr, fin_te = _load_finishes([r["key"] for r in tr]), _load_finishes([r["key"] for r in te])
    Xc_tr, yc_tr, mc_tr, Xr_tr, yr_tr, mr_tr = build(tr, ent_tr, fin_tr)
    Xc_te, yc_te, mc_te, Xr_te, yr_te, mr_te = build(te, ent_te, fin_te)
    print(f"レース行 学習 {len(Xr_tr):,} / 検定 {len(Xr_te):,}"
          f"   軸1が飛ぶ率 学習 {yr_tr.mean():.2%} / 検定 {yr_te.mean():.2%}\n")

    # ── 1. 検出できるか ──
    det = fit(Xr_tr, yr_tr, args.rounds)
    s_te = det.predict(Xr_te)
    print(f"【1. 軸1バストの検出】AUC {auc(yr_te, s_te):.4f}")
    dq = np.quantile(s_te, [i / 10 for i in range(1, 10)])
    b = np.digitize(s_te, dq)
    print(f"{'十分位':>8}{'件数':>8}{'実測バスト率':>13}{'予測平均':>10}")
    for i in range(10):
        m = b == i
        if m.sum():
            print(f"{f'D{i+1}':>8}{m.sum():>8,}{yr_te[m].mean():>13.2%}"
                  f"{s_te[m].mean():>10.2%}")

    # 交差適合で学習窓のスコアを作る（層の定義用）
    # 🔴 fold は**日で交互**に割る。年で割ると学習窓が1年しか無いとき
    #    fold が作れず in-sample のスコアで層を切ってしまう（層が過度に綺麗になる）。
    days_sorted = sorted({d[1] for d in mr_tr})
    fold_of = {d: i % 2 for i, d in enumerate(days_sorted)}
    folds = np.array([fold_of[d[1]] for d in mr_tr])
    s_tr = np.zeros(len(Xr_tr))
    for k_ in (0, 1):
        m = folds == k_
        s_tr[m] = fit(Xr_tr[~m], yr_tr[~m], args.rounds).predict(Xr_tr[m])
    thr = np.quantile(s_tr, 1 - args.top_frac)
    hi_tr = s_tr >= thr
    hi_te = s_te >= thr
    print(f"\n  高リスク層のしきい値 {thr:.3f}"
          f"  → 学習 {hi_tr.mean():.1%} / 検定 {hi_te.mean():.1%}"
          f"   その層の実測バスト率 学習 {yr_tr[hi_tr].mean():.2%}"
          f" / 検定 {yr_te[hi_te].mean():.2%}")

    # ── 3. 飛んだとき誰が来たか ──
    print(f"\n【3. 軸1が飛んだレースの1着車】")
    bust = [m for m, b_ in zip(mr_te, yr_te) if b_]
    ok = [m for m, b_ in zip(mr_te, yr_te) if not b_]
    dist = defaultdict(int)
    for m in bust:
        dist[m[4]] += 1
    print(f"  軸1バスト {len(bust):,}R の1着車の p3 順位: " +
          " / ".join(f"{k}位 {dist[k]/len(bust):.1%}" for k in sorted(dist) if k > 0))
    dist2 = defaultdict(int)
    for m in ok:
        dist2[m[4]] += 1
    print(f"  （参考）軸1が3着内 {len(ok):,}R: " +
          " / ".join(f"{k}位 {dist2[k]/len(ok):.1%}" for k in sorted(dist2) if k > 0))

    # ── 2. 層別学習 ──
    key_hi_te = {m[0] for m, h in zip(mr_te, hi_te) if h}
    key_hi_tr = {m[0] for m, h in zip(mr_tr, hi_tr) if h}
    m_glob = fit(Xc_tr, yc_tr, args.rounds)
    sel = np.array([m[0] in key_hi_tr for m in mc_tr])
    m_strat = fit(Xc_tr[sel], yc_tr[sel], args.rounds)
    m_pure = fit(Xc_tr[~sel], yc_tr[~sel], args.rounds)
    p_glob, p_strat, p_pure = (m_glob.predict(Xc_te), m_strat.predict(Xc_te),
                               m_pure.predict(Xc_te))
    print(f"\n  層別モデルの学習量: {sel.sum():,}行"
          f"（{sel.sum()//7:,}R） / 純化モデル {(~sel).sum():,}行")

    by_race = defaultdict(list)
    for (key, date, c, rk, isw), a, b_, c_ in zip(mc_te, p_glob, p_strat, p_pure):
        by_race[key].append((rk, c, float(a), float(b_), float(c_), isw))
    top3_of = {}
    for r in te:
        o3 = fin_te.get(r["key"])
        if o3:
            top3_of[r["key"]] = {c for w in winning_trifectas(o3) for c in w}
    date_of = {m[0]: m[1] for m in mr_te}

    def stratum_report(title, keys):
        """各腕でレース内を並べ替え、上位1車・上位2車の的中を測る。"""
        arms = {"現行(p3最大)": lambda x: -x[0],   # p3 順位の昇順
                "全体モデル": lambda x: x[2],
                "層別モデル": lambda x: x[3],
                "純化モデル": lambda x: x[4]}
        d1 = {a: defaultdict(lambda: [0, 0, 0]) for a in arms}
        n = 0
        for key in keys:
            v = by_race.get(key); t3 = top3_of.get(key)
            if not v or len(v) != 7 or t3 is None:
                continue
            n += 1
            d = date_of[key]
            for a, kf in arms.items():
                seq = sorted(v, key=kf, reverse=True)
                z = d1[a][d]
                z[0] += 1
                z[1] += int(seq[0][1] in t3)
                z[2] += int(seq[0][1] in t3 and seq[1][1] in t3)
        if not n:
            return
        print(f"\n【2. {title}・{n:,}R】")
        print(f"{'腕':>14}{'軸1の3着内':>12}{'（対現行）':>26}"
              f"{'二軸的中':>10}{'（対現行）':>26}")
        base = d1["現行(p3最大)"]
        b1 = sum(z[1] for z in base.values()) / n
        b2 = sum(z[2] for z in base.values()) / n
        for a in arms:
            h1 = sum(z[1] for z in d1[a].values()) / n
            h2 = sum(z[2] for z in d1[a].values()) / n
            if a == "現行(p3最大)":
                print(f"{a:>14}{h1:>12.2%}{'':>26}{h2:>10.2%}{'':>26}")
                continue
            dd1 = {d: [base[d][0], base[d][1], d1[a][d][1]] for d in base}
            dd2 = {d: [base[d][0], base[d][2], d1[a][d][2]] for d in base}
            _, l1, u1 = day_diff_ci(dd1)
            _, l2, u2 = day_diff_ci(dd2)
            f1 = "🟢" if l1 > 0 else ("🔴" if u1 < 0 else "")
            f2 = "🟢" if l2 > 0 else ("🔴" if u2 < 0 else "")
            c1 = f"Δ{(h1-b1)*100:+.2f}pt[{l1*100:+.2f},{u1*100:+.2f}]{f1}"
            c2 = f"Δ{(h2-b2)*100:+.2f}pt[{l2*100:+.2f},{u2*100:+.2f}]{f2}"
            print(f"{a:>14}{h1:>12.2%}{c1:>26}{h2:>10.2%}{c2:>26}")

    stratum_report(f"高リスク層（上位{args.top_frac:.0%}）", key_hi_te)
    stratum_report("低リスク層（残り）",
                   {m[0] for m, h in zip(mr_te, hi_te) if not h})
    stratum_report("全レース", {m[0] for m in mr_te})

    # ── 5. 振り分け（高リスク層を見送る） ──
    board = load_boards([r["key"] for r in te])
    stake = unit_stake(1)
    seg = defaultdict(lambda: [0, 0, 0.0, 0.0])   # 層 -> [n, hit, bet, pay]
    for r in te:
        key = r["key"]
        v = by_race.get(key); bd = board.get(key); o3 = fin_te.get(key)
        if not v or bd is None or not o3 or len(v) != 7:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        o = r["order"]
        k = frozenset((o[0], o[1], o[2]))
        if k not in bd:
            continue
        lab = "高リスク" if key in key_hi_te else "低リスク"
        z = seg[lab]
        z[0] += 1; z[2] += stake
        if k in wins:
            z[1] += 1; z[3] += int(bd[k] * 100) * stake // 100
    print(f"\n【5. 振り分け（現行の3車1点・三連複）】")
    print(f"{'層':>10}{'件数':>9}{'的中%':>9}{'ROI':>9}")
    for lab in ("低リスク", "高リスク"):
        z = seg[lab]
        if z[0]:
            print(f"{lab:>10}{z[0]:>9,}{z[1]/z[0]:>9.2%}{z[3]/z[2]:>9.1%}")
    tot_n = sum(z[0] for z in seg.values())
    tot_h = sum(z[1] for z in seg.values())
    tot_b = sum(z[2] for z in seg.values()); tot_p = sum(z[3] for z in seg.values())
    print(f"{'全体':>10}{tot_n:>9,}{tot_h/tot_n:>9.2%}{tot_p/tot_b:>9.1%}")
    # ── 6. 振り分けの対照実験 ──
    # 🔴 検出器の寄与上位は p3_1 / p3_std / p3_ent ＝「本命がどれだけ抜けているか」。
    #    それなら **p3 だけの単純なゲート**で足りるはずで、検出器が要るとは限らない。
    #    同じ足切り率で並べて、検出器が単純ゲートを上回るかを見る。
    idx = {m[0]: i for i, m in enumerate(mr_te)}
    _i1, _i2, _ie = (RACE_FEATS.index("p3_1"), RACE_FEATS.index("p3_2"),
                     RACE_FEATS.index("p3_ent"))
    p3_1 = {k_: float(Xr_te[i][_i1]) for k_, i in idx.items()}
    axsum = {k_: float(Xr_te[i][_i1] + Xr_te[i][_i2]) for k_, i in idx.items()}
    ent_r = {k_: float(Xr_te[i][_ie]) for k_, i in idx.items()}
    scr = {k_: float(s_te[i]) for k_, i in idx.items()}
    rows = []
    for r in te:
        key = r["key"]
        v = by_race.get(key); bd = board.get(key); o3 = fin_te.get(key)
        if not v or bd is None or not o3 or len(v) != 7 or key not in scr:
            continue
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        o = r["order"]
        k = frozenset((o[0], o[1], o[2]))
        if k not in bd:
            continue
        hit = k in wins
        rows.append(dict(key=key, date=date_of[key], hit=int(hit),
                         pay=int(bd[k] * 100) * stake // 100 if hit else 0))
    gates = {"バスト検出器(低いほど残す)": lambda k: -scr[k],
             "p3_1（本命の強さ）": lambda k: p3_1[k],
             "p3_1+p3_2（軸2車の合計）": lambda k: axsum[k],
             "p3エントロピー(低いほど残す)": lambda k: -ent_r[k]}
    print(f"\n【6. 振り分けの対照実験（同じ足切り率で比べる）・{len(rows):,}R】")
    print(f"{'ゲート':>26}" + "".join(f"{f'残す{int(f*100)}%':>16}"
                                    for f in (1.0, 0.8, 0.6, 0.4)))
    for name, kf in gates.items():
        order_ = sorted(rows, key=lambda x: kf(x["key"]), reverse=True)
        cells = []
        for frac in (1.0, 0.8, 0.6, 0.4):
            sub = order_[:int(len(order_) * frac)]
            h = sum(x["hit"] for x in sub) / len(sub)
            roi = sum(x["pay"] for x in sub) / (len(sub) * stake)
            cells.append(f"{h:.1%}/{roi:.1%}")
        print(f"{name:>26}" + "".join(f"{c:>16}" for c in cells))
    print("     （セルは 的中率/ROI）")

    imp = sorted(zip(RACE_FEATS, det.feature_importance("gain")), key=lambda x: -x[1])
    print("\n  バスト検出の寄与上位: " + " / ".join(k for k, _ in imp[:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
