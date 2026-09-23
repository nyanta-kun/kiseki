"use client";

/**
 * 逃げ先頭ライン（`L_lead`）の検証ページ（2026-09-24 新設・**検証中・入稿しない**）
 *
 * 「もし売っていたら」を、**同じレースで実際に出した商品（買わなくなるランク）**と
 * 並べて見る。置き換えの定義と会計は `backend/src/services/keirin_line_lead_verify.py`。
 *
 * - 新ランク: 得点1位でないラインの逃げ先頭（得点5位以下）→番手→3着・1レース1万円均等
 * - 買わなくなるランク: 新ランクの行があるレースで netkeirin へ出した商品
 * - 現行 ↔ 置き換え: 売った全商品 ↔ 新ランクのレースだけ置き換えた場合
 *
 * 🔴 **1日・1か月の数字で判断しない。** 的中は 1日 0.7本前後で、大当たり数本で
 *    回収率が大きく動く（2026-09 は1本で +20pt）。「大当たり3本を除く回収率」を必ず併せて見る。
 * 設計と実測: keirin/docs/type_lab/line_lead_2026_09_24.md
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowLeft, FlaskConical, RefreshCw } from "lucide-react";
import {
  fetchLineLead, hhmmJst, profit, roiTone,
  type LineLeadDay, type LineLeadRace, type LineLeadResponse, type LineLeadTally,
} from "@/lib/keirinLineLead";

const yen = (n: number | null | undefined) =>
  n == null ? "—" : `${Math.round(n).toLocaleString()}円`;
/** スマホの幅では万円へ丸める（桁が多いと折り返す）。 */
const man = (n: number | null | undefined) =>
  n == null ? "—" : Math.abs(n) >= 10000 ? `${(n / 10000).toFixed(1)}万` : `${Math.round(n).toLocaleString()}`;
const pct = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(1)}%`);
const signed = (n: number) => `${n >= 0 ? "+" : ""}${man(n)}`;

/** n 日前の ISO 日付（**端末のローカル時刻**・`/keirin/type-lab` と同じ理由で UTC を使わない）。 */
function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
const mmdd = (iso: string) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}`;

const PRESETS: { label: string; days: number }[] = [
  { label: "7日", days: 6 }, { label: "30日", days: 29 }, { label: "90日", days: 89 },
];

