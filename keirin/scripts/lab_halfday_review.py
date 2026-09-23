#!/usr/bin/env python3
"""半日ごとの当落傾向レビュー — 仮説を Discord へ出す（本番は一切変更しない）

設計は docs/SELF_IMPROVEMENT_ROUTINE.md。このスクリプトの役割は**観測と提案**だけで、
type_lab.py の定数も入稿も触らない。

  python3 scripts/lab_halfday_review.py --dry-run     # 表示のみ
  python3 scripts/lab_halfday_review.py               # Discord(review) へ送信

## なぜこの形か（素直に書くと嘘をつく道具になる）

素朴に「当たった条件 / 外れた条件」を並べると、次の 3 つで必ず嘘が出る。
2026-09-23 に実データで確かめた数字を添えて、対策を固定する。

1. **多重比較**: 132 セルを無補正で見ると 57 セルが「有意」になった。
   → Benjamini-Hochberg で FDR を 10% に制御する。
2. **プラン構成による交絡**: 会場ごとに走るプランが違う（券種も点数も違う）ので、
   会場の差がプランの差になる。→ **プラン内で比較して賭け金で加重**する（層別）。
   プラン同士は比較しない（定義から違うものを比べても仮説にならない）。
3. **小標本**: 実売の日次 ROI は sd 0.374、プラスの日は 8.5% しかない。
   ROI +0.05 の検出に約 220 日かかる。→ セル最小 n、日ブロックブートストラップ、
   期間の前後半で同符号、さらに **paper の長期履歴と符号が一致するか**を見る。

出力は「検討に値する仮説」であって結論ではない。採否は
docs/SELF_IMPROVEMENT_ROUTINE.md の四半期ゲートでのみ行う。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2

LIVE_WINDOW_DAYS = 90
PAPER_WINDOW_DAYS = 400
MIN_CELL_N = 120
MIN_PLANS = 2            # 1 プランしか無いセルはプラン効果と区別できない
BOOT = 1500
FDR_Q = 0.10
TOP_N = 8
SEED = 20260923

SQL = """
select race_date, race_type, n_entries, day_index, venue_name, type_label, plan_key,
       axis_sum, arare, n_legs, budget, coalesce(payout, 0) as payout, hit
from keirin.type_lab_picks
where mode = any(%s) and settled_at is not null and race_date >= current_date - %s
"""


# --- 予測オッズの誤差（第一の監視対象）---------------------------------------
# 買った 1 点ごとに「予測オッズ」と「確定オッズ」を突き合わせる。
# 2026-09-23 の実測（21日・39,652点）: 外れた目は中央 1.138、的中した目は 0.862。
# ＝当たる目ほど締まり、外れる目ほど流れる。点数・配分・払戻ゲートはすべて
# 予測オッズの上に乗っているので、ここのずれは買い目の効率へ直接効く。
ODDS_SQL = """
with legs as (
  select p.race_key, p.bet_type, p.plan_key, p.venue_name, p.race_date,
         (x->>'combo') as combo, (x->>'pred_odds')::numeric as pred_odds,
         ((x->>'combo') = p.win_combo) as won
  from keirin.type_lab_picks p, lateral jsonb_array_elements(p.legs) x
  where p.mode in ('live','live9') and p.settled_at is not null
    and p.race_date >= current_date - %s and (x->>'pred_odds')::numeric > 0),
 fin as (
  select distinct on (race_key, bet_type, combination)
         race_key, bet_type, combination, odds_value
  from keirin.wt_odds
  where race_key in (select distinct race_key from legs)
  order by race_key, bet_type, combination, collected_at desc)
select l.race_date, l.plan_key, l.venue_name, l.pred_odds, l.won,
       f.odds_value as final_odds
from legs l join fin f
  on f.race_key = l.race_key and f.bet_type = l.bet_type and f.combination = l.combo
