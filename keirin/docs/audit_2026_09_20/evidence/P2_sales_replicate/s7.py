from common import *
d = pd.read_pickle("ds.pkl")
t=d[d["date"]>="20260829"].copy()
print("origin 分布:"); print(t.groupby(["origin"]).agg(n=("paid","size"),paid=("paid","mean")).round(1).to_string())
print("\norigin × plan:"); print(pd.crosstab(t["plan"],t["origin"]).to_string())
