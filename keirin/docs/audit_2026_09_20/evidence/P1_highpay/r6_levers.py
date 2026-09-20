"""T を固定して買い方のレバーを振る。現行(T,prob順,max600,ダッチ,三連単)と対応比較。"""
import sys, numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design, engine as E
races=lib.get_races()
def bcd(r): return r["type"] in ("B","C","D")
ARMS=[
 ("現行(prob,max600,dutch)",dict()),
 ("max_legs=1",dict(max_legs=1)),
 ("max_legs=2",dict(max_legs=2)),
 ("max_legs=3",dict(max_legs=3)),
 ("max_odds=60",dict(max_odds=60.0)),
 ("max_odds=100",dict(max_odds=100.0)),
 ("max_odds=200",dict(max_odds=200.0)),
 ("min_odds=20",dict(min_odds=20.0)),
 ("min_odds=30",dict(min_odds=30.0)),
 ("均等配分",dict(amode="equal")),
 ("確率比例配分",dict(amode="prob")),
 ("三連複",dict(bet_type="trio")),
 ("軸1を外す(bust)",dict(bust=True)),
 ("オッズ昇順(人気順)",dict(order="odds")),
]
for wn,ws in [("探索",lib.window(races,lib.EXPLORE)),("確認",lib.window(races,lib.CONFIRM))]:
    for T in (150_000,100_000):
        base=design.build(ws,T,pop=bcd)
        print(f"\n===== {wn} T={T:,} 母集団=型B/C/D ({len(ws)}R) =====")
        print(f"{'腕':<24}{'n':>6}{'点':>5}{'的中%':>7}{'表示%':>7}{'ROI%':>8}{'ΔROI vs現行(CI)':>26}{'10万+/日':>9}{'計画払戻':>10}")
        for nm,kw in ARMS:
            rows=design.build(ws,T,pop=bcd,**kw)
            if not rows: print(f"{nm:<24} n=0"); continue
            s=E.summarize(rows,nm)
            d,lo,hi=E.boot_diff(rows,base)
            print(f"{nm:<24}{s['n']:>6}{s['n_legs']:>5.1f}{s['hit']*100:>7.2f}{s['disp']*100:>7.2f}"
                  f"{s['roi']*100:>8.2f}  {d*100:>+7.2f}[{lo*100:>+6.1f},{hi*100:>+6.1f}]"
                  f"{s['n100k_day']:>9.3f}{s['plan_pay']:>10,.0f}")
        # 無作為対照 20seed（同じ T・同じ詰め方で順序だけ無作為）
        rs=[]
        for sd in range(20):
            rng=np.random.default_rng(sd)
            rows=design.build(ws,T,pop=bcd,order="rand",rng=rng)
            s=E.summarize(rows,"rand")
            rs.append((s["roi"],s["disp"],s["n100k_day"],s["n"]))
        a=np.array(rs)
        sb=E.summarize(base,"base")
        print(f"{'無作為対照(20seed) 中央':<24}{np.median(a[:,3]):>6.0f}{'':>5}{'':>7}"
              f"{np.median(a[:,1])*100:>7.2f}{np.median(a[:,0])*100:>8.2f}"
              f"  [{np.percentile(a[:,0],5)*100:.1f},{np.percentile(a[:,0],95)*100:.1f}]  "
              f"10万+/日 {np.median(a[:,2]):.3f}  現行が勝つseed {int((a[:,0]<sb['roi']).sum())}/20 "
              f"表示的中で勝つ {int((a[:,1]<sb['disp']).sum())}/20")
