"use client";

/**
 * 型ラボ 表示確認ページ（2026-08-27 新設 / 同日レスポンシブ化）
 *
 * 🔴 **既存の /keirin（一覧）や /keirin/stats とはデータが別**。
 *    見ているのは `keirin.type_lab_picks` だけで、picks_history も
 *    netkeirin_submissions も「現行との比較」以外には使わない。
 *    既存商品の全面置き換えを想定した設計を、混ぜずに検証するためのページ。
 *
 * ## レイアウトの方針（2026-08-27）
 * スマホでも確認するので **モバイル優先**。指標が10列近くあるため、
 * `sm` 未満では表をやめて**1プラン=1カード**にし、主要指標を 3列グリッドで出す。
 * `sm` 以上でだけ表に切り替える（横スクロール前提の表はスマホで読めない）。
 *
 * 設計と実測: keirin/docs/type_lab/SUMMARY.md
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, FlaskConical, RefreshCw } from "lucide-react";
import {
  fetchKeirinTypeLab, type TypeLabComparisonRow, type TypeLabPick,
  type TypeLabResponse, type TypeLabSummary,
} from "@/lib/api";

const TYPE_NAME: Record<string, string> = {
  A: "鉄板", B: "堅い・中", C: "堅いが崩れ筋",
  D: "混戦・軸あり", E: "混戦・中", F: "大混戦",
};
const PLAN_NOTE: Record<string, string> = {
  A_hit: "三連単 1着=軸1・2着=軸2 固定 → 3着へ3車（表示的中）",
  A_pay: "三連単 1着=軸1固定・2着2車 → 3着流し（払戻）",
  B_hit: "三連単 確率上位を想定平均払戻3万円の床まで",
  C_hit: "三連単 予測20倍以上から確率上位12点",
  D_hit: "三連複 軸2車＋相手4点（最人気の相手を外す）",
  E_hit: "三連単 予測30倍以上から確率上位14点",
  F_hit: "三連単 軸2車＋相手2車の6順列すべて（12点）",
  F_pay: "三連単 1着=軸1固定・2着2車 → 3着流し（一撃）",
};

const yen = (n: number | null | undefined) =>
  n == null ? "—" : `${Math.round(n).toLocaleString()}円`;
/** スマホの幅では万円へ丸める（桁が多いと折り返す）。 */
const yenShort = (n: number | null | undefined) =>
  n == null ? "—" : n >= 10000 ? `${(n / 10000).toFixed(1)}万` : `${Math.round(n).toLocaleString()}`;
