"use client";

/**
 * 型ラボ 表示確認ページ（2026-08-27 新設）
 *
 * 🔴 **既存の /keirin（一覧）や /keirin/stats とはデータが別**。
 *    見ているのは `keirin.type_lab_picks` だけで、picks_history も
 *    netkeirin_submissions も読んでいない。既存商品の全面置き換えを
 *    想定した設計を、混ぜずに検証するためのページ。
 *
 * 設計と実測: keirin/docs/type_lab/SUMMARY.md
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, FlaskConical, RefreshCw } from "lucide-react";
import {
  fetchKeirinTypeLab, type TypeLabPick, type TypeLabResponse,
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
const pct = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(2)}%`);

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

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      setData(await fetchKeirinTypeLab({ mode, dateFrom, dateTo, limit: 1000 }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [mode, dateFrom, dateTo]);

  useEffect(() => { void load(); }, [load]);

  const picks = useMemo(
    () => (data?.picks ?? []).filter((p) => !planFilter || p.plan_key === planFilter),
    [data, planFilter],
  );

  return (
    <main className="mx-auto max-w-7xl p-4 space-y-4">
      <header className="flex items-center gap-3">
        <Link href="/keirin" className="text-slate-500 hover:text-slate-800">
          <ArrowLeft size={18} />
        </Link>
        <FlaskConical size={20} className="text-indigo-600" />
        <h1 className="text-lg font-bold">型ラボ（検証用）</h1>
        <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
          既存商品とは別データ・入稿しません
        </span>
      </header>

      <p className="text-xs leading-relaxed text-slate-500">
        レースを 6 型（A 鉄板 / B 堅い・中 / C 堅いが崩れ筋 / D 混戦・軸あり / E 混戦・中 /
        F 大混戦）に分け、型ごとに決めた買い方で組んだ検証用の買い目です。
        <b>ペーパー</b>は過去を vintage 予測で組んだもの、<b>実地</b>は当日を本番モデルで
        組んだもの。どちらも <code>keirin.type_lab_picks</code> にしか書かれず、
        一覧・統計・入稿には出ません。
      </p>

      <div className="flex flex-wrap items-end gap-3 rounded border bg-white p-3">
        <label className="text-sm">
          <span className="mr-2 text-slate-500">モード</span>
          <select className="rounded border px-2 py-1" value={mode}
                  onChange={(e) => setMode(e.target.value as "live" | "paper")}>
            <option value="live">実地（当日・本番モデル）</option>
            <option value="paper">ペーパー（過去・vintage）</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="mr-2 text-slate-500">期間</span>
          <input type="date" className="rounded border px-2 py-1" value={dateFrom}
                 onChange={(e) => setDateFrom(e.target.value)} />
        </label>
        <span className="pb-1 text-slate-400">〜</span>
        <input type="date" className="rounded border px-2 py-1" value={dateTo}
               onChange={(e) => setDateTo(e.target.value)} />
        <button onClick={() => void load()} disabled={loading}
                className="flex items-center gap-1 rounded bg-indigo-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />再取得
        </button>
        {data?.rule_versions?.length ? (
          <span className="ml-auto text-xs text-slate-400">
            rule_version: {data.rule_versions.join(", ")}
          </span>
        ) : null}
      </div>

      {err && <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">{err}</div>}

      {/* ── プラン別サマリ ── */}
      <section className="overflow-x-auto rounded border bg-white">
        <table className="w-full min-w-[1000px] text-sm">
          <thead className="bg-slate-50 text-xs text-slate-600">
            <tr>
              <th className="p-2 text-left">プラン</th>
              <th className="p-2 text-left">型</th>
              <th className="p-2 text-right">件/日</th>
              <th className="p-2 text-right">採点</th>
              <th className="p-2 text-right">的中</th>
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
                <td className="p-2">{s.type_label} {TYPE_NAME[s.type_label] ?? ""}</td>
                <td className="p-2 text-right">{s.per_day.toFixed(2)}</td>
                <td className="p-2 text-right">{s.n_settled}/{s.n}</td>
                <td className="p-2 text-right">{pct(s.hit_rate)}</td>
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
              <tr><td colSpan={12} className="p-6 text-center text-slate-400">データがありません</td></tr>
            )}
          </tbody>
        </table>
      </section>
      <p className="text-xs text-slate-400">
        ⚠️ <b>ROI で採否を決めないこと</b>（この層は ±2.5pt に収めるのに約15.6年かかる）。
        判断指標は 件/日・表示的中（ガミ除く）・払戻中央・2倍以上の的中件/日・ガミ率。
        行をクリックするとそのプランだけに絞り込みます。
      </p>

      {/* ── 現行推奨との比較 ── */}
      <section className="overflow-x-auto rounded border bg-white">
        <div className="border-b bg-slate-50 px-3 py-2 text-sm font-semibold">
          現行推奨との比較
          <span className="ml-2 font-normal text-xs text-slate-500">
            両方に採点済みの記録がある**同じレース**だけで並べています
          </span>
        </div>
        <table className="w-full min-w-[900px] text-sm">
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
                <td className="p-2 text-right">{c.n_races}（{c.n_days}日）</td>
                <td className="p-2 text-right">
                  <b className={c.lab_shown_hit >= c.cur_shown_hit ? "text-emerald-700" : "text-slate-700"}>
                    {pct(c.lab_shown_hit)}
                  </b>
                  <span className="text-slate-400"> / {pct(c.cur_shown_hit)}</span>
                </td>
                <td className="p-2 text-right">
                  <b className={c.lab_median_payout >= c.cur_median_payout ? "text-emerald-700" : "text-slate-700"}>
                    {yen(c.lab_median_payout)}
                  </b>
                  <span className="text-slate-400"> / {yen(c.cur_median_payout)}</span>
                </td>
                <td className="p-2 text-right">
                  <b className={c.lab_two_per_day >= c.cur_two_per_day ? "text-emerald-700" : "text-slate-700"}>
                    {c.lab_two_per_day.toFixed(2)}
                  </b>
                  <span className="text-slate-400"> / {c.cur_two_per_day.toFixed(2)}</span>
                </td>
                <td className="p-2 text-right text-slate-500">
                  {c.lab_roi.toFixed(1)}% / {c.cur_roi.toFixed(1)}%
                </td>
              </tr>
            ))}
            {!loading && !(data?.comparison ?? []).length && (
              <tr><td colSpan={6} className="p-4 text-center text-slate-400">
                比較できる記録がありません（現行側の採点待ちの可能性があります）
              </td></tr>
            )}
          </tbody>
        </table>
      </section>
      <p className="text-xs text-slate-400">
        🔴 現行側は <code>picks_history</code>（<b>ランクの候補</b>）です。実売との不一致が
        18% あるため「売った商品」そのものではありませんが、型ラボも「設計が何を買うか」なので
        <b>設計どうしの比較としては同じ土俵</b>です。実際に入稿されたかは各カードの
        「入稿: …」で確認できます。
      </p>

      {/* ── 買い目一覧 ── */}
      <section className="space-y-2">
        {picks.map((p) => <PickCard key={`${p.race_key}-${p.plan_key}`} p={p} />)}
      </section>
    </main>
  );
}

