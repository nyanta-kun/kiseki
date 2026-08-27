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
import {
  ArrowLeft, ChevronLeft, ChevronRight, FlaskConical, RefreshCw,
} from "lucide-react";
import {
  fetchKeirinTypeLab, fetchKeirinTypeLabCombo, fetchKeirinTypeLabOutcome,
  type TypeLabComboResponse, type TypeLabComboRow, type TypeLabComparisonRow,
  type TypeLabOutcomeMatrix, type TypeLabOutcomeResponse,
  type TypeLabPick, type TypeLabResponse, type TypeLabSummary,
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

/** モード。`paper9` は9車の検証行（`build_type_lab_picks --n-entries 9` が書く）。
 *  三連単の予測オッズ `odds_tf_n9`（2026-08-27 新設）が入ったので**8プランとも組める**。
 *  🔴 **7車の実地検証と混ぜて読まないこと。** 型の出方が違う（9車は F 大混戦が
 *     58% を占める ↔ 7車は 31%）ので、件数も配当帯も別物になる。 */
type TypeLabMode = "live" | "paper" | "paper9";

/** 決着クラスの表示。サーバー（`keirin_type_lab_outcome.FINISH_CLASSES`）と対。
 *  🔴 key を増やしたら**両方**へ足すこと（片方だけだと「—」になって気づけない）。 */
const FINISH_LABEL: Record<string, string> = {
  firm34: "順当", firm_ana: "軸2+穴", half34: "片軸+中位",
  half_ana: "片軸+穴", broken: "軸崩壊",
};
/** 決着クラスの色。堅い決着＝緑 → 崩れた決着＝赤。 */
const FINISH_TONE: Record<string, string> = {
  firm34: "bg-emerald-50 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  firm_ana: "bg-teal-50 text-teal-800 dark:bg-teal-900/40 dark:text-teal-200",
  half34: "bg-amber-50 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  half_ana: "bg-orange-50 text-orange-800 dark:bg-orange-900/40 dark:text-orange-200",
  broken: "bg-rose-50 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
};

/** 表示順。`PLAN_NOTE` の並びをそのまま使う（挿入順が保たれる）。 */
const PLAN_KEYS = Object.keys(PLAN_NOTE);
/** 組み合わせの初期値。**型ごとに1つずつ**＝競合が起きない並び。 */
const DEFAULT_COMBO = ["A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"];

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

/** ISO 日付を n 日ずらす。`Date` の月跨ぎ処理に任せる。 */
function shiftISO(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function TypeLabPage() {
  const [mode, setMode] = useState<TypeLabMode>("live");
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
  // 組み合わせ集計。チェックの付け外しで**軽い専用 API だけ**を叩き直す
  // （買い目つきの本体を毎回引き直すと重い）。
  const [comboPlans, setComboPlans] = useState<string[]>(DEFAULT_COMBO);
  const [combo, setCombo] = useState<TypeLabComboResponse | null>(null);
  const [comboErr, setComboErr] = useState<string | null>(null);
  // 軸信頼ゲート（検証中の候補・既定はOFF）。既存の行に後から当てるだけなので
  // 実地検証の最中でも買い目は一切作り直さない。
  const [axisGate, setAxisGate] = useState(false);
  // 型分けの答え合わせ。買い目を引かない軽い専用 API（本体とは別に読む）。
  const [outcome, setOutcome] = useState<TypeLabOutcomeResponse | null>(null);
  const [outcomeErr, setOutcomeErr] = useState<string | null>(null);

  /** 期間の**幅を保ったまま**日数ぶん前後へずらす。 */
  const shiftRange = useCallback((sign: number) => {
    const span = Math.round(
      (new Date(`${dateTo}T00:00:00`).getTime() - new Date(`${dateFrom}T00:00:00`).getTime())
      / 86400000) + 1;
    const step = sign * span;
    setDateFrom(shiftISO(dateFrom, step));
    setDateTo(shiftISO(dateTo, step));
  }, [dateFrom, dateTo]);

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

  const loadCombo = useCallback(async () => {
    if (!comboPlans.length) { setCombo(null); setComboErr(null); return; }
    try {
      setCombo(await fetchKeirinTypeLabCombo({
        plans: comboPlans, mode, dateFrom, dateTo, venue, axisGate,
      }));
      setComboErr(null);
    } catch (e) {
      setCombo(null);
      setComboErr(e instanceof Error ? e.message : String(e));
    }
  }, [comboPlans, mode, dateFrom, dateTo, venue, axisGate]);

  useEffect(() => { void loadCombo(); }, [loadCombo]);

  const loadOutcome = useCallback(async () => {
    try {
      setOutcome(await fetchKeirinTypeLabOutcome({ mode, dateFrom, dateTo, venue }));
      setOutcomeErr(null);
    } catch (e) {
      setOutcome(null);
      setOutcomeErr(e instanceof Error ? e.message : String(e));
    }
  }, [mode, dateFrom, dateTo, venue]);

  useEffect(() => { void loadOutcome(); }, [loadOutcome]);

  const togglePlan = useCallback((plan: string) => {
    setComboPlans((prev) => (prev.includes(plan)
      ? prev.filter((x) => x !== plan)
      : [...prev, plan]));
  }, []);

  const picks = useMemo(
    () => (data?.picks ?? []).filter((p) => !planFilter || p.plan_key === planFilter),
    [data, planFilter],
  );

  return (
    <main className="w-full px-3 py-3 sm:mx-auto sm:max-w-6xl sm:px-4 sm:py-4 space-y-3 pb-16">
      <header className="flex items-center gap-2">
        <Link href="/keirin" className="text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:text-gray-100" aria-label="戻る">
          <ArrowLeft size={18} />
        </Link>
        <FlaskConical size={18} className="text-indigo-600" />
        <h1 className="text-base font-bold text-gray-900 dark:text-white sm:text-lg">型ラボ</h1>
        <span className="rounded bg-amber-100 dark:bg-amber-900 px-1.5 py-0.5 text-[10px] text-amber-800 dark:text-amber-200 sm:text-xs">
          検証用・入稿しません
        </span>
      </header>

      <details className="rounded border border-gray-200 bg-white text-gray-800 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 text-xs">
        <summary className="cursor-pointer px-3 py-2 font-semibold text-gray-700 dark:text-gray-300">
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
      <div className="space-y-2 rounded border border-gray-200 bg-white text-gray-800 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 p-3">
        <div className="flex items-center gap-2">
          <select
            className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-800 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 sm:flex-none"
            value={mode} onChange={(e) => setMode(e.target.value as TypeLabMode)}
          >
            <option value="live">実地（当日・本番モデル）</option>
            <option value="paper">ペーパー（過去・vintage）</option>
            <option value="paper9">9車ペーパー（検証・買い目は三連複のみ）</option>
          </select>
          <button
            onClick={() => void load()} disabled={loading}
            className="flex shrink-0 items-center gap-1 rounded bg-indigo-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            <span className="hidden sm:inline">再取得</span>
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          {/* 日付送り。**期間の幅を保ったまま**前後へずらす（片側だけ動くと窓が伸び縮みする）。 */}
          <StepButton label="前の期間へ" onClick={() => shiftRange(-1)} dir="prev" />
          <input type="date" className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                 value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          <span className="shrink-0 text-gray-500 dark:text-gray-400">〜</span>
          <input type="date" className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                 value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          <StepButton label="次の期間へ" onClick={() => shiftRange(1)} dir="next" />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <QuickRange label="今日" onClick={() => { setDateFrom(isoDaysAgo(0)); setDateTo(isoDaysAgo(0)); }} />
          <QuickRange label="直近7日" onClick={() => { setDateFrom(isoDaysAgo(6)); setDateTo(isoDaysAgo(0)); }} />
          <QuickRange label="今月" onClick={() => {
            const to = isoDaysAgo(0);
            setDateFrom(`${to.slice(0, 8)}01`); setDateTo(to);
          }} />
        </div>
        <VenueTabs
          venues={venueOptions.length ? venueOptions : (data?.venues ?? [])}
          value={venue} onChange={setVenue}
        />
        {data?.rule_versions?.length ? (
          <div className="text-[10px] text-gray-500 dark:text-gray-400">rule_version: {data.rule_versions.join(", ")}</div>
        ) : null}
      </div>

      {err && (
        <div className="rounded border border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950 p-3 text-sm text-red-700">{err}</div>
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
            <thead className="bg-gray-50 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">
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
                    className={`cursor-pointer border-t hover:bg-indigo-50 dark:bg-indigo-950 ${planFilter === s.plan_key ? "bg-indigo-50" : ""}`}
                    onClick={() => setPlanFilter(planFilter === s.plan_key ? "" : s.plan_key)}>
                  <td className="p-2">
                    <div className="font-mono font-semibold text-gray-900 dark:text-white">{s.plan_key}</div>
                    <div className="text-xs text-gray-600 dark:text-gray-400">{PLAN_NOTE[s.plan_key] ?? ""}</div>
                  </td>
                  <td className="p-2 whitespace-nowrap text-gray-900 dark:text-gray-100">{s.type_label} {TYPE_NAME[s.type_label] ?? ""}</td>
                  <td className="p-2 text-right text-gray-900 dark:text-gray-100">{s.per_day.toFixed(2)}</td>
                  <td className="p-2 text-right text-gray-900 dark:text-gray-100">{s.n_settled}/{s.n}</td>
                  <td className="p-2 text-right font-semibold text-gray-900 dark:text-white">{pct(s.shown_hit_rate)}</td>
                  <td className="p-2 text-right text-gray-900 dark:text-gray-100">{pct(s.gami_rate)}</td>
                  <td className="p-2 text-right font-semibold text-gray-900 dark:text-white">{yen(s.median_payout)}</td>
                  <td className="p-2 text-right text-gray-600 dark:text-gray-400">{yen(s.median_pred_mean)}</td>
                  <td className="p-2 text-right text-gray-900 dark:text-gray-100">{s.two_plus_per_day.toFixed(2)}</td>
                  <td className="p-2 text-right text-gray-900 dark:text-gray-100">{s.big_per_day.toFixed(3)}</td>
                  <td className="p-2 text-right text-gray-600 dark:text-gray-400">{s.roi.toFixed(1)}%</td>
                </tr>
              ))}
              {!loading && !(data?.summaries ?? []).length && (
                <tr><td colSpan={11} className="p-6 text-center text-gray-500 dark:text-gray-400">データがありません</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      <p className="text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
        ⚠️ <b>ROI で採否を決めないこと</b>（この層は ±2.5pt に収めるのに約15.6年）。
        判断指標は 件/日・表示的中（ガミ除く）・払戻中央・2倍以上の的中件/日・ガミ率。
        {planFilter
          ? <> 絞り込み中: <b>{planFilter}</b>（もう一度押すと解除）</>
          : <> プランを押すとその買い目だけに絞れます。</>}
      </p>

      {/* ── 組み合わせの合計 ── */}
      <Section title="プランを組み合わせた合計"
               note={combo ? `${combo.plans.length}プラン・${combo.n_days}日` : undefined}>
        <div className="space-y-2 p-2 sm:p-3">
          <p className="text-[11px] leading-relaxed text-gray-600 dark:text-gray-400">
            売るプランを選んだときの合計です。
            <b>1レースの推奨は1プラン</b>なので、選んだプランが<b>同じレースに2つ以上
            当たったレースは集計から外します</b>（どちらを買ったことにするか決められないため）。
            型ごとに1つずつ選べば競合は起きません。
          </p>
          <div className="flex flex-wrap gap-1.5">
            {PLAN_KEYS.map((plan) => (
              <PlanCheck key={plan} plan={plan} checked={comboPlans.includes(plan)}
                         onClick={() => togglePlan(plan)} />
            ))}
            <button type="button" onClick={() => setComboPlans(DEFAULT_COMBO)}
                    className="rounded-full border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-600 hover:border-indigo-400 hover:text-indigo-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300">
              既定に戻す
            </button>
            <button type="button" onClick={() => setComboPlans([])}
                    className="rounded-full border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-600 hover:border-indigo-400 hover:text-indigo-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300">
              すべて外す
            </button>
          </div>

          {comboErr && (
            <div className="rounded border border-red-300 bg-red-50 p-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
              {comboErr}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 rounded border border-gray-200 bg-gray-50 px-2 py-1.5 dark:border-gray-700 dark:bg-gray-800">
            <button
              type="button" onClick={() => setAxisGate(!axisGate)}
              role="switch" aria-checked={axisGate}
              className={`shrink-0 rounded-full border px-2.5 py-1 text-xs transition-colors ${
                axisGate
                  ? "border-emerald-600 bg-emerald-600 font-semibold text-white"
                  : "border-gray-300 bg-white text-gray-700 hover:border-emerald-400 hover:text-emerald-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200"
              }`}
            >
              {axisGate ? "✓ " : ""}軸信頼ゲート
            </button>
            <span className="text-[11px] leading-relaxed text-gray-600 dark:text-gray-400">
              各プランの中で<b>軸信頼（上位2車の3着内率の合計）が下位2割</b>のレースを外します。
              {combo?.axis_gate && combo.n_axis_gated_out > 0 && (
                <> — 今回 <b>{combo.n_axis_gated_out}件</b>を除外</>
              )}
            </span>
          </div>
          {axisGate && (
            <div className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1.5 text-[11px] leading-relaxed text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
              🔬 <b>検証中の候補で、まだ採用ではありません。</b>
              20か月の台（探索 2025年 / 確認 2026年）で、ペーパーでは
              49.7 → 39.2件/日・表示的中 25.3 → 27.1%・ROI 79.3 → 82.4%
              （全体との差 +3.1pt CI[+1.3, +4.8]・無作為に同数を落とす対照20本に20/20で勝ち）。
              外した側は ROI 67.8%・表示的中 18.6% とはっきり悪く、向きは6プラン中5つで
              両窓一致します。実地で確かめるために置いています。
            </div>
          )}

          {combo && combo.n_conflict_races > 0 && (
            <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
              競合で除外: <b>{combo.n_conflict_races}レース</b>
              （選んだプランのうち2つ以上が同じレースに当たったもの）
            </div>
          )}

          {!comboPlans.length ? (
            <Empty text="プランを選んでください" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs sm:text-sm">
                <thead className="bg-gray-50 text-[11px] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                  <tr>
                    <th className="p-2 text-left">プラン</th>
                    <th className="p-2 text-right">対象R</th>
                    <th className="p-2 text-right">的中</th>
                    <th className="p-2 text-right">払戻</th>
                    <th className="p-2 text-right">ROI</th>
                  </tr>
                </thead>
                <tbody>
                  {(combo?.rows ?? []).map((r) => (
                    <ComboTr key={r.plan_key} r={r} />
                  ))}
                  {combo && <ComboTr r={combo.total} total nDays={combo.n_days} />}
                  {!combo && (
                    <tr><td colSpan={5} className="p-4 text-center text-gray-500 dark:text-gray-400">
                      集計中…
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Section>

      <p className="text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
        「対象R」は競合を除いたあとのレース数、括弧内は採点済みの数です。
        <b>的中・払戻・ROI は採点済みのレースだけ</b>で計算しています
        （未採点を分母に入れると、当日の朝ほど ROI が 0 に近く見えてしまうため）。
        括弧の「表示」はガミ（払戻が賭け金以下）を除いた的中数です。
      </p>

      {/* ── 型分けの答え合わせ ── */}
      <Section
        title="型分けの答え合わせ"
        note={outcome
          ? `採点済み ${outcome.n_races_settled}R / 分類できた ${outcome.n_races}R`
          : undefined}>
        {outcomeErr && (
          <div className="p-3 text-sm text-rose-700 dark:text-rose-300">{outcomeErr}</div>
        )}
        {!outcomeErr && outcome && !outcome.n_races && (
          <Empty text="分類できるレースがありません（採点済み かつ 指数の並びを持つ行が要ります）" />
        )}
        {!outcomeErr && outcome && !!outcome.n_races && (
          <div className="space-y-3 p-2 sm:p-3">
            {outcome.n_unclassified > 0 && (
              <p className="rounded bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-800 dark:bg-amber-900/30 dark:text-amber-200">
                ⚠️ 採点済み {outcome.n_races_settled}R のうち <b>{outcome.n_unclassified}R</b> は
                指数の並びが行に無いため分類できていません。並びは<b>行を作った時点でしか残せない</b>
                （モデルが再学習されると当時と別の並びになる）ので、古い行は
                <code className="mx-0.5">backfill_type_lab_outcome.py</code>で復元します。
              </p>
            )}
            {outcome.matrices.map((m) => <MatrixTable key={m.key} m={m} />)}
            <p className="text-[11px] leading-relaxed text-gray-600 dark:text-gray-400">
              🔴 母集団は<b>型ラボが実際に買ったレース</b>です（買い目が組めずゲートで落ちた
              レースは入っていないので、全7車レースでの型の分布とは一致しません）。
              🔴 <b>分割が当たっている＝儲かる ではありません。</b>型は edge を作らず、
              決めるのは「同じ買い方でどの帯へ落ちるか」と「どのレースを拾えるか」だけです。
            </p>
          </div>
        )}
      </Section>

      {/* ── 現行推奨との比較 ── */}
      <Section title="現行推奨との比較"
               note="両方に採点済みの記録がある同じレースだけで並べています">
        <div className="space-y-2 sm:hidden">
          {(data?.comparison ?? []).map((c) => <CompareCard key={c.plan_key} c={c} />)}
          {!loading && !(data?.comparison ?? []).length && <Empty text="比較できる記録がありません" />}
        </div>
        <div className="hidden overflow-x-auto sm:block">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">
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
                  <td className="p-2 font-mono text-gray-900 dark:text-gray-100">{c.plan_key}</td>
                  <td className="p-2 text-right whitespace-nowrap text-gray-900 dark:text-gray-100">{c.n_races}（{c.n_days}日）</td>
                  <td className="p-2 text-right whitespace-nowrap text-gray-900 dark:text-gray-100">
                    <Win a={c.lab_shown_hit} b={c.cur_shown_hit} fmt={pct} />
                  </td>
                  <td className="p-2 text-right whitespace-nowrap text-gray-900 dark:text-gray-100">
                    <Win a={c.lab_median_payout} b={c.cur_median_payout} fmt={yen} />
                  </td>
                  <td className="p-2 text-right whitespace-nowrap text-gray-900 dark:text-gray-100">
                    <Win a={c.lab_two_per_day} b={c.cur_two_per_day} fmt={(v) => v.toFixed(2)} />
                  </td>
                  <td className="p-2 text-right whitespace-nowrap text-gray-600 dark:text-gray-400">
                    {c.lab_roi.toFixed(1)}% / {c.cur_roi.toFixed(1)}%
                  </td>
                </tr>
              ))}
              {!loading && !(data?.comparison ?? []).length && (
                <tr><td colSpan={6} className="p-4 text-center text-gray-500 dark:text-gray-400">
                  比較できる記録がありません
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      <p className="text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
        🔴 現行側は <code>picks_history</code>（<b>ランクの候補</b>）です。実売との不一致が
        18% あるため「売った商品」そのものではありませんが、型ラボも「設計が何を買うか」なので
        <b>設計どうしの比較としては同じ土俵</b>です。実際に入稿されたかは各カードの
        「入稿: …」で確認できます。
      </p>

      {/* ── 買い目一覧 ── */}
      <div className="space-y-2">
        <p className="text-[11px] text-gray-600 dark:text-gray-400">
          発走の早い順（日付が複数ある場合は新しい日から）。
        </p>
        {picks.map((p) => <PickCard key={`${p.race_key}-${p.plan_key}`} p={p} />)}
        {!loading && !picks.length && <Empty text="買い目がありません" />}
      </div>
    </main>
  );
}

/** 競輪場の切り替え。**タブ + 「すべて」ボタン**（2026-08-27 ユーザー指定）。
 *
 * 場は日によって 3〜10 程度あり、スマホ幅では並びきらないので**横スクロール**にする。
 * `-mx-*`＋`px-*` で端まで流し、スクロールできることが見た目で分かるようにしている。
 */
function StepButton({ label, onClick, dir }: {
  label: string; onClick: () => void; dir: "prev" | "next";
}) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label}
            className="shrink-0 rounded border border-gray-300 bg-white px-2 py-1.5 text-gray-700 hover:border-indigo-400 hover:text-indigo-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:text-indigo-300">
      {dir === "prev" ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
    </button>
  );
}

function QuickRange({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
            className="rounded-full border border-gray-300 bg-white px-2.5 py-0.5 text-xs text-gray-700 hover:border-indigo-400 hover:text-indigo-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200">
      {label}
    </button>
  );
}

function VenueTabs({ venues, value, onChange }: {
  venues: string[]; value: string; onChange: (v: string) => void;
}) {
  if (!venues.length) return null;
  const item = (v: string, label: string) => {
    const active = value === v;
    return (
      <button
        key={v || "__all__"} type="button" onClick={() => onChange(v)}
        aria-pressed={active}
        className={`shrink-0 rounded-full border px-3 py-1 text-xs transition-colors ${
          active
            ? "border-indigo-600 bg-indigo-600 font-semibold text-white"
            : "border-gray-300 bg-white text-gray-700 hover:border-indigo-400 hover:text-indigo-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:border-indigo-500 dark:hover:text-indigo-300"
        }`}
      >
        {label}
      </button>
    );
  };
  return (
    <div className="-mx-3 overflow-x-auto px-3 sm:mx-0 sm:px-0">
      <div className="flex w-max gap-1.5 pb-0.5">
        {item("", `すべて（${venues.length}場）`)}
        {venues.map((v) => item(v, v))}
      </div>
    </div>
  );
}


function Section({ title, note, children }: {
  title: string; note?: string; children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded border border-gray-200 bg-white text-gray-800 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
      <div className="border-b border-gray-200 bg-gray-50 px-3 py-2 text-sm font-semibold text-gray-800 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100">
        {title}
        {note && <span className="ml-2 text-[11px] font-normal text-gray-600 dark:text-gray-400">{note}</span>}
      </div>
      <div className="p-2 sm:p-0">{children}</div>
    </section>
  );
}

/** 事前の分割（行）× 実際の決着（列）のマトリクス。
 *
 * 🔴 **ダークモードでは本文色がほぼ白**なので、セルには必ず文字色を明示する
 *    （色指定の無い数値が背景に溶けて消える。2026-08-27 に一度踏んだ）。
 * 濃淡は「その行の中でどこへ寄っているか」を一目で見せるためのもので、
 * 色そのものに意味は持たせない（値は必ず数字でも出す）。
 */
function MatrixTable({ m }: { m: TypeLabOutcomeMatrix }) {
  const rows = m.total ? [...m.rows, m.total] : m.rows;
  const hasHit = rows.some((r) => r.cells.some((c) => c.hit_rate != null));
  const showOdds = rows.some((r) => r.median_tf_odds != null);
  return (
    <div className="overflow-hidden rounded border border-gray-200 dark:border-gray-700">
      <div className="border-b border-gray-200 bg-gray-50 px-2 py-1.5 dark:border-gray-700 dark:bg-gray-800">
        <div className="text-xs font-semibold text-gray-900 dark:text-gray-100">{m.title}</div>
        {m.note && (
          <div className="text-[11px] text-gray-600 dark:text-gray-400">{m.note}</div>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-xs">
          <thead className="bg-gray-50 text-[10px] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
            <tr>
              <th className="p-1.5 text-left">区分</th>
              <th className="p-1.5 text-right">R数</th>
              {m.columns.map((c) => (
                <th key={c.key} className="p-1.5 text-right" title={c.note || undefined}>
                  {c.label}
                </th>
              ))}
              {showOdds && <th className="p-1.5 text-right">中央倍率</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}
                  className={`border-t border-gray-200 dark:border-gray-700 ${
                    r.key === "ALL" ? "bg-gray-50 font-semibold dark:bg-gray-800" : ""}`}>
                <td className="whitespace-nowrap p-1.5 text-gray-900 dark:text-gray-100">{r.label}</td>
                <td className="p-1.5 text-right tabular-nums text-gray-700 dark:text-gray-300">{r.n}</td>
                {r.cells.map((c) => {
                  // 濃淡は「行内の偏り」＝割合。的中率の表でも割合で塗る
                  // （的中率で塗ると母数1件のセルが最も濃くなって誤読を招く）。
                  const tint = Math.min(c.pct, 60) / 60 * 0.30;
                  return (
                    <td key={c.key} className="p-1.5 text-right"
                        style={{ backgroundColor: `rgba(99, 102, 241, ${tint})` }}>
                      {hasHit ? (
                        <>
                          <div className="tabular-nums font-semibold text-gray-900 dark:text-gray-100">
                            {c.n ? `${(c.hit_rate ?? 0).toFixed(0)}%` : "—"}
                          </div>
                          <div className="text-[10px] tabular-nums text-gray-600 dark:text-gray-400">
                            {c.n_hit ?? 0}/{c.n}R
                          </div>
                        </>
                      ) : (
                        <>
                          <div className="tabular-nums font-semibold text-gray-900 dark:text-gray-100">
                            {c.pct.toFixed(1)}%
                          </div>
                          <div className="text-[10px] tabular-nums text-gray-600 dark:text-gray-400">
                            {c.n}R
                          </div>
                        </>
                      )}
                    </td>
                  );
                })}
                {showOdds && (
                  <td className="p-1.5 text-right tabular-nums text-gray-900 dark:text-gray-100">
                    {r.median_tf_odds != null ? `${r.median_tf_odds.toFixed(1)}倍` : "—"}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Empty({ text = "データがありません" }: { text?: string }) {
  return <div className="p-6 text-center text-sm text-gray-500 dark:text-gray-400">{text}</div>;
}

/** ラボ / 現行 を並べ、良いほうを強調する。 */
function Win({ a, b, fmt }: { a: number; b: number; fmt: (v: number) => string }) {
  return (
    <>
      <b className={a >= b ? "text-emerald-700" : "text-gray-800 dark:text-gray-200"}>{fmt(a)}</b>
      <span className="text-gray-500 dark:text-gray-400"> / {fmt(b)}</span>
    </>
  );
}

function Metric({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-gray-500 dark:text-gray-400">{label}</div>
      <div className={`truncate text-sm text-gray-900 dark:text-gray-100 ${strong ? "font-semibold" : ""}`}>{value}</div>
    </div>
  );
}

function SummaryCard({ s, active, onClick }: {
  s: TypeLabSummary; active: boolean; onClick: () => void;
}) {
  return (
    <button onClick={onClick}
            className={`w-full rounded border p-2 text-left ${
              active
                ? "border-indigo-400 bg-indigo-50 dark:border-indigo-600 dark:bg-indigo-950"
                : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900"
            }`}>
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm font-semibold text-gray-900 dark:text-white">{s.plan_key}</span>
        <span className="rounded bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 text-[10px]">
          型{s.type_label} {TYPE_NAME[s.type_label] ?? ""}
        </span>
        <span className="ml-auto text-[10px] text-gray-500 dark:text-gray-400">{s.n_settled}/{s.n} 採点</span>
      </div>
      <div className="mt-0.5 line-clamp-2 text-[10px] text-gray-600 dark:text-gray-400">{PLAN_NOTE[s.plan_key] ?? ""}</div>
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
    <div className="rounded border border-gray-200 bg-white text-gray-800 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 p-2">
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm font-semibold text-gray-900 dark:text-white">{c.plan_key}</span>
        <span className="ml-auto text-[10px] text-gray-500 dark:text-gray-400">{c.n_races}R / {c.n_days}日</span>
      </div>
      <div className="mt-1 grid grid-cols-[auto_1fr_1fr] gap-x-2 gap-y-1 text-xs">
        <span className="text-[10px] text-gray-500 dark:text-gray-400" />
        <span className="text-right text-[10px] text-gray-500 dark:text-gray-400">ラボ</span>
        <span className="text-right text-[10px] text-gray-500 dark:text-gray-400">現行</span>
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
      <span className="text-gray-600 dark:text-gray-400">{label}</span>
      <span className={`text-right ${win ? "font-semibold text-emerald-700" : ""}`}>{a}</span>
      <span className="text-right text-gray-500 dark:text-gray-400">{b}</span>
    </>
  );
}

function PickCard({ p }: { p: TypeLabPick }) {
  const settled = p.settled;
  const tone = !settled ? "border-gray-200 dark:border-gray-700"
    : p.hit ? ((p.payout ?? 0) >= p.budget ? "border-emerald-400" : "border-amber-400")
    : "border-gray-200 opacity-70 dark:border-gray-700";
  return (
    <div className={`rounded border-2 bg-white p-2 dark:bg-gray-900 sm:p-3 ${tone}`}>
      {/* 1行目: レース */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs sm:text-sm">
        {/* 発走時刻。一覧は**発走の早い順**に並んでいる（サーバー側で並べ替え済み）。 */}
        <span className="rounded bg-gray-900 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-white dark:bg-gray-700">
          {p.start_time ?? "--:--"}
        </span>
        <span className="font-semibold text-gray-900 dark:text-white">{p.venue_name ?? "—"} {p.race_no ?? "?"}R</span>
        <span className="text-gray-600 dark:text-gray-400">{p.race_date}</span>
        <span className="text-[10px] text-gray-600 dark:text-gray-400 sm:text-xs">{p.race_type ?? ""}</span>
        <span className="rounded bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 text-[10px]">
          型{p.type_label}{TYPE_NAME[p.type_label] ? ` ${TYPE_NAME[p.type_label]}` : ""}
        </span>
        <span className="rounded bg-indigo-100 dark:bg-indigo-900 px-1.5 py-0.5 font-mono text-[10px] text-indigo-800">
          {p.plan_key}
        </span>
      </div>
      {/* 2行目: 商品と結果（モバイルでは折り返す） */}
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-600 dark:text-gray-400">
        <span className="text-gray-800 dark:text-gray-200">{p.bet_type === "trio" ? "三連複" : "三連単"} {p.n_legs}点</span>
        <span>想定平均 {yen(p.pred_mean_payout)}</span>
        <span className="ml-auto">
          {settled
            ? (p.hit
                ? <b className={(p.payout ?? 0) >= p.budget ? "text-emerald-700" : "text-amber-700"}>
                    的中 {yen(p.payout)}（{p.win_combo} / {p.final_odds?.toFixed(1)}倍）
                    {(p.payout ?? 0) < p.budget ? " ※ガミ" : ""}
                  </b>
                : <span className="text-gray-500 dark:text-gray-400">不的中（{p.win_combo}）</span>)
            : <span className="text-gray-500 dark:text-gray-400">未確定</span>}
        </span>
      </div>
      {/* 決着の中身。事前の型（左）に対して実際がどう決まったか（右）。 */}
      {settled && p.finish_class && (
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
          <span className={`rounded px-1.5 py-0.5 font-semibold ${
            FINISH_TONE[p.finish_class] ?? "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200"}`}>
            決着 {FINISH_LABEL[p.finish_class] ?? p.finish_class}
          </span>
          {p.win_tf_odds != null && (
            <span className="text-gray-600 dark:text-gray-400">
              三連単 {p.win_tf_odds.toFixed(1)}倍
            </span>
          )}
        </div>
      )}
      {p.current && (
        <div className="mt-1.5 rounded bg-gray-50 dark:bg-gray-800 px-2 py-1 text-[10px] leading-relaxed text-gray-700 dark:text-gray-300">
          <span className="font-semibold">現行</span>
          <span className="ml-1.5 font-mono">{p.current.rank.replace("RANK_", "")}</span>
          {p.current.n_combos ? <span className="ml-1 text-gray-500 dark:text-gray-400">{p.current.n_combos}点</span> : null}
          {p.current.settled
            ? (p.current.hit
                ? <span className="ml-1.5 text-emerald-700">的中 {yen(p.current.payout)}</span>
                : <span className="ml-1.5 text-gray-500 dark:text-gray-400">不的中</span>)
            : <span className="ml-1.5 text-gray-500 dark:text-gray-400">未採点</span>}
          {p.current.sold_rank_key
            ? <span className="ml-1.5 rounded bg-indigo-100 dark:bg-indigo-900 px-1 text-indigo-700">入稿 {p.current.sold_rank_key}</span>
            : <span className="ml-1.5 text-gray-500 dark:text-gray-400">（入稿なし）</span>}
          <div className="mt-0.5 break-all font-mono text-gray-600 dark:text-gray-400">{p.current.pred_combo ?? "—"}</div>
        </div>
      )}
      {/* 買い目 */}
      <div className="mt-1.5 flex flex-wrap gap-1">
        {p.legs.map((l) => (
          <span key={l.combo}
                className={`rounded px-1.5 py-0.5 font-mono text-[10px] leading-tight sm:text-xs ${
                  settled && p.win_combo === l.combo
                    ? "bg-emerald-600 text-white" : "bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200"}`}>
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

/** 組み合わせに入れるプランのチェック（複数選択ボタン）。 */
function PlanCheck({ plan, checked, onClick }: {
  plan: string; checked: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button" onClick={onClick} role="checkbox" aria-checked={checked}
      title={PLAN_NOTE[plan] ?? plan}
      className={`shrink-0 rounded-full border px-2.5 py-1 font-mono text-xs transition-colors ${
        checked
          ? "border-indigo-600 bg-indigo-600 font-semibold text-white"
          : "border-gray-300 bg-white text-gray-700 hover:border-indigo-400 hover:text-indigo-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:border-indigo-500 dark:hover:text-indigo-300"
      }`}
    >
      {checked ? "✓ " : ""}{plan}
    </button>
  );
}

/** 組み合わせ表の1行。合計行だけ強調する。 */
function ComboTr({ r, total = false, nDays }: {
  r: TypeLabComboRow; total?: boolean; nDays?: number;
}) {
  const perDay = nDays && nDays > 0 ? r.n_races / nDays : null;
  return (
    <tr className={`border-t border-gray-200 dark:border-gray-700 ${
      total ? "bg-indigo-50 font-semibold dark:bg-indigo-950" : ""}`}>
      <td className="p-2 text-gray-900 dark:text-gray-100">
        {total ? (
          <>
            合計
            {perDay != null && (
              <span className="ml-1 text-[10px] font-normal text-gray-600 dark:text-gray-400">
                {perDay.toFixed(1)}件/日
              </span>
            )}
          </>
        ) : <span className="font-mono">{r.plan_key}</span>}
      </td>
      <td className="p-2 text-right whitespace-nowrap text-gray-900 dark:text-gray-100">
        {r.n_races}
        <span className="ml-1 text-[10px] font-normal text-gray-600 dark:text-gray-400">
          ({r.n_settled})
        </span>
      </td>
      <td className="p-2 text-right whitespace-nowrap text-gray-900 dark:text-gray-100">
        {r.n_hit}
        <span className="ml-1 text-[10px] font-normal text-gray-600 dark:text-gray-400">
          (表示 {r.n_shown_hit})
        </span>
      </td>
      <td className="p-2 text-right whitespace-nowrap text-gray-900 dark:text-gray-100">
        {yen(r.returned)}
        <span className="ml-1 text-[10px] font-normal text-gray-600 dark:text-gray-400">
          / 投資 {yen(r.invested)}
        </span>
      </td>
      <td className="p-2 text-right whitespace-nowrap text-gray-700 dark:text-gray-300">
        {r.roi.toFixed(1)}%
      </td>
    </tr>
  );
}
