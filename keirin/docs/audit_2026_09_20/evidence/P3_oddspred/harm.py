"""実害の測定（2026-08-07〜09-20・実売）:
  (a) 入稿ゲート（Σ1/po<0.5 かつ 全点po>=2.0）を確定オッズで判定し直した場合の不一致率
  (b) ダッチ配分の歪みによる的中時払戻の目減り（月別）
  (c) 表示的中（払戻>賭け金）の取りこぼし率（的中したのにガミになった率）
"""
import csv, glob, json, sys, statistics
import numpy as np, pandas as pd

sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/B_live")
from mklib import parse_bd, ORDERED, combo_label, load_finishers, win_labels  # noqa: E402

BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
LIVE = f"{BASE}/B_live"

subs = list(csv.DictReader(open(f"{LIVE}/subs.csv")))
fin = load_finishers(f"{LIVE}/finishers.csv")
odds = {}
for p in sorted(glob.glob(f"{LIVE}/odds_*.csv")):
    for r in csv.DictReader(open(p, newline="")):
        odds[(r["rk"], r["bt"], r["cb"])] = float(r["odds_value"]) if r["odds_value"] else None

MIN_PAYOUT_GATE = 20000.0

rows = []
for s in subs:
    if not s["bet_detail"]:
        continue
    rk = s["race_key"]
    if rk[:8] < "20260807":
        continue
    if s["status"] not in ("published", "submitted") or s["deleted_at"]:
        continue
    d = parse_bd(s["bet_detail"])
    if not d:
        continue
    lines = d.get("lines") or []
    legs = []
    ok_all_final = True
    for ln in lines:
        o = ORDERED.get(str(ln.get("bet_type") or ""))
        lab = combo_label(ln.get("combo"), o)
        if lab is None:
            ok_all_final = False
            continue
        cars = lab.replace("=", "-")
        bt = "trifecta" if o else "trio"
        fo = odds.get((rk, bt, cars))
        po = ln.get("odds")
        stake = int(ln.get("stake") or 0)
        if fo is None or po is None:
            ok_all_final = False
            continue
        legs.append(dict(lab=lab, bt=bt, po=float(po), fo=float(fo), stake=stake))
    if not legs or not ok_all_final:
        continue

    inv_po = sum(1.0 / l["po"] for l in legs)
    inv_fo = sum(1.0 / l["fo"] for l in legs)
    min_po = min(l["po"] for l in legs)
    min_fo = min(l["fo"] for l in legs)
    tot_stake = sum(l["stake"] for l in legs)
    # 「平均想定払戻」= 各点の (賭け金×オッズ) を単純平均（B_live/oddsgap.py と同じ定義）
    mean_pay_po = sum(l["stake"] * l["po"] for l in legs) / len(legs) if legs else None
    mean_pay_fo = sum(l["stake"] * l["fo"] for l in legs) / len(legs) if legs else None

    gate_po = (inv_po < 0.5) and (min_po >= 2.0) and (mean_pay_po is not None and mean_pay_po > MIN_PAYOUT_GATE)
    gate_fo = (inv_fo < 0.5) and (min_fo >= 2.0) and (mean_pay_fo is not None and mean_pay_fo > MIN_PAYOUT_GATE)

    won = set(win_labels(fin.get(rk, [])))
    hit_legs = [l for l in legs if l["lab"] in won]
    perfect_dutch_pay = tot_stake / inv_fo if inv_fo > 0 else None  # 確定オッズで完全ダッチした場合の的中払戻

    settled_bet = float(s["settled_bet"] or 0)
    settled_payout = float(s["settled_payout"] or 0)
    settled_hit = s["settled_hit"] == "t"

    rows.append(dict(
        rk=rk, ym=rk[:6], rank_key=s["rank_key"], n_entries=s["n_entries"],
        gate_po=gate_po, gate_fo=gate_fo,
        settled_bet=settled_bet, settled_payout=settled_payout, settled_hit=settled_hit,
        perfect_dutch_pay=perfect_dutch_pay, mean_pay_po=mean_pay_po, mean_pay_fo=mean_pay_fo,
        n_legs=len(legs),
    ))

df = pd.DataFrame(rows)
df["period"] = df.rk.str[:8].apply(lambda d: "旧ランク(-8/28)" if d < "20260829" else "型ラボ(8/29-)")
print("対象プラン数(=入稿=全てpo基準ゲート通過想定)", len(df), "races", df.rk.nunique())

print("\n=== (a) ゲート不一致率（全て gate_po=True のはずの母集団で、gate_fo が False になる率）月別 ===")
g = df.groupby("ym")
print(g.agg(n=("gate_po", "size"),
            gate_po_true_rate=("gate_po", "mean"),
            gate_fo_true_rate=("gate_fo", "mean"),
            mismatch_rate=("gate_fo", lambda s: (~s).mean())).round(4))

print("\n=== (a') 期間別（旧ランク/型ラボ）× 月 ===")
print(df.groupby(["period", "ym"]).agg(n=("gate_po", "size"),
            gate_po_true_rate=("gate_po", "mean"),
            gate_fo_true_rate=("gate_fo", "mean"),
            mismatch_rate=("gate_fo", lambda s: (~s).mean())).round(4))

print("\n=== (b) 計画払戻(po平均) vs 確定オッズでの計画払戻(fo平均)。的中時のみ実払戻と比較。月別 ===")
hitdf = df[df.settled_hit]
def summarize(sub, label):
    b = sub.settled_bet.sum()
    a = sub.settled_payout.sum()
    perf = sub.perfect_dutch_pay.sum()
    pm_po = sub.mean_pay_po.sum()
    print(f"{label}: n(的中)={len(sub)} bet={b:,.0f} 実払戻={a:,.0f} ROI={100*a/b:.2f}% "
          f"確定オッズ完全ダッチなら払戻={perf:,.0f} ROI={100*perf/b:.2f}% "
          f"計画払戻(po,合計)={pm_po:,.0f} 実/計画={a/pm_po:.3f}")
for ym, sub in hitdf.groupby("ym"):
    summarize(sub, ym)
summarize(hitdf, "全期間(的中分)")

print("\n=== (b') 期間別（旧ランク/型ラボ）===")
for period, sub in hitdf.groupby("period"):
    summarize(sub, period)

print("\n=== (b'') 型ラボ期のみ・月別 ===")
tl = hitdf[hitdf.period == "型ラボ(8/29-)"]
for ym, sub in tl.groupby("ym"):
    summarize(sub, f"型ラボ {ym}")

print("\n=== (c) 表示的中の取りこぼし（的中したのにpayout<bet=ガミ）月別・期間別 ===")
df["gami"] = df.settled_hit & (df.settled_payout < df.settled_bet)
g2 = df.groupby("ym")
print(g2.agg(n_hit=("settled_hit", "sum"),
             n_gami=("gami", "sum"),
             gami_rate_among_hits=("gami", lambda s: s.sum() / max(1, df.loc[s.index, "settled_hit"].sum()))).round(4))
print(df.groupby(["period", "ym"]).apply(
    lambda sub: pd.Series({
        "n_hit": sub.settled_hit.sum(),
        "n_gami": sub.gami.sum(),
        "gami_rate_among_hits": sub.gami.sum() / max(1, sub.settled_hit.sum()),
    })).round(4))

df.to_pickle(f"{BASE}/P3_oddspred/harm_rows.pkl")
