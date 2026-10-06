#!/usr/bin/env python3
"""H21 集計: 腕 ①②③（と感度・④）の表を作る。採否は書かない（機械判定まで）。

    PYTHONPATH=. .venv/bin/python scripts/exp_bet_review/h21_report.py
出力: data/exp_bet_review/h21/h21_tables.md
"""
from __future__ import annotations
import collections, json, pickle, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(REPO))
import h21_core as K                                       # noqa: E402

H = K.H
JST = timezone(timedelta(hours=9))
NB = 2000
RNG = np.random.default_rng(20261006)
BANDS = [("朝(〜11:59)", 0, 12), ("昼(12〜14:59)", 12, 15), ("夕(15〜17:59)", 15, 18), ("夜(18:00〜)", 18, 25)]
OUT = []


def P(s=""):
    print(s)
    OUT.append(s)


def band_of(epoch):
    h = datetime.fromtimestamp(epoch, JST).hour
    for name, a, b in BANDS:
        if a <= h < b:
            return name
    return BANDS[-1][0]


def load(n):
    return pickle.load(open(H / f"arm_{n}.pkl", "rb"))


recs, skip = K.load_window()
idx = {r["key"]: i for i, r in enumerate(recs)}
DAYS = sorted({r["date"] for r in recs})
DPOS = {d: i for i, d in enumerate(DAYS)}
ND = len(DAYS)
PRE = np.array([i for i, d in enumerate(DAYS) if d <= K.BOARD_END])
POST = np.array([i for i, d in enumerate(DAYS) if d > K.BOARD_END])
BAND = [band_of(r["start"]) for r in recs]
SLOT_REC = {"main": "main", "highpay": "sign", "lead": "lead"}


def rows_of(z):
    """売った行（日次ラインナップ）→ dict のリスト。"""
    out = []
    for d, (sold, _) in z["lineup"].items():
        for s in sold:
            i = idx[s["key"]]
            out.append(dict(s, day=d, i=i, band=BAND[i]))
    return out


def day_arrays(rows, sel=lambda r: True):
    inv = np.zeros(ND); pay = np.zeros(ND); n = np.zeros(ND); hit = np.zeros(ND); big = np.zeros(ND); win = np.zeros(ND)
    for r in rows:
        if not sel(r):
            continue
        p = DPOS[r["day"]]
        inv[p] += r["inv"]; pay[p] += r["pay"]; n[p] += 1; hit[p] += r["pay"] > r["inv"]
        big[p] += r["pay"] >= 100_000; win[p] += r["pay"] > 0
    return dict(inv=inv, pay=pay, n=n, hit=hit, big=big, win=win)


def roi(a, ix=None):
    ix = slice(None) if ix is None else ix
    return a["pay"][ix].sum() / a["inv"][ix].sum() * 100 if a["inv"][ix].sum() > 0 else float("nan")


def ratio(a, num, den, ix=None):
    ix = slice(None) if ix is None else ix
    return a[num][ix].sum() / a[den][ix].sum() * 100 if a[den][ix].sum() > 0 else float("nan")


def boot(ix):
    ix = np.arange(ND) if ix is None else ix
    return ix[RNG.integers(0, len(ix), size=(NB, len(ix)))]


B_ALL = boot(None)
B_PRE = boot(PRE)
B_POST = boot(POST)


def delta_ci(a, b, num="pay", den="inv", B=B_ALL, scale=100.0):
    """(a−b) の点推定と日ブートストラップ 95%CI（同じ添字を両腕へ）。"""
    def f(x):
        return x[num][B].sum(1) / np.maximum(x[den][B].sum(1), 1e-9) * scale
    d = f(a) - f(b)
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def pt(a, b, num="pay", den="inv", ix=None, scale=100.0):
    ix = slice(None) if ix is None else ix
    fa = a[num][ix].sum() / a[den][ix].sum() * scale if a[den][ix].sum() > 0 else float("nan")
    fb = b[num][ix].sum() / b[den][ix].sum() * scale if b[den][ix].sum() > 0 else float("nan")
    return fa - fb


def fmt_ci(lo, hi, nd=1):
    return f"[{lo:+.{nd}f}, {hi:+.{nd}f}]"


