"""板(P3)が honest walk-forward かを C_model の独立WF予測と突き合わせる。"""
import numpy as np, pandas as pd, sys
AUD="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit"
z=np.load("/tmp/race_type_board.npz", allow_pickle=True)
KEY=[str(k) for k in z["KEY"]]; P3=z["P3"]; PW=z["PW"]
rows=[]
for i,k in enumerate(KEY):
    for c in range(7):
        rows.append((k,c+1,float(P3[i,c]),float(PW[i,c])))
B=pd.DataFrame(rows,columns=["race_key","car","p3_board","pw_board"])
W=pd.read_pickle(f"{AUD}/C_model/wf_preds_audit.pkl")
print("WF cols",list(W.columns)[:20], W.shape)
