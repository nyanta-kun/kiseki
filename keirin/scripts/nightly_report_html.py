#!/usr/bin/env python3
"""夜間レビューを図表つき HTML にする（2026-08-30 新設・ユーザー要望）。

Discord のテキストは長くて読みにくいので、**本体は HTML・Discord はリンクだけ**にする。

## 設計

- **数字は `nightly_review_type_lab` の関数をそのまま呼ぶ。** 集計をここで書き直すと
  「Discord の数字」と「HTML の数字」が静かに食い違う。判定（ゲート・決着クラス・
  決着帯・看板）も向こう経由で kiseki 側の正本に束縛される
- **グラフは inline SVG。** 外部 CDN も JS も使わない。VPS の nginx から素の
  ファイルとして配るので、依存が増えるほど「開かないページ」になりやすい
- **Claude の所見は別ファイル（`<日付>.triage.md`）を読んで埋め込む。**
  無ければその欄を出さない。**HTML の生成が Claude の応答に依存しない**ようにする
  （Mac が寝ていてもチャートつきのページは 00:10 に出る）

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/nightly_report_html.py YYYY-MM-DD --out out.html
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import nightly_review_type_lab as NR  # noqa: E402  （同じ scripts/ 配下）
from src.sold_performance import group_by, summarize  # noqa: E402
from src.type_lab import SELL_PLANS  # noqa: E402

# ── 配色。淡色/暗色の両方で読めるトークンだけを使う ───────────────────
CSS = """
:root{--bg:#fbfbfa;--fg:#1c1b1a;--mut:#6b6864;--line:#e3e0dc;--card:#fff;
--ok:#2f7d4f;--ng:#c0392b;--warn:#b7791f;--accent:#3b6ea5;--accent2:#8b5cf6;
--b1:#3b6ea5;--b2:#4d9de0;--b3:#7fb069;--b4:#e0a458;--b5:#c05746;}
@media (prefers-color-scheme:dark){:root{--bg:#16181a;--fg:#e8e6e3;--mut:#9a9691;
--line:#2c2f33;--card:#1d2023;--ok:#5cb87f;--ng:#e2725f;--warn:#d9a441;
--accent:#7fb3e8;--accent2:#a78bfa;--b1:#7fb3e8;--b2:#5aa9e6;--b3:#94c973;
--b4:#e8b96b;--b5:#e07a63;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:24px 18px 64px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h2 small{font-weight:400;color:var(--mut);font-size:12px;margin-left:8px}
.sub{color:var(--mut);font-size:12.5px;margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:12px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .l{font-size:11.5px;color:var(--mut);letter-spacing:.03em}
.kpi .v{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums;margin-top:2px}
.kpi .n{font-size:11.5px;color:var(--mut);margin-top:2px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;
font-variant-numeric:tabular-nums;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--mut);font-weight:600;font-size:11.5px}
.scroll{overflow-x:auto}
.alert{display:flex;gap:9px;align-items:flex-start;padding:7px 0;
border-bottom:1px solid var(--line);font-size:13.5px}
.alert:last-child{border-bottom:0}
.tag{flex:none;font-size:11px;font-weight:700;padding:1px 7px;border-radius:5px;
color:#fff;margin-top:3px}
.tag.ok{background:var(--ok)}.tag.ng{background:var(--ng)}.tag.info{background:var(--mut)}
.note{color:var(--mut);font-size:12px;margin-top:8px;line-height:1.6}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:var(--mut);margin:6px 0 2px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;
vertical-align:-1px}
.prose{white-space:pre-wrap;font-size:13.5px;line-height:1.75}
/* 🔴 等幅ブロックは折り返さない。桁を揃えて書いてあるので pre-wrap にすると
   1行が2行になって列が読めなくなる（横スクロールに逃がす）。 */
.mono{white-space:pre;font-family:ui-monospace,SFMono-Regular,Menlo,"Courier New",monospace;
font-size:12px;line-height:1.65;margin:0}
.prose strong{font-weight:650}
.warnbox{border-left:3px solid var(--warn);padding-left:12px;color:var(--mut);font-size:12.5px}
"""

BAND_COLORS = ["var(--b1)", "var(--b2)", "var(--b3)", "var(--b4)", "var(--b5)"]


def esc(x: object) -> str:
    return html.escape(str(x))


def esc_md(x: object) -> str:
    """`**強調**` だけを太字にして、あとはエスケープする。

    レポート本文は端末向けに書いてあるので、そのまま埋め込むと `**` が生で出る。
    """
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(str(x)))


# ────────────────────────────── SVG 部品 ──────────────────────────────

def bars(rows: list[tuple[str, float, str]], vmax: float, unit: str = "%",
         w: int = 620, rh: int = 26) -> str:
    """横棒。rows = [(ラベル, 値, 表示文字列)]。**vmax は呼び出し側が決める**
    （グラフごとに勝手に伸縮すると図どうしを見比べられない）。"""
    if not rows:
        return '<p class="note">データなし</p>'
    lw, pad = 74, 92
    h = len(rows) * rh + 8
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img">']
    for i, (lab, v, txt) in enumerate(rows):
        y = i * rh + 4
        bw = max(1.0, (v / vmax) * (w - lw - pad)) if vmax > 0 else 1.0
        out.append(f'<text x="0" y="{y + 14}" font-size="12" fill="var(--mut)">{esc(lab)}</text>')
        out.append(f'<rect x="{lw}" y="{y + 4}" width="{bw:.1f}" height="14" rx="3" '
                   f'fill="var(--accent)" opacity="0.85"/>')
        out.append(f'<text x="{lw + bw + 6:.1f}" y="{y + 15}" font-size="11.5" '
                   f'fill="var(--fg)">{esc(txt)}</text>')
    out.append("</svg>")
    return "".join(out)


def stacked(rows: list[tuple[str, list[tuple[str, int]]]], labels: list[str],
            w: int = 620, rh: int = 26) -> str:
    """帯の内訳を100%積み上げで。rows = [(ラベル, [(帯key, 件数)...])]。"""
    if not rows:
        return '<p class="note">データなし</p>'
    lw = 74
    h = len(rows) * rh + 8
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img">']
    for i, (lab, items) in enumerate(rows):
        y = i * rh + 4
        tot = sum(n for _, n in items) or 1
        x = lw
        span = w - lw - 46
        for key, n in items:
            if n <= 0:
                continue
            bw = span * n / tot
            c = BAND_COLORS[labels.index(key) % len(BAND_COLORS)] if key in labels else "var(--mut)"
            out.append(f'<rect x="{x:.1f}" y="{y + 4}" width="{bw:.1f}" height="14" '
                       f'fill="{c}"><title>{esc(key)} {n}件</title></rect>')
            x += bw
        out.append(f'<text x="0" y="{y + 15}" font-size="12" fill="var(--mut)">{esc(lab)}</text>')
        out.append(f'<text x="{w - 40}" y="{y + 15}" font-size="11.5" '
                   f'fill="var(--mut)">{tot}件</text>')
    out.append("</svg>")
    return "".join(out)


def range_marker(lo: float, mid: float, hi: float, today: float, pct: float,
                 fmt: str = "{:.1%}", w: int = 620) -> str:
    """参照分布の 5〜95% の帯と、今日の位置。

    🔴 **この図が「1日では何も言えない」を一目で伝えるための本体。**
       数字だけを出すと必ず単日で反応されるので、幅を必ず一緒に描く。
    """
    h = 62
    x0, x1 = 40, w - 40
    span = max(hi - lo, 1e-9)

    def px(v: float) -> float:
        return x0 + (max(lo, min(hi, v)) - lo) / span * (x1 - x0)

    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img">']
    out.append(f'<rect x="{x0}" y="20" width="{x1 - x0}" height="12" rx="6" '
               f'fill="var(--accent)" opacity="0.16"/>')
    out.append(f'<line x1="{px(mid):.1f}" y1="16" x2="{px(mid):.1f}" y2="36" '
               f'stroke="var(--mut)" stroke-width="1.5" stroke-dasharray="3 2"/>')
    tx = px(today)
    out.append(f'<circle cx="{tx:.1f}" cy="26" r="6.5" fill="var(--accent2)"/>')
    out.append(f'<text x="{tx:.1f}" y="12" font-size="12" font-weight="650" '
               f'text-anchor="middle" fill="var(--accent2)">'
               f'今日 {esc(fmt.format(today))}</text>')
    out.append(f'<text x="{x0}" y="50" font-size="11" fill="var(--mut)">'
               f'5% {esc(fmt.format(lo))}</text>')
    out.append(f'<text x="{px(mid):.1f}" y="50" font-size="11" text-anchor="middle" '
               f'fill="var(--mut)">中央 {esc(fmt.format(mid))}</text>')
    out.append(f'<text x="{x1}" y="50" font-size="11" text-anchor="end" '
               f'fill="var(--mut)">95% {esc(fmt.format(hi))}</text>')
    out.append("</svg>")
    return "".join(out)


# ────────────────────────────── 本体 ──────────────────────────────

def build(day: str, n_boot: int = 2000) -> str:
    sold, n_skipped, subs = NR._sold(day)
    live = NR._live_rows(day)
    pool = NR._baseline_pool()
    base = NR._band_baseline()
    titles: dict[str, str] = {}
    for x in subs:
        if x["deleted_at"] is None and x.get("title"):
            titles.setdefault(str(x["rank_key"]), str(x["title"]).split("｜")[0])

    total = summarize(sold, n_no_detail=n_skipped)
    by_plan = group_by(sold, "rank_key")
    wd = "月火水木金土日"[date.fromisoformat(day).weekday()]
    P: list[str] = []
    A = P.append

    A(f"<h1>型ラボ 夜間レビュー　{esc(day)}（{wd}）</h1>")
    A(f'<p class="sub">生成 {datetime.now():%Y-%m-%d %H:%M}　／　'
      f'売った商品＝netkeirin_submissions + bet_detail　／　'
      f'前向き確認の起点 {esc(NR.REVIEW_EPOCH)}</p>')

    # KPI
    A('<div class="kpis">')
    for lab, val, note in (
        ("売った商品", f"{total.n_races}件", f"投資 {total.bet:,}円"),
        ("表示的中", f"{(total.net_hit_rate or 0):.1%}",
         f"素の的中 {(total.hit_rate or 0):.1%}"),
        ("ROI", f"{(total.roi or 0):.1%}", f"払戻 {total.payout:,}円"),
        ("的中時の中央払戻", f"{(total.median_payout or 0):,}円",
         f"最高 {max(total.payouts) if total.payouts else 0:,}円"),
    ):
        A(f'<div class="kpi"><div class="l">{esc(lab)}</div>'
          f'<div class="v">{esc(val)}</div><div class="n">{esc(note)}</div></div>')
    A("</div>")

    # §1
    A('<h2>§1 異常検知 <small>単日で黒白がつく唯一の層。ここだけは今日直す</small></h2>')
    alerts, n_ng = NR.section_alerts(day, sold, n_skipped, subs, live)
    A('<div class="card">')
    for line in alerts:
        t = line.strip()
        if t.startswith("[OK]"):
            A(f'<div class="alert"><span class="tag ok">OK</span>'
              f'<span>{esc(t[4:].strip())}</span></div>')
        elif t.startswith("[NG]"):
            A(f'<div class="alert"><span class="tag ng">NG</span>'
              f'<span>{esc(t[4:].strip())}</span></div>')
        elif t.startswith("----"):
            A(f'<div class="alert"><span class="tag info">情報</span>'
              f'<span>{esc(t[4:].strip())}</span></div>')
    A("</div>")

    # §2
    A('<h2>§2 当日成績 <small>単日では判断しない — 参照分布の中のどこか、だけを見る</small></h2>')
    mix = {k: s.n_races for k, s in by_plan.items()}
    boot = NR._bootstrap(pool, mix, n_boot, seed=int(day.replace("-", "")))
    if boot and total.roi is not None:
        rois = sorted(b[0] for b in boot)
        hits = sorted(b[1] for b in boot)
        A('<div class="card">')
        A(f'<div class="l" style="font-size:12px;color:var(--mut)">ROI</div>')
        A(range_marker(NR._q(rois, .05), NR._q(rois, .50), NR._q(rois, .95),
                       total.roi, NR._pct(rois, total.roi)))
        A(f'<div class="l" style="font-size:12px;color:var(--mut)">表示的中</div>')
        A(range_marker(NR._q(hits, .05), NR._q(hits, .50), NR._q(hits, .95),
                       total.net_hit_rate or 0, NR._pct(hits, total.net_hit_rate or 0)))
        A(f'<p class="note">同じプラン構成・同じ件数を {len(boot):,}回 復元抽出した分布'
          f'（{esc(NR.BASELINE_WINDOW[0])}〜{esc(NR.BASELINE_WINDOW[1])} のペーパー行）。'
          f'今日は ROI が <b>{NR._pct(rois, total.roi):.0f}%点</b>・'
          f'表示的中が <b>{NR._pct(hits, total.net_hit_rate or 0):.0f}%点</b>。'
          f'<b>この帯の内側なら今日の数字は情報を持たない。</b></p>')
        A("</div>")

    A('<div class="card"><div class="scroll"><table><tr>'
      "<th>プラン</th><th>タイトル</th><th>R数</th><th>表示的中</th><th>ROI</th>"
      "<th>投資</th><th>払戻</th><th>的中中央</th></tr>")
    for k, s in by_plan.items():
        A(f"<tr><td>{esc(k)}</td><td>{esc(titles.get(k, '—'))}</td>"
          f"<td>{s.n_races}</td><td>{(s.net_hit_rate or 0):.1%}</td>"
          f"<td>{(s.roi or 0):.1%}</td><td>{s.bet:,}</td><td>{s.payout:,}</td>"
          f"<td>{(s.median_payout or 0):,}</td></tr>")
    A("</table></div>")
    A('<div class="legend" style="margin-top:10px">プラン別 表示的中</div>')
    A(bars([(k, (s.net_hit_rate or 0) * 100, f"{(s.net_hit_rate or 0):.0%}（{s.n_races}件）")
            for k, s in by_plan.items()], vmax=50.0))
    A("</div>")

    # §3
    A('<h2>§3 外れの分解 <small>台帳へ積む。反実仮想は出さない</small></h2>')
    brk_lines, brk = NR.section_breakdown(sold, live)
    counts = brk["counts"]
    order = [c["key"] for c in NR._OUTCOME.FINISH_CLASSES]
    lab = {c["key"]: c["label"] for c in NR._OUTCOME.FINISH_CLASSES}
    n = sum(counts.values())
    A('<div class="card">')
    if n:
        A(stacked([("決着", [(k, counts.get(k, 0)) for k in order])], order))
        A('<div class="legend">'
          + "".join(f'<span><i style="background:{BAND_COLORS[i % 5]}"></i>'
                    f'{esc(lab[k])} {counts.get(k, 0)}件</span>'
                    for i, k in enumerate(order)) + "</div>")
    for line in brk_lines:
        if line.strip().startswith("分解"):
            A(f'<p class="note" style="font-size:13px;color:var(--fg)">'
              f'{esc(line.strip())}</p>')
    A('<p class="note">軸2車そろい（モデル側）× 相手カバー（買い目側）に分けて見る。'
      'どちらが効いているかで打ち手が変わる。<b>1日ぶんでは判断しない。</b></p>')
    A("</div>")

    # §3.5 / §3.6
    A('<h2>§3.5 狙ったオッズ帯で決着したか <small>帯が合って外れ＝買い目側／帯自体が外れ＝型判定側</small></h2>')
    bkeys = [b["key"] for b in NR._OUTCOME.PAYOUT_BANDS]
    blab = {b["key"]: b["label"] for b in NR._OUTCOME.PAYOUT_BANDS}
    A('<div class="card">')
    A('<div class="legend">' + "".join(
        f'<span><i style="background:{BAND_COLORS[i % 5]}"></i>{esc(blab[k])}</span>'
        for i, k in enumerate(bkeys)) + "</div>")
    rows_land = []
    for plan in sorted(by_plan):
        items = []
        for r in sold:
            if r.rank_key != plan:
                continue
            d = next((x for x in live if str(x["race_key"]) == r.race_key
                      and str(x["plan_key"]) == plan), None)
            if d is None or d.get("win_tf_odds") is None:
                continue
            b = NR._OUTCOME.payout_band(d["win_tf_odds"])
            if b:
                items.append(b)
        if items:
            rows_land.append((plan, [(k, items.count(k)) for k in bkeys]))
    A(stacked(rows_land, bkeys))
    for line in NR.section_landing(sold, live, base, titles):
        if line.strip().startswith("狙い帯（"):
            A(f'<p class="note" style="font-size:13px;color:var(--fg)">{esc(line.strip())}</p>')
    A('<p class="note">狙い帯は参照（20か月）でそのプランが最も多く落ちる帯（±1帯を許容）。</p>')
    A("</div>")

    A('<h2>§3.6 買った買い目は狙った帯にあったか <small>結果と違い、これは自分で決めた量</small></h2>')
    A('<div class="card"><div class="scroll"><pre class="mono">'
      + esc_md("\n".join(NR.section_bet_band(sold, live, titles))) + "</pre></div></div>")

    # §4 / §5 / §6
    for title, small, lines in (
        ("§4 軸信頼ゲートの答え合わせ", "累積で見る（前向き実地検証）",
         NR.section_gate(day, live)),
        ("§5 「自信あり」フラグの精度", "同日内の無作為対照と比べる",
         NR.section_confident(day, min(n_boot, 800), int(day.replace("-", "")))),
        ("§6 台帳と発火条件", f"{NR.ESCALATE_MIN_N}件たまった軸だけ検証候補へ",
         NR.section_escalate(pool)),
    ):
        A(f"<h2>{esc(title)} <small>{esc(small)}</small></h2>")
        A('<div class="card"><div class="scroll"><pre class="mono">'
          + esc_md("\n".join(lines)) + "</pre></div></div>")

    return "".join(P), n_ng, total


def render(day: str, body: str, triage: str | None) -> str:
    head = (f'<meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow">'
            f'<title>型ラボ 夜間レビュー {esc(day)}</title><style>{CSS}</style>')
    tri = ""
    if triage:
        tri = ('<h2>課題の取捨 <small>Claude による仕分け</small></h2>'
               '<div class="card"><div class="prose">' + esc_md(triage) + "</div></div>")
    return (f"<!doctype html><html lang=\"ja\"><head>{head}</head><body>"
            f'<div class="wrap">{body}{tri}'
            f'<p class="note" style="margin-top:32px">'
            f'このページは自動生成です（`keirin/scripts/nightly_report_html.py`）。'
            f'数字の出どころは同日の Markdown レポートと同一の関数。</p>'
            f"</div></body></html>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?", default=date.today().isoformat())
    ap.add_argument("--out", default="")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--triage", default="", help="Claude の所見（テキストファイル）")
    ap.add_argument("--summary-out", default="",
                    help="Discord へ出す1行要約の書き出し先")
    args = ap.parse_args()

    body, n_ng, total = build(args.day, args.boot)
    triage = ""
    if args.triage and Path(args.triage).exists():
        triage = Path(args.triage).read_text(encoding="utf-8").strip()
    out = Path(args.out) if args.out else (
        REPO / "data" / "analysis" / "nightly" / f"{args.day}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(args.day, body, triage or None), encoding="utf-8")
    print(f"[nightly_html] 保存: {out}  （異常 {n_ng}件）")

    if args.summary_out:
        # 🔴 Discord へ出すのは**この1行とリンクだけ**。本文は HTML 側にある。
        head = f"🔴 異常 {n_ng}件" if n_ng else "🟢 異常なし"
        Path(args.summary_out).write_text(
            f"{head} ／ 売った商品 {total.n_races}件 ・ 表示的中 "
            f"{(total.net_hit_rate or 0):.1%} ・ ROI {(total.roi or 0):.1%} "
            f"・ 払戻 {total.payout:,}円", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
