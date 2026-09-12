#!/usr/bin/env python3
"""B/C. 旧ランクの量（bust_p / upset_p）を型ラボの**第2層（商品の選び分け）**と
**第3層（選別）**へ入れたときの商品KPI（2026-09-11）。

台: keirin.type_lab_picks mode='paper'（7車・2025-01〜2026-08・ゲート適用）
    × /tmp/old_rank_ideas_rows.pkl（bust_p / upset_p / 既存量）
窓: 探索 2025 / 確認 2026。件数が動く腕には無作為対照20seed（中央値）。
🔴 型をまたぐ差し替えは paper 台では測れない（その型のプラン行しか無い）。
"""
from __future__ import annotations

import os
import pickle
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
from src.type_lab import sell_plans_for  # noqa: E402
import importlib.util  # noqa: E402
_s = importlib.util.spec_from_file_location(
    "gate", "/Users/ysuzuki/GitHub/kiseki/backend/src/services/keirin_type_lab_gate.py")
gate = importlib.util.module_from_spec(_s); _s.loader.exec_module(gate)

SCORES = pd.DataFrame(pickle.load(open("/tmp/old_rank_ideas_rows.pkl", "rb"))
                      ).set_index("race_key")

con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
cur.execute("""SELECT race_key,race_date,type_label,plan_key,budget,pred_mean_payout,
                      payout,legs,race_type,axis_sum,pw_ent
               FROM keirin.type_lab_picks
               WHERE mode='paper' AND settled_at IS NOT NULL AND n_entries=7""")
PL = defaultdict(dict); INFO = {}
for rk, d, tl, pk, bud, pmp, pay, legs, rt, axs, pwe in cur.fetchall():
    PL[rk][pk] = dict(bud=bud, pmp=float(pmp or 0), legs=legs, pay=pay or 0,
                      axs=float(axs or 0))
    INFO[rk] = dict(d=str(d), tl=tl, rt=rt, pwe=float(pwe) if pwe is not None else None)


def gated(r):
    if r["pmp"] <= 20000:
        return False
    po = [l.get("pred_odds") for l in r["legs"] if l.get("pred_odds")]
    return bool(po) and min(po) >= 2.0


def base_plan(rk):
    p = PL[rk]; i = INFO[rk]
    trio_ok = "A_trio" in p and gated(p["A_trio"])
    sp = sell_plans_for(i["tl"], 7, i["rt"], pw_ent=i["pwe"], trio_ok=trio_ok)
    return sp[0].key if sp else None


ROWS = []
for rk in PL:
    pk = base_plan(rk)
    if pk is None:
        continue
    r = PL[rk].get(pk)
    if r is None or not gated(r) or not gate.passes_axis_gate(pk, r["axs"]):
        continue
    i = INFO[rk]
    s = SCORES.loc[rk] if rk in SCORES.index else None
    ROWS.append(dict(rk=rk, d=i["d"], tl=i["tl"], rt=i["rt"], plan=pk,
                     bud=r["bud"], pay=r["pay"], axs=r["axs"], pwe=i["pwe"],
                     bust_p=float(s["bust_p"]) if s is not None else np.nan,
                     upset_p=float(s["upset_p"]) if s is not None else np.nan,
                     win="探索" if i["d"] < "2026-01-01" else "確認"))
B = pd.DataFrame(ROWS)
B["pw_ent"] = B["pwe"]
print(f"ベースライン台 {len(B):,}行  bust_p 欠損 {B.bust_p.isna().mean()*100:.1f}% / "
      f"upset_p 欠損 {B.upset_p.isna().mean()*100:.1f}%")


def kpi(pays, buds, ndays):
    pays = np.asarray(pays, float); buds = np.asarray(buds, float)
    n = len(pays)
    if n == 0:
        return dict(n=0)
    return dict(n=n, per=n / ndays, shown=(pays > buds).mean() * 100,
                roi=pays.sum() / buds.sum() * 100,
                med=float(np.median(pays[pays > 0])) if (pays > 0).any() else 0,
                hp=(pays >= 100000).sum() / ndays)


def show(tag, k):
    if k.get("n", 0) == 0:
        print(f"{tag}: 0件"); return
    print(f"{tag:34s} n={k['n']:5d} 件/日={k['per']:5.2f} 表示的中={k['shown']:5.2f}% "
          f"ROI={k['roi']:5.1f}% 中央={int(k['med']):7d} 10万+/日={k['hp']:.3f}")


def reroute(type_label, base_key, alt_key, score, frac, seeds=20):
    """型 `type_label` の中で、score 上位 frac を base→alt に差し替える（ベクトル化）。"""
    out = {}
    for w in ("探索", "確認"):
        d = B[B.win == w].reset_index(drop=True)
        nd = d.d.nunique()
        base_pay = d.pay.values.astype(float); base_bud = d.bud.values.astype(float)
        cand = np.where((d.tl.values == type_label) & (d.plan.values == base_key)
                        & d[score].notna().values)[0]
        if len(cand) < 50:
            out[w] = None; continue
        alt_pay = base_pay.copy(); alt_bud = base_bud.copy(); alt_ok = np.zeros(len(d), bool)
        for i in cand:
            a = PL[d.rk.values[i]].get(alt_key)
            if a is not None and gated(a) and gate.passes_axis_gate(alt_key, a["axs"]):
                alt_ok[i] = True; alt_pay[i] = a["pay"]; alt_bud[i] = a["bud"]
        k = int(round(len(cand) * frac))
        order = cand[np.argsort(-d[score].values[cand])]
        sel = order[:k]

        def build(selidx):
            m = np.zeros(len(d), bool); m[selidx] = True
            drop = m & ~alt_ok                       # 差し替え先がゲートで落ちたら出さない
            use = ~drop
            pay = np.where(m & alt_ok, alt_pay, base_pay)[use]
            bud = np.where(m & alt_ok, alt_bud, base_bud)[use]
            return kpi(pay, bud, nd)

        base = kpi(base_pay, base_bud, nd)
        arm = build(sel)
        rng = np.random.default_rng(0)
        ctrl = [build(rng.choice(cand, size=k, replace=False)) for _ in range(seeds)]
        out[w] = (base, arm, ctrl, k, len(cand))
    return out


