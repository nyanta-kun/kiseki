import numpy as np, pandas as pd
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
z=np.load("/tmp/race_type_board.npz", allow_pickle=True)
KEY=np.array([str(k) for k in z["KEY"]]); P3=z["P3"]; PW=z["PW"]; DATE=np.array([str(d) for d in z["DATE"]])
n=len(KEY)
B=pd.DataFrame({"race_key":np.repeat(KEY,7),"frame_no":np.tile(np.arange(1,8),n),
                "p3_board":P3.reshape(-1),"pw_board":PW.reshape(-1),
                "date":np.repeat(DATE,7)})
W=pd.read_pickle(f"{AUD}/C_model/wf_preds_audit.pkl")[["race_key","frame_no","p3","pw","race_date"]]
m=B.merge(W,on=["race_key","frame_no"],how="inner")
print("merged",len(m))
for yr in ["2024","2025","2026"]:
    s=m[m.date.str[:4]==yr]
    if len(s)==0: continue
    print(yr,len(s),"corr p3 %.4f pw %.4f"%(s.p3_board.corr(s.p3), s.pw_board.corr(s.pw)),
          "mean board %.4f wf %.4f"%(s.p3_board.mean(), s.p3.mean()))
