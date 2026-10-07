#!/usr/bin/env python3
"""H20 手順3（記述・判定外）: 実売 2026-08-27〜最新で、上期の候補層の回収率を見る。読み取りのみ。

    set -a; source ~/.config/kiseki/env >/dev/null 2>&1; set +a
    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h20_sales.py
- 母集団 = `netkeirin_submissions`（published・未削除・採点済み・settled_bet>0）× `type_lab_picks`(mode='live'・7車) 。払戻 = settled_payout（実払戻）。
- 層ラベルは h20_common と同じ作り方（切点は 2025 上期のもの＝h20_result.pkl）。
- 版A の候補 11 層と、版B の候補に最も近かった層（CI 上限 − 全体 が小さい上位）を見る。
"""
from __future__ import annotations
import json, pickle, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _db import q                      # noqa: E402
import h20_common as H                 # noqa: E402

D = H.D
JST = timezone(timedelta(hours=9))
N_BOOT = 2000
FROM = "20260827"


def load():
    _, rows = q("""
        select s.race_key, s.rank_key, s.settled_bet, s.settled_payout, s.origin,
               t.type_label, t.axis_sum, t.axis1, t.axis2, t.pred_mean_payout, t.budget,
               w.venue_id, w.grade, w.race_type, w.day_index, w.race_date, w.n_entries
          from keirin.netkeirin_submissions s
          join keirin.type_lab_picks t on t.race_key = s.race_key and t.plan_key = s.rank_key and t.mode = 'live'
          join keirin.wt_races w on w.race_key = s.race_key
         where s.race_key >= %s and s.deleted_at is null and s.status = 'published'
           and s.settled_at is not null and s.settled_bet > 0 and w.n_entries = 7""", (FROM,))
    cols = ["race_key", "plan", "bet", "pay", "origin", "tl", "axis", "a1", "a2", "mean", "budget", "venue", "grade", "rtype", "dayi", "date", "ne"]
    subs = [dict(zip(cols, r)) for r in rows]
    _, st = q("select race_key, venue_id, race_date, start_at from keirin.wt_races where race_date >= '2026-08-27'")
    first = {}
    for rk, v, d, s in st:
        try:
            h = datetime.fromtimestamp(int(s), JST)
        except (TypeError, ValueError):
            continue
        k = (str(d), str(v)); first[k] = min(first.get(k, 99.0), h.hour + h.minute / 60)
    keys = sorted({s["race_key"] for s in subs})
    _, en = q("select race_key, frame_no, line_group, prediction_mark from keirin.wt_entries where race_key = any(%s)", (keys,))
    ent = defaultdict(list)
    for rk, fn, lg, mk in en:
        ent[rk].append((fn, str(lg), mk))
    return subs, first, ent


def labels(s, first, ent, cut_axis, bank):
    rt = str(s["rtype"] or "")
    chal = rt.startswith("チャレンジ")
    core = rt[len("チャレンジ"):] if chal else rt
    gr = s["grade"]
    grade = "チャレンジ" if chal else ("S" if gr == "S級" else ("A" if gr == "A級" else "その他"))
    e = ent.get(s["race_key"], [])
    groups = defaultdict(list)
    for fn, lg, mk in e:
        groups[lg].append(fn)
    wt = {fn for fn, lg, mk in e if mk in (1, 2)}
    ov = "印なし" if not wt else str(len({s["a1"], s["a2"]} & wt))
    fh = first.get((str(s["date"]), str(s["venue"])))
    return {
        "場": str(s["venue"]), "周長": str(int(bank.get(str(s["venue"]), 0))) if str(s["venue"]) in bank else "不明",
        "級班": grade, "種別": core, "開催日目": str(s["dayi"]), "時間帯": H.meeting_band(fh), "型": s["tl"],
        "axis_sum五分位": H.quint(float(s["axis"]), cut_axis),
        "ライン数": str(len(groups)), "単騎の数": str(sum(1 for v in groups.values() if len(v) == 1)),
        "wt_overlap_n": ov, "月": str(s['date'])[5:7],
    }


