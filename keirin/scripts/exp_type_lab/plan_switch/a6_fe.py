from a4_grid import run, key_for, rows, summ, paired, fmt, HEAD, np, G, ok
from a5_decomp import half
def f2e(cond, incl_sign=False):
    def pol(r):
        k = key_for(r)
        if k in (("F_hit", "F_sign") if incl_sign else ("F_hit",)) and cond(r) and ok(r, "E_hit"):
            return "E_hit"
        return k
    return pol
arms = {
  "F_hit→E_hit arare=1": f2e(lambda r: r["arare"] == 1),
  "F_hit→E_hit arare>=2": f2e(lambda r: r["arare"] >= 2),
  "F_hit→E_hit 全部": f2e(lambda r: True),
  "F_hit+F_sign→E_hit arare=1": f2e(lambda r: r["arare"] == 1, True),
}
print(HEAD + "   Δ表示[CI]        ΔROI[CI]")
for per in ["2024H2", "2025H1", "2025H2", "2026H1", "2026H2", "exp", "conf"]:
    R = [r for r in rows if (half(r["date"]) == per if "H" in per else r["win"] == per)]
    days = {r["date"] for r in R}; nd = len(days); base = run(R, key_for)
    print(f"-- {per}"); print(fmt("現行", summ(base, nd)))
    for name, f in arms.items():
        rs = run(R, f); ci = paired(base, rs, days); sb, sa = summ(base, nd), summ(rs, nd)
        mv = sum(1 for x in rs if x[1] == "E_hit") - sum(1 for x in base if x[1] == "E_hit")
        print(fmt(name, sa) + f"  [{ci[0][0]:+.1f},{ci[0][1]:+.1f}] [{ci[1][0]:+.1f},{ci[1][1]:+.1f}] ΔROI {sa['roi']-sb['roi']:+.1f} 除10 {sa['roi10']-sb['roi10']:+.1f} 移{mv/nd:.1f}/日")
