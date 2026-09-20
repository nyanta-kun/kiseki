import numpy as np, pandas as pd
CACHE="/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"
df=pd.read_pickle(CACHE)
df["race_date"]=pd.to_datetime(df["race_date"])
d=df[df.race_date>="2025-01-01"][["player_id","venue_id","race_date","race_key",
    "first_rate","third_rate","race_point","s_count","b_count","prediction_mark","finish_order"]].copy()
d=d.sort_values(["player_id","race_date"])
d["prev_date"]=d.groupby("player_id")["race_date"].shift()
d["prev_venue"]=d.groupby("player_id")["venue_id"].shift()
d["gap"]=(d.race_date-d.prev_date).dt.days
same=d[(d.gap>=1)&(d.gap<=3)&(d.venue_id==d.prev_venue)]   # 同一開催の連日（近似）
print("同一開催の連日ペア n=", len(same))
for c in ["first_rate","third_rate","race_point","s_count","b_count"]:
    prev=d.groupby("player_id")[c].shift()
    ch=(same[c].values!=prev.loc[same.index].values)
    print(f"  {c}: 節内の連日で値が変わる割合 {ch.mean():.4f}")
# 開催をまたぐ（gap>=7）ときは変わるはず＝PIT更新の証拠
diff=d[(d.gap>=7)]
print("\n開催をまたぐ（7日以上空き）ペア n=", len(diff))
for c in ["first_rate","race_point","b_count"]:
    prev=d.groupby("player_id")[c].shift()
    ch=(diff[c].values!=prev.loc[diff.index].values)
    print(f"  {c}: 値が変わる割合 {ch.mean():.4f}")
# prediction_mark と結果の関係（市場代理として妥当か）
m=df[df.race_date>="2025-01-01"]
print("\nprediction_mark 別 3着内率 / 1着率")
print(m.groupby("prediction_mark").agg(n=("top3_flag","size"),top3=("top3_flag","mean"),
                                        win=("win_flag","mean")).to_string(float_format=lambda x:f"{x:.4f}"))
