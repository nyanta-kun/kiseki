from common import *
d=pd.read_pickle("ds.pkl")
t=d[(d["date"]>="20260906")&(d["n_entries"]==7)].copy()
t=t[t["plan"].str.match(r"^[BCD]_")]
t["hp"]=(t["origin"]=="highpay_fill")
print("### 高額枠が置かれたレースは本当に『質が低い』か（axis_sum = 軸信頼）")
print(t.groupby("hp").agg(n=("paid","size"),axs=("axis_sum","median"),gap=("gap","median"),
   final=("final","mean"),semi=("semi","mean"),yosen=("yosen","mean"),
   hit=("n_hits_excl_garami",lambda x:(x>0).mean()),paid=("paid","mean")).round(3).to_string())
