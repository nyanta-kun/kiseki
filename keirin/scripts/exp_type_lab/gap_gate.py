#!/usr/bin/env python3
"""`gap`（相手の開き）を商品に使えるか（2026-08-28）。

    PYTHONPATH=. .venv/bin/python3 scripts/exp_type_lab/gap_gate.py

## 何を確かめたいか

`gap` は `src/type_lab.race_shape` が計算しているのに**型判定にも買い目にも
使われていない**。答え合わせでは型に匹敵する分離がある（通期・7車）:

    順当    gap 小 28.1% → 大 46.7%
    軸崩壊  gap 小  9.0% → 大  5.0%

ただし測られたのは**決着クラス**であって、**表示的中・ROI では一度も測っていない**。

🔴 **本命の問い: 既に稼働している軸信頼ゲートの上に重ねて効くか。**
   答え合わせでは「相手の開きは二軸そろいを −2.7pt しか動かさない＝①軸の堅さと独立」
   と出ているので、独立なら足し算になるはず。重複していれば何も足さない。

## 作法（この repo の規約。破ると結論が逆になる）

🔴 **件数を減らす検証には必ず無作為対照を置く**。`race_filter_2026_08_27.md` では
   無作為半分の対照（83.0〜84.8%）が、型・種別・場で絞った全案（79.5〜81.3%）より
   高かった。件数を減らすと CI が広がるので上振れを効果と誤読する。
🔴 **絶対閾値ではなくプラン内の相対順位で切る**。軸信頼ゲートで効いたのはその形だけ。
🔴 CI を並べて「重なっていない」で判断しない。**同じ日でペアにした差**を bootstrap する。
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.database import get_connection                      # noqa: E402
from axis_gate import paired_diff                            # noqa: E402
from ev_axis_rank import NQ, PLANS, edges_from, qof, win     # noqa: E402
from race_filter import CONFIRM, EXPLORE, per_day, roi, shown_hit  # noqa: E402

#: 稼働中の軸信頼ゲート（正本 `backend/src/services/keirin_type_lab_gate.py`）。
#: 🔴 **写さずに読む**（片方だけ動くと「重ねた効果」を別の設定で測ることになる）。
def _axis_gate_min() -> dict[str, float]:
    import ast
    src = (REPO.parent / "backend" / "src" / "services"
           / "keirin_type_lab_gate.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.AnnAssign)
                and getattr(node.target, "id", "") == "AXIS_GATE_MIN"):
            return ast.literal_eval(node.value)
    raise SystemExit("AXIS_GATE_MIN を読めない")


AXIS_GATE_MIN = _axis_gate_min()


def load() -> list[dict]:
    """`gap` を含めて読む（既存の `ev_axis_rank.load` は gap を引いていない）。"""
    cols = ("race_key", "race_date", "race_type", "type_label", "plan_key",
            "axis_sum", "gap", "budget", "hit", "payout", "legs", "win_tf_odds")
    q = (f"SELECT {', '.join(cols)} FROM type_lab_picks "
         "WHERE mode='paper' AND settled_at IS NOT NULL "
         f"  AND plan_key IN ({','.join(['?'] * len(PLANS))})")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r))) for r in c.execute(q, PLANS).fetchall()]
    out = []
    for r in rows:
        legs = r["legs"]
        legs = json.loads(legs) if isinstance(legs, str) else (legs or [])
        if sum(float(x.get("stake") or 0) for x in legs) <= 0:
            continue
        r["race_date"] = str(r["race_date"])
        r["budget"] = int(r["budget"])
        r["payout"] = int(r["payout"] or 0)
        for k in ("axis_sum", "gap", "win_tf_odds"):
            r[k] = float(r[k]) if r[k] is not None else None
        if r["gap"] is None:
            continue
        out.append(r)
    print(f"読み込み {len(out):,}行（gap を持つ採点済み / 元 {len(rows):,}行）")
    return out


def passes_axis(r: dict) -> bool:
    """稼働中の軸信頼ゲート（下位1/5 外し）を通るか。"""
    floor = AXIS_GATE_MIN.get(r["plan_key"])
    return floor is None or r["axis_sum"] is None or r["axis_sum"] >= floor


def control(base: list[dict], n_keep: int, seed0: int = 100, n: int = 20):
    """同数を無作為に落とす対照。"""
    out = sorted(roi(random.Random(seed0 + s).sample(base, n_keep)) for s in range(n))
    return out


def report(label: str, keep: list[dict], base: list[dict]) -> None:
    if not keep or len(keep) >= len(base):
        print(f"   {label}: 対象なし / 全件"); return
    pt, lo, hi = paired_diff(keep, base)
    ctrl = control(base, len(keep))
    wins = sum(1 for c in ctrl if c < roi(keep))
    print(f"   {label:22} {per_day(keep):5.1f}件/日 表示的中 {shown_hit(keep):5.2f}% "
          f"ROI {roi(keep):5.1f}%  差 {pt:+5.1f}pt CI[{lo:+.1f},{hi:+.1f}]"
          f"{'🟢' if lo > 0 else '🔴'}  対照 {wins:2}/20（中央 {ctrl[10]:.1f}%）")


def main() -> None:
    rows = load()
    ex, cf = win(rows, EXPLORE), win(rows, CONFIRM)

    # ── 0. gap は axis_sum と独立か ──
    import statistics as st
    xs = [r["axis_sum"] for r in cf if r["axis_sum"] is not None]
    ys = [r["gap"] for r in cf if r["axis_sum"] is not None]
    print(f"\n== 0. gap と axis_sum の相関（確認窓 n={len(xs):,}）: "
          f"{st.correlation(xs, ys):+.3f}")

    edges = {p: edges_from([r for r in ex if r["plan_key"] == p], "gap") for p in PLANS}
    print("\n== 探索窓で決めたプラン内の五分位境界（gap）")
    for p in PLANS:
        print(f"   {p:7} {[round(e, 4) for e in edges[p]]}")

    def q(r):
        return qof(r["gap"], edges[r["plan_key"]])

    # ── 1. gap 単独（軸信頼ゲート無し）──
    print("\n== 1. gap のプラン内下位を外す（軸信頼ゲート**なし**・確認窓）")
    print(f"   {'基準（絞らない）':22} {per_day(cf):5.1f}件/日 "
          f"表示的中 {shown_hit(cf):5.2f}% ROI {roi(cf):5.1f}%")
    for cut in (1, 2):
        report(f"gap 下位{cut}/5 を外す", [r for r in cf if q(r) >= cut], cf)
    for cut in (1, 2):
        report(f"gap 上位{cut}/5 を外す", [r for r in cf if q(r) < NQ - cut], cf)

    # ── 2. 🔴 本命: 稼働中の軸信頼ゲートの上に重ねる ──
    ag = [r for r in cf if passes_axis(r)]
    print(f"\n== 2. 🔴 軸信頼ゲート（下位1/5外し・稼働中）の上に重ねる")
    print(f"   {'軸信頼ゲートのみ':22} {per_day(ag):5.1f}件/日 "
          f"表示的中 {shown_hit(ag):5.2f}% ROI {roi(ag):5.1f}%")
    for cut in (1, 2):
        report(f"+ gap 下位{cut}/5 を外す", [r for r in ag if q(r) >= cut], ag)

    # ── 3. 探索窓でも同じ向きか ──
    print("\n== 3. 探索窓でも同じ向きか")
    for cut in (1, 2):
        k = [r for r in ex if q(r) >= cut]
        pt, lo, hi = paired_diff(k, ex)
        print(f"   gap 下位{cut}/5 外し: ROI {roi(k):5.1f}% 差 {pt:+5.1f}pt "
              f"CI[{lo:+.1f},{hi:+.1f}] {'🟢' if lo > 0 else '🔴'}")

    # ── 4. プラン別の向き（両窓で一致するか）──
    print("\n== 4. プラン別（下位1/5 vs 残り・ROI 差）")
    print(f"{'plan':8}{'確認':>10}{'探索':>10}")
    for p in PLANS:
        gc = [r for r in cf if r["plan_key"] == p]
        ge = [r for r in ex if r["plan_key"] == p]
        if not (edges[p] and gc and ge):
            continue
        lc, rc = [r for r in gc if q(r) == 0], [r for r in gc if q(r) > 0]
        le, re_ = [r for r in ge if q(r) == 0], [r for r in ge if q(r) > 0]
        if not (lc and rc and le and re_):
            continue
        dc, de = roi(rc) - roi(lc), roi(re_) - roi(le)
        mark = "🟢" if dc > 0 and de > 0 else ("🔴 反転" if dc * de < 0 else "")
        print(f"{p:8}{dc:+9.1f}pt{de:+9.1f}pt {mark}")

    # ── 5. 決着クラスの再現（既知の分離が確認窓でも出るか）──
    print("\n== 5. gap 3分位 × 決着（確認窓・既知の分離の再現）")
    v = sorted(r["gap"] for r in cf)
    e3 = [v[len(v) // 3], v[len(v) * 2 // 3]]
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "outc", REPO.parent / "backend" / "src" / "services" / "keirin_type_lab_outcome.py")
    outc = importlib.util.module_from_spec(spec); spec.loader.exec_module(outc)
    seen, agg = set(), {i: {"n": 0, "firm": 0, "broken": 0} for i in range(3)}
    with get_connection() as c:
        keys = sorted({r["race_key"] for r in cf})
        po = {}
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            for rk, o, w in c.execute(
                    "SELECT race_key, p3_order, win_combo FROM type_lab_picks WHERE "
                    f"race_key IN ({','.join('?' * len(ch))}) AND mode='paper'", ch).fetchall():
                po[rk] = (o, w)
    for r in cf:
        if r["race_key"] in seen:
            continue
        seen.add(r["race_key"])
        i = sum(1 for e in e3 if r["gap"] >= e)
        o, w = po.get(r["race_key"], (None, None))
        fc = outc.finish_class(w, o) if (o and w) else None
        if not fc:
            continue
        agg[i]["n"] += 1
        agg[i]["firm"] += fc == "firm34"
        agg[i]["broken"] += fc == "broken"
    for i, lab in enumerate(("小", "中", "大")):
        a = agg[i]
        if a["n"]:
            print(f"   gap {lab}: n={a['n']:5,}  順当 {a['firm']/a['n']*100:5.2f}%  "
                  f"軸崩壊 {a['broken']/a['n']*100:5.2f}%")


if __name__ == "__main__":
    main()