def report(name, res, key="shown", higher=True):
    print(f"\n### {name}")
    for w in ("探索", "確認"):
        if res.get(w) is None:
            print(f"[{w}] 標本不足"); continue
        base, arm, ctrl, k, ntgt = res[w]
        cv = np.array([c[key] for c in ctrl])
        wins = int((arm[key] > cv).sum()) if higher else int((arm[key] < cv).sum())
        print(f"[{w}] 対象{ntgt}件中 {k}件を差し替え")
        show("  現行", base); show("  腕", arm)
        show("  無作為対照(中央)", {**ctrl[0], key: float(np.median(cv)),
                                    "n": int(np.median([c['n'] for c in ctrl])),
                                    "per": float(np.median([c['per'] for c in ctrl])),
                                    "shown": float(np.median([c['shown'] for c in ctrl])),
                                    "roi": float(np.median([c['roi'] for c in ctrl])),
                                    "med": float(np.median([c['med'] for c in ctrl])),
                                    "hp": float(np.median([c['hp'] for c in ctrl]))})
        print(f"  → 対照20seed に {wins}/20 で勝ち（{key}）")


if __name__ == "__main__":
    print("\n## ベースライン")
    for w in ("探索", "確認"):
        d = B[B.win == w]
        show(f"{w} 全商品", kpi(d.pay.values, d.bud.values, d.d.nunique()))
        for pk in sorted(d.plan.unique()):
            dd = d[d.plan == pk]
            show(f"  {w} {pk}", kpi(dd.pay.values, dd.bud.values, d.d.nunique()))

    arms = 0
    # B-1 型A の穴狙い（A_ana）の選抜を pw_ent → bust_p / upset_p へ
    for sc in ("bust_p", "upset_p", "pw_ent"):
        for fr in (0.10, 0.20):
            report(f"B-1 型A: A_hit の上位{int(fr*100)}% を {sc} で A_ana へ",
                   reroute("A", "A_hit", "A_ana", sc, fr), key="shown")
            arms += 1
    # B-2 看板枠（{型}_sign）の置き場所を bust_p / upset_p で選ぶ
    for tl in ("A", "B", "C", "D", "E", "F"):
        base = f"{tl}_hit"
        for sc in ("bust_p", "upset_p"):
            report(f"B-2 型{tl}: {base} の上位20% を {sc} で {tl}_sign へ",
                   reroute(tl, base, f"{tl}_sign", sc, 0.20), key="hp")
            arms += 1
    print(f"\n腕の総数(差し替え系) = {arms}")

    # B-3 7H1 が拾っていた層で型ラボは何を売っているか
    print("\n### B-3 7H1 の選別層（市場合意 ∧ 抜け度>=0.20 ∧ バスト確率 上位10%）")
    S = SCORES
    for w in ("探索", "確認"):
        d = B[B.win == w].copy()
        nd = d.d.nunique()
        s2 = S.reindex(d.rk)
        d["agree"] = s2["agree"].values
        d["gap12"] = s2["pw_gap12"].values
        pool = S[(S.agree == True) & (S.pw_gap12 >= 0.20)]
        thr = float(np.nanquantile(pool["bust_p"], 0.90))
        m = d.agree.fillna(False).astype(bool) & (d.gap12 >= 0.20) & (d.bust_p >= thr)
        show(f"[{w}] 全商品", kpi(d.pay.values, d.bud.values, nd))
        show(f"[{w}] 7H1層({int(m.sum())}件)", kpi(d[m].pay.values, d[m].bud.values, nd))
        show(f"[{w}] 7H1層以外", kpi(d[~m].pay.values, d[~m].bud.values, nd))
        print("   7H1層の商品構成:", d[m].plan.value_counts().to_dict())

    # B-4 第3層: 既存ゲートに bust_p / upset_p を足す（同件数の無作為対照つき）
    print("\n### B-4 第3層 選別に足す（下位/上位を落とす・同件数の無作為対照20seed）")
    for sc in ("bust_p", "upset_p"):
        for fr in (0.10, 0.20):
            for hi in (True, False):
                for w in ("探索", "確認"):
                    d = B[(B.win == w) & B[sc].notna()].copy()
                    nd = d.d.nunique()
                    k = int(round(len(d) * fr))
                    cut = set(d.sort_values(sc, ascending=not hi).head(k).rk)
                    keep = d[~d.rk.isin(cut)]
                    a = kpi(keep.pay.values, keep.bud.values, nd)
                    rng = np.random.default_rng(1)
                    cs = []
                    for _ in range(20):
                        c = set(rng.choice(d.rk.values, size=k, replace=False))
                        kk = d[~d.rk.isin(c)]
                        cs.append(kpi(kk.pay.values, kk.bud.values, nd))
                    cv = np.array([c["shown"] for c in cs])
                    show(f"[{w}] {sc} {'上位' if hi else '下位'}{int(fr*100)}%を落とす", a)
                    print(f"      対照中央 表示的中={np.median(cv):5.2f}% ROI={np.median([c['roi'] for c in cs]):5.1f}% "
                          f"→ 対照に {int((a['shown'] > cv).sum())}/20 で勝ち")
