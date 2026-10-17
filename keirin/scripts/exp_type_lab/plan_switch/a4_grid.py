"""Part3: 型の境界（AXIS_SUM_FIRM・arare の列）と看板枠の種別を動かす。日次上限あり（現行 cap_priority）。"""
from collections import defaultdict

from common import *
import src.type_lab as TL
rows = load()
SIGN0 = TL.SIGNBOARD_RACE_TYPES

def typ(r, firm, lo, hi):
    a = r["arare"]; s = -1 if a <= lo else (1 if a >= hi else 0)
    return ("ABC" if r["axis_sum"] >= firm else "DEF")[s + 1]

def key_for(r, firm=1.44, lo=-1, hi=1):
    t = typ(r, firm, lo, hi)
    a = r["plans"].get("A_trio")
    s = sell_plans_for(t, 7, r["rtype"], pw_ent=r["pw_ent"], trio_ok=bool(a and a["gate"]) if t == "A" else None)
    k = s[0].key if s else None
    return k if k and ok(r, k) else None

def run(R, pol, cap=True):
    byday = defaultdict(list)
    for r in R:
        b = pol(r)
        if b: byday[r["date"]].append((r, b))
    out = []
    for day, c in byday.items():
        judged = [x for x in c if not G.daily_cap_exempt(x[0]["rtype"], x[0]["cupg"]) and not G.daily_cap_exempt_plan(x[1])]
        budget = max(1, int(len(judged) * 0.5)) if judged else 0
        n = 0
        for r, b in sorted(c, key=lambda x: -G.cap_priority(x[1], x[0]["axis_sum"], x[0]["rp_sd"])):
            ex = G.daily_cap_exempt(r["rtype"], r["cupg"]) or G.daily_cap_exempt_plan(b)
            if cap and not ex:
                if n >= budget: continue
                n += 1
            v = r["plans"][b]; out.append((day, b, v["inv"], v["pay"], r))
    return out

import collections
print("arare 分布", sorted(collections.Counter(r["arare"] for r in rows).items()))
arms = {"現行 1.44 / s(-1,1)": dict()}
for f in (1.40, 1.50, 1.58): arms[f"FIRM {f}"] = dict(firm=f)
for lo, hi in ((-2, 1), (-1, 2), (-2, 2), (0, 1), (-1, 0)): arms[f"列 lo{lo} hi{hi}"] = dict(lo=lo, hi=hi)
sign_arms = {"看板: 準決勝系を外す(F_hit)": frozenset({"決勝", "チャレンジ決勝"}),
             "看板: 無し(F_hit)": frozenset({"__none__"})}
if __name__ == "__main__":
  for cap in (False, True):
      for w in ("exp", "conf"):
          R = [r for r in rows if r["win"] == w]
          days = {r["date"] for r in R}; nd = len(days)
          print(f"\n== {w} 上限{'あり' if cap else 'なし'}"); print(HEAD + "   Δ表示[CI]        ΔROI[CI]")
          base = run(R, key_for, cap)
          for name, kw in arms.items():
              rs = run(R, lambda r, kw=kw: key_for(r, **kw), cap)
              ci = paired(base, rs, days)
              print(fmt(name, summ(rs, nd)) + ("" if not kw else f"  [{ci[0][0]:+.1f},{ci[0][1]:+.1f}] [{ci[1][0]:+.1f},{ci[1][1]:+.1f}]"))
          for name, st in sign_arms.items():
              TL.SIGNBOARD_RACE_TYPES = st
              rs = run(R, key_for, cap)
              TL.SIGNBOARD_RACE_TYPES = SIGN0
              ci = paired(base, rs, days)
              print(fmt(name, summ(rs, nd)) + f"  [{ci[0][0]:+.1f},{ci[0][1]:+.1f}] [{ci[1][0]:+.1f},{ci[1][1]:+.1f}]")
