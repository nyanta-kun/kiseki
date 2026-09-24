"use client";

/**
 * 逃げ先頭ライン（`L_lead`）の検証ページ（2026-09-24 新設・検証中）
 *
 * 行は条件を満たす全レースで作り、**売るのは型ラボが売らないレースへ1日5本**（穴狙い）。
 * 「全部売っていたら」を、**同じレースで実際に出した他の商品（買わなくなるランク）**と
 * 並べて見る。実際に出したレースには「入稿済み」の印を付ける。
 * 置き換えの定義と会計は `backend/src/services/keirin_line_lead_verify.py`。
 *
 * 表示は2つ（2026-09-24 ユーザー要望で「1日」を追加・既定）:
 * - **1日**: その日の推奨レースを発走順に、買い目（1点ごとの賭け金・想定払戻）と結果、
 *   同じレースで売っていた商品を並べる。◀ ▶ で日を移動する
 * - **期間の集計**: まとめ・日別の表・レース一覧（日付を押すとその日の「1日」へ移る）
 *
 * 🔴 **1日・1か月の数字で判断しない。** 的中は 1日 0.7本前後で、大当たり数本で
 *    回収率が大きく動く（2026-09 は1本で +20pt）。「大当たり3本を除く回収率」を必ず併せて見る。
 * 設計と実測: keirin/docs/type_lab/line_lead_2026_09_24.md
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowLeft, ChevronLeft, ChevronRight, FlaskConical, RefreshCw } from "lucide-react";
import {
  expectedPayout, fetchLineLead, hhmmJst, profit, roiTone, shiftDay,
  type LineLeadDay, type LineLeadRace, type LineLeadResponse, type LineLeadTally,
} from "@/lib/keirinLineLead";

const yen = (n: number | null | undefined) =>
  n == null ? "—" : `${Math.round(n).toLocaleString()}円`;
/** スマホの幅では万円へ丸める（桁が多いと折り返す）。 */
const man = (n: number | null | undefined) =>
  n == null ? "—" : Math.abs(n) >= 10000 ? `${(n / 10000).toFixed(1)}万` : `${Math.round(n).toLocaleString()}`;
const pct = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(1)}%`);
const signed = (n: number) => `${n >= 0 ? "+" : ""}${man(n)}`;

/** 今日の ISO 日付（**端末のローカル時刻**・`/keirin/type-lab` と同じ理由で UTC を使わない）。 */
const today = () => shiftDay(new Date().toLocaleDateString("sv-SE"), 0);
const mmdd = (iso: string) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}`;
const WEEK = "日月火水木金土";
const withWeekday = (iso: string) => `${mmdd(iso)}（${WEEK[new Date(`${iso}T00:00:00`).getDay()]}）`;

type View = "day" | "range";
const PRESETS: { label: string; days: number }[] = [
  { label: "7日", days: 6 }, { label: "30日", days: 29 }, { label: "90日", days: 89 },
];

export default function LineLeadPage() {
  const [view, setView] = useState<View>("day");
  const [day, setDay] = useState<string>(today());
  const [dateFrom, setDateFrom] = useState(shiftDay(today(), -6));
  const [dateTo, setDateTo] = useState(today());
  const [data, setData] = useState<LineLeadResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setData(await fetchLineLead(view === "day"
        ? { dateFrom: day, dateTo: day } : { dateFrom, dateTo }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [view, day, dateFrom, dateTo]);

  useEffect(() => { void load(); }, [load]);

  /** 期間の集計から、その日の「1日」へ移る。 */
  const openDay = (d: string) => { setDay(d); setView("day"); };

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
          検証中・入稿は1日5本
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
            <b>入稿</b>: 対象の全レースを記録・採点し、実際に出すのは<b>型ラボが売っていない・モーニング開催でない・
            発走の早いレースへ1日5本</b>（穴狙いアイコン）。出したレースには「入稿済み」の印が付きます。
          </p>
          <p>
            <b>買わなくなるランク</b>: 新ランクのレースで実際に netkeirin へ出した他の商品（新ランク自身は含めない）。
            <b>現行 ↔ 置き換え</b>は、売った全商品と「新ランクのレースだけ新ランクに置き換え（売っていなければ足す）」の比較。
          </p>
          <p className="text-amber-700 dark:text-amber-300">
            的中は1日1本前後で、少数の大当たりで回収率が大きく動きます。「大当たり3本を除く回収率」を必ず併せて見てください。
          </p>
        </div>
      </details>

      <div role="tablist" className="flex gap-1 text-xs">
        {([["day", "1日"], ["range", "期間の集計"]] as const).map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={view === k} onClick={() => setView(k)}
            className={`rounded px-3 py-1.5 ${view === k
              ? "bg-indigo-600 text-white dark:bg-indigo-500"
              : "border border-gray-300 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"}`}>
            {label}
          </button>
        ))}
        <button type="button" onClick={() => void load()} aria-label="再読み込み"
          className="ml-auto rounded border border-gray-300 p-1.5 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {view === "day" ? (
        <section className="flex items-center gap-1.5 text-xs">
          <button type="button" aria-label="前の日" onClick={() => setDay(shiftDay(day, -1))}
            className="rounded border border-gray-300 p-1.5 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
            <ChevronLeft size={14} />
          </button>
          <input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)}
            aria-label="日付"
            className="rounded border border-gray-300 bg-white px-1.5 py-1 text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" />
          <button type="button" aria-label="次の日" onClick={() => setDay(shiftDay(day, 1))}
            className="rounded border border-gray-300 p-1.5 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
            <ChevronRight size={14} />
          </button>
          <button type="button" onClick={() => setDay(today())}
            className="rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
            今日
          </button>
          <span className="font-semibold text-gray-900 dark:text-gray-100">{withWeekday(day)}</span>
        </section>
      ) : (
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
              onClick={() => { setDateFrom(shiftDay(today(), -p.days)); setDateTo(today()); }}
              className="rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
              {p.label}
            </button>
          ))}
        </section>
      )}

      {err && <p className="rounded bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-950 dark:text-rose-300">{err}</p>}

      {data && view === "day" && <DayView data={data} />}
      {data && view === "range" && <RangeView data={data} onOpenDay={openDay} />}
    </main>
  );
}

