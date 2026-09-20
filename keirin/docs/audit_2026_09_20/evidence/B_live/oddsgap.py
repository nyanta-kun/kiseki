import csv, glob, json, os, statistics, collections
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from mklib import *
subs=load_subs(); fin=load_finishers()
odds={}
for p in sorted(glob.glob("odds_*.csv")):
    for r in csv.DictReader(open(p, newline="")):
        odds[(r["rk"],r["bt"],r["cb"])]=float(r["odds_value"]) if r["odds_value"] else None
rec={(x["race_key"],x["rank_key"]):x for x in json.load(open("recomputed.json"))}
rows=[]
ratios=[]; nofinal=0
for s in subs:
    k=(s["race_key"], s["rank_key"])
    x=rec[k]
    if x["deleted"] or x["status"] not in ("published","submitted"): continue
    if not (x["recomp"]["settled"] and x["recomp"]["bet"]>0): continue
    if s["race_key"][:8] < "20260807": continue
    d=parse_bd(s["bet_detail"]); lines=d["lines"]
    won=set(win_labels(fin.get(s["race_key"], [])))
    tot=0; plan_pay=[]; inv_final=0.0; ok=True; act=0
    for ln in lines:
        o=ORDERED.get(str(ln.get("bet_type") or ""))
        lab=combo_label(ln.get("combo"), o); cars=lab.replace("=","-")
        bt="trifecta" if o else "trio"
        fo=odds.get((s["race_key"],bt,cars))
        st=int(ln.get("stake") or 0); tot+=st
        po=ln.get("odds")
        if fo and po: ratios.append((float(po)/fo, s["race_key"][:6], s["rank_key"], float(po), fo))
        if not fo: ok=False; continue
        inv_final += 1.0/fo
        plan_pay.append(st*float(po) if po else None)
        if lab in won: act = pay100(fo)*st//100
    if not ok: nofinal+=1; continue
    perfect = tot/inv_final if inv_final>0 else 0     # 確定オッズで完全ダッチしたときの当たり払戻
    rows.append(dict(rk=s["race_key"], plan=s["rank_key"], ym=s["race_key"][:6], bet=tot,
                     act=x["recomp"]["payout"], hit=x["recomp"]["hit"],
                     plan_mean=statistics.mean([p for p in plan_pay if p is not None]) if any(p is not None for p in plan_pay) else None,
                     perfect=perfect, n=len(lines)))
print("対象", len(rows), "確定オッズが引けず除外", nofinal)
r=[x[0] for x in ratios]
r.sort()
print(f"予測/確定 比 n={len(r)} 中央={statistics.median(r):.3f} 平均={statistics.mean(r):.3f} "
      f"p05={r[int(.05*len(r))]:.3f} p25={r[int(.25*len(r))]:.3f} p75={r[int(.75*len(r))]:.3f} p95={r[int(.95*len(r))]:.3f}")
print(f"  ±2倍以内 {100*sum(1 for x in r if 0.5<=x<=2)/len(r):.1f}%  予測が高すぎ(>1.5) {100*sum(1 for x in r if x>1.5)/len(r):.1f}%  低すぎ(<0.667) {100*sum(1 for x in r if x<0.667)/len(r):.1f}%")
for ym in ("202608","202609"):
    rr=sorted(x[0] for x in ratios if x[1]==ym)
    print(f"  {ym}: n={len(rr)} 中央={statistics.median(rr):.3f}")
def sm(R,label):
    b=sum(x["bet"] for x in R); a=sum(x["act"] for x in R)
    pf=sum(x["perfect"] for x in R if x["hit"])
    pm=sum(x["plan_mean"] for x in R if x["plan_mean"])
    nh=sum(1 for x in R if x["hit"])
    print(f"{label}: n={len(R)} bet={b:,} 実払戻={a:,} ROI={100*a/b:.2f}%  "
          f"確定オッズで完全ダッチなら={pf:,.0f} ROI={100*pf/b:.2f}%  的中{nh}")
    print(f"     計画払戻(予測オッズ・平均)合計={pm:,.0f}  的中時の実払戻合計={a:,}  実/計画(的中分)={a/sum(x['plan_mean'] for x in R if x['hit'] and x['plan_mean']):.3f}")
sm(rows,"全体(8/07-)")
sm([x for x in rows if x["rk"][:8]>="20260829"],"型ラボ期(8/29-)")
sm([x for x in rows if x["rk"][:8]<"20260829"],"旧ランク期(-8/28)")
json.dump(rows, open("oddsgap.json","w"))
