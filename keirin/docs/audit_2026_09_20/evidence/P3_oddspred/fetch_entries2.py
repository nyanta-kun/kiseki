import os, time, pandas as pd
from sqlalchemy import create_engine, text

BASE = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
OUT = f"{BASE}/P3_oddspred"
s = pd.read_csv(f"{BASE}/C_model/sample_races.csv")
eng = create_engine(os.environ["KEIRIN_DB_URL"])

COLS = ("race_key, frame_no, race_point, prediction_mark, player_class, style, "
        "line_group, line_size, line_pos, is_line_leader, first_rate, second_rate, "
        "third_rate, pred_win_pct, pred_top3_pct")

parts = []
for ym, grp in s.groupby("ym"):
    keys = grp.race_key.tolist()
    t0 = time.time()
    with eng.connect() as c:
        df = pd.read_sql_query(
            text(f"SELECT {COLS} FROM keirin.wt_entries WHERE race_key = ANY(:ks)"),
            c, params={"ks": keys})
    print(ym, len(keys), "races ->", len(df), "rows",
          "pred_null=", df.pred_win_pct.isna().sum(), f"{time.time()-t0:.1f}s", flush=True)
    parts.append(df)
    time.sleep(2)
eng.dispose()
all_ = pd.concat(parts, ignore_index=True)
all_.to_pickle(f"{OUT}/entries_sample2.pkl")
print("total", all_.shape)