const pct = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(1)}%`);

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export default function TypeLabPage() {
  const [mode, setMode] = useState<"live" | "paper">("live");
  const [dateFrom, setDateFrom] = useState(isoDaysAgo(6));
  const [dateTo, setDateTo] = useState(isoDaysAgo(0));
  const [data, setData] = useState<TypeLabResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [planFilter, setPlanFilter] = useState<string>("");
  // 競輪場フィルタ。**サーバー側で絞る**ので、まとめ・比較もその場だけの数字になる。
  const [venue, setVenue] = useState<string>("");
  // 選択肢は絞り込み前の一覧を保持する（絞ると自分の場しか返らないため）
  const [venueOptions, setVenueOptions] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetchKeirinTypeLab({ mode, dateFrom, dateTo, venue, limit: 1000 });
      setData(r);
      // 絞っていないときの一覧を覚えておく（絞ると1場しか返らない）
      if (!venue) setVenueOptions(r.venues);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [mode, dateFrom, dateTo, venue]);

  useEffect(() => { void load(); }, [load]);

  const picks = useMemo(
    () => (data?.picks ?? []).filter((p) => !planFilter || p.plan_key === planFilter),
    [data, planFilter],
  );

  return (
    <main className="w-full px-3 py-3 sm:mx-auto sm:max-w-6xl sm:px-4 sm:py-4 space-y-3 pb-16">
      <header className="flex items-center gap-2">
        <Link href="/keirin" className="text-slate-500 hover:text-slate-800" aria-label="戻る">
          <ArrowLeft size={18} />
        </Link>
        <FlaskConical size={18} className="text-indigo-600" />
        <h1 className="text-base font-bold sm:text-lg">型ラボ</h1>
        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800 sm:text-xs">
          検証用・入稿しません
        </span>
      </header>

      <details className="rounded border bg-white text-xs text-slate-500">
        <summary className="cursor-pointer px-3 py-2 font-semibold text-slate-600">
          このページは何か
        </summary>
        <p className="px-3 pb-3 leading-relaxed">
          レースを 6 型（A 鉄板 / B 堅い・中 / C 堅いが崩れ筋 / D 混戦・軸あり /
          E 混戦・中 / F 大混戦）に分け、型ごとに決めた買い方で組んだ検証用の買い目です。
          <b>ペーパー</b>は過去を vintage 予測で、<b>実地</b>は当日を本番モデルで組んだもの。
          どちらも <code>keirin.type_lab_picks</code> にしか書かれず、一覧・統計・入稿には出ません。
        </p>
        <p className="px-3 pb-3 leading-relaxed">
          プラン名の <b>_hit</b> と <b>_pay</b> は<b>同じ型に対する狙いの違い</b>です。
          <b>_hit</b> は当たる回数を取りにいく形（点数を広げる・低い帯）、
          <b>_pay</b> は1点あたりの購入額を増やして払戻を取りにいく形（点数を絞る）。
          ROI はどちらも控除率の壁の周辺で変わらず、
          <b>的中率と払戻の大きさを交換しているだけ</b>です。
          例: 型A はペーパーで <b>A_hit 表示的中 29.7% / 払戻中央 1.9万円</b> ↔
          <b>A_pay 19.1% / 2.8万円</b>（2倍以上の的中件数は 1.62件/日 で同じ）。
          型F は <b>F_hit 24.2% / 2.4万円</b> ↔ <b>F_pay 8.5% / 5.9万円</b>。
        </p>
      </details>

      {/* ── 条件 ── */}
      <div className="space-y-2 rounded border bg-white p-3">
        <div className="flex items-center gap-2">
          <select
            className="min-w-0 flex-1 rounded border px-2 py-1.5 text-sm sm:flex-none"
            value={mode} onChange={(e) => setMode(e.target.value as "live" | "paper")}
          >
            <option value="live">実地（当日・本番モデル）</option>
            <option value="paper">ペーパー（過去・vintage）</option>
          </select>
          <button
            onClick={() => void load()} disabled={loading}
            className="flex shrink-0 items-center gap-1 rounded bg-indigo-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            <span className="hidden sm:inline">再取得</span>
          </button>
        </div>
        <div className="flex items-center gap-2">
          <input type="date" className="min-w-0 flex-1 rounded border px-2 py-1.5 text-sm"
                 value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          <span className="shrink-0 text-slate-400">〜</span>
          <input type="date" className="min-w-0 flex-1 rounded border px-2 py-1.5 text-sm"
                 value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <select
          className="w-full rounded border px-2 py-1.5 text-sm"
          value={venue} onChange={(e) => setVenue(e.target.value)}
        >
          <option value="">すべての競輪場{venueOptions.length ? `（${venueOptions.length}場）` : ""}</option>
          {(venueOptions.length ? venueOptions : (data?.venues ?? [])).map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        {data?.rule_versions?.length ? (
          <div className="text-[10px] text-slate-400">rule_version: {data.rule_versions.join(", ")}</div>
        ) : null}
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">{err}</div>
      )}

      {/* ── プラン別サマリ ── */}
      <Section title="プラン別のまとめ"
               note={venue ? `${venue} に絞り込み中` : undefined}>
        {/* モバイル: 1プラン=1カード */}
        <div className="space-y-2 sm:hidden">
          {(data?.summaries ?? []).map((s) => (
            <SummaryCard key={s.plan_key} s={s} active={planFilter === s.plan_key}
                         onClick={() => setPlanFilter(planFilter === s.plan_key ? "" : s.plan_key)} />
          ))}
          {!loading && !(data?.summaries ?? []).length && <Empty />}
        </div>
        {/* sm 以上: 表 */}
        <div className="hidden overflow-x-auto sm:block">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-600">
              <tr>
                <th className="p-2 text-left">プラン</th>
                <th className="p-2 text-left">型</th>
                <th className="p-2 text-right">件/日</th>
                <th className="p-2 text-right">採点</th>
                <th className="p-2 text-right">表示的中</th>
                <th className="p-2 text-right">ガミ</th>
                <th className="p-2 text-right">払戻中央</th>
                <th className="p-2 text-right">想定平均</th>
                <th className="p-2 text-right">2倍+/日</th>
                <th className="p-2 text-right">10万+/日</th>
                <th className="p-2 text-right">ROI</th>
              </tr>
            </thead>
            <tbody>
              {(data?.summaries ?? []).map((s) => (
                <tr key={s.plan_key}
                    className={`cursor-pointer border-t hover:bg-indigo-50 ${planFilter === s.plan_key ? "bg-indigo-50" : ""}`}
                    onClick={() => setPlanFilter(planFilter === s.plan_key ? "" : s.plan_key)}>
                  <td className="p-2">
                    <div className="font-mono font-semibold">{s.plan_key}</div>
                    <div className="text-xs text-slate-500">{PLAN_NOTE[s.plan_key] ?? ""}</div>
                  </td>
                  <td className="p-2 whitespace-nowrap">{s.type_label} {TYPE_NAME[s.type_label] ?? ""}</td>
                  <td className="p-2 text-right">{s.per_day.toFixed(2)}</td>
                  <td className="p-2 text-right">{s.n_settled}/{s.n}</td>
                  <td className="p-2 text-right font-semibold">{pct(s.shown_hit_rate)}</td>
                  <td className="p-2 text-right">{pct(s.gami_rate)}</td>
                  <td className="p-2 text-right font-semibold">{yen(s.median_payout)}</td>
                  <td className="p-2 text-right text-slate-500">{yen(s.median_pred_mean)}</td>
                  <td className="p-2 text-right">{s.two_plus_per_day.toFixed(2)}</td>
                  <td className="p-2 text-right">{s.big_per_day.toFixed(3)}</td>
                  <td className="p-2 text-right text-slate-500">{s.roi.toFixed(1)}%</td>
                </tr>
              ))}
              {!loading && !(data?.summaries ?? []).length && (
                <tr><td colSpan={11} className="p-6 text-center text-slate-400">データがありません</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      <p className="text-[11px] leading-relaxed text-slate-400">
        ⚠️ <b>ROI で採否を決めないこと</b>（この層は ±2.5pt に収めるのに約15.6年）。
        判断指標は 件/日・表示的中（ガミ除く）・払戻中央・2倍以上の的中件/日・ガミ率。
        {planFilter
          ? <> 絞り込み中: <b>{planFilter}</b>（もう一度押すと解除）</>
          : <> プランを押すとその買い目だけに絞れます。</>}
      </p>

      {/* ── 現行推奨との比較 ── */}
      <Section title="現行推奨との比較"
               note="両方に採点済みの記録がある同じレースだけで並べています">
        <div className="space-y-2 sm:hidden">
          {(data?.comparison ?? []).map((c) => <CompareCard key={c.plan_key} c={c} />)}
          {!loading && !(data?.comparison ?? []).length && <Empty text="比較できる記録がありません" />}
        </div>
        <div className="hidden overflow-x-auto sm:block">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-600">
              <tr>
                <th className="p-2 text-left">プラン</th>
                <th className="p-2 text-right">対象R</th>
                <th className="p-2 text-right">表示的中 ラボ / 現行</th>
                <th className="p-2 text-right">払戻中央 ラボ / 現行</th>
                <th className="p-2 text-right">2倍+/日 ラボ / 現行</th>
                <th className="p-2 text-right">ROI ラボ / 現行</th>
              </tr>
            </thead>
            <tbody>
              {(data?.comparison ?? []).map((c) => (
                <tr key={c.plan_key} className="border-t">
                  <td className="p-2 font-mono">{c.plan_key}</td>
                  <td className="p-2 text-right whitespace-nowrap">{c.n_races}（{c.n_days}日）</td>
                  <td className="p-2 text-right whitespace-nowrap">
                    <Win a={c.lab_shown_hit} b={c.cur_shown_hit} fmt={pct} />
                  </td>
                  <td className="p-2 text-right whitespace-nowrap">
                    <Win a={c.lab_median_payout} b={c.cur_median_payout} fmt={yen} />
                  </td>
                  <td className="p-2 text-right whitespace-nowrap">
                    <Win a={c.lab_two_per_day} b={c.cur_two_per_day} fmt={(v) => v.toFixed(2)} />
                  </td>
                  <td className="p-2 text-right whitespace-nowrap text-slate-500">
                    {c.lab_roi.toFixed(1)}% / {c.cur_roi.toFixed(1)}%
                  </td>
                </tr>
              ))}
              {!loading && !(data?.comparison ?? []).length && (
                <tr><td colSpan={6} className="p-4 text-center text-slate-400">
                  比較できる記録がありません
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      <p className="text-[11px] leading-relaxed text-slate-400">
        🔴 現行側は <code>picks_history</code>（<b>ランクの候補</b>）です。実売との不一致が
        18% あるため「売った商品」そのものではありませんが、型ラボも「設計が何を買うか」なので
        <b>設計どうしの比較としては同じ土俵</b>です。実際に入稿されたかは各カードの
        「入稿: …」で確認できます。
      </p>

      {/* ── 買い目一覧 ── */}
      <div className="space-y-2">
        {picks.map((p) => <PickCard key={`${p.race_key}-${p.plan_key}`} p={p} />)}
        {!loading && !picks.length && <Empty text="買い目がありません" />}
      </div>
    </main>
  );
}

function Section({ title, note, children }: {
  title: string; note?: string; children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded border bg-white">
      <div className="border-b bg-slate-50 px-3 py-2 text-sm font-semibold">
        {title}
        {note && <span className="ml-2 text-[11px] font-normal text-slate-500">{note}</span>}
      </div>
      <div className="p-2 sm:p-0">{children}</div>
    </section>
  );
}

function Empty({ text = "データがありません" }: { text?: string }) {
  return <div className="p-6 text-center text-sm text-slate-400">{text}</div>;
}

/** ラボ / 現行 を並べ、良いほうを強調する。 */
function Win({ a, b, fmt }: { a: number; b: number; fmt: (v: number) => string }) {
  return (
    <>
      <b className={a >= b ? "text-emerald-700" : "text-slate-700"}>{fmt(a)}</b>
      <span className="text-slate-400"> / {fmt(b)}</span>
    </>
  );
}

function Metric({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className={`truncate text-sm ${strong ? "font-semibold" : ""}`}>{value}</div>
    </div>
  );
}

function SummaryCard({ s, active, onClick }: {
  s: TypeLabSummary; active: boolean; onClick: () => void;
}) {
  return (
    <button onClick={onClick}
            className={`w-full rounded border p-2 text-left ${active ? "border-indigo-400 bg-indigo-50" : "bg-white"}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm font-semibold">{s.plan_key}</span>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px]">
          型{s.type_label} {TYPE_NAME[s.type_label] ?? ""}
        </span>
        <span className="ml-auto text-[10px] text-slate-400">{s.n_settled}/{s.n} 採点</span>
      </div>
      <div className="mt-0.5 line-clamp-2 text-[10px] text-slate-500">{PLAN_NOTE[s.plan_key] ?? ""}</div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <Metric label="件/日" value={s.per_day.toFixed(2)} />
        <Metric label="表示的中" value={pct(s.shown_hit_rate)} strong />
        <Metric label="ガミ" value={pct(s.gami_rate)} />
        <Metric label="払戻中央" value={yenShort(s.median_payout)} strong />
        <Metric label="2倍+/日" value={s.two_plus_per_day.toFixed(2)} />
        <Metric label="10万+/日" value={s.big_per_day.toFixed(3)} />
      </div>
    </button>
  );
}

