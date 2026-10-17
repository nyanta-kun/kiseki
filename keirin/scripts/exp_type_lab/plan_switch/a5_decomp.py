"""列 lo-1 hi2 の分解（どの移動が効いたか）と半期ごとの安定性。日次上限あり。"""
import sys
sys.argv = ["x"]
from a4_grid import run, key_for, rows, summ, paired, fmt, HEAD, np, G
def half(d):
    y, m = d[:4], int(d[5:7]); return f"{y}{'H1' if m <= 6 else 'H2'}"
arms = {
  "列 lo-1 hi2 (全部)": lambda r: key_for(r, lo=-1, hi=2),
  "  C→B だけ(arare=1・堅い)": lambda r: key_for(r, lo=-1, hi=2) if r["arare"] == 1 and r["axis_sum"] >= 1.44 else key_for(r),
  "  F→E だけ(arare=1・混戦)": lambda r: key_for(r, lo=-1, hi=2) if r["arare"] == 1 and r["axis_sum"] < 1.44 else key_for(r),
  "看板: 準決勝系を外す": None,
}
import src.type_lab as TL
S0 = TL.SIGNBOARD_RACE_TYPES
print(HEAD + "   Δ表示[CI]        ΔROI[CI]")
for per in ["2024H2", "2025H1", "2025H2", "2026H1", "2026H2", "exp", "conf"]:
    R = [r for r in rows if (half(r["date"]) == per if "H" in per else r["win"] == per)]
    days = {r["date"] for r in R}; nd = len(days)
    base = run(R, key_for)
    print(f"-- {per} {nd}日"); print(fmt("現行", summ(base, nd)))
    for name, f in arms.items():
        if f is None:
            TL.SIGNBOARD_RACE_TYPES = frozenset({"決勝", "チャレンジ決勝"}); rs = run(R, key_for); TL.SIGNBOARD_RACE_TYPES = S0
        else:
            rs = run(R, f)
        ci = paired(base, rs, days)
        sb, sa = summ(base, nd), summ(rs, nd)
        print(fmt(name, sa) + f"  [{ci[0][0]:+.1f},{ci[0][1]:+.1f}] [{ci[1][0]:+.1f},{ci[1][1]:+.1f}]  ΔROI {sa['roi']-sb['roi']:+.1f} 除10 {sa['roi10']-sb['roi10']:+.1f}")