function PickCard({ p }: { p: TypeLabPick }) {
  const settled = p.settled;
  const tone = !settled ? "border-slate-200"
    : p.hit ? ((p.payout ?? 0) >= p.budget ? "border-emerald-400" : "border-amber-400")
    : "border-slate-200 opacity-70";
  return (
    <div className={`rounded border-2 bg-white p-3 ${tone}`}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-500">{p.race_date}</span>
        <span className="font-semibold">{p.venue_name ?? "—"} {p.race_no ?? "?"}R</span>
        <span className="text-xs text-slate-500">{p.race_type ?? ""}</span>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs">
          型{p.type_label} {TYPE_NAME[p.type_label] ?? ""}（軸和 {p.axis_sum?.toFixed(2)} / 荒れ度 {p.arare}）
        </span>
        <span className="rounded bg-indigo-100 px-2 py-0.5 font-mono text-xs text-indigo-800">
          {p.plan_key}
        </span>
        <span className="text-xs text-slate-500">
          {p.bet_type === "trio" ? "三連複" : "三連単"} {p.n_legs}点 / 想定平均 {yen(p.pred_mean_payout)}
        </span>
        <span className="ml-auto text-xs">
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
        <div className="mt-2 rounded bg-slate-50 px-2 py-1 text-xs text-slate-600">
          <span className="font-semibold">現行推奨</span>
          <span className="ml-2 font-mono">{p.current.rank.replace("RANK_", "")}</span>
          <span className="ml-2 font-mono">{p.current.pred_combo ?? "—"}</span>
          {p.current.n_combos ? <span className="ml-1 text-slate-400">（{p.current.n_combos}点）</span> : null}
          {p.current.settled
            ? (p.current.hit
                ? <span className="ml-2 text-emerald-700">的中 {yen(p.current.payout)}</span>
                : <span className="ml-2 text-slate-400">不的中</span>)
            : <span className="ml-2 text-slate-400">未採点</span>}
          {p.current.sold_rank_key
            ? <span className="ml-2 rounded bg-indigo-100 px-1 text-indigo-700">入稿: {p.current.sold_rank_key}</span>
            : <span className="ml-2 text-slate-400">（入稿記録なし）</span>}
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-1">
        {p.legs.map((l) => (
          <span key={l.combo}
                className={`rounded px-2 py-0.5 font-mono text-xs ${
                  settled && p.win_combo === l.combo
                    ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-700"}`}>
            {l.combo}
            <span className="ml-1 text-[10px] opacity-70">
              {l.stake.toLocaleString()}円 / {l.pred_odds.toFixed(1)}倍
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