// ───────────────────────────── 1日 ─────────────────────────────

function DayView({ data }: { data: LineLeadResponse }) {
  const s = data.summary;
  const races = [...data.races].sort((a, b) => (a.start_at ?? 0) - (b.start_at ?? 0));
  return (
    <>
      <section className="rounded border border-gray-200 bg-white p-3 text-xs dark:border-gray-700 dark:bg-gray-900">
        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-4">
          <Row k="推奨レース" v={`${s.lead.n + s.lead.pending}R${s.lead.pending ? `（未確定 ${s.lead.pending}）` : ""}`} />
          <Row k="的中" v={`${s.lead.hits}本`} />
          <Row k="投資（確定分）" v={yen(s.lead.invest)} />
          <Row k="払戻" v={yen(s.lead.payout)} />
          <Row k="回収率" v={<span className={roiTone(s.lead.roi)}>{pct(s.lead.roi)}</span>} />
          <Row k="最高払戻" v={yen(s.lead.max_payout)} />
          <Row k="買わなくなるランク 回収率" v={<span className={roiTone(s.displaced.roi)}>{pct(s.displaced.roi)}</span>} />
          <Row k="置き換えの収支差" v={
            <b className={profit(s.combined) >= profit(s.current) ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}>
              {signed(profit(s.combined) - profit(s.current))}
            </b>} />
          {s.lead_sold && (
            <Row k="実際に出した分（穴狙い）" v={
              `${s.lead_sold.n + s.lead_sold.pending}R・的中${s.lead_sold.hits}・${pct(s.lead_sold.roi)}`} />
          )}
        </div>
      </section>
      {races.length === 0 ? (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          この日の推奨レースはありません（生成は毎朝 7:11・昼と夕に組み直し）。
        </p>
      ) : (
        <ul className="space-y-2">
          {races.map((r) => <RaceCard key={r.race_key} r={r} />)}
        </ul>
      )}
    </>
  );
}