"""


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


def odds_drift(rows: list[dict]) -> list[str]:
    """予測オッズと確定オッズのずれを、区分・帯・プラン別に要約する。"""
    if not rows:
        return ["• 予測オッズ: 突き合わせできる点がありません（wt_odds の取り込み待ちの可能性）"]
    ratio = [(r, float(r["final_odds"]) / float(r["pred_odds"])) for r in rows]
    won = [v for r, v in ratio if r["won"]]
    lost = [v for r, v in ratio if not r["won"]]
    detail = (f"  全点 {_median([v for _, v in ratio]):.3f}"
              f" / 的中した目 {_median(won):.3f}（n={len(won)}）"
              f" / 外れた目 {_median(lost):.3f}（n={len(lost)}）")
    out = [f"**予測オッズの誤差**（{len(rows):,}点・確定÷予測の中央値）", detail]
    if won and _median(won) < 0.95:
        shortfall = (1 - _median(won)) * 100
        out.append(f"  → 当たる目が中央で {shortfall:.0f}% 締まっている。"
                   f"配分・払戻ゲートはその分だけ甘い側で判定している")
    bands = [(0, 20, "〜20倍"), (20, 50, "20-50"), (50, 100, "50-100"),
             (100, 300, "100-300"), (300, 10**9, "300倍〜")]
    parts = []
    for lo, hi, lab in bands:
        vs = [v for r, v in ratio if lo <= float(r["pred_odds"]) < hi]
        if len(vs) >= 100:
            parts.append(f"{lab} {_median(vs):.2f}(n={len(vs)})")
    if parts:
        out.append("  帯別: " + " / ".join(parts))
    per_plan = defaultdict(list)
    for r, v in ratio:
        per_plan[r["plan_key"]].append(v)
    worst = sorted(((k, _median(v), len(v)) for k, v in per_plan.items() if len(v) >= 200),
                   key=lambda t: t[1])[:3]
    if worst:
        out.append("  プラン別に締まりが大きい順: "
                   + " / ".join(f"{k} {m:.2f}(n={n})" for k, m, n in worst))
    return out


def _bucket(v, edges, labels):
    if v is None:
        return None
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


def cells_of(r: dict) -> list[tuple[str, str]]:
    """1 行が属する (切り口, セル) を列挙する。プランは層別に使うので含めない。"""
    out = [
        ("レース種別", r["race_type"] or "不明"),
        ("車立て", f"{r['n_entries']}車"),
        ("開催日次", f"{r['day_index']}日目"),
        ("会場", r["venue_name"]),
        ("型", r["type_label"]),
        ("点数", f"{r['n_legs']}点"),
    ]
    axis = _bucket(float(r["axis_sum"]) if r["axis_sum"] is not None else None,
                   [1.25, 1.35, 1.45, 1.60],
                   ["〜1.25", "1.25-1.35", "1.35-1.45", "1.45-1.60", "1.60〜"])
    if axis:
        out.append(("軸信頼", axis))
    arare = _bucket(r["arare"], [-1, 1, 8, 12], ["〜-1", "-1-0", "1-7", "8-11", "12〜"])
    if arare:
        out.append(("ライン形", arare))
    return [(d, n) for d, n in out if n]


def fetch(conn, modes: list[str], window: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(SQL, (modes, window))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


class Board:
    """日 × プラン × セル の集計台。ブートストラップは日単位で再標本する。"""

    def __init__(self, rows: list[dict]):
        self.days = sorted({r["race_date"] for r in rows})
        self.acc: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0, 0])))
        for r in rows:
            v = (r["budget"], r["payout"], 1, 1 if r["hit"] else 0, 1 if r["payout"] >= 100_000 else 0)
            for key in [None, *cells_of(r)]:
                a = self.acc[r["race_date"]][r["plan_key"]][key]
                for i in range(5):
                    a[i] += v[i]
        self.cells = {c for d in self.acc for p in self.acc[d] for c in self.acc[d][p] if c is not None}

    def gather(self, days, cell):
        per_plan: dict = defaultdict(lambda: [[0, 0], [0, 0]])   # plan -> [[in bet,pay],[out bet,pay]]
        n = hits = p100 = 0
        plans = set()
        for d in days:
            for plan, cells in self.acc[d].items():
                tot = cells.get(None)
                if not tot:
                    continue
                inn = cells.get(cell)
                if inn:
                    per_plan[plan][0][0] += inn[0]
                    per_plan[plan][0][1] += inn[1]
                    n += inn[2]
                    hits += inn[3]
                    p100 += inn[4]
                    plans.add(plan)
                per_plan[plan][1][0] += tot[0] - (inn[0] if inn else 0)
                per_plan[plan][1][1] += tot[1] - (inn[1] if inn else 0)
        return per_plan, n, hits, p100, plans

    @staticmethod
    def stratified_diff(per_plan) -> float | None:
        """プランごとの (セル内 ROI − セル外 ROI) を、セル内賭け金で加重平均する。"""
        num = den = 0.0
        for (b_in, p_in), (b_out, p_out) in per_plan.values():
            if b_in <= 0 or b_out <= 0:
                continue
            num += b_in * (p_in / b_in - p_out / b_out)
            den += b_in
        return num / den if den else None

    def effect(self, cell, days=None):
        per_plan, *_ = self.gather(days or self.days, cell)
        return self.stratified_diff(per_plan)


def analyse(board: Board, rng: random.Random) -> list[dict]:
    half = board.days[len(board.days) // 2]
    first = [d for d in board.days if d < half]
    second = [d for d in board.days if d >= half]
    tests = []
    for cell in board.cells:
        per_plan, n, hits, p100, plans = board.gather(board.days, cell)
        if n < MIN_CELL_N or len(plans) < MIN_PLANS:
            continue
        eff = board.stratified_diff(per_plan)
        if eff is None:
            continue
        boots = []
        for _ in range(BOOT):
            samp = [rng.choice(board.days) for _ in board.days]
            bb, nn, *_ = board.gather(samp, cell)
            if nn < 30:
                continue
            e = board.stratified_diff(bb)
            if e is not None:
                boots.append(e)
        if len(boots) < BOOT * 0.5:
            continue
        boots.sort()
        lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]
        side = min(sum(1 for x in boots if x <= 0), sum(1 for x in boots if x >= 0)) / len(boots)
        p = max(2 * side, 1.0 / len(boots))
        e1, e2 = board.effect(cell, first), board.effect(cell, second)
        tests.append({
            "cell": cell, "n": n, "plans": len(plans), "effect": eff, "lo": lo, "hi": hi,
            "p": p, "hit": hits / n, "p100k": p100,
            "consistent": bool(e1 is not None and e2 is not None and e1 * e2 > 0),
        })
    # Benjamini-Hochberg
    tests.sort(key=lambda t: t["p"])
    m = len(tests)
    keep = 0
    for i, t in enumerate(tests, 1):
        if t["p"] <= FDR_Q * i / m:
            keep = i
    for t in tests:
        t["tested"] = m
    return tests[:keep]


MATERIAL = 0.02   # これ未満の長期効果は「実質ゼロ」として扱う


def hypothesis(t: dict, paper_eff: float | None) -> str:
    """仮説を 1 件分の文にする。**長期の裏付けの強さを誇張しない**のがこの関数の要点。

    直近 live の差は分散で簡単に ±0.3 動く（実売の日次 ROI は sd 0.374）。
    paper の長期履歴で同符号でも、効果量が 0.02 未満なら「裏が取れた」とは書かない。
    """
    dim, name = t["cell"]
    worse = t["effect"] < 0
    verb = "落としている" if worse else "稼いでいる"
    base = (f"**{dim}={name}** が層別 ROI を {t['effect']:+.3f} {verb}"
            f"（n={t['n']}・{t['plans']}プラン・95%CI [{t['lo']:+.3f},{t['hi']:+.3f}]）")
    if paper_eff is None:
        note = "paper 履歴に同じセルの十分な標本が無く、長期の裏が取れていない"
        strength = "弱"
    elif abs(paper_eff) < MATERIAL:
        note = (f"paper 履歴（長期）では {paper_eff:+.3f} ＝ **実質ゼロ**。"
                f"直近の {t['effect']:+.3f} は大半が分散とみてよい")
        strength = "弱"
    elif paper_eff * t["effect"] > 0:
        ratio = abs(t["effect"]) / abs(paper_eff)
        note = (f"paper 履歴（長期）も同符号 {paper_eff:+.3f}。"
                + (f"ただし直近はその {ratio:.1f} 倍と大きく、差の大半は分散" if ratio >= 2
                   else "効果量も同程度＝**長期の裏が取れている**"))
        strength = "中" if ratio >= 2 else "強"
    else:
        note = f"⚠️ paper 履歴では逆符号 {paper_eff:+.3f} ＝ 直近だけの現象の可能性が高い"
        strength = "弱"
    action = "このセルを外す / 配分を薄くする" if worse else "このセルへ寄せる"
    return f"[裏付け:{strength}] {base}\n   → 検討: {action}。{note}"


def post_discord(text: str, env_path: Path) -> bool:
    url = None
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("DISCORD_WEBHOOK_URL_REVIEW="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
    url = url or os.environ.get("DISCORD_WEBHOOK_URL_REVIEW")
    if not url:
        print("WARN: DISCORD_WEBHOOK_URL_REVIEW が見つからないため送信しません", file=sys.stderr)
        return False
    body = json.dumps({"content": text[:1900]}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as res:
        return 200 <= res.status < 300


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Discord へ送らず表示する")
    ap.add_argument("--window", type=int, default=LIVE_WINDOW_DAYS)
    ap.add_argument("--odds-window", type=int, default=21,
                    help="予測オッズ突き合わせの窓（日）。wt_odds が重いので既定は短め")
    ap.add_argument("--label", default="", help="通知に付ける見出し（例: 昼 / 夜）")
    args = ap.parse_args()

    dsn = os.environ.get("KEIRIN_DB_URL")
    if not dsn:
        print("KEIRIN_DB_URL が未設定", file=sys.stderr)
        return 2
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True)          # 🔴 この道具は DB を書き換えない
    live_rows = fetch(conn, ["live", "live9"], args.window)
    paper_rows = fetch(conn, ["paper", "paper9"], PAPER_WINDOW_DAYS)
    cur = conn.cursor()
    cur.execute(ODDS_SQL, (args.odds_window,))
    cols = [d[0] for d in cur.description]
    odds_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    odds_rows_summary = odds_drift(odds_rows)
    if not live_rows:
        print("live の採点済みデータが無い", file=sys.stderr)
        return 1

    rng = random.Random(SEED)
    live = Board(live_rows)
    paper = Board(paper_rows) if paper_rows else None
    found = analyse(live, rng)
    consistent = [t for t in found if t["consistent"]]

    days = len(live.days)
    bet = sum(v[0] for d in live.acc for p in live.acc[d] for k, v in live.acc[d][p].items() if k is None)
    pay = sum(v[1] for d in live.acc for p in live.acc[d] for k, v in live.acc[d][p].items() if k is None)
    head = (f"🔎 **型ラボ 半日レビュー{(' ' + args.label) if args.label else ''}** "
            f"({datetime.now(tz=ZoneInfo('Asia/Tokyo')):%m/%d %H:%M})\n"
            f"live 直近 {days} 日 / {len(live_rows)} 件 / ROI {pay / bet:.3f}"
            f"（ROI は日次 sd 0.374 で判定には使えない。傾向のみ）")

    lines = [head, ""]
    lines += odds_rows_summary
    lines.append("")
    if not consistent:
        lines.append("**当落の傾向: 検討に値するセルはありません**"
                     "（層別・FDR・前後半一致を通ったものが無い＝分散の範囲）")
    else:
        lines.append("**当落の傾向**")
        for t in sorted(consistent, key=lambda x: -abs(x["effect"]))[:TOP_N]:
            paper_eff = paper.effect(t["cell"]) if paper and t["cell"] in paper.cells else None
            lines.append("• " + hypothesis(t, paper_eff))
    lines.append("")
    lines.append("_この通知は仮説であって結論ではありません。採否は四半期ゲート"
                 "（事前登録・両窓同符号・無作為対照20seed・TEST 4窓）でのみ行います。_")
    text = "\n".join(lines)

    print(text)
    if not args.dry_run:
        env = Path(__file__).resolve().parent.parent / ".env"
        ok = post_discord(text, env)
        print(f"\nDiscord 送信: {'成功' if ok else '失敗'}", file=sys.stderr)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
