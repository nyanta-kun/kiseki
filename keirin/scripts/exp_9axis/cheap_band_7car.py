"""同じ測定の**7車版**（2026-08-25）。平均払戻ゲートの向きが7車でも同じかを見る。

窓は 2025-07-01〜2026-08-04（探索 2025後半 / 確認 2026）。全期間だとオッズ取得が重い。

腕（賭け金はすべて1レース10,000円・**均等配分**で揃える）:
  A 三連複5点流し（現行）
  B 三連複2点（相手を3着内率の上位2車に絞る）
  C **ワイド1点（軸1-軸2）** … 的中条件は「二軸そろい」そのもの

軸は本番と同じ: ゲート通過→p3上位2車 / ゲート不通過→`_axes()` のライン組み替え。
帯は①信頼度（較正後 p3_sum_top2・発走前に確定）と
    ②想定平均払戻（確定オッズから逆算＝**探索用の当たり**・look-ahead に注意）。
"""
import sys, glob, pickle, re, numpy as np, pandas as pd
_SPLIT = re.compile(r'[=\-]')
sys.path.insert(0, '.')
from src.database import get_connection
from src.p3_calibration import calibrated_p3_sum_top2
from src.strategy_wt import (RANK_7C_LEG_P3_MIN as LEG_MIN, RANK_7C_LEGS_MIN as LEGS_MIN,
                             RANK_7C_P3_SUM_MIN as SUM_MIN, rank_7c_select_legs)
from scripts.submit_marquee_wt import _axes

fr = []
for f in sorted(glob.glob("data/exp_cache/wf_preds_*.pkl")):
    d = pickle.load(open(f, "rb")); fr.append(d[["race_key", "frame_no", "pp3", "ppw"]])
P = pd.concat(fr, ignore_index=True).drop_duplicates(["race_key", "frame_no"])
# 🔴 窓を先に絞る（全期間の trio+wide は数百万行になり実用にならない）
P = P[P.race_key.str[:8].between("20250701", "20260804")]
keys = sorted(P.race_key.unique())
print(f"対象レース {len(keys):,}（オッズ取得）", flush=True)
meta = {}; ent = {}; trio = {}; wide = {}
with get_connection() as c:
    for i in range(0, len(keys), 500):
        ch = keys[i:i+500]; ph = ",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,race_date,race_type,day_index,cup_grade,n_entries "
                           f"FROM wt_races WHERE race_key IN ({ph})", ch):
            meta[r["race_key"]] = dict(r)
        for r in c.execute(f"SELECT race_key,frame_no,finish_order,line_group,is_line_leader,"
                           f"line_size,n_lines FROM wt_entries WHERE race_key IN ({ph})", ch):
            ent.setdefault(r["race_key"], []).append(dict(r))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds "
                           f"WHERE bet_type='trio' AND race_key IN ({ph})", ch):
            trio.setdefault(r["race_key"], {})[frozenset(int(x) for x in _SPLIT.split(r["combination"]) if x.strip().isdigit())] = float(r["odds_value"])
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds "
                           f"WHERE bet_type='quinellaPlace' AND race_key IN ({ph})", ch):
            wide.setdefault(r["race_key"], {})[frozenset(int(x) for x in _SPLIT.split(r["combination"]) if x.strip().isdigit())] = float(r["odds_value"])

