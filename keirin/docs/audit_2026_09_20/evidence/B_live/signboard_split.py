"""高額・看板枠と本線の分割（REPORT.md 要旨 4 / 3-5b / 3-5c の再現）。"""
import json, os, random
os.chdir(os.path.dirname(os.path.abspath(__file__)))
rec=json.load(open("recomputed.json"))
def live(lo="00000000", hi="99999999"):
    return [x for x in rec if not x["deleted"] and x["status"] in ("published","submitted")
            and x["recomp"]["settled"] and x["recomp"]["bet"]>0 and lo<=x["race_key"][:8]<=hi]
OLD_HP={"7T1","7T3","7H1","7H2","9H1","7M1"}
NEW_HP={"F_sign","C_sign","B_sign","D_sign","A_sign","E_sign",
        "C_big","B_big","D_big","A_big","E_big","F_big","T_upset"}
HP=OLD_HP|NEW_HP
def s(V,l,B=4000):
    b=sum(x["recomp"]["bet"] for x in V); p=sum(x["recomp"]["payout"] for x in V)
    n=sum(1 for x in V if x["recomp"]["hit"] and x["recomp"]["payout"]>=x["recomp"]["bet"])
    rnd=random.Random(4); v=[]
    for _ in range(B):
        t=[V[rnd.randrange(len(V))] for _ in range(len(V))]
        bb=sum(y["recomp"]["bet"] for y in t); pp=sum(y["recomp"]["payout"] for y in t); v.append(100*pp/bb)
    v.sort()
    print(f"{l}: n={len(V)} 投資={b:,} ROI={100*p/b:.2f}% [{v[int(.025*B)]:.2f},{v[int(.975*B)]:.2f}] "
          f"表示={100*n/len(V):.2f}% 10万+={sum(1 for x in V if x['recomp']['payout']>=100000)}")
for tag, lo, hi, hp in [("通算","00000000","99999999",HP),
                        ("旧ランク期","00000000","20260828",OLD_HP),
                        ("型ラボ期","20260829","99999999",NEW_HP)]:
    R=live(lo,hi)
    print(f"--- {tag} ---")
    s(R,"全部"); s([x for x in R if x["rank_key"] not in hp],"本線"); s([x for x in R if x["rank_key"] in hp],"高額・看板枠")
