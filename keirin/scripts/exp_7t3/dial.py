"""新枠(30倍+5点) の母集団を広げる目盛り。種別 × ライン関係。"""
import pickle, sys, numpy as np, pandas as pd
sys.path.insert(0,'.'); sys.path.insert(0,'/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/7bc12223-92ab-4766-81d1-18bc01d1b207/scratchpad')
rng=np.random.default_rng(707)
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],Z["AO"].astype(float),Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
O=pd.read_pickle("/tmp/overlap.pkl").set_index("race_key")
cross=O.cross.reindex(RK).fillna(True).values.astype(bool)
rt=F.race_type.astype(str).values
band=PO>=30; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:5]
v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
nd=len(np.unique(DATE))
def rep(m,lbl):
    R=int(m.sum())
    if R<100: return
    pay=np.where(hit[m],AO[m]*100,0.0); inv=npt[m].sum()*100
    r=np.zeros(R); r[hit[m]]=pay[hit[m]]
    b=[r[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1200)]
    print(f"  {lbl:<30} {R:5d}R {R/nd:5.2f}件/日 的中 {hit[m].mean()*100:5.2f}% "
          f"週{hit[m].mean()*(R/nd)*7:5.2f}ヒット ROI {pay.sum()/inv*100:6.1f}% "
          f"CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}] 中央 {int(np.median(pay[hit[m]])*20):>8,}円")
KES=np.isin(rt,["決勝","チャレンジ決勝"])
JUN=np.isin(rt,["準決勝","チャレンジ準決勝"])
TOK=np.isin(rt,["特選","初特選","選抜","チャレンジ選抜"])
ok=npt>0
print(f"新枠(30倍+5点)・払戻中央は1レース1万円(2,000円/点)換算・{nd}日")
print("\n■ 同ライン（7T1 が捨てる側）")
rep(ok&KES&~cross,"決勝×同ライン"); rep(ok&JUN&~cross,"準決勝×同ライン"); rep(ok&TOK&~cross,"特選・選抜×同ライン")
rep(ok&(KES|JUN)&~cross,"決勝+準決勝×同ライン")
rep(ok&(KES|JUN|TOK)&~cross,"決勝系すべて×同ライン")
print("\n■ 別ライン（7T1 が取る側）")
rep(ok&KES&cross,"決勝×別ライン"); rep(ok&JUN&cross,"準決勝×別ライン")
print("\n■ 参考: 決勝すべて / 全レース")
rep(ok&KES,"決勝すべて"); rep(ok,"全レース")