def main():
    res = pickle.load(open(D / "h20" / "h20_result.pkl", "rb"))
    bank = json.load(open(D / "h18" / "bank_length.json"))
    subs, first, ent = load()
    plans_ok = lambda p: p.endswith(("_hit", "_sign", "_big")) or p in ("A_trio", "A_ana", "L_lead")
    n0 = len(subs)
    subs = [s for s in subs if plans_ok(s["plan"])]
    print(f"採点済み 7車 live の入稿 {n0} → 台に存在するプランのみ {len(subs)}")
    days = sorted({str(s["date"]) for s in subs})
    print(f"開催日 {len(days)}（{days[0]}〜{days[-1]}）")
    out = {}
    for name in ("A", "B"):
        r = res[name]
        cuts = [np.array(c) for c in r["cuts_prod"]]
        cut_axis = np.array(r["cut_axis"])
        rows = []
        for s in subs:
            lab = labels(s, first, ent, cut_axis, bank)
            lab["型×プラン"] = f"{s['tl']}×{s['plan']}"
            if s["mean"] and s["budget"]:
                lab["予測合成オッズ五分位"] = H.quint(float(s["mean"]) / float(s["budget"]), cuts[0])
                lab["平均想定払戻五分位"] = H.quint(float(s["mean"]), cuts[1])
            else:
                lab["予測合成オッズ五分位"] = lab["平均想定払戻五分位"] = "不明"
            rows.append((s, lab))
        if name == "B":      # 版B は L_lead 段なし
            rows = [(s, l) for s, l in rows if s["plan"] != "L_lead"]
        dpos = {d: i for i, d in enumerate(days)}
        nd = len(days)
        rng = np.random.default_rng(20261011)
        Bi = rng.integers(0, nd, size=(N_BOOT, nd))
        def agg(sel):
            inv = np.zeros(nd); pay = np.zeros(nd); n = np.zeros(nd); hit = np.zeros(nd)
            for s, l in rows:
                if sel(l):
                    i = dpos[str(s["date"])]
                    inv[i] += s["bet"]; pay[i] += s["pay"]; n[i] += 1; hit[i] += s["pay"] > s["bet"]
            return inv, pay, n, hit
        def stat(sel):
            inv, pay, n, hit = agg(sel)
            if inv.sum() == 0:
                return None
            with np.errstate(invalid="ignore", divide="ignore"):
                b = pay[Bi].sum(1) / inv[Bi].sum(1) * 100
            return dict(n=int(n.sum()), days=int((n > 0).sum()), roi=float(pay.sum() / inv.sum() * 100),
                        lo=float(np.nanpercentile(b, 2.5)), hi=float(np.nanpercentile(b, 97.5)),
                        shown=float(hit.sum() / n.sum() * 100))
        allst = stat(lambda l: True)
        # 注目層: 版A は候補、版B は「候補に最も近い層」（上期 CI 上限 − 全体 の小さい順・n>=200, days>=60）
        if name == "A":
            focus = [(t["dim"], t["val"], t) for t in r["cands"]]
        else:
            near = [t for t in r["tab"] if t["n"] >= 200 and t["days"] >= 60]
            near.sort(key=lambda t: t["hi"] - r["overall"])
            focus = [(t["dim"], t["val"], t) for t in near[:6]]
        lst = []
        for dim, val, t in focus:
            st_ = stat(lambda l, dim=dim, val=val: l[dim] == val)
            lst.append((dim, val, t, st_))
        # 候補の和集合（版Aのみ）
        union = None
        if name == "A":
            cr = [(d, v) for d, v, _ in focus if d in H.RACE_DIMS]
            cp = [(d, v) for d, v, _ in focus if d in H.PROD_DIMS]
            union = stat(lambda l: any(l[d] == v for d, v in cr + cp))
            rest = stat(lambda l: not any(l[d] == v for d, v in cr + cp))
        out[name] = dict(all=allst, focus=lst, union=union, rest=rest if name == "A" else None)
        print(f"\n== 版{name}（実売 {len(rows)} 本）全体: {allst}")
        for dim, val, t, st_ in lst:
            print(f"  {dim}={val}: 上期 n={t['n']} ROI {t['roi']:.1f} [{t['lo']:.1f},{t['hi']:.1f}] 全体 {r['overall']:.1f}  | 実売 {st_}")
        if union:
            print("  候補の和集合に当たる:", union, " 当たらない:", rest)
    pickle.dump(out, open(D / "h20" / "h20_sales.pkl", "wb"))


if __name__ == "__main__":
    main()
