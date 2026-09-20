import os, sys, time, pandas as pd
from sqlalchemy import create_engine, text
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
s=pd.read_csv(f"{OUT}/sample_races.csv")
eng=create_engine(os.environ["KEIRIN_DB_URL"])
parts=[]
for ym,grp in s.groupby("ym"):
    keys=grp.race_key.tolist()
    t0=time.time()
    with eng.connect() as c:
        df=pd.read_sql_query(text("SELECT race_key,bet_type,combination,odds_value "
            "FROM keirin.wt_odds WHERE bet_type='trifecta' AND race_key = ANY(:ks)"),
            c, params={"ks":keys})
    print(ym, len(keys), "races ->", len(df), "rows", f"{time.time()-t0:.1f}s", flush=True)
    parts.append(df)
    time.sleep(3)
eng.dispose()
all_=pd.concat(parts, ignore_index=True)
all_.to_pickle(f"{OUT}/odds_trifecta_sample.pkl")
print("total", all_.shape)
