"""予測オッズ帯ごとの『無情報』回収率（板の全210点）＋ モデル確率順位の効き。"""
import sys, numpy as np, collections
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, engine as E
races=lib.get_races()
BND=[(2,10),(10,15),(15,30),(30,60),(60,100),(100,200),(200,300),(300,600),(600,1e9)]
def bi(o):
    for j,(lo,hi) in enumerate(BND):
        if lo<=o<hi: return j
    return -1
for wn,ws in [("探索",lib.window(races,lib.EXPLORE)),("確認",lib.window(races,lib.CONFIRM))]:
    # 1点100円買いの回収率 / 予測オッズ帯 × レース内確率順位の3分位
    tot=np.zeros((len(BND),4)); num=np.zeros((len(BND),4)); nhit=np.zeros((len(BND),4))
    ratio=collections.defaultdict(list)
    for r in ws:
        po=r["po"]; pr=r["probs"]; win=r["win"]; pay=r["pay"]/100.0
        if win is None: continue
        items=[(k,po[k],pr.get(k,0.0)) for k in po]
        for k,o,p in items:
            j=bi(o)
            if j<0: continue
            # 帯内でのモデル確率の順位（帯ごとに後で分位化するのは重いので簡易に
            # レース内全体の確率順位パーセンタイルを使う）
            tot[j,3]+=1; num[j,3]+= (pay if k==win else 0.0); nhit[j,3]+= (1 if k==win else 0)
        if win in po: ratio[bi(po[win])].append(pay/float(po[win]))
    print(f"\n=== {wn}: 予測オッズ帯 × 1点100円買いの回収率（無情報ベースライン） ===")
    print(f"{'帯':>10} {'点数':>9} {'的中率':>8} {'回収率':>8} {'確定/予測(中央)':>14}")
    for j,(lo,hi) in enumerate(BND):
        if tot[j,3]==0: continue
        rr=np.median(ratio[j]) if ratio[j] else float("nan")
        print(f"{lo:>4}-{hi if hi<1e8 else 0:<5.0f} {tot[j,3]:>9.0f} {nhit[j,3]/tot[j,3]*100:>7.3f}% "
              f"{num[j,3]/tot[j,3]*100:>7.2f}% {rr:>14.3f}")