rows = []
for rk, g in P.groupby("race_key"):
    m = meta.get(rk); es = ent.get(rk); tb = trio.get(rk); wb = wide.get(rk)
    if not m or not es or not tb or not wb or m["n_entries"] != 7:
        continue
    fo = {e["frame_no"]: e["finish_order"] for e in es}
    win = {f for f, o in fo.items() if o and 1 <= int(o) <= 3}
    if len(win) != 3:
        continue
    p3 = dict(zip(g.frame_no.astype(int), g.pp3.astype(float)))
    if len(p3) != 7:
        continue
    E = {e["frame_no"]: e for e in es}
    order = sorted(p3, key=lambda f: (-p3[f], f))
    gate = float(calibrated_p3_sum_top2(p3, m["race_type"], m["cup_grade"]) or 0)
    # 本番の軸: ゲート通過なら p3上位2車 / 通らなければライン組み替え
    c_legs = rank_7c_select_legs([f for f in order[2:]], p3, p3_min=LEG_MIN)
    passes = gate >= SUM_MIN and len(c_legs) >= LEGS_MIN
    if passes:
        a1, a2 = order[0], order[1]
    else:
        ax = _axes({"riders": [{"frame_no": f, "ai_rank": i} for i, f in enumerate(order)]},
                   {n: {"line_group": E[n]["line_group"], "is_line_leader": E[n]["is_line_leader"],
                        "line_size": E[n]["line_size"]} for n in E})
        if ax is None:
            continue
        a1, a2 = ax
    others = sorted([f for f in p3 if f not in (a1, a2)], key=lambda f: (-p3[f], f))
    legs = rank_7c_select_legs(others, p3, p3_min=LEG_MIN)
    if len(legs) < LEGS_MIN:
        legs = others[:LEGS_MIN]
    legs = [f for f in legs if frozenset({a1, a2, f}) in tb]
    if len(legs) < 2:
        continue
    wo = wb.get(frozenset({a1, a2}))
    if not wo:
        continue
    both = int(a1 in win and a2 in win)

    def trio_arm(ls):
        st = 10000 / len(ls)
        for L in ls:
            if {a1, a2, L} == win:
                return 1, st * tb[frozenset({a1, a2, L})]
        return 0, 0.0
    hA, pA = trio_arm(legs)
    hB, pB = trio_arm(legs[:2])
    hC, pC = both, (10000 * wo if both else 0.0)
    # 想定平均払戻（ダッチング前提の逆算）— **確定オッズなので探索用**
    mean_pay = 10000 / sum(1 / tb[frozenset({a1, a2, L})] for L in legs)
    rows.append(dict(rk=rk, date=str(m["race_date"]), rtype=m["race_type"], day=m["day_index"],
                     gate=gate, passes=int(passes), n=len(legs), both=both,
                     hA=hA, pA=pA, hB=hB, pB=pB, hC=hC, pC=pC, wo=wo, mean_pay=mean_pay,
                     nl=E[a1]["n_lines"] or 0))
D = pd.DataFrame(rows); D.to_pickle("/tmp/cheap7.pkl")
D = D[D.date >= "2025-07-01"].copy()
D["win_"] = D.date < "2026-01-01"
print(f"7車 {len(D):,}R（探索 {int(D.win_.sum()):,} / 確認 {int((~D.win_).sum()):,}）")
print(f"ワイド1点の平均オッズ {D.wo.mean():.2f}倍 / 中央 {D.wo.median():.2f}倍\n")

rng = np.random.default_rng(77)
def line(s, lab, pad=22):
    if len(s) < 80:
        print(f"  {lab:<{pad}} {len(s):5d}R ← 件数不足"); return
    out = [f"  {lab:<{pad}} {len(s):5d}R"]
    for k, nm in (("A", "三連複5点"), ("B", "三連複2点"), ("C", "ワイド1点")):
        pay = s[f"p{k}"].values; inv = 10000 * len(s)
        b = [pay[rng.integers(0, len(pay), len(pay))].sum()/inv*100 for _ in range(1200)]
        out.append(f"{nm} 的中{s[f'h{k}'].mean()*100:5.1f}% ROI{pay.sum()/inv*100:6.1f}%"
                   f"[{np.percentile(b,2.5):3.0f},{np.percentile(b,97.5):3.0f}]")
    print("  ".join(out))

print("=== 全体 ===")
line(D, "全網羅"); line(D[D.win_], " 探索窓"); line(D[~D.win_], " 確認窓")
print("\n=== 信頼度（較正後 p3_sum_top2）別 ===")
D["gb"] = pd.cut(D.gate, [0, 1.30, 1.40, 1.44, 1.55, 1.70, 3.0])
for v, s in D.groupby("gb", observed=True):
    line(s, str(v)); line(s[~s.win_], "   確認窓")
print("\n=== 想定平均払戻（確定オッズ逆算・探索用）別 ===")
D["mb"] = pd.cut(D.mean_pay, [0, 10000, 15000, 20000, 30000, 50000, 1e9],
                 labels=["〜1万", "1-1.5万", "1.5-2万", "2-3万", "3-5万", "5万〜"])
for v, s in D.groupby("mb", observed=True):
    line(s, str(v)); line(s[~s.win_], "   確認窓")