function CompareCard({ c }: { c: TypeLabComparisonRow }) {
  const rows: [string, string, string, boolean][] = [
    ["表示的中", pct(c.lab_shown_hit), pct(c.cur_shown_hit), c.lab_shown_hit >= c.cur_shown_hit],
    ["払戻中央", yenShort(c.lab_median_payout), yenShort(c.cur_median_payout),
      c.lab_median_payout >= c.cur_median_payout],
    ["2倍+/日", c.lab_two_per_day.toFixed(2), c.cur_two_per_day.toFixed(2),
      c.lab_two_per_day >= c.cur_two_per_day],
    ["ROI", `${c.lab_roi.toFixed(1)}%`, `${c.cur_roi.toFixed(1)}%`, false],
  ];
  return (
    <div className="rounded border bg-white p-2">
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm font-semibold">{c.plan_key}</span>
        <span className="ml-auto text-[10px] text-slate-400">{c.n_races}R / {c.n_days}日</span>
      </div>
      <div className="mt-1 grid grid-cols-[auto_1fr_1fr] gap-x-2 gap-y-1 text-xs">
        <span className="text-[10px] text-slate-400" />
        <span className="text-right text-[10px] text-slate-400">ラボ</span>
        <span className="text-right text-[10px] text-slate-400">現行</span>
        {rows.map(([label, a, b, win]) => (
          <Fragmented key={label} label={label} a={a} b={b} win={win} />
        ))}
      </div>
    </div>
  );
}

