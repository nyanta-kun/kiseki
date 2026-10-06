import pickle, numpy as np, sys
from pathlib import Path
H = Path(__file__).resolve().parents[2] / "data/exp_bet_review/h21"
def load(n): return pickle.load(open(H / f"arm_{n}.pkl", "rb"))
for n in ("A1", "A2", "A3", "A2s90", "A3s90"):
    z = load(n)
    inv = pay = 0; k = 0; hit = 0
    for d, (sold, _) in z["lineup"].items():
        for s in sold:
            inv += s["inv"]; pay += s["pay"]; k += 1; hit += s["pay"] > s["inv"]
    print(n, "rows", k, "ROI %.1f" % (pay / inv * 100), "表示的中 %.2f" % (hit / k * 100), "used", sum(1 for x in z["info"] if x["used"]))