export default function LineLeadPage() {
  const [dateFrom, setDateFrom] = useState(isoDaysAgo(6));
  const [dateTo, setDateTo] = useState(isoDaysAgo(0));
  const [data, setData] = useState<LineLeadResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [day, setDay] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setData(await fetchLineLead({ dateFrom, dateTo }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo]);

  useEffect(() => { void load(); }, [load]);

  const races = useMemo(
    () => (data?.races ?? []).filter((r) => !day || r.race_date === day),
    [data, day],
  );
  const s = data?.summary;

  return (
    <main className="w-full px-3 py-3 sm:mx-auto sm:max-w-6xl sm:px-4 sm:py-4 space-y-3 pb-16">
      <header className="flex items-center gap-2">
        <Link href="/keirin/type-lab" aria-label="型ラボへ戻る"
          className="text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100">
          <ArrowLeft size={18} />
        </Link>
        <FlaskConical size={18} className="text-indigo-600 dark:text-indigo-300" />
        <h1 className="text-base font-bold text-gray-900 dark:text-white sm:text-lg">逃げ先頭ラインの検証</h1>
        <span className="rounded bg-amber-100 dark:bg-amber-900 px-1.5 py-0.5 text-[10px] text-amber-800 dark:text-amber-200 sm:text-xs">
          検証中・入稿しません
        </span>
      </header>

      <details className="rounded border border-gray-200 bg-white text-gray-800 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 text-xs">
        <summary className="cursor-pointer px-3 py-2 font-semibold text-gray-700 dark:text-gray-300">
          このページは何か
        </summary>
        <div className="px-3 pb-3 leading-relaxed space-y-1">
          <p>
            <b>新ランク</b>: 得点1位の居ないラインで、先頭が「逃」かつ先頭の競走得点がレース内5位以下のラインを選び、
            <b>先頭→番手→3着</b>を買う（3着は第三のラインの番手以降・指数最下位・得点最下位を外す）。
            1レース1万円を均等に割る。得点1位のラインが4車のレースは対象外。
          </p>
          <p>
            <b>買わなくなるランク</b>: 新ランクのレースで実際に netkeirin へ出した商品。
            <b>現行 ↔ 置き換え</b>は、売った全商品と「新ランクのレースだけ新ランクに置き換え（売っていなければ足す）」の比較。
          </p>
          <p className="text-amber-700 dark:text-amber-300">
            的中は1日1本前後で、少数の大当たりで回収率が大きく動きます。「大当たり3本を除く回収率」を必ず併せて見てください。
          </p>
        </div>
      </details>

      <section className="flex flex-wrap items-end gap-2 text-xs">
        <label className="flex flex-col gap-0.5 text-gray-600 dark:text-gray-400">
          開始
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
            className="rounded border border-gray-300 bg-white px-1.5 py-1 text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" />
        </label>
        <label className="flex flex-col gap-0.5 text-gray-600 dark:text-gray-400">
          終了
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
            className="rounded border border-gray-300 bg-white px-1.5 py-1 text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" />
        </label>
        {PRESETS.map((p) => (
          <button key={p.label} type="button"
            onClick={() => { setDateFrom(isoDaysAgo(p.days)); setDateTo(isoDaysAgo(0)); setDay(""); }}
            className="rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
            {p.label}
          </button>
        ))}
        <button type="button" onClick={() => void load()} aria-label="再読み込み"
          className="rounded border border-gray-300 p-1.5 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </section>

      {err && <p className="rounded bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-950 dark:text-rose-300">{err}</p>}

      {s && (
        <section className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <SummaryCard title="新ランク（逃げ先頭）" t={s.lead}
            extra={[
              ["大当たり3本を除く回収率", pct(s.lead_roi_wo_top3)],
              ["回収率100%超えの日", `${s.lead_days_over_100} / ${s.n_days}日`],
            ]} />
          <SummaryCard title="買わなくなるランク" t={s.displaced}
            extra={[["新ランクとの収支差", signed(profit(s.lead) - profit(s.displaced))]]} />
          <div className="rounded border border-gray-200 bg-white p-3 text-xs dark:border-gray-700 dark:bg-gray-900">
            <div className="mb-1 font-semibold text-gray-800 dark:text-gray-100">現行 ↔ 置き換え</div>
            <Row k="現行 回収率" v={<span className={roiTone(s.current.roi)}>{pct(s.current.roi)}</span>} />
            <Row k="置き換え 回収率" v={<span className={roiTone(s.combined.roi)}>{pct(s.combined.roi)}</span>} />
            <Row k="現行 収支" v={yen(profit(s.current))} />
            <Row k="置き換え 収支" v={yen(profit(s.combined))} />
            <Row k="差" v={<b className={profit(s.combined) >= profit(s.current) ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}>
              {signed(profit(s.combined) - profit(s.current))}</b>} />
            <Row k="表示的中（現行 → 置き換え）" v={`${s.current.shown_hits} → ${s.combined.shown_hits}`} />
          </div>
        </section>
      )}

      {data && <DayTable days={data.days} selected={day} onSelect={setDay} />}

      {data && (
        <section className="space-y-1.5">
          <div className="flex items-center gap-2 text-xs">
            <h2 className="font-semibold text-gray-800 dark:text-gray-100">
              レース別{day ? `（${mmdd(day)}）` : ""}
            </h2>
            <span className="text-gray-500 dark:text-gray-400">{races.length}R</span>
            {day && (
              <button type="button" onClick={() => setDay("")}
                className="text-indigo-700 underline dark:text-indigo-300">全日を表示</button>
            )}
          </div>
          {races.length === 0 && (
            <p className="text-xs text-gray-500 dark:text-gray-400">対象レースはありません。</p>
          )}
          <ul className="space-y-1.5">
            {races.map((r) => <RaceItem key={r.race_key} r={r} />)}
          </ul>
        </section>
      )}
    </main>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-2 py-0.5">
      <span className="text-gray-600 dark:text-gray-400">{k}</span>
      <span className="whitespace-nowrap text-right text-gray-900 dark:text-gray-100">{v}</span>
    </div>
  );
}

