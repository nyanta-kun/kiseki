"""看板枠(T=15万・現行レシピ)を『どのレースに置くか』"""
import sys, numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design, engine as E
GATE={"A_hit":1.596,"B_hit":1.539,"C_hit":1.500,"D_hit":1.304,"E_hit":1.284,"F_hit":1.246}
races=lib.get_races()
W=[("探索",lib.window(races,lib.EXPLORE)),("確認",lib.window(races,lib.CONFIRM))]
def show(nm,pop):
    o=[]
    for wn,ws in W:
        rows=design.build(ws,150_000,pop=pop)
        if not rows: o.append(None); continue
        s=E.summarize(rows,nm); lo,hi=E.boot_roi(rows)
        o.append((s,lo,hi))
    a,b=o
    f=lambda s,lo,hi: (f"{s['n']:>5} {s['roi']*100:>6.2f}% [{lo*100:>5.1f},{hi*100:>6.1f}] "
                       f"表示{s['disp']*100:>5.2f}% 10万+{s['n100k']/s['n']*100:>5.2f}%")
    print(f"{nm:<26} 探索 {f(*a) if a else 'n=0':<46} | 確認 {f(*b) if b else 'n=0'}")
print("=== 型（レースの型ラベル）別 ===")
for t in "ABCDEF": show(f"型{t}", lambda r,t=t: r["type"]==t)
show("堅い型 A/B/C", lambda r: r["type"] in "ABC")
show("荒れ型 D/E/F", lambda r: r["type"] in "DEF")
print("=== 種別別（型B/C/D） ===")
import collections
cnt=collections.Counter(r["rtype"] for r in races)
for rt,_ in cnt.most_common(8):
    show(f"{rt}(型BCD)", lambda r,rt=rt: r["type"] in "BCD" and r["rtype"]==rt)
print("=== 軸信頼（本線ゲート）別（型B/C/D） ===")
show("本線がゲート落ち", lambda r: r["type"] in "BCD" and r["axis_sum"]<GATE[f"{r['type']}_hit"])
show("本線がゲート通過", lambda r: r["type"] in "BCD" and r["axis_sum"]>=GATE[f"{r['type']}_hit"])
print("=== 軸信頼 axis_sum 五分位（全型） ===")
q=np.percentile([r["axis_sum"] for r in races],[20,40,60,80])
for i in range(5):
    lo=-9 if i==0 else q[i-1]; hi=9 if i==4 else q[i]
    show(f"axis_sum Q{i+1} [{lo:.2f},{hi:.2f})", lambda r,lo=lo,hi=hi: lo<=r["axis_sum"]<hi)
# 旧ランク 201件の整合（板プールを真値75%へ縮めて n=201 を引く）
print("\n=== 旧ランク高額 201件 ROI 42.83% の整合 ===")
conf=lib.window(races,lib.CONFIRM)
pool=np.array([x["payout"]/x["bet"] for x in design.build(conf,150_000,pop=lambda r:r["type"] in "BCD")])
pool=pool*(0.75/pool.mean())
rng=np.random.default_rng(9); out=np.array([rng.choice(pool,201).mean() for _ in range(20000)])
print(f"  真値75%・的中率5.8%級の商品を n=201 引いたとき: 中央 {np.median(out)*100:.1f}% "
      f"5%点 {np.percentile(out,5)*100:.1f}%  P(<=42.83%)={np.mean(out<=0.4283):.4f}")
