"""尾（高額的中）が数レースの偶然か、半期ごとに安定して出る差かを見る。上限なし（a2 と同じ母集団）。"""
import sys, io, contextlib
sys.argv = ["x"]
with contextlib.redirect_stdout(io.StringIO()):
    import a2_switch as A2
rows = A2.rows
half = lambda d: f"{d[:4]}{'H1' if int(d[5:7]) <= 6 else 'H2'}"
arms = ["現行", "Σp最大(的中系)", "セル最良(探索表示的中)"]
print(f"{'半期':<8}{'日':>4}" + "".join(f"{a[:8]:>26}" for a in arms))
print(f"{'':<12}" + f"{'4万+件 10万+件 4万+払戻/賭%':>26}" * 3)
for h in ["2024H2", "2025H1", "2025H2", "2026H1", "2026H2"]:
    R = [r for r in rows if half(r["date"]) == h]
    s = f"{h:<8}{len({r['date'] for r in R}):>4}"
    for a in arms:
        rs = A2.recs(R, A2.pols[a]); inv = sum(x[2] for x in rs)
        b4 = [x[3] for x in rs if x[3] >= 40000]
        s += f"{len(b4):>10}{sum(p >= 100000 for p in b4):>7}{sum(b4) / inv * 100:>9.1f}"
    print(s)