# ───────────────────────────────── 0. 母集団 ─────────────────────────────────
P("## 0. 母集団（窓 2026-06-08〜10-05・7車）")
cov = pickle.load(open(H / "cover.pkl", "rb"))
P(f"- 窓内の7車レース（`wt_races.n_entries=7`・発走時刻あり）: **{len(cov['races'])}R**。台に載ったもの（以降の全腕の母集団）: **{len(recs)}R**。")
P(f"- 載らなかった内訳: {dict(collections.Counter(skip.values()))}（`no_board` = 2026-08-04 までの台（`race_type_board.npz`・paper と同じ `OKPRED`）に無いレース）。")
P(f"- 台由来（〜08-04）{sum(1 for r in recs if r['src']=='board')}R / DB 由来（08-05〜）{sum(1 for r in recs if r['src']=='db')}R・開催日 {ND}（前半 {len(PRE)} / 後半 {len(POST)}）。")
P(f"- 同着で当たり目が複数のレース: {sum(1 for r in recs if r['win_n']>1)}R（代表1通りで採点・全腕共通）。")
P()

# ───────────────────────────────── 1. 被覆 ─────────────────────────────────
P("## 1. 発走前スナップショットの被覆（手順1）")
A = {n: load(n) for n in ("A1", "A2", "A3", "A2s90", "A3s90", "A2f", "A3f")}
info2, info3 = A["A2"]["info"], A["A3"]["info"]
P("### 1.1 窓内の全7車レース（7,800R）で、発走の120分／60分前以前の最後の1枚が取れた割合（発走時刻帯別）")
races, snaps = cov["races"], cov["snaps"]
cut = {120: {}, 60: {}}
for rk, (d, st) in races.items():
    stt = datetime.fromtimestamp(st, JST).replace(tzinfo=None)
    for L in (120, 60):
        c = [s for s in snaps.get(rk, []) if s[1] <= stt - timedelta(minutes=L)]
        cut[L][rk] = max(c, key=lambda s: s[1]) if c else None
P("| 発走時刻帯 | 7車R | 120分前 取れた | うち有効組≥60 | うち≥90% | 60分前 取れた | うち有効組≥60 | うち≥90% |")
P("|---|---|---|---|---|---|---|---|")
rows_b = collections.defaultdict(lambda: collections.Counter())
for rk, (d, st) in races.items():
    b = band_of(st); c = rows_b[b]; c["n"] += 1
    for L in (120, 60):
        s = cut[L][rk]
        if s:
            c[f"g{L}"] += 1; c[f"v{L}"] += s[3] >= 60; c[f"s{L}"] += s[3] / 210 >= 0.9
tot = collections.Counter()
for name, _, _ in BANDS:
    c = rows_b[name]; tot.update(c)
    P(f"| {name} | {c['n']} | {c['g120']/c['n']*100:.1f}% | {c['v120']/c['n']*100:.1f}% | {c['s120']/c['n']*100:.1f}% | {c['g60']/c['n']*100:.1f}% | {c['v60']/c['n']*100:.1f}% | {c['s60']/c['n']*100:.1f}% |")
c = tot
P(f"| **全体** | {c['n']} | {c['g120']/c['n']*100:.1f}% | {c['v120']/c['n']*100:.1f}% | {c['s120']/c['n']*100:.1f}% | {c['g60']/c['n']*100:.1f}% | {c['v60']/c['n']*100:.1f}% | {c['s60']/c['n']*100:.1f}% |")
P()
no_any = [rk for rk in races if not snaps.get(rk)]
gap_days = collections.Counter(races[rk][0] for rk in no_any)
n120 = sum(1 for rk in races if cut[120][rk] is None); n60 = sum(1 for rk in races if cut[60][rk] is None)
P(f"- 取れなかった理由（全7,800R）: **その日のスナップショットが丸ごと無い（収集の欠け）{len(no_any)}R**（{len(gap_days)} 開催日・うち2026-06-08〜06-12 に {sum(v for d,v in gap_days.items() if d<='2026-06-12')}R）／"
  f"スナップショットはあるが全て締切時刻より後 = 120分前 {n120-len(no_any)}R・60分前 {n60-len(no_any)}R（主に朝の早いレースで、最初の板 morning 07:04 が発走の120分前より後）。")
