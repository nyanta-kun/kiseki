import sys
sys.path.insert(0,"/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib, design
from src import type_lab as TL
races=lib.get_races(); w=lib.window(races,lib.CONFIRM)
same=0; tot=0
for r in w:
    if r["type"]!="C": continue
    a=TL.build_legs(r["shape"], TL.PLANS["C_sign"], r["po"], r["probs"])
    b,_,_=design.signboard_legs(r,150_000)
    tot+=1
    if (a or [])==(list(b) if b else []): same+=1
print("C_sign 再現:", same, "/", tot)
same=0; tot=0
for r in w:
    if r["type"]!="C": continue
    a=TL.build_legs(r["shape"], TL.PLANS["C_big"], r["po"], r["probs"])
    b,_,_=design.signboard_legs(r,400_000,bust=True)
    tot+=1
    if (a or [])==(list(b) if b else []): same+=1
print("C_big 再現:", same, "/", tot)
