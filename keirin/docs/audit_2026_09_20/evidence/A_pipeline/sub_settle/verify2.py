import csv, json
from collections import defaultdict
from itertools import permutations
def wins_tf(fin):
    g = defaultdict(list)
    for o, c in fin: g[o].append(c)
    out=[()]; rem=3
    for o in sorted(g):
        if rem<=0: break
        cars=sorted(g[o]); take=min(len(cars),rem)
        out=[p+q for p in out for q in permutations(cars,take)]; rem-=take
    return [] if rem>0 else sorted(out)
fin=defaultdict(list)
for rk,fo,fn in csv.reader(open('fin.csv')): fin[rk].append((int(fo),int(fn)))
bad=[]; n=0; nowc=0
for row in csv.reader(open('picks.csv')):
    pid,rk,bt,budget,legs_s,hit_s,pay_s,wc,fo_s,settled=row
    if not settled: continue
    f=fin.get(rk); w=wins_tf(f) if f else []
    if not w: continue
    n+=1
    if hit_s in ('t','true','True'): continue   # win_combo = bought winning leg
    rep=w[0]
    exp = '='.join(map(str,sorted(rep))) if bt=='trio' else '-'.join(map(str,rep))
    if wc != exp: bad.append((pid,rk,bt,wc,exp))
print("non-hit settled rows checked:",n,"win_combo mismatches:",len(bad))
for b in bad[:10]: print(" ",b)