P()
P("### 1.2 台に載った母集団（6,826R）での使用状況と、実際のスナップショットの時刻（発走の何分前か）")
P("| 腕 | 実オッズで組んだR | ①のまま: スナップ無し | ①のまま: 有効組<60（薄い板） | 使った板の 発走何分前（p10/中央/p90） | 使った板の有効組/210（p10/中央/p90） |")
P("|---|---|---|---|---|---|")
for n in ("A2", "A3", "A2s90", "A3s90"):
    inf = A[n]["info"]
    u = [x for x in inf if x["used"]]
    c = collections.Counter(x["why"] for x in inf)
    lm = np.percentile([x["lead_min"] for x in u], [10, 50, 90]); sh = np.percentile([x["share"] * 210 for x in u], [10, 50, 90])
    thin = c["thin"] + c["thin90"]
    P(f"| {n} | {len(u)} ({len(u)/len(inf)*100:.1f}%) | {c['no_snapshot']} | {thin} | {lm[0]:.0f} / {lm[1]:.0f} / {lm[2]:.0f} | {sh[0]:.0f} / {sh[1]:.0f} / {sh[2]:.0f} |")
P()
P("### 1.3 使ったスナップショットの種別（台に載った母集団・②=120分前 / ③=60分前）")
for n in ("A2", "A3"):
    c = collections.Counter(x["type"] for x in A[n]["info"] if x["used"])
    P(f"- {n}: " + " / ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])))
P()
P("### 1.4 発走時刻帯別（台に載った母集団）の使用率")
P("| 発走時刻帯 | R | ②で実オッズ | ③で実オッズ |")
P("|---|---|---|---|")
for name, _, _ in BANDS:
    ii = [i for i in range(len(recs)) if BAND[i] == name]
    P(f"| {name} | {len(ii)} | {sum(info2[i]['used'] for i in ii)/len(ii)*100:.1f}% | {sum(info3[i]['used'] for i in ii)/len(ii)*100:.1f}% |")
P()

# ───────────────────────────────── 2. 主表 ─────────────────────────────────
ROWS = {n: rows_of(A[n]) for n in A}
DA = {n: day_arrays(ROWS[n]) for n in A}


def gate_counts(z):
    by_i = z["by_i"]
    n_main = sum(1 for r in by_i.values() if r["main"]); n_gate = sum(1 for r in by_i.values() if r["main"] and r["main"]["gate"])
    n_axis = sum(1 for r in by_i.values() if r["main"] and r["main"]["gate"] and r["main"]["axis_ok"])
    return n_main, n_gate, n_axis


def main_table(names, title):
    P(title)
    P("| 腕 | 売った行 | 件/日 | 回収率 | ΔROI (vs ①) | 95%CI | 前半 Δ | 後半 Δ | 表示的中 | Δ表示的中(pt) | 95%CI | 10万+/日 | 的中時払戻 中央 |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    a1 = DA["A1"]
    for n in names:
        a = DA[n]
        rows = ROWS[n]
        paid = [r["pay"] for r in rows if r["pay"] > 0]
        if n == "A1":
            P(f"| {n} ①予測 | {int(a['n'].sum())} | {a['n'].sum()/ND:.1f} | {roi(a):.1f}% | — | — | — | — | {ratio(a,'hit','n'):.2f}% | — | — | {a['big'].sum()/ND:.2f} | {np.median(paid):,.0f} |")
            continue
        lo, hi = delta_ci(a, a1)
        hlo, hhi = delta_ci(a, a1, "hit", "n")
        P(f"| {n} | {int(a['n'].sum())} | {a['n'].sum()/ND:.1f} | {roi(a):.1f}% | {pt(a,a1):+.2f} | {fmt_ci(lo,hi,2)} | {pt(a,a1,ix=PRE):+.2f} | {pt(a,a1,ix=POST):+.2f} | {ratio(a,'hit','n'):.2f}% | {pt(a,a1,'hit','n'):+.2f} | {fmt_ci(hlo,hhi,2)} | {a['big'].sum()/ND:.2f} | {np.median(paid):,.0f} |")


P("## 2. 主表（日次ラインナップ全体・本番の規則・窓全体）")
P("回収率 = Σ払戻 / Σ投資（払戻 = 最終オッズ × 賭け金）。ΔROI の CI は開催日ブートストラップ 2,000回（同じ添字を両腕へ）。表示的中 = 払戻 > 投資の行 / 売った行。")
main_table(["A1", "A2", "A3"], "### 2.1 事前登録の腕")
P()
main_table(["A2s90", "A3s90"], "### 2.2 感度（判定には使わない）: 有効組 ≥90% の板だけ実オッズ（それ以外は①）")
P()
main_table(["A2f", "A3f"], "### 2.2b 感度（判定には使わない）: 三連複は実三連複スナップショットではなく、実三連単スナップショットを畳んで作る（本番の導出と同じ）")
P()
P("### 2.3 前半・後半の内訳（回収率）")
P("| 腕 | 前半(〜08-04) 回収率 | 前半 Δ [CI] | 後半(08-05〜) 回収率 | 後半 Δ [CI] |")
P("|---|---|---|---|---|")
for n in ("A1", "A2", "A3", "A2s90", "A3s90", "A2f", "A3f"):
    a = DA[n]; a1 = DA["A1"]
    if n == "A1":
        P(f"| {n} | {roi(a,PRE):.1f}% | — | {roi(a,POST):.1f}% | — |")
        continue
    lo1, hi1 = delta_ci(a, a1, B=B_PRE); lo2, hi2 = delta_ci(a, a1, B=B_POST)
    P(f"| {n} | {roi(a,PRE):.1f}% | {pt(a,a1,ix=PRE):+.2f} {fmt_ci(lo1,hi1,1)} | {roi(a,POST):.1f}% | {pt(a,a1,ix=POST):+.2f} {fmt_ci(lo2,hi2,1)} |")
P()
gap_set = {d for d in DAYS if all(not snaps.get(rk) for rk, (dd, _) in races.items() if dd == d)}
PRE_S = np.array([i for i in PRE if DAYS[i] not in gap_set])
B_PRE_S = boot(PRE_S)
P(f"### 2.3b 前半のうちスナップショットが1枚も無い日（{len(gap_set)}日・全レースが①のまま＝Δ=0）を除く")
P(f"前半 {len(PRE)}日 = スナップショット有 {len(PRE_S)}日 + 無し {len(PRE)-len(PRE_S)}日（2026-06-17 と 06-19〜07-15）。")
P("| 腕 | 前半(スナップ有の日のみ) ΔROI [CI] |")
P("|---|---|")
for n in ("A2", "A3"):
    a = DA[n]; a1 = DA["A1"]
    lo, hi = delta_ci(a, a1, B=B_PRE_S)
    P(f"| {n} | {pt(a,a1,ix=PRE_S):+.2f} {fmt_ci(lo,hi,1)} |")
P()
P("### 2.4 機械判定（事前登録: ②または③ − ① の ΔROI の CI 下限 > 0 ∧ 前半・後半で同符号 ∧ 表示的中の低下 ≤ 1pt）")
P("| 腕 | ΔROI CI下限>0 | 前半・後半 同符号 | 表示的中の低下 ≤1pt | 判定（機械） |")
P("|---|---|---|---|---|")
verdict = {}
for n in ("A2", "A3"):
    a = DA[n]; a1 = DA["A1"]
    lo, hi = delta_ci(a, a1)
    c1 = lo > 0
    dpre, dpost = pt(a, a1, ix=PRE), pt(a, a1, ix=POST)
    c2 = np.sign(dpre) == np.sign(dpost)
    dh = pt(a, a1, "hit", "n")
    c3 = dh >= -1.0
    verdict[n] = c1 and c2 and c3
    P(f"| {n} | {'○' if c1 else '×'} (下限 {lo:+.2f}) | {'○' if c2 else '×'} (前 {dpre:+.2f} / 後 {dpost:+.2f}) | {'○' if c3 else '×'} (Δ {dh:+.2f}pt) | **{'基準を満たす' if verdict[n] else '基準を満たさない'}** |")
P()
P("### 2.5 入稿ゲートの通過件数（全レースに対する商品の組成と通過）")
P("`main` = その日の主力商品（型ごとの売り物・`sell_plans_for` の先頭）が組めたレース、`gate` = 平均想定払戻 > 20,000円 ∧ 最小オッズ ≥ 2 を通ったレース、`axis` = さらに軸信頼ゲートを通ったレース（日次上限の前）。")
P("| 腕 | main が組めたR | 入稿ゲート通過R | 軸ゲートも通過R | 売った行 | うち main | うち 高額枠 | うち L_lead |")
P("|---|---|---|---|---|---|---|---|")
for n in A:
    nm, ng, na = gate_counts(A[n])
    c = collections.Counter(r["slot"] for r in ROWS[n])
    P(f"| {n} | {nm} | {ng} ({ng-gate_counts(A['A1'])[1]:+d}) | {na} ({na-gate_counts(A['A1'])[2]:+d}) | {len(ROWS[n])} | {c['main']} | {c['highpay']} | {c['lead']} |")
P()

# ───────────────────────────────── 3. 実オッズで組めたレースだけ ─────────────────────────────────
P("### 2.6 高額払戻への依存（各腕で自分の払戻の上位 k 行を除いた回収率）")
P("| 腕 | k=0 | k=3 | k=10 | 払戻上位3行の合計が全払戻に占める割合 |")
P("|---|---|---|---|---|")
for n in ("A1", "A2", "A3"):
    pays = np.array(sorted([r["pay"] for r in ROWS[n]], reverse=True)); invs = sum(r["inv"] for r in ROWS[n])
    P(f"| {n} | {pays.sum()/invs*100:.1f}% | {pays[3:].sum()/invs*100:.1f}% | {pays[10:].sum()/invs*100:.1f}% | {pays[:3].sum()/pays.sum()*100:.1f}% |")
P()
P("## 3. 実オッズで組んだレースだけを比べる（希釈の確認・同じレース集合の①と比較）")
P("②③は、スナップショットが無い／薄いレースでは①のままなので、全体の Δ は薄まる。実オッズで組んだレース集合 U について、各腕の売った行を U で切り、①の売った行も同じ U で切る（売る商品がレースごとに違うため行数は一致しない）。")
P("| 腕 | U のレース数 | 腕の売った行(U内) | 回収率 | ①の売った行(U内) | 回収率 | Δ | 95%CI | 表示的中 腕 / ① |")
P("|---|---|---|---|---|---|---|---|---|")
for n in ("A2", "A3", "A2s90", "A3s90", "A2f", "A3f"):
    U = {i for i, x in enumerate(A[n]["info"]) if x["used"]}
    a = day_arrays(ROWS[n], lambda r: r["i"] in U); a1 = day_arrays(ROWS["A1"], lambda r: r["i"] in U)
    lo, hi = delta_ci(a, a1)
    P(f"| {n} | {len(U)} | {int(a['n'].sum())} | {roi(a):.1f}% | {int(a1['n'].sum())} | {roi(a1):.1f}% | {pt(a,a1):+.2f} | {fmt_ci(lo,hi,2)} | {ratio(a,'hit','n'):.2f}% / {ratio(a1,'hit','n'):.2f}% |")
P()

# ───────────────────────────────── 4. 商品別・時間帯別・スナップ種別 ─────────────────────────────────
P("## 4. 商品別（売った行の plan・slot）")
P("回収率（Δ は ② − ① / ③ − ①・点推定。行数が少ない商品は読まないこと）。")
plans = sorted({r["plan"] for n in A for r in ROWS[n]})
P("| 商品 | ①行 | ① 回収率 | ②行 | ② 回収率 | ②−① | ③行 | ③ 回収率 | ③−① |")
P("|---|---|---|---|---|---|---|---|---|")
for pl in plans:
    cells = []
    base = day_arrays(ROWS["A1"], lambda r: r["plan"] == pl)
    for n in ("A1", "A2", "A3"):
        a = day_arrays(ROWS[n], lambda r: r["plan"] == pl)
        cells.append(a)
    def c(a):
        return f"{int(a['n'].sum())} | {roi(a):.1f}%" if a['inv'].sum() > 0 else f"{int(a['n'].sum())} | —"
    d2 = f"{pt(cells[1],cells[0]):+.1f}" if cells[0]['inv'].sum() > 0 and cells[1]['inv'].sum() > 0 else "—"
    d3 = f"{pt(cells[2],cells[0]):+.1f}" if cells[0]['inv'].sum() > 0 and cells[2]['inv'].sum() > 0 else "—"
    P(f"| {pl} | {c(cells[0])} | {c(cells[1])} | {d2} | {c(cells[2])} | {d3} |")
P()
P("### 4.1 枠別（main / highpay / lead）")
P("| 枠 | ①行 | ① 回収率 | ②行 | ② 回収率 | ②−①(CI) | ③行 | ③ 回収率 | ③−①(CI) |")
P("|---|---|---|---|---|---|---|---|---|")
for sl in ("main", "highpay", "lead"):
    cs = [day_arrays(ROWS[n], lambda r: r["slot"] == sl) for n in ("A1", "A2", "A3")]
    l2, h2 = delta_ci(cs[1], cs[0]); l3, h3 = delta_ci(cs[2], cs[0])
    P(f"| {sl} | {int(cs[0]['n'].sum())} | {roi(cs[0]):.1f}% | {int(cs[1]['n'].sum())} | {roi(cs[1]):.1f}% | {pt(cs[1],cs[0]):+.1f} {fmt_ci(l2,h2)} | {int(cs[2]['n'].sum())} | {roi(cs[2]):.1f}% | {pt(cs[2],cs[0]):+.1f} {fmt_ci(l3,h3)} |")
P()
P("## 5. 発走時刻帯別（朝・昼・夕・夜）")
P("| 発走時刻帯 | ①行 | ① 回収率 | ②行 | ② 回収率 | ②−①(CI) | ③行 | ③ 回収率 | ③−①(CI) | 表示的中 ①/②/③ |")
P("|---|---|---|---|---|---|---|---|---|---|")
for name, _, _ in BANDS:
    cs = [day_arrays(ROWS[n], lambda r: r["band"] == name) for n in ("A1", "A2", "A3")]
    l2, h2 = delta_ci(cs[1], cs[0]); l3, h3 = delta_ci(cs[2], cs[0])
    P(f"| {name} | {int(cs[0]['n'].sum())} | {roi(cs[0]):.1f}% | {int(cs[1]['n'].sum())} | {roi(cs[1]):.1f}% | {pt(cs[1],cs[0]):+.1f} {fmt_ci(l2,h2)} | {int(cs[2]['n'].sum())} | {roi(cs[2]):.1f}% | {pt(cs[2],cs[0]):+.1f} {fmt_ci(l3,h3)} | {ratio(cs[0],'hit','n'):.1f} / {ratio(cs[1],'hit','n'):.1f} / {ratio(cs[2],'hit','n'):.1f}% |")
P()
P("## 5.1 使ったスナップショット種別別（②=120分前の腕・③=60分前の腕。U = その種別を使ったレース）")
P("| 腕 | 種別 | U のレース数 | 腕の行 | 回収率 | ①の行(U内) | 回収率 | Δ |")
P("|---|---|---|---|---|---|---|---|")
for n in ("A2", "A3"):
    inf = A[n]["info"]
    types = sorted({x["type"] for x in inf if x["used"]})
    for t in types:
        U = {i for i, x in enumerate(inf) if x["used"] and x["type"] == t}
        a = day_arrays(ROWS[n], lambda r: r["i"] in U); a1 = day_arrays(ROWS["A1"], lambda r: r["i"] in U)
        P(f"| {n} | {t} | {len(U)} | {int(a['n'].sum())} | {roi(a):.1f}% | {int(a1['n'].sum())} | {roi(a1):.1f}% | {pt(a,a1):+.1f} |")
P()

# ───────────────────────────────── 6. オッズの動き ─────────────────────────────────
P("## 6. 入稿時点 → 最終のオッズの動き（買った各点の 最終 ÷ 組んだときのオッズ）")
P("比 > 1 = 最終で上に跳ねた（払戻は買った時点の想定より大きい）、比 < 1 = 下がった。最終が無い（9999・欠落）点は除く。")


def drift(name, only_used=None):
    z = A[name]
    ratios, w, hitr = [], [], []
    n_missing = 0
    for r in ROWS[name]:
        rec = z["by_i"][idx_to_i(r)]
        built = rec[SLOT_REC[r["slot"]]]
        if built is None:
            continue
        if only_used is not None and (only_used(r["i"])) is False:
            continue
        fin = recs[r["i"]]["final"]
        win = recs[r["i"]]["win"]
        for combo, stake, o in built["legs"]:
            fo = fin["trio"].get(frozenset(combo)) if built["trio"] else fin["tf"].get(tuple(combo))
            if fo is None:
                n_missing += 1
                continue
            ratios.append(fo / o); w.append(stake)
            hitr.append((frozenset(combo) == frozenset(win)) if built["trio"] else (tuple(combo) == tuple(win)))
    return np.array(ratios), np.array(w, float), np.array(hitr, bool), n_missing


def idx_to_i(r):
    return r["i"]


def wq(x, w, q):
    o = np.argsort(x); cw = np.cumsum(w[o]) / w.sum()
    return float(x[o][np.searchsorted(cw, q)])


def drift_row(label, rat, w, hitm, miss):
    if len(rat) == 0:
        return f"| {label} | 0 | | | | | | | |"
    up = (rat > 1.0).mean() * 100; dn = (rat < 1.0).mean() * 100
    up2 = (rat > 1.25).mean() * 100; dn2 = (rat < 0.8).mean() * 100
    wup = w[rat > 1.0].sum() / w.sum() * 100
    h = f"{np.median(rat[hitm]):.3f} (n={hitm.sum()})" if hitm.any() else "—"
    return (f"| {label} | {len(rat)} | {up:.1f}% | {dn:.1f}% | {up2:.1f}% / {dn2:.1f}% | {np.median(rat):.3f} | "
            f"{wq(rat, w, 0.5):.3f} | {np.percentile(rat,[10,90])[0]:.2f} / {np.percentile(rat,[10,90])[1]:.2f} | {h} |")


P("| 腕・対象 | 買った点 | 上に跳ねた(>1) | 下がった(<1) | >1.25 / <0.8 | 比の中央値 | 賭け金加重の中央値 | p10 / p90 | 当たった点の比の中央値 |")
P("|---|---|---|---|---|---|---|---|---|")
for n, lab in (("A1", "① 予測オッズ → 最終（参考）"), ("A2", "② 120分前 → 最終"), ("A3", "③ 60分前 → 最終")):
    rat, w, hm, miss = drift(n)
    P(drift_row(lab + "（全行）", rat, w, hm, miss))
    if n != "A1":
        inf = A[n]["info"]
        rat, w, hm, miss = drift(n, only_used=lambda i, inf=inf: inf[i]["used"])
        P(drift_row(lab + "（実オッズで組んだレースのみ）", rat, w, hm, miss))
P()
P("### 6.1 実オッズで組んだレースの比を発走までの間隔で割る（②③の合算・買った点）")
P("| 板の取得 → 発走（分前） | 買った点 | 上に跳ねた | 下がった | 比の中央値 |")
P("|---|---|---|---|---|")
bins = [(0, 90), (90, 150), (150, 210), (210, 400)]
acc = {b: ([], ) for b in bins}
for n in ("A2", "A3"):
    z = A[n]; inf = z["info"]
    for r in ROWS[n]:
        i = r["i"]
        if not inf[i]["used"]:
            continue
        built = z["by_i"][i][SLOT_REC[r["slot"]]]
        if built is None:
            continue
        lm = inf[i]["lead_min"]
        for b in bins:
            if b[0] <= lm < b[1]:
                fin = recs[i]["final"]
                for combo, stake, o in built["legs"]:
                    fo = fin["trio"].get(frozenset(combo)) if built["trio"] else fin["tf"].get(tuple(combo))
                    if fo is not None:
                        acc[b][0].append(fo / o)
for b in bins:
    r_ = np.array(acc[b][0])
    if len(r_):
        P(f"| {b[0]}〜{b[1]} | {len(r_)} | {(r_>1).mean()*100:.1f}% | {(r_<1).mean()*100:.1f}% | {np.median(r_):.3f} |")
P()

P("### 6.2 組んだときのオッズ帯別（②③の合算・実オッズで組んだレースの買った点）")
P("| 組んだときのオッズ | 買った点 | 上に跳ねた | 下がった | 比の中央値 | 賭け金の合計に占める割合 |")
P("|---|---|---|---|---|---|")
obins = [(0, 10), (10, 30), (30, 100), (100, 300), (300, 1e9)]
accb = {b: ([], []) for b in obins}
for n in ("A2", "A3"):
    z = A[n]; inf = z["info"]
    for r in ROWS[n]:
        i = r["i"]
        if not inf[i]["used"]:
            continue
        built = z["by_i"][i][SLOT_REC[r["slot"]]]
        if built is None:
            continue
        fin = recs[i]["final"]
        for combo, stake, o in built["legs"]:
            fo = fin["trio"].get(frozenset(combo)) if built["trio"] else fin["tf"].get(tuple(combo))
            if fo is None:
                continue
            for b in obins:
                if b[0] <= o < b[1]:
                    accb[b][0].append(fo / o); accb[b][1].append(stake)
tot_stake = sum(sum(v[1]) for v in accb.values())
for b in obins:
    r_ = np.array(accb[b][0])
    if len(r_):
        lab = f"{b[0]}〜{b[1]:.0f}" if b[1] < 1e8 else f"{b[0]}以上"
        P(f"| {lab} | {len(r_)} | {(r_>1).mean()*100:.1f}% | {(r_<1).mean()*100:.1f}% | {np.median(r_):.3f} | {sum(accb[b][1])/tot_stake*100:.1f}% |")
P()

# ───────────────────────────────── 7. ④ 参考 ─────────────────────────────────
P("## 7. 参考 ④（判定外）H2 型: 60分前の実オッズ・100倍以上の安い順10点・`mkt_p100` 上位20%（窓内分位）")
P("60分前の板が有効組 ≥90% のレース（台に載った母集団）だけ。q = 1/odds を有効組で正規化、mkt_p100 = Σ q（odds ≥ 100）。買い目 = 有効な組のうち odds ≥ 100 の安い順10点・ダッチ（1万円・100円単位・各点最低100円）。払戻 = 最終オッズ × 賭け金。")
q4 = []
for i, r in enumerate(recs):
    sn = r["snap"].get(60)
    if sn is None or len(sn["tf"]) / 210 < 0.9:
        continue
    tf = sn["tf"]
    inv_ = {k: 1.0 / v for k, v in tf.items()}
    z_ = sum(inv_.values())
    mp100 = sum(v / z_ for k, v in inv_.items() if tf[k] >= 100)
    cand = sorted((k for k in tf if tf[k] >= 100), key=lambda k: tf[k])[:10]
    if len(cand) < 10:
        continue
    wsum = sum(1.0 / tf[k] for k in cand)
    stakes = {k: max(np.floor(10000 * (1.0 / tf[k]) / wsum / 100) * 100, 100) for k in cand}
    inv_amt = sum(stakes.values())
    win = r["win"]
    pay = stakes.get(win, 0.0) * r["pay_tf"] if win in stakes else 0.0
    q4.append(dict(i=i, day=r["date"], mp=mp100, inv=inv_amt, pay=pay, band=BAND[i]))
mp = np.array([x["mp"] for x in q4])
thr = np.percentile(mp, 80)
sel = [x for x in q4 if x["mp"] >= thr]
P(f"- 対象レース {len(q4)}（60分前の板が有効≥90%・100倍以上が10点以上）。上位20% の切点 mkt_p100 ≥ {thr:.4f}（窓内・in-sample）→ {len(sel)}R。")


def arr4(rows):
    inv = np.zeros(ND); pay = np.zeros(ND); n = np.zeros(ND); big = np.zeros(ND); hit = np.zeros(ND)
    for x in rows:
        p = DPOS[x["day"]]
        inv[p] += x["inv"]; pay[p] += x["pay"]; n[p] += 1; big[p] += x["pay"] >= 100_000; hit[p] += x["pay"] > 0
    return dict(inv=inv, pay=pay, n=n, big=big, hit=hit)


a4s, a4a = arr4(sel), arr4(q4)
l_, h_ = delta_ci(a4s, a4a)
P("| 対象 | R | 件/日 | 回収率 | 前半 | 後半 | 的中率 | 10万+/日 | 回収率の 95%CI（日ブートストラップ） |")
P("|---|---|---|---|---|---|---|---|---|")
for lab, a in (("④ 選定（上位20%）", a4s), ("④ の買い方を同じ母集団の全レースへ", a4a)):
    rr = a["pay"][B_ALL].sum(1) / a["inv"][B_ALL].sum(1) * 100
    P(f"| {lab} | {int(a['n'].sum())} | {a['n'].sum()/ND:.1f} | {roi(a):.1f}% | {roi(a,PRE):.1f}% | {roi(a,POST):.1f}% | {a['hit'].sum()/a['n'].sum()*100:.1f}% | {a['big'].sum()/ND:.2f} | [{np.percentile(rr,2.5):.1f}, {np.percentile(rr,97.5):.1f}] |")
P(f"- 選定 − 全レース: {pt(a4s,a4a):+.1f}pt {fmt_ci(l_,h_)}。")
selset = {x["i"] for x in sel}
a_cur = day_arrays(ROWS["A1"], lambda r: r["i"] in selset)
P(f"- 同じ選定レースで ①（現行）が売った行: {int(a_cur['n'].sum())} 行・回収率 {roi(a_cur):.1f}%。④の ROI との差 {roi(a4s)-roi(a_cur):+.1f}pt（レース集合は同じ・売る商品の数が違う）。")
P()

open(H / "h21_tables.md", "w").write("\n".join(OUT))
