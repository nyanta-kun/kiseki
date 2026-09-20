"""推奨案の事前登録用の数値（対応比較・無作為対照20seed）"""
import sys, numpy as np
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design, engine as E
races=lib.get_races()
W=[("探索",lib.window(races,lib.EXPLORE)),("確認",lib.window(races,lib.CONFIRM))]
def bcd(r): return r["type"] in ("B","C","D")
print("### 案1: 5枠すべて T=15万（_big を廃止）— 1商品あたりの確率")
for wn,ws in W:
    a=design.build(ws,150_000,pop=bcd)
    b=design.build(ws,400_000,pop=bcd,bust=True)
    for nm,rows in [("T=15万 _sign",a),("T=40万 _big",b)]:
        n=len(rows)
        p1=sum(1 for x in rows if x["payout"]>=100_000)/n
        p2=sum(1 for x in rows if x["payout"]>=200_000)/n
        p3=sum(1 for x in rows if x["payout"]>=300_000)/n
        roi=sum(x["payout"] for x in rows)/sum(x["bet"] for x in rows)
        lo,hi=E.boot_roi(rows)
        print(f"  {wn} {nm}: n={n} ROI {roi*100:.2f}% [{lo*100:.1f},{hi*100:.1f}] "
              f"P(10万+)={p1*100:.2f}% P(20万+)={p2*100:.2f}% P(30万+)={p3*100:.2f}%")
    d,lo,hi=E.boot_diff(a,b)
    print(f"  {wn} ΔROI(_sign − _big) = {d*100:+.2f}pt [{lo*100:+.1f},{hi*100:+.1f}]")
print("\n### 案2: 高額枠の対象からチャレンジ予選を外す（型B/C/D・T=15万）")
for wn,ws in W:
    base=design.build(ws,150_000,pop=bcd)
    keep=design.build(ws,150_000,pop=lambda r: bcd(r) and r["rtype"]!="チャレンジ予選")
    drop_n=len(base)-len(keep)
    s0=E.summarize(base,"現行"); s1=E.summarize(keep,"チャレンジ予選を外す")
    print(f"  {wn}: 現行 n={s0['n']} ROI {s0['roi']*100:.2f}% 10万+率 {s0['n100k']/s0['n']*100:.2f}%"
          f" → 除外後 n={s1['n']}(−{drop_n}) ROI {s1['roi']*100:.2f}% 10万+率 {s1['n100k']/s1['n']*100:.2f}%")
    # 無作為に同数落とす対照 20seed
    ro=[];rb=[]
    for sd in range(20):
        rng=np.random.default_rng(sd)
        idx=rng.permutation(len(base))[:len(base)-drop_n]
        sub=[base[i] for i in idx]; s=E.summarize(sub,"rand")
        ro.append(s["roi"]); rb.append(s["n100k"]/s["n"])
    print(f"     無作為同数対照 中央 ROI {np.median(ro)*100:.2f}% 10万+率 {np.median(rb)*100:.2f}%"
          f"  除外案が勝つ seed: ROI {int((np.array(ro)<s1['roi']).sum())}/20 "
          f"10万+ {int((np.array(rb)<s1['n100k']/s1['n']).sum())}/20")
print("\n### 参考: 高額枠を型F へ広げない根拠（T=15万）")
for wn,ws in W:
    f=design.build(ws,150_000,pop=lambda r: r["type"]=="F")
    b=design.build(ws,150_000,pop=bcd)
    sf=E.summarize(f,"F"); sb=E.summarize(b,"BCD")
    print(f"  {wn}: 型F ROI {sf['roi']*100:.2f}% 10万+率 {sf['n100k']/sf['n']*100:.2f}% ↔ "
          f"型B/C/D ROI {sb['roi']*100:.2f}% 10万+率 {sb['n100k']/sb['n']*100:.2f}%")
