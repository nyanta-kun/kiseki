import csv, json, sys
from collections import defaultdict
from itertools import permutations

# --- independent re-implementation (NOT importing repo code) -----------------
def wins_tf(fin):
    """fin: list[(order, car)] for orders 1..3 -> all winning trifectas."""
    g = defaultdict(list)
    for o, c in fin:
        g[o].append(c)
    out = [()]
    rem = 3
    for o in sorted(g):
        if rem <= 0:
            break
        cars = sorted(g[o])
        take = min(len(cars), rem)
        out = [p + q for p in out for q in permutations(cars, take)]
        rem -= take
    if rem > 0:
        return []
    return sorted(out)

def wins_trio(fin):
    return sorted({frozenset(t) for t in wins_tf(fin)}, key=lambda s: sorted(s))

fin = defaultdict(list)
for rk, fo, fn in csv.reader(open('fin_dh.csv')):
    fin[rk].append((int(fo), int(fn)))

odds = defaultdict(dict)
for rk, bt, comb, ov in csv.reader(open('odds_dh.csv')):
    try:
        v = float(ov)
    except Exception:
        continue
    nums = [int(x) for x in comb.replace('=', '-').split('-') if x.isdigit()]
    if len(nums) != 3:
        continue
    k = '='.join(map(str, sorted(nums))) if bt == 'trio' else '-'.join(map(str, nums))
    odds[rk][(bt, k)] = v

bad_hit, bad_pay, unsettled_should, ok = [], [], [], 0
n = 0
for row in csv.reader(open('picks_dh.csv')):
    pid, rk, bt, budget, legs_s, hit_s, pay_s, wc, fo_s, settled = row
    legs = json.loads(legs_s)
    f = fin.get(rk)
    n += 1
    if not f or not wins_tf(f):
        if settled:
            unsettled_should.append((pid, rk, 'settled but no finish data'))
        continue
    if bt == 'trio':
        ws = {'='.join(map(str, sorted(w))) for w in wins_trio(f)}
    else:
        ws = {'-'.join(map(str, w)) for w in wins_tf(f)}
    won = [l for l in legs if l['combo'] in ws]
    exp_hit = bool(won)
    if not settled:
        continue
    got_hit = (hit_s in ('t','true','True'))
    if exp_hit != got_hit:
        bad_hit.append((pid, rk, bt, exp_hit, got_hit, sorted(ws)))
        continue
    ok += 1
    # payout check only where we have odds (September dump)
    if rk not in odds or not exp_hit:
        continue
    miss = [l for l in won if (bt, l['combo']) not in odds[rk]]
    if miss:
        continue
    exp_pay = sum(int(round(l['stake'] * odds[rk][(bt, l['combo'])])) for l in won)
    if pay_s and int(pay_s) != exp_pay:
        bad_pay.append((pid, rk, bt, pay_s, exp_pay, [(l['combo'], l['stake']) for l in won]))

print(f"rows={n} checked_ok={ok}")
print(f"hit mismatches = {len(bad_hit)}")
for x in bad_hit[:20]:
    print("  HIT", x)
print(f"payout mismatches (Sep) = {len(bad_pay)}")
for x in bad_pay[:20]:
    print("  PAY", x)
print(f"settled-without-finish = {len(unsettled_should)}")
for x in unsettled_should[:10]:
    print("  ", x)
