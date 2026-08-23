#!/usr/bin/env python3
"""§24 の三者同時確率は **9C の相手選定へ移植できるか**（2026-08-23・§30）。

## なぜ 9C を測るのか

§29 で 7C への移植を否定したとき、移植先の第一候補として 9C を挙げた
（[[keirin_trio_joint_probability_2026_08_23]]）。根拠は
`RANK_9C_LEGS_MIN = 3` と「相手は7車」＝「7候補から3点以上」という**定数の読み**
だった。🔴 **定数から買い方を推測してはいけない**（CLAUDE.md 検証の作法 #1）ので、
実装の前に**本番が実際に何点買っているか**から測り直す。

## 本番の 9C（コードとDBから書き出したもの）

| 項目 | 実際の値 |
|---|---|
| 券種 | 三連複2軸流し（`BET_KIND_TRIO_AXIS2`） |
| 軸 | `rank_7c_select_axis` ＝ p3 上位2車（3ヘッド軸ではない） |
| 相手 | `rank_7c_select_legs(others7, p3, 0.15)` — **落差カットは無い**（7C と違う） |
| ゲート | 較正後 `p3_sum_top2 >= 1.30` ∧ 相手3点以上 |
| 穴埋め経路 | `_manual_partners` — 同じ足切りだが**3点まで戻す**（レースを落とせない） |
| 賭け金 | `tilt_stakes: True`（予測オッズでダッチング・1レース10,000円） |

## 腕（点数は必ず本番に揃える）

| 腕 | 相手の選び方 |
|---|---|
| 現行 | `p3 >= 0.15` |
| **同時確率(同点数)** | 同じ点数を `P(3車すべて3着内)` の上位から |
| 参考: 3点 | 同時確率の上位3点（点数下限まで絞った形） |
| 参考: 総流し7点 | 7点すべて |

🔴 一次指標は**的中率**（9C は「ROI で語る商品ではない」と設計に明記）。
🔴 学習・検定は年をまたぐ独立窓（`--swap` で逆向きも）。
🔴 ROI は**均等配分**で出す（§29 と同じ土俵）。本番はダッチングなので目安。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    build_A, fit, load_any, load_boards, load_entries, race_context, _adj, _same)
from src.database import get_connection  # noqa: E402
from src.marquee import _load_canonical  # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN, RANK_9C_P3_SUM_MIN,
    rank_7c_select_legs, unit_stake)

CACHE = "data/exp/trio9_cache.jsonl"
CACHE_WF = "data/exp/trio9_cache_wf.jsonl"
WF_DIR = "data/exp_cache"
_is_fill = _load_canonical().is_fill_target
NE = 9
NLEG = NE - 2          # 相手候補は7車


def build_cache(path: str) -> None:
    """9車レースの p3 / 着順 / 種別を1本の jsonl にする（7車キャッシュの9車版）。"""
    q = """SELECT e.race_key, r.race_date, r.race_type, r.cup_grade,
                  e.frame_no, e.pred_top3_pct
           FROM keirin.wt_entries e JOIN keirin.wt_races r ON r.race_key = e.race_key
           WHERE r.n_entries = ? AND e.pred_top3_pct IS NOT NULL"""
    races: dict[str, dict] = {}
    with get_connection() as c:
        for x in c.execute(q, (NE,)).fetchall():
            rk = x["race_key"]
            r = races.setdefault(rk, dict(race_key=rk, race_date=str(x["race_date"]),
                                          race_type=x["race_type"],
                                          cup_grade=x["cup_grade"], p3={}))
            r["p3"][int(x["frame_no"])] = float(x["pred_top3_pct"]) / 100
    n = 0
    with open(path, "w") as f:
        for r in races.values():
            if len(r["p3"]) != NE:
                continue
            r["order"] = sorted(r["p3"], key=lambda k: (-r["p3"][k], k))
            f.write(json.dumps(r) + "\n")
            n += 1
    print(f"[cache] {path} に {n:,}R を書き出した")


def build_cache_wf(path: str) -> None:
    """🔴 **vintage の walk-forward 予測**（`wf_preds9_*.pkl`）で9車キャッシュを作る。

    `wt_entries.pred_top3_pct` は 2026-07-19 に追加された列で**過去分は後から
    backfill されている**＝そのレースより未来を知っているモデルの出力
    （model-vintage look-ahead）。足切り・相手選定をこれで測ると必ず良く見える。
    `open_tasks_register` R-6 が「必ず `wf_preds9_*.pkl` を使う」と明示している。
    """
    import glob
    import pickle

    p3_by: dict[str, dict[int, float]] = defaultdict(dict)
    for f in sorted(glob.glob(f"{WF_DIR}/wf_preds9_*.pkl")):
        d = pickle.load(open(f, "rb"))
        for rk, fn, pp3 in zip(d["race_key"], d["frame_no"], d["pp3"]):
            p3_by[rk][int(fn)] = float(pp3)
    meta = {}
    with get_connection() as c:
        for x in c.execute(
                "SELECT race_key, race_date, race_type, cup_grade, n_entries "
                "FROM keirin.wt_races WHERE n_entries = ?", (NE,)).fetchall():
            meta[x["race_key"]] = (str(x["race_date"]), x["race_type"], x["cup_grade"])
    n = 0
    with open(path, "w") as f:
        for rk, p3 in p3_by.items():
            if len(p3) != NE or rk not in meta:
                continue
            date, rtype, grade = meta[rk]
            r = dict(race_key=rk, race_date=date, race_type=rtype, cup_grade=grade,
                     p3=p3, order=sorted(p3, key=lambda k: (-p3[k], k)))
            f.write(json.dumps(r) + "\n")
            n += 1
    print(f"[cache/wf] {path} に {n:,}R を書き出した（vintage walk-forward）")


def load_cache(path: str) -> list[dict]:
    out = []
    for line in open(path):
        r = json.loads(line)
        r["p3"] = {int(k): v for k, v in r["p3"].items()}
        r["order"] = [int(c) for c in r["order"]]
        out.append(r)
    return out


def build_rows(races, ent, fins):
    """軸2車固定・相手7候補（1レース7行）。特徴は `build_A` と同じ定義。"""
    X, y, meta = [], [], []
    for r in races:
        e = ent.get(r["race_key"]); o3 = fins.get(r["race_key"])
        if not e or not o3 or len(e) < NE:
            continue
        o, p3 = r["order"], r["p3"]
        wins = {frozenset(w) for w in winning_trifectas(o3)}
        ctx = race_context(o, p3, e)
        a1, a2 = o[0], o[1]
        vals = [p3[c] for c in o]
        for i in range(2, NE):
            c = o[i]
            nxt = vals[i + 1] if i + 1 < len(vals) else 0.0
            ec = e[c]
            X.append([
                p3[c], float(i + 1), p3[a2] - p3[c], p3[c] - nxt,
                p3[c] / max(p3[a2], 1e-9),
                p3[a1], p3[a2], ctx["axis_sum"], p3[a1] * p3[a2] * p3[c],
                p3[a1] + p3[a2] + p3[c],
                _same(e, a1, c), _same(e, a2, c), _adj(e, a1, c), _adj(e, a2, c),
                ec["lsize"], ec["lp"], ec["leader"], ec["rp"],
                abs(ec["rp"] - e[a2]["rp"]),
                ctx["n_lines"], ctx["max_lsize"], ctx["p3_ent"], ctx["p3_std"],
                _same(e, a1, a2), int(_same(e, a1, a2) and
                                      {e[a1]["lp"], e[a2]["lp"]} == {1.0, 2.0}),
                float(len({e[x]["lg"] for x in (a1, a2, c)})),
            ])
            y.append(int(frozenset((a1, a2, c)) in wins))
            meta.append((r["race_key"], r["race_date"], a1, a2, c, i + 1))
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int8), meta


def load_wf7(d_from: str, d_to: str) -> list[dict]:
    """7車の vintage walk-forward 予測（`wf_preds_*.pkl`）を学習追加用に読む。"""
    import glob
    import pickle

    p3_by: dict[str, dict[int, float]] = defaultdict(dict)
    for f in sorted(glob.glob(f"{WF_DIR}/wf_preds_*.pkl")):
        d = pickle.load(open(f, "rb"))
        for rk, fn, pp3 in zip(d["race_key"], d["frame_no"], d["pp3"]):
            p3_by[rk][int(fn)] = float(pp3)
    keys = list(p3_by)
    dates = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, race_date FROM keirin.wt_races "
                 "WHERE n_entries = 7 AND race_key IN (%s)" % ",".join("?" * len(ch)))
            for x in c.execute(q, ch).fetchall():
                dates[x["race_key"]] = str(x["race_date"])
    out = []
    for rk, p3 in p3_by.items():
        d = dates.get(rk)
        if d is None or not (d_from <= d <= d_to) or len(p3) != 7:
            continue
        out.append(dict(key=rk, date=d, p3=p3,
                        order=sorted(p3, key=lambda k: (-p3[k], k))))
    return out


def ci_diff(days, B=4000, seed=97):
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)]


def report(rows, arms, base):
    print(f"{'腕':>20}{'的中%':>9}{'ROI':>9}{'的中Δ':>24}{'ROIΔ':>24}")
    bh = br = None
    for a in arms:
        seg = rows[a]
        if not seg:
            continue
        dh = defaultdict(lambda: [0, 0, 0])
        dr = defaultdict(lambda: [0.0, 0.0, 0.0])
        for (d, h, p, b), (_, h2, p2, _b2) in zip(seg, rows[base]):
            z = dh[d]; z[0] += 1; z[1] += h2; z[2] += h
            z = dr[d]; z[0] += b; z[1] += p2; z[2] += p
        hit = sum(x[1] for x in seg) / len(seg)
        roi = sum(x[2] for x in seg) / sum(x[3] for x in seg)
        if a == base:
            bh, br = hit, roi
            print(f"{a:>20}{hit:>9.2%}{roi:>9.1%}{'':>24}{'':>24}")
            continue
        lh, uh = ci_diff(dh); lr, ur = ci_diff(dr)
        fh = "🟢" if lh > 0 else ("🔴" if uh < 0 else "")
        fr = "🟢" if lr > 0 else ("🔴" if ur < 0 else "")
        print(f"{a:>20}{hit:>9.2%}{roi:>9.1%}"
              f"{f'{(hit-bh)*100:+.2f}pt[{lh*100:+.2f},{uh*100:+.2f}]{fh}':>24}"
              f"{f'{(roi-br)*100:+.1f}pt[{lr*100:+.1f},{ur*100:+.1f}]{fr}':>24}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-cache", action="store_true")
    ap.add_argument("--wf", action="store_true",
                    help="vintage walk-forward 予測を使う（look-ahead なし・こちらが正）")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--split", default="2026-01-01", help="学習/検定の境界")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--swap", action="store_true")
    ap.add_argument("--pool7", action="store_true",
                    help="7車データも学習に混ぜる（9車の標本不足を切り分ける）")
    ap.add_argument("--cache7-train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--cache7-test", default="data/exp/tf_shape_cache4.jsonl")
    args = ap.parse_args()

    if args.wf and args.cache == CACHE:
        args.cache = CACHE_WF
    if args.build_cache:
        (build_cache_wf if args.wf else build_cache)(args.cache)
        return 0

    allr = load_cache(args.cache)
    tr = [r for r in allr if r["race_date"] < args.split]
    te = [r for r in allr if r["race_date"] >= args.split]
    if args.swap:
        tr, te = te, tr

    def span(v):
        return f"{min(r['race_date'] for r in v)}〜{max(r['race_date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）")
    print(f"9C: 足切り p3 >= {RANK_9C_LEG_P3_MIN} / 最低 {RANK_9C_LEGS_MIN} 点 / "
          f"ゲート 較正後 p3_sum >= {RANK_9C_P3_SUM_MIN}\n")

    kt = [r["race_key"] for r in tr]; ke = [r["race_key"] for r in te]
    ent_tr, ent_te = load_entries(kt), load_entries(ke)
    fin_tr, fin_te = _load_finishes(kt), _load_finishes(ke)
    Xtr, ytr, _ = build_rows(tr, ent_tr, fin_tr)
    if args.pool7:
        # 🔴 7車の行を**学習にだけ**足す。検定は 9車のみ（商品は 9C なので）。
        if args.wf:
            r7 = load_wf7(min(r["race_date"] for r in tr),
                          max(r["race_date"] for r in tr))
        else:
            r7 = load_any(args.cache7_test if args.swap else args.cache7_train)
        k7 = [r["key"] for r in r7]
        X7, y7, _ = build_A(r7, load_entries(k7), _load_finishes(k7))
        print(f"[pool7] 7車 {len(r7):,}R / {len(X7):,}行 を学習へ追加 "
              f"（9車 {len(Xtr):,}行）")
        Xtr = np.vstack([Xtr, X7]); ytr = np.concatenate([ytr, y7])
    Xte, _yte, mte = build_rows(te, ent_te, fin_te)
    m = fit(Xtr, ytr, args.rounds)
    pred = m.predict(Xte)
    board = load_boards(ke)

    by_race = defaultdict(list)
    axes, date_of = {}, {}
    for (key, date, a1, a2, c, _rk), p in zip(mte, pred):
        by_race[key].append((float(p), c))
        axes[key] = (a1, a2)
        date_of[key] = date
    rec = {r["race_key"]: r for r in te}
    wins_of = {}
    for r in te:
        o3 = fin_te.get(r["race_key"])
        if o3:
            wins_of[r["race_key"]] = {frozenset(w) for w in winning_trifectas(o3)}

    arms = ["現行(p3足切り)", "同時確率(同点数)", "同時確率3点", "総流し7点"]
    # ゲート通過（rank 経路）と 穴埋め経路（ゲートなし・3点まで戻す）を分けて集計する。
    paths = {"rank": {a: [] for a in arms}, "fill": {a: [] for a in arms}}
    npt = {"rank": defaultdict(int), "fill": defaultdict(int)}
    same = {"rank": [0, 0], "fill": [0, 0]}
    byn = {"rank": defaultdict(list), "fill": defaultdict(list)}

    for key, v in by_race.items():
        bd = board.get(key); w = wins_of.get(key)
        if not bd or not w or len(v) != NLEG:
            continue
        a1, a2 = axes[key]
        r = rec[key]; p3 = r["p3"]
        others = [c for _, c in v]
        sel = rank_7c_select_legs(others, p3, RANK_9C_LEG_P3_MIN)
        cal = calibrated_p3_sum_top2(p3, r["race_type"], r["cup_grade"]) or 0.0
        jnt = [c for _, c in sorted(v, key=lambda x: -x[0])]

        for path in ("rank", "fill"):
            # 🔴 穴埋め経路の母集団は**看板（穴埋め対象）レースだけ**。
            #    全9車レースで代用すると本番より4倍広い別母集団になる（作法#3）。
            if path == "fill" and not _is_fill(r["race_type"], r["cup_grade"]):
                continue
            if path == "rank":
                if cal < RANK_9C_P3_SUM_MIN or len(sel) < RANK_9C_LEGS_MIN:
                    continue          # 9C はこのレースを買わない
                cur = sel
            else:
                cur = sel if len(sel) >= RANK_9C_LEGS_MIN else \
                    sorted(others, key=lambda c: (-p3.get(c, 0.0), c))[:RANK_9C_LEGS_MIN]
            picks = {"現行(p3足切り)": cur, "同時確率(同点数)": jnt[:len(cur)],
                     "同時確率3点": jnt[:RANK_9C_LEGS_MIN], "総流し7点": jnt}
            ks = {a: [frozenset((a1, a2, c)) for c in legs] for a, legs in picks.items()}
            if any(any(k not in bd for k in vv) for vv in ks.values()):
                continue
            npt[path][len(cur)] += 1
            byn[path][min(len(cur), 5)].append(
                (date_of[key], set(cur), set(picks["同時確率(同点数)"]),
                 ks["現行(p3足切り)"], ks["同時確率(同点数)"], w, bd, len(cur)))
            same[path][0] += int(set(cur) == set(picks["同時確率(同点数)"]))
            same[path][1] += 1
            for a, legs in picks.items():
                st = unit_stake(len(legs))
                hit = any(k in w for k in ks[a])
                pay = sum(int(bd[k] * 100) * st // 100 for k in ks[a] if k in w)
                paths[path][a].append((date_of[key], int(hit), pay, len(legs) * st))

    for path, label in (("rank", "ゲート通過（rank 経路）"),
                        ("fill", "看板穴埋め（穴埋め対象レースのみ・3点まで戻す）")):
        n = same[path][1]
        if not n:
            continue
        d = npt[path]
        print(f"■ {label}  {n:,}R   平均 {sum(k*c for k, c in d.items())/n:.2f}点   "
              + " / ".join(f"{k}点 {d[k]/n:.0%}" for k in sorted(d)))
        print(f"   同時確率が現行と**同じ集合**になった率: {same[path][0]/n:.1%}")
        report(paths[path], arms, "現行(p3足切り)")
        # 事前指定の切り口: 層③は「多くの候補から少なく選ぶ」ほど効くはず（§29）。
        # 現行の点数別に、同点数の並べ替えだけを比べる。
        print(f"   {'点数':>6}{'R':>7}{'同集合':>8}{'現行的中':>9}{'同時確率':>9}{'Δ':>22}")
        for k in sorted(byn[path]):
            seg = byn[path][k]
            dh = defaultdict(lambda: [0, 0, 0])
            for d, sc, sj, kc, kj, w, _bd, _n in seg:
                z = dh[d]; z[0] += 1
                z[1] += int(any(x in w for x in kc)); z[2] += int(any(x in w for x in kj))
            hc = sum(v[1] for v in dh.values()) / len(seg)
            hj = sum(v[2] for v in dh.values()) / len(seg)
            sm = sum(int(sc == sj) for _d, sc, sj, *_ in seg) / len(seg)
            lo, hi = ci_diff(dh)
            f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
            lbl = f"{k}点" if k < 5 else "5点以上"
            print(f"   {lbl:>6}{len(seg):>7,}{sm:>8.0%}{hc:>9.1%}{hj:>9.1%}"
                  f"{f'{(hj-hc)*100:+.2f}pt[{lo*100:+.2f},{hi*100:+.2f}]{f}':>22}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
