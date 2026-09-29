import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lineup_sim import board, ctx

z = board()
idx = [i for i in range(len(z["KEY"])) if bool(z["OKPRED"][i]) and int(z["WIN"][i]) >= 0
       and "2024-07-01" <= str(z["DATE"][i]) <= "2026-08-04"]
cache = {}
for n, i in enumerate(idx):
    cache[i] = ctx(i)
    if n % 5000 == 0: print(n, len(idx), flush=True)
bad = sum(1 for i, x in cache.items() if x is not None and x.shape.type_label != str(z["TYPE"][i]))
print("TYPE 不一致", bad, "/", sum(x is not None for x in cache.values()))
pickle.dump(cache, open(sys.argv[1], "wb"))