function Fragmented({ label, a, b, win }: {
  label: string; a: string; b: string; win: boolean;
}) {
  return (
    <>
      <span className="text-slate-500">{label}</span>
      <span className={`text-right ${win ? "font-semibold text-emerald-700" : ""}`}>{a}</span>
      <span className="text-right text-slate-400">{b}</span>
    </>
  );
}

function PickCard({ p }: { p: TypeLabPick }) {
  const settled = p.settled;
  const tone = !settled ? "border-slate-200"
    : p.hit ? ((p.payout ?? 0) >= p.budget ? "border-emerald-400" : "border-amber-400")
    : "border-slate-200 opacity-70";
  return (
    <div className={`rounded border-2 bg-white p-2 sm:p-3 ${tone}`}>
      {/* 1行目: レース */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs sm:text-sm">
        <span className="text-slate-500">{p.race_date}</span>
        <span className="font-semibold">{p.venue_name ?? "—"} {p.race_no ?? "?"}R</span>
        <span className="text-[10px] text-slate-500 sm:text-xs">{p.race_type ?? ""}</span>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px]">
          型{p.type_label}{TYPE_NAME[p.type_label] ? ` ${TYPE_NAME[p.type_label]}` : ""}
        </span>
        <span className="rounded bg-indigo-100 px-1.5 py-0.5 font-mono text-[10px] text-indigo-800">
          {p.plan_key}
        </span>
      </div>
      {/* 2行目: 商品と結果（モバイルでは折り返す） */}
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
        <span>{p.bet_type === "trio" ? "三連複" : "三連単"} {p.n_legs}点</span>
        <span>想定平均 {yen(p.pred_mean_payout)}</span>
        <span className="ml-auto">
          {settled
            ? (p.hit
                ? <b className={(p.payout ?? 0) >= p.budget ? "text-emerald-700" : "text-amber-700"}>
                    的中 {yen(p.payout)}（{p.win_combo} / {p.final_odds?.toFixed(1)}倍）
                    {(p.payout ?? 0) < p.budget ? " ※ガミ" : ""}
                  </b>
                : <span className="text-slate-400">不的中（{p.win_combo}）</span>)
            : <span className="text-slate-400">未確定</span>}
        </span>
      </div>
      {p.current && (
        <div className="mt-1.5 rounded bg-slate-50 px-2 py-1 text-[10px] leading-relaxed text-slate-600">
          <span className="font-semibold">現行</span>
          <span className="ml-1.5 font-mono">{p.current.rank.replace("RANK_", "")}</span>
          {p.current.n_combos ? <span className="ml-1 text-slate-400">{p.current.n_combos}点</span> : null}
          {p.current.settled
            ? (p.current.hit
                ? <span className="ml-1.5 text-emerald-700">的中 {yen(p.current.payout)}</span>
                : <span className="ml-1.5 text-slate-400">不的中</span>)
            : <span className="ml-1.5 text-slate-400">未採点</span>}
          {p.current.sold_rank_key
            ? <span className="ml-1.5 rounded bg-indigo-100 px-1 text-indigo-700">入稿 {p.current.sold_rank_key}</span>
            : <span className="ml-1.5 text-slate-400">（入稿なし）</span>}
          <div className="mt-0.5 break-all font-mono text-slate-500">{p.current.pred_combo ?? "—"}</div>
        </div>
      )}
      {/* 買い目 */}
      <div className="mt-1.5 flex flex-wrap gap-1">
        {p.legs.map((l) => (
          <span key={l.combo}
                className={`rounded px-1.5 py-0.5 font-mono text-[10px] leading-tight sm:text-xs ${
                  settled && p.win_combo === l.combo
                    ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-700"}`}>
            {l.combo}
            <span className="ml-1 opacity-70">
              {(l.stake / 100).toFixed(0)}00円/{l.pred_odds.toFixed(1)}倍
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