function SummaryCard({ title, t, extra }: { title: string; t: LineLeadTally; extra: [string, string][] }) {
  return (
    <div className="rounded border border-gray-200 bg-white p-3 text-xs dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-1 font-semibold text-gray-800 dark:text-gray-100">{title}</div>
      <Row k="件数（未確定）" v={`${t.n}${t.pending ? `（${t.pending}）` : ""}`} />
      <Row k="的中 / 表示的中" v={`${t.hits} / ${t.shown_hits}`} />
      <Row k="投資" v={yen(t.invest)} />
      <Row k="払戻" v={yen(t.payout)} />
      <Row k="回収率" v={<span className={roiTone(t.roi)}>{pct(t.roi)}</span>} />
      <Row k="最高払戻" v={yen(t.max_payout)} />
      {extra.map(([k, v]) => <Row key={k} k={k} v={v} />)}
    </div>
  );
}

function DayTable({ days, selected, onSelect }: {
  days: LineLeadDay[]; selected: string; onSelect: (d: string) => void;
}) {
  if (days.length === 0) {
    return <p className="text-xs text-gray-500 dark:text-gray-400">この期間の記録はありません。</p>;
  }
  return (
    <section className="space-y-1.5">
      <h2 className="text-xs font-semibold text-gray-800 dark:text-gray-100">日別（日付を押すとレース別を絞り込み）</h2>
      {/* スマホ: 1日=1カード */}
      <ul className="space-y-1.5 sm:hidden">
        {days.map((d) => (
          <li key={d.date}>
            <button type="button" onClick={() => onSelect(d.date)}
              className={`w-full rounded border p-2 text-left text-xs ${selected === d.date
                ? "border-indigo-400 bg-indigo-50 dark:border-indigo-600 dark:bg-indigo-950"
                : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"}`}>
              <div className="flex items-baseline justify-between">
                <b className="text-gray-900 dark:text-gray-100">{mmdd(d.date)}</b>
                <span className={roiTone(d.lead.roi)}>新 {pct(d.lead.roi)}</span>
              </div>
              <div className="mt-1 grid grid-cols-3 gap-1 text-gray-700 dark:text-gray-300">
                <span>新 {d.lead.n}R・的中{d.lead.hits}</span>
                <span>最高 {man(d.lead.max_payout)}</span>
                <span className={roiTone(d.displaced.roi)}>旧 {pct(d.displaced.roi)}</span>
                <span>現行 {signed(profit(d.current))}</span>
                <span>置換 {signed(profit(d.combined))}</span>
                <span className={d.diff >= 0 ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}>
                  差 {signed(d.diff)}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>
      {/* sm 以上: 表 */}
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full text-xs">
          <thead className="text-gray-600 dark:text-gray-400">
            <tr className="border-b border-gray-200 dark:border-gray-700">
              <th rowSpan={2} className="p-1.5 text-left">日付</th>
              <th colSpan={6} className="p-1.5 text-center">新ランク（逃げ先頭）</th>
              <th colSpan={3} className="p-1.5 text-center">買わなくなるランク</th>
              <th colSpan={3} className="p-1.5 text-center">収支（現行 ↔ 置き換え）</th>
            </tr>
            <tr className="border-b border-gray-200 dark:border-gray-700 text-right">
              <th className="p-1.5">R</th><th className="p-1.5">的中</th><th className="p-1.5">投資</th>
              <th className="p-1.5">払戻</th><th className="p-1.5">回収率</th><th className="p-1.5">最高</th>
              <th className="p-1.5">件</th><th className="p-1.5">表示的中</th><th className="p-1.5">回収率</th>
              <th className="p-1.5">現行</th><th className="p-1.5">置き換え</th><th className="p-1.5">差</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.date} onClick={() => onSelect(d.date)}
                className={`cursor-pointer border-b border-gray-100 text-right dark:border-gray-800 ${selected === d.date
                  ? "bg-indigo-50 dark:bg-indigo-950" : "hover:bg-gray-50 dark:hover:bg-gray-800"}`}>
                <td className="p-1.5 text-left font-semibold text-gray-900 dark:text-gray-100">{mmdd(d.date)}</td>
                <td className="p-1.5">{d.lead.n}{d.lead.pending ? <span className="text-gray-400">+{d.lead.pending}</span> : null}</td>
                <td className="p-1.5">{d.lead.hits}</td>
                <td className="p-1.5">{man(d.lead.invest)}</td>
                <td className="p-1.5">{man(d.lead.payout)}</td>
                <td className={`p-1.5 ${roiTone(d.lead.roi)}`}>{pct(d.lead.roi)}</td>
                <td className="p-1.5">{man(d.lead.max_payout)}</td>
                <td className="p-1.5">{d.displaced.n}</td>
                <td className="p-1.5">{d.displaced.shown_hits}</td>
                <td className={`p-1.5 ${roiTone(d.displaced.roi)}`}>{pct(d.displaced.roi)}</td>
                <td className="p-1.5">{signed(profit(d.current))}</td>
                <td className="p-1.5">{signed(profit(d.combined))}</td>
                <td className={`p-1.5 font-semibold ${d.diff >= 0 ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}`}>
                  {signed(d.diff)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RaceItem({ r }: { r: LineLeadRace }) {
  const t = hhmmJst(r.start_at);
  const result = !r.settled
    ? <span className="text-gray-400">未確定</span>
    : r.hit
      ? <b className="text-emerald-700 dark:text-emerald-300">的中 {yen(r.payout)}</b>
      : <span className="text-gray-500 dark:text-gray-400">外れ{r.win_combo ? `（${r.win_combo}）` : ""}</span>;
  return (
    <li className="rounded border border-gray-200 bg-white p-2 text-xs dark:border-gray-700 dark:bg-gray-900">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <b className="text-gray-900 dark:text-gray-100">
          {mmdd(r.race_date)} {r.venue_name ?? ""}{r.race_no ?? ""}R
        </b>
        {t && <span className="text-gray-500 dark:text-gray-400">{t}</span>}
        {r.race_type && <span className="text-gray-500 dark:text-gray-400">{r.race_type}</span>}
        <span className="ml-auto">{result}</span>
      </div>
      <div className="mt-1 text-gray-700 dark:text-gray-300">
        三連単 {r.combos.length}点 × {yen(r.stake)}：
        <span className="break-all">
          {r.combos.map((c) => (
            <span key={c} className={`mr-1 ${r.hit && c === r.win_combo ? "font-bold text-emerald-700 dark:text-emerald-300" : ""}`}>{c}</span>
          ))}
        </span>
      </div>
      <div className="mt-0.5 text-gray-600 dark:text-gray-400">
        買わなくなるランク：
        {r.sold.length === 0
          ? "なし（売っていないレース）"
          : r.sold.map((x) => (
            <span key={x.rank_key} className="mr-2">
              {x.rank_key} {x.settled ? (x.payout > 0 ? `払戻 ${yen(x.payout)}` : "外れ") : "未確定"}
            </span>
          ))}
      </div>
    </li>
  );
}
