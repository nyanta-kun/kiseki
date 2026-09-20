from common import *
d=pd.read_pickle("ds.pkl")
d["head"]=d["title"].fillna("").str.split("｜").str[0]
f=d[(d["plan"].isin(["F_hit","C_hit","B_hit","E_hit"]))&(d["date"]>="20260829")].copy()
print("F_hit のタイトル変更日を特定:")
print(d[d["plan"]=="F_hit"].groupby(["date","head"]).size().unstack(fill_value=0).head(25).to_string())
