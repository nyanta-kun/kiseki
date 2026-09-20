"""settled_* 列の独立再計算と突き合わせ。"""
import csv, glob, json, os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from mklib import *

subs = load_subs(); fin = load_finishers()
odds = {}
for p in sorted(glob.glob("odds_*.csv")):
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            odds[(r["rk"], r["bt"], r["cb"])] = float(r["odds_value"]) if r["odds_value"] else None

def settle(d, finishers, rk):
    lines = (d or {}).get("lines") or []
    bet = 0; labels = []
    for ln in lines:
        try: bet += int(ln.get("stake") or 0)
        except Exception: pass
        o = ORDERED.get(str(ln.get("bet_type") or ""))
        labels.append(None if o is None else combo_label(ln.get("combo"), o))
    won = win_labels(finishers or [])
    if not lines or not won or any(x is None for x in labels):
        return dict(bet=bet, payout=0, hit=False, settled=False, n=len(lines), won=won)
    wonset = set(won); payout = 0; hit = False; known = True
    for ln, lab in zip(lines, labels):
        if lab not in wonset: continue
        hit = True
        ordered = "-" in lab
        per100 = pay100(odds.get((rk, "trifecta" if ordered else "trio", lab.replace("=","-"))))
        if per100: payout += per100 * int(ln.get("stake") or 0) // 100
        else: known = False
    return dict(bet=bet, payout=payout, hit=hit, settled=(not hit) or known, n=len(lines), won=won)

rows = []
for s in subs:
    d = parse_bd(s["bet_detail"])
    r = settle(d, fin.get(s["race_key"]), s["race_key"]) if d else dict(bet=0,payout=0,hit=False,settled=False,n=0,won=[])
    rows.append((s, r))

# --- 突き合わせ ---
def I(x): return None if x in ("", None) else int(x)
def B(x): return None if x in ("", None) else (x == "t")

mism = {"bet":[], "payout":[], "hit":[], "n":[], "unsettled_but_cached":[], "settled_but_nocache":[]}
ncmp = 0
for s, r in rows:
    sb, sp, sh, sn = I(s["settled_bet"]), I(s["settled_payout"]), B(s["settled_hit"]), I(s["settled_n_combos"])
    if s["settled_at"] in ("", None):
        if r["settled"] and r["bet"] > 0 and s["bet_detail"]:
            mism["settled_but_nocache"].append((s["race_key"], s["rank_key"], s["status"]))
        continue
    ncmp += 1
    if not r["settled"]:
        mism["unsettled_but_cached"].append((s["race_key"], s["rank_key"], s["status"], sb, sp, sh, r))
        continue
    if sb != r["bet"]: mism["bet"].append((s["race_key"], s["rank_key"], sb, r["bet"]))
    if sp != r["payout"]: mism["payout"].append((s["race_key"], s["rank_key"], s["status"], sb, sp, r["payout"], r["hit"], sh, r["won"]))
    if sh != r["hit"]: mism["hit"].append((s["race_key"], s["rank_key"], sh, r["hit"], sp, r["payout"]))
    if sn != r["n"]: mism["n"].append((s["race_key"], s["rank_key"], sn, r["n"]))

print(f"比較対象（settled_at あり）: {ncmp}")
for k, v in mism.items():
    print(f"  {k}: {len(v)}")
print()
for k in ("bet","hit","n"):
    for x in mism[k][:10]: print(k, x)
print("--- payout 不一致 上位20 ---")
for x in sorted(mism["payout"], key=lambda y: -abs((y[5] or 0)-(y[4] or 0)))[:20]:
    print(f"{x[0]} {x[1]} {x[2]} bet={x[3]} cached_pay={x[4]} recomp={x[5]} hit(recomp)={x[6]} hit(cache)={x[7]}")
print("--- unsettled なのにキャッシュ 上位10 ---")
for x in mism["unsettled_but_cached"][:10]: print(x[:6], "won=", x[6]["won"][:4], "recomp_pay=", x[6]["payout"])
print("--- settled なのに未キャッシュ 上位10 ---")
for x in mism["settled_but_nocache"][:10]: print(x)

json.dump([{ "race_key": s["race_key"], "rank_key": s["rank_key"], "status": s["status"],
             "race_date": s["race_date"], "n_entries": s["n_entries"], "race_type": s["race_type"],
             "origin": s["origin"], "is_confident": s["is_confident"],
             "deleted": s["deleted_at"] not in ("", None),
             "cached": {"bet": I(s["settled_bet"]), "payout": I(s["settled_payout"]),
                        "hit": B(s["settled_hit"]), "n": I(s["settled_n_combos"]),
                        "settled_at": s["settled_at"]},
             "recomp": {k: r[k] for k in ("bet","payout","hit","settled","n")},
             "nk_race_id": s["netkeirin_race_id"] }
           for s, r in rows], open("recomputed.json","w"))
print("\nwrote recomputed.json")