function RaceCard({ r }: { r: LineLeadRace }) {
  const t = hhmmJst(r.start_at);
  const badge = !r.settled
    ? <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600 dark:bg-gray-800 dark:text-gray-300">未確定</span>
    : r.hit
      ? <span className="rounded bg-emerald-100 px-1.5 py-0.5 font-bold text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">的中 {yen(r.payout)}</span>
      : <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600 dark:bg-gray-800 dark:text-gray-400">外れ</span>;
  const legs = r.legs.length ? r.legs : r.combos.map((c) => ({ combo: c, stake: r.stake, pred_odds: null, won: false }));
  return (
    <li className={`rounded border p-2.5 text-xs ${r.hit
      ? "border-emerald-300 bg-emerald-50/40 dark:border-emerald-800 dark:bg-emerald-950/30"
      : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {t && <span className="font-mono text-gray-600 dark:text-gray-400">{t}</span>}
        <b className="text-sm text-gray-900 dark:text-gray-100">{r.venue_name ?? ""}{r.race_no ?? ""}R</b>
        {r.race_type && <span className="text-gray-500 dark:text-gray-400">{r.race_type}</span>}
        {r.submitted && (
          <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200">
            入稿済み・穴狙い
          </span>
        )}
        <span className="ml-auto">{badge}</span>
      </div>

      <table className="mt-1.5 w-full">
        <thead className="text-[10px] text-gray-500 dark:text-gray-400">
          <tr><th className="text-left font-normal">三連単</th><th className="text-right font-normal">賭け金</th>
            <th className="text-right font-normal">予測</th><th className="text-right font-normal">想定払戻</th></tr>
        </thead>
        <tbody>
          {legs.map((l) => (
            <tr key={l.combo} className={l.won ? "font-bold text-emerald-700 dark:text-emerald-300" : "text-gray-800 dark:text-gray-200"}>
              <td className="font-mono">{l.combo}{l.won ? " ◎" : ""}</td>
              <td className="text-right">{yen(l.stake)}</td>
              <td className="text-right">{l.pred_odds ? `${l.pred_odds.toFixed(1)}倍` : "—"}</td>
              <td className="text-right">{yen(expectedPayout(l))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-0.5 text-right text-[10px] text-gray-500 dark:text-gray-400">
        {legs.length}点・合計 {yen(r.invest)}
      </div>

      {r.settled && (
        <div className="mt-1 text-gray-700 dark:text-gray-300">
          結果 <b className="font-mono">{r.win_combo ?? "—"}</b>
          {r.win_tf_odds != null && <>（三連単 {r.win_tf_odds.toFixed(1)}倍）</>}
        </div>
      )}

      <div className="mt-1.5 border-t border-gray-100 pt-1.5 text-gray-600 dark:border-gray-800 dark:text-gray-400">
        <span className="text-[10px]">買わなくなるランク</span>
        {r.sold.length === 0 ? (
          <div>なし（このレースは売っていない）</div>
        ) : r.sold.map((x) => (
          <div key={x.rank_key} className="mt-0.5">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <b className="text-gray-800 dark:text-gray-200">{x.rank_key}</b>
              {x.title && <span className="truncate">{x.title}</span>}
              <span className="ml-auto whitespace-nowrap">
                {!x.settled ? "未確定" : x.payout > 0 ? `払戻 ${yen(x.payout)}` : "外れ"}
              </span>
            </div>
            {x.lines.length > 0 && (
              <div className="font-mono text-[11px] break-all">{x.lines.join(" / ")}</div>
            )}
          </div>
        ))}
      </div>
    </li>
  );
}

// ───────────────────────────── 期間の集計 ─────────────────────────────

function RangeView({ data, onOpenDay }: { data: LineLeadResponse; onOpenDay: (d: string) => void }) {
  const s = data.summary;
  return (
    <>
      <section className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <SummaryCard title="新ランク（逃げ先頭）" t={s.lead}
          extra={[
            ["大当たり3本を除く回収率", pct(s.lead_roi_wo_top3)],
            ["回収率100%超えの日", `${s.lead_days_over_100} / ${s.n_days}日`],
            ...(s.lead_sold
              ? [["実際に出した分（穴狙い）",
                  `${s.lead_sold.n + s.lead_sold.pending}R・的中${s.lead_sold.hits}・${pct(s.lead_sold.roi)}`] as [string, string]]
              : []),
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
      <DayTable days={data.days} onSelect={onOpenDay} />
    </>
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

function DayTable({ days, onSelect }: { days: LineLeadDay[]; onSelect: (d: string) => void }) {
  if (days.length === 0) {
    return <p className="text-xs text-gray-500 dark:text-gray-400">この期間の記録はありません。</p>;
  }
  return (
    <section className="space-y-1.5">
      <h2 className="text-xs font-semibold text-gray-800 dark:text-gray-100">日別（日付を押すとその日の推奨・買い目・結果へ）</h2>
      {/* スマホ: 1日=1カード */}
      <ul className="space-y-1.5 sm:hidden">
        {days.map((d) => (
          <li key={d.date}>
            <button type="button" onClick={() => onSelect(d.date)}
              className="w-full rounded border border-gray-200 bg-white p-2 text-left text-xs dark:border-gray-700 dark:bg-gray-900">
              <div className="flex items-baseline justify-between">
                <b className="text-gray-900 dark:text-gray-100">{withWeekday(d.date)}</b>
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
                className="cursor-pointer border-b border-gray-100 text-right hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800">
                <td className="p-1.5 text-left font-semibold text-indigo-700 underline dark:text-indigo-300">{withWeekday(d.date)}</td>
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
