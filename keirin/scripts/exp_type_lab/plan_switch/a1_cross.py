"""現行が売るレース（型別）に、他のプランを当てたら？（同じレース・ゲートを通る分だけ）"""
from common import *
rows = load()
ALT = ["A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_sign", "A_ana", "L_lead", "L_flat"]
for t in "ABCDEF":
    print(f"\n### 型{t}（現行の行がある＝ゲート通過レース）  各セル: 表示% / ROI / 除10 / 4万+率%  [探索 | 確認]  n=代替が組めた割合")
    for k in ALT:
        line = f"{k:<8}"
        for w in ("exp", "conf"):
            R = [r for r in rows if r["win"] == w and r["type"] == t and baseline(r)]
            S = [r for r in R if ok(r, k, axis_gate=False) and k in r["plans"]]
            if len(S) < 30:
                line += f" | {'-':>34}"; continue
            inv = np.array([r["plans"][k]["inv"] for r in S]); pay = np.array([r["plans"][k]["pay"] for r in S])
            base_inv = np.array([r["plans"][baseline(r)]["inv"] for r in S]); base_pay = np.array([r["plans"][baseline(r)]["pay"] for r in S])
            ps = np.sort(pay)[::-1]
            line += (f" | {len(S)/len(R)*100:>4.0f}% {np.mean(pay>inv)*100:5.1f} {pay.sum()/inv.sum()*100:6.1f} "
                     f"{(pay.sum()-ps[:10].sum())/inv.sum()*100:5.1f} {np.mean(pay>=40000)*100:4.1f}"
                     f" (現{base_pay.sum()/base_inv.sum()*100:5.1f})")
        print(line)
