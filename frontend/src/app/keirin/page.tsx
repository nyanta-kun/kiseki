"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { Bike, HelpCircle, ChevronDown, ChevronUp, BarChart2 } from "lucide-react";
import { fetchKeirinPicks, fetchKeirinSummary, refreshKeirinPicks, triggerKeirinFetchOdds, triggerKeirinFetchResults, type KeirinPick, type KeirinSummary } from "@/lib/api";
import { todayYYYYMMDD } from "@/lib/utils";

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

// ガミ足切り閾値（keirin側と揃える）: レース単位 min(全目)≥7.0（2026-07-10 SS/S→R置き換え）
const GAMI_THRESHOLD = 7.0;

// Number.prototype.toFixed は浮動小数点誤差で四捨五入に失敗することがある
// （例: (15.45).toFixed(1) === "15.4"。15.45は内部的に15.449999...として
// 保持されるため）。目安として表示する合成オッズ等はEpsilon補正した
// Math.roundで先に小数第1位に丸めてからtoFixedする。
function formatRoundHalfUp(value: number, decimals = 1): string {
  const factor = 10 ** decimals;
  const rounded = Math.round((value + Number.EPSILON) * factor) / factor;
  return rounded.toFixed(decimals);
}

// pred_win_pct/pred_top3_pct（選手ごと独立モデルの生確率）をレース内合計が
// 一定値になるよう補正するためのロジット空間シフト。個々の確率は0〜1に留まる
// （sigmoidの値域による）ため、単純な比例配分と違い100%への頭打ちが起きにくい。
function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-x));
}
function logit(p: number): number {
  const eps = 1e-6;
  const c = Math.min(Math.max(p, eps), 1 - eps);
  return Math.log(c / (1 - c));
}
// probs（0〜1の生確率配列）に対し、Σ sigmoid(logit(p_i)+shift) = target となる
// shift を二分探索で求める。target は 0 < target < probs.length である必要がある。
function solveLogitShift(probs: number[], target: number): number {
  let lo = -50;
  let hi = 50;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    const sum = probs.reduce((s, p) => s + sigmoid(logit(p) + mid), 0);
    if (sum < target) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

// 候補ランク判定閾値（keirin側 notify_prerace_wt.py の定数と揃える）
// S1(7PLUS_R・旧称SS): gap12≥0.10 ∧ gap23≥1pt ∧ 三連複min≥7（オッズ条件は発走前確定）
// ※ 2026-07-16 ランク名称整理: SS→S1 / U→S2 / M→S3・A 新設（内部rankコードは不変）
// ※ S/S+（三連単F 7PLUS_ST/STP）は優位性なしのため 2026-07-15 に全廃（過去行の履歴表示のみ残す）
const SS_GAP12 = 0.10;
const SS_GAP23_PT = 1.0;

type CandRank = "S1";

// 候補(7PLUS_CAND)が指数条件上なり得るランク。オッズ条件（三連複min≥7）は
// 発走前まで未確定のため、ここでは gap 条件のみで可能性を判定する。
// gap 未取得（過去日等）は空を返す。
function candPossibleRanks(pick: KeirinPick): CandRank[] {
  if (pick.rank !== "7PLUS_CAND" || pick.gap12 == null) return [];
  const ranks: CandRank[] = [];
  if (pick.gap12 >= SS_GAP12 && (pick.gap23 == null || pick.gap23 >= SS_GAP23_PT)) {
    ranks.push("S1");
  }
  return ranks;
}

// 候補の pred_combo「p1-p2-t1,t2,..」をパースする（三連複フォーメーション）
function parseCandCombo(pred: string | null): { p1: string; p2: string; thirds: string[] } | null {
  if (!pred || pred.includes(":")) return null;
  const parts = pred.split("-");
  if (parts.length < 3) return null;
  const thirds = parts[2].split(",").filter(Boolean);
  if (!parts[0] || !parts[1] || thirds.length === 0) return null;
  return { p1: parts[0], p2: parts[1], thirds };
}

// ガミ落ち = オッズ条件（三連複 <閾値倍）で購入不成立になった候補。
// 未購入行は採点で全て miwokuri=TRUE になるため（2026-07-08 正本化）、
// 見送り行は prerace_gami<閾値 を「ガミ落ち」として灰色の見送りと区別する。
// 購入済み R(S1) は全目min≥閾値が購入条件のため prerace_gami<閾値 にならない（書込時不変条件）。
// ペーパー検証ランク（S2/S3）はガミ閾値と無関係のため対象外だった（2026-07-21全廃で消滅）。
// （同一レースのS1系判定で prerace_gami が書き込まれ得るが、ガミ落ち扱いにしない）。
function computeGamiSkip(pick: KeirinPick): boolean {
  const pgBelow = pick.prerace_gami != null && pick.prerace_gami < GAMI_THRESHOLD;
  return pgBelow && (pick.miwokuri || pick.rank !== "7PLUS_R");
}

function fmtYMD(yyyymmdd: string): string {
  if (yyyymmdd.length !== 8) return yyyymmdd;
  return `${yyyymmdd.slice(0, 4)}/${yyyymmdd.slice(4, 6)}/${yyyymmdd.slice(6, 8)}`;
}

function toISODate(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

function prevDay(yyyymmdd: string): string {
  const d = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`);
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

function nextDay(yyyymmdd: string): string {
  const d = new Date(`${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`);
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

// 月移動（月末日超過は移動先の月末にクランプ。例: 3/31 → 2/28）
function addMonths(yyyymmdd: string, delta: number): string {
  const y = parseInt(yyyymmdd.slice(0, 4), 10);
  const m = parseInt(yyyymmdd.slice(4, 6), 10) - 1;
  const day = parseInt(yyyymmdd.slice(6, 8), 10);
  const lastDay = new Date(Date.UTC(y, m + delta + 1, 0)).getUTCDate();
  const d = new Date(Date.UTC(y, m + delta, Math.min(day, lastDay)));
  return d.toISOString().slice(0, 10).replace(/-/g, "");
}

// 未来日は今日にクランプ（YYYYMMDD は文字列比較で大小判定可能）
function clampToToday(yyyymmdd: string): string {
  const today = todayYYYYMMDD();
  return yyyymmdd > today ? today : yyyymmdd;
}

function formatROI(roi: number | null): string {
  if (roi == null) return "—";
  return (roi * 100).toFixed(1) + "%";
}

function fmtStartAt(startAt: number | string | null): string | null {
  if (startAt == null) return null;
  const ts = typeof startAt === "number" ? startAt : parseInt(String(startAt), 10);
  if (isNaN(ts)) return null;
  return new Date(ts * 1000).toLocaleTimeString("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

// 現行ランク体系（2026-07-21〜: S1/SS/S の3ペーパーランク）。
// S1は新設計（win軸1着固定×3着内モデル相手2車・SEVEN_S1）で2026-07-19導入。
// SS/Sは単勝×複勝指数トップ3重なり軸×波乱度選出（三連複2軸総流し・SEVEN_S4）を
// WINTICKET公式◎◯との軸重なり数で分割した表示ランク（2026-07-21導入）:
//   軸2車がWT◎◯と全く重ならない → SS（全期間 対象943R・的中率39.4%・ROI232.8%）
//   軸2車の片方だけがWT◎◯と重なる → S（全期間 対象8,984R・的中率36.0%・ROI120.6%）
// 内部rankカラムはSS/Sいずれも SEVEN_S4 のまま。区別は gate_label(SS|S) で行い、
// バックエンドが計算した pick.display_rank(S1|SS|S) をそのままキーに使う。
// S2(7PLUS_U)/S3(7PLUS_M)は対象レース数・的中率・期待値の観点で継続困難と判断し
// 2026-07-21 全廃（行は picks_history_u_archive / _m_archive へ退避済み・新規生成なし）。
// 旧新S1（SIX_S1・6車三連単）と A（7PLUS_A・一致波乱二連単）は正規プロトコル再検証で
// 不合格のため 2026-07-17 全廃（行は picks_history_r_archive / _a_archive へ退避済み）。
// 旧S1(7PLUS_R・7車三連複)・#CAND書き込みは 2026-07-16 全廃・S/S+（7PLUS_ST/STP）は 2026-07-15 全廃
// （行は picks_history_r_archive へ退避済み）。
// 旧方式(7PLUS_SS/7PLUS_S・素のSS/S/A/B/WIDE)の行は全期間再構築済み or route='ks' で API に現れない。
// 未知 rank は RankBadge が「非」フォールバック表示する。
const RANK_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  // S1=win軸1着固定×3着内モデル相手2車（2026-07-19新設計）
  "S1":         { bg: "#ea580c", text: "#fff", label: "S1" },
  // SS+=SS内、軸2車の級班に各グレード最上位(S1/A1)を含まない観察用サブランク（2026-07-23追加）
  "SS+":        { bg: "#a16207", text: "#fff", label: "SS+" },
  // SS=SEVEN_S4のうち軸2車がWT◎◯と全く重ならない選出（2026-07-21再編）
  "SS":         { bg: "#ca8a04", text: "#fff", label: "SS" },
  // S=SEVEN_S4のうち軸2車の片方だけがWT◎◯と重なる選出（2026-07-21再編）
  "S":          { bg: "#16a34a", text: "#fff", label: "S" },
  "7PLUS_CAND": { bg: "#9ca3af", text: "#fff", label: "候補" },
};

// ---------------------------------------------------------------------------
// サブコンポーネント
// ---------------------------------------------------------------------------

// 候補ランクチップ（該当し得るランクの表示。RANK_STYLE と同系色のアウトライン表示）
const CAND_RANK_CHIP_STYLE: Record<CandRank, string> = {
  "S1": "border-amber-500 text-amber-600 dark:text-amber-400",
};

function CandRankChip({ rank }: { rank: CandRank }) {
  return (
    <span className={`inline-flex items-center justify-center min-w-5 px-1 h-4 rounded border text-[10px] font-bold flex-shrink-0 ${CAND_RANK_CHIP_STYLE[rank]}`}>
      {rank}
    </span>
  );
}

// 候補行のランク別買い目（S1=三連複全目）
function CandBuyLines({ ranks, combo }: { ranks: CandRank[]; combo: { p1: string; p2: string; thirds: string[] } }) {
  return (
    <div className="flex-1 min-w-0 space-y-0.5">
      {ranks.includes("S1") && (
        <div className="flex items-center gap-1.5 text-xs sm:text-sm font-medium text-gray-700 dark:text-gray-200">
          <CandRankChip rank="S1" />
          <span className="break-words min-w-0">
            3連複: {combo.p1}-{combo.p2}-{combo.thirds.join(",")} ({combo.thirds.length}点)
          </span>
        </div>
      )}
    </div>
  );
}

function RankBadge({ rank, purchased }: { rank: string; purchased?: boolean }) {
  const badgeCls = "inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0";

  // 購入対象（買い目確定・ペーパーは記録確定）になったら緑の○で囲う。
  // 全ランク統一（SS/S=15分前判定 buy 成立）。
  const ringStyle: React.CSSProperties | undefined = purchased
    ? { outline: "2px solid #10b981", outlineOffset: "2px" }
    : undefined;

  // 見送り行も常に元表示。見送り理由は右側の「見送り」「ガミ落ち」表示で判別する。
  const s = RANK_STYLE[rank];
  if (!s) {
    return (
      <span style={{ background: "#9ca3af", color: "#fff", ...ringStyle }} className={badgeCls}>
        非
      </span>
    );
  }
  return (
    <span style={{ background: s.bg, color: s.text, ...ringStyle }} className={badgeCls}>
      {s.label}
    </span>
  );
}

function PayoutInfo({ trio, trifecta }: { trio: number; trifecta?: number }) {
  if (trio <= 0 && (trifecta ?? 0) <= 0) {
    return <span className="text-xs text-gray-400 dark:text-gray-500 flex-shrink-0">払戻 —</span>;
  }
  return (
    <span className="text-xs text-gray-500 dark:text-gray-400 tabular-nums flex-shrink-0">
      {trio > 0 && (
        <>三連複 <span className="font-semibold text-gray-700 dark:text-gray-200">¥{trio.toLocaleString()}</span></>
      )}
      {(trifecta ?? 0) > 0 && (
        <>{trio > 0 && <span className="mx-1 text-gray-300 dark:text-gray-600">|</span>}三連単 <span className="font-semibold text-gray-700 dark:text-gray-200">¥{(trifecta ?? 0).toLocaleString()}</span></>
      )}
    </span>
  );
}

function HitBadge({ hit, payout, trioPayout, trifectaPayout, bet, isSettled, isReference, isMiwokuri, isGamiSkip }: {
  hit: boolean; payout: number; trioPayout: number; trifectaPayout?: number; bet: number; isSettled: boolean; isReference?: boolean; isMiwokuri?: boolean; isGamiSkip?: boolean;
}) {
  if (isGamiSkip) {
    if (!isSettled) return <span className="text-xs text-orange-400 dark:text-orange-500">ガミ落ち</span>;
    return (
      <div className="flex items-center justify-between w-full gap-2">
        <span className="text-xs text-orange-400 dark:text-orange-500">ガミ条件落ち</span>
        <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
      </div>
    );
  }

  if (isMiwokuri) {
    if (!isSettled) return <span className="text-xs text-gray-400">未確定</span>;
    return (
      <div className="flex items-center justify-between w-full gap-2">
        {hit ? (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-purple-50 text-purple-600 border border-purple-200">
            見送り 的中
          </span>
        ) : (
          <span className="text-xs text-gray-400 dark:text-gray-500">見送り</span>
        )}
        <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
      </div>
    );
  }

  if (isReference) {
    return (
      <div className="flex items-center justify-between w-full gap-2">
        <span className="text-xs text-gray-400 dark:text-gray-500">参考</span>
        <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
      </div>
    );
  }

  // 購入済みレース
  if (hit) {
    const isGami = bet > 0 && payout < bet;
    return (
      <div className="flex items-center justify-between w-full gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          {isGami ? (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-orange-100 text-orange-700 border border-orange-300">
              ガ 的中
            </span>
          ) : (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-700 border border-emerald-300">
              ✓ 的中
            </span>
          )}
          <span className="text-xs text-gray-600">
            {bet > 0 && <>¥{bet.toLocaleString()} → </>}
            <span className={`font-semibold ${isGami ? "text-orange-600" : "text-emerald-600"}`}>¥{payout.toLocaleString()}</span>
            {bet > 0 && <span className="text-gray-400 ml-1">({(payout / bet).toFixed(1)}倍)</span>}
          </span>
        </div>
        {trioPayout > 0 && <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />}
      </div>
    );
  }
  if (!isSettled) {
    return <span className="text-xs text-gray-400">未確定</span>;
  }
  return (
    <div className="flex items-center justify-between w-full gap-2">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold bg-red-50 text-red-600 border border-red-200">
          ✗ 不的中
        </span>
        {bet > 0 && <span className="text-xs text-gray-400">¥{bet.toLocaleString()}</span>}
      </div>
      <PayoutInfo trio={trioPayout} trifecta={trifectaPayout} />
    </div>
  );
}


function EntryTable({ entries }: { entries: KeirinPick["entries"] }) {
  if (!entries.length) return <p className="text-xs text-gray-400 dark:text-gray-500 px-3 py-2">出走情報なし</p>;
  const sorted = [...entries].sort((a, b) => (b.race_point ?? -Infinity) - (a.race_point ?? -Infinity));
  // pred_win_pct/pred_top3_pct は選手ごと独立モデルの生確率でレース内合計の保証がない
  // （実例: 単勝合計9.7%・複勝合計43.9%等）。単純な比例配分（線形スケール）だと
  // 必要な補正倍率が大きく(例: 複勝は約6.8倍)個々の値が100%を超えて頭打ちが頻発するため、
  // ロジット(対数オッズ)空間で一律シフトしてからシグモイドで戻す方式でレース内合計を
  // 単勝=100%・複勝=min(出走数,3)*100%に補正する。シグモイドの性質上100%は超えない。
  const winProbs = entries.map((e) => (e.pred_win_pct ?? 0) / 100);
  const top3Probs = entries.map((e) => (e.pred_top3_pct ?? 0) / 100);
  const winShift = winProbs.some((p) => p > 0) ? solveLogitShift(winProbs, 1) : null;
  const top3Shift = top3Probs.some((p) => p > 0)
    ? solveLogitShift(top3Probs, Math.min(entries.length, 3))
    : null;
  const normWin = (v: number | null) =>
    v != null && winShift != null ? 100 * sigmoid(logit(v / 100) + winShift) : null;
  const normTop3 = (v: number | null) =>
    v != null && top3Shift != null ? 100 * sigmoid(logit(v / 100) + top3Shift) : null;
  return (
    <table className="w-full">
      <thead>
        <tr className="border-b border-gray-100 dark:border-gray-700">
          <th className="text-center px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-7 sm:w-8">車</th>
          <th className="text-left px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs">選手名</th>
          <th className="text-center px-1 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-9 sm:w-12">戦法</th>
          <th className="text-right px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14 whitespace-nowrap">単勝指数</th>
          <th className="text-right px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14 whitespace-nowrap">複勝指数</th>
          <th className="text-right px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14 whitespace-nowrap">指数</th>
          <th className="text-center px-1 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-8 sm:w-10">着</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((e) => (
          <tr key={e.frame_no} className="border-b border-gray-50 dark:border-gray-700 last:border-0">
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 font-bold text-center text-xs sm:text-sm text-gray-700 dark:text-gray-200">{e.frame_no}</td>
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 text-xs sm:text-sm text-gray-800 dark:text-gray-100">{e.name ?? "—"}</td>
            <td className="px-1 sm:px-3 py-1 sm:py-1.5 text-center text-gray-500 dark:text-gray-400 text-xs">{e.style ?? "—"}</td>
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 text-right font-mono text-xs sm:text-sm text-gray-700 dark:text-gray-200">
              {normWin(e.pred_win_pct) != null ? `${normWin(e.pred_win_pct)!.toFixed(1)}%` : "—"}
            </td>
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 text-right font-mono text-xs sm:text-sm text-gray-700 dark:text-gray-200">
              {normTop3(e.pred_top3_pct) != null ? `${normTop3(e.pred_top3_pct)!.toFixed(1)}%` : "—"}
            </td>
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 text-right font-mono text-xs sm:text-sm text-gray-700 dark:text-gray-200">
              {e.race_point != null ? e.race_point.toFixed(1) : "—"}
            </td>
            <td className="px-1 sm:px-3 py-1 sm:py-1.5 text-center">
              {e.finish_order != null && e.finish_order > 0 ? (
                <span
                  className={`inline-flex items-center justify-center w-5 h-5 sm:w-6 sm:h-6 rounded-full text-xs font-bold
                    ${e.finish_order === 1 ? "bg-amber-400 text-white" :
                      e.finish_order <= 3 ? "bg-blue-100 text-blue-700" : "text-gray-400"}`}
                >
                  {e.finish_order}
                </span>
              ) : e.finish_order === 0 ? (
                <span className="text-xs text-gray-400">失</span>
              ) : (
                <span className="text-gray-300">—</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// コンポーネント外に置くことで react-hooks/purity を回避
function computeIsSettled(status: number, startAt: number | string | null): boolean {
  if (status === 3) return true;
  const sec = typeof startAt === "number" ? startAt : parseInt(String(startAt ?? ""), 10);
  // VPS同期遅延を考慮し発走から90分後も確定とみなす
  return !isNaN(sec) && sec + 5400 < Date.now() / 1000;
}

function CollapsedResult({ hit, payout, trioPayout, trifectaPayout, bet, isPurchased, isMiwokuri, isGamiSkip }: {
  hit: boolean; payout: number; trioPayout: number; trifectaPayout?: number; bet: number; isPurchased: boolean; isMiwokuri: boolean; isGamiSkip?: boolean;
}) {
  const tp = trifectaPayout ?? 0;
  const trioEl = (trioPayout > 0 || tp > 0)
    ? (
      <span className="text-xs text-gray-400 dark:text-gray-500 tabular-nums">
        {trioPayout > 0 && <>複¥{trioPayout.toLocaleString()}</>}
        {tp > 0 && <>{trioPayout > 0 && " "}単¥{tp.toLocaleString()}</>}
      </span>
    )
    : null;

  if (isGamiSkip) {
    const label = <span className="text-xs text-orange-400 dark:text-orange-500">ガミ落ち</span>;
    if (!trioEl) return label;
    return <div className="flex items-center gap-1.5 flex-shrink-0">{label}{trioEl}</div>;
  }

  if (isMiwokuri) {
    const label = hit
      ? <span className="text-xs text-purple-500 font-semibold">見送 的中</span>
      : <span className="text-xs text-gray-400 dark:text-gray-500">見送り</span>;
    if (!trioEl) return label;
    return <div className="flex items-center gap-1.5 flex-shrink-0">{label}{trioEl}</div>;
  }

  if (isPurchased) {
    if (hit) {
      const isGami = bet > 0 && payout < bet;
      const hitEl = (
        <span className={`text-xs font-semibold ${isGami ? "text-orange-500" : "text-emerald-600 dark:text-emerald-400"}`}>
          ✓ ¥{payout.toLocaleString()}
        </span>
      );
      if (!trioEl) return hitEl;
      return <div className="flex items-center gap-1.5 flex-shrink-0">{hitEl}{trioEl}</div>;
    }
    const missEl = <span className="text-xs text-red-500 font-semibold">✗</span>;
    if (!trioEl) return missEl;
    return <div className="flex items-center gap-1.5 flex-shrink-0">{missEl}{trioEl}</div>;
  }

  return trioEl;
}

function NoPickRow({ pick }: { pick: KeirinPick }) {
  const [collapsed, setCollapsed] = useState(true);
  const startTime = fmtStartAt(pick.start_at);
  const isSettled = computeIsSettled(pick.status, pick.start_at);
  const hasPayout = pick.trio_payout > 0 || (pick.trifecta_payout ?? 0) > 0;
  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden opacity-75">
      <button
        type="button"
        onClick={() => setCollapsed(v => !v)}
        className={`w-full flex items-center gap-2 px-3 sm:px-4 py-2 bg-gray-50 dark:bg-gray-800 text-left${collapsed ? "" : " border-b border-gray-100 dark:border-gray-700"}`}
      >
        <span className="inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0 bg-gray-200 dark:bg-gray-700 text-gray-400 dark:text-gray-500">—</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-1.5 sm:gap-2 flex-wrap">
            <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{pick.venue_name}</span>
            <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{pick.race_no}R</span>
            {startTime && <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{startTime}</span>}
            {(pick.grade || pick.race_type) && (
              <span className="text-gray-400 dark:text-gray-500 text-xs">{pick.grade ?? ""} {pick.race_type ?? ""}</span>
            )}
          </div>
        </div>
        {/* 確定後は折りたたみ時も払戻をインライン表示（推奨外レースの結果確認用） */}
        {collapsed && isSettled && hasPayout && (
          <span className="text-xs text-gray-400 dark:text-gray-500 tabular-nums flex-shrink-0">
            {pick.trio_payout > 0 && <>複¥{pick.trio_payout.toLocaleString()}</>}
            {(pick.trifecta_payout ?? 0) > 0 && <>{pick.trio_payout > 0 && " "}単¥{(pick.trifecta_payout ?? 0).toLocaleString()}</>}
          </span>
        )}
        <span className="text-[10px] text-gray-300 dark:text-gray-600 flex-shrink-0 mr-1">推奨外</span>
        <ChevronDown
          size={15}
          className={`flex-shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-150${collapsed ? "" : " rotate-180"}`}
        />
      </button>
      {!collapsed && (
        <>
          <EntryTable entries={pick.entries} />
          {isSettled && (
            <div className="px-3 sm:px-4 py-2 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex items-center justify-between gap-2">
              <span className="text-xs text-gray-400 dark:text-gray-500">推奨外</span>
              <PayoutInfo trio={pick.trio_payout} trifecta={pick.trifecta_payout} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function PickCard({ pick, cardId }: { pick: KeirinPick; cardId?: string }) {
  const isSettled = computeIsSettled(pick.status, pick.start_at);
  const [collapsed, setCollapsed] = useState(true);
  const isMiwokuri = pick.miwokuri;
  const isPurchased = !isMiwokuri && pick.bet_amount > 0;
  const gamiThr = GAMI_THRESHOLD;
  const isGamiSkip = computeGamiSkip(pick);
  // ペーパー検証ランク（S1/SS/S。2026-07-21にS2/S3全廃）は旧S1の三連複ガミ閾値と無関係のため
  // ガミ判定チップ（✓/⚠）を表示しない
  const isPaperRank = pick.rank === "SEVEN_S1" || pick.rank === "SEVEN_S4";
  const gamiStatus: "ok" | "ng" | null = !isPaperRank && pick.prerace_gami != null && (!isMiwokuri || isGamiSkip)
    ? pick.prerace_gami >= gamiThr ? "ok" : "ng"
    : null;

  const rankStr = pick.rank ?? "";
  // 候補行: 指数条件上なり得るランク（S1）と買い目（三連複全目）。
  // ガミ落ち確定行は「ガミ落ち」表示を優先し候補ランクは出さない。
  const candRanks = isGamiSkip ? [] : candPossibleRanks(pick);
  const candCombo = candRanks.length > 0 ? parseCandCombo(pick.pred_combo) : null;
  // バッジ表示はランク名を直接出す（2026-07-16に導入・2026-07-21にSS/S再編でAPI側の
  // display_rank(S1/SS/S)をそのまま使う方式へ変更）:
  // S1候補（7PLUS_CAND ∧ gap条件成立）は「候補」ではなく S1 バッジで表示し、
  // 直前オッズ判定で購入対象になったら緑○で囲う（RankBadge purchased）。
  const badgeRank = rankStr === "7PLUS_CAND" && candRanks.includes("S1")
    ? "7PLUS_R"
    : (pick.display_rank ?? rankStr);
  // 購入対象判定: 採点済みは bet_amount>0。当日の S1 買い成立は #CAND 行の
  // rank が 7PLUS_R に昇格した時点（bet_amount は翌朝採点まで 0 のため）。
  const isBuyConfirmed = !isMiwokuri && !isGamiSkip && (pick.bet_amount > 0 || rankStr === "7PLUS_R");
  // 券種ラベル: S1（win軸新設計）のみ三連単、SS/S（SEVEN_S4）は三連複
  const betTypeLabel = rankStr === "SEVEN_S1" ? "3連単" : "3連複";
  const comboLabel = pick.pred_combo
    ? `${betTypeLabel}: ${pick.pred_combo}${pick.n_combos && pick.n_combos > 1 ? ` (${pick.n_combos}点)` : ""}`
    : undefined;

  const startTime = fmtStartAt(pick.start_at);

  return (
    <div id={cardId} className={`bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden${isMiwokuri || isGamiSkip ? " opacity-55" : ""}`}>
      {/* ヘッダー行（クリックで折りたたみトグル） */}
      <button
        type="button"
        onClick={() => setCollapsed(v => !v)}
        className={`w-full flex items-center gap-2 px-3 sm:px-4 py-2 bg-gray-50 dark:bg-gray-800 text-left${collapsed ? "" : " border-b border-gray-100 dark:border-gray-700"}`}
      >
        {/* 左バッジ = display_rank(S1/SS/S)の直接表示（全ランク統一）。購入対象は緑○で囲う */}
        <RankBadge rank={badgeRank} purchased={isBuyConfirmed} />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-1.5 sm:gap-2 flex-wrap">
            <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{pick.venue_name}</span>
            <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{pick.race_no}R</span>
            {startTime && (
              <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{startTime}</span>
            )}
            {(pick.grade || pick.race_type) && (
              <span className="text-gray-500 dark:text-gray-400 text-xs">{pick.grade ?? ""} {pick.race_type ?? ""}</span>
            )}
          </div>
        </div>
        {/* 折りたたみ時: 結果サマリー or オッズ（最低=ガミ判定値・合成）をインライン表示 */}
        {collapsed && isSettled && (
          <CollapsedResult hit={pick.hit} payout={pick.payout} trioPayout={pick.trio_payout} trifectaPayout={pick.trifecta_payout} bet={pick.bet_amount} isPurchased={isPurchased} isMiwokuri={isMiwokuri} isGamiSkip={isGamiSkip} />
        )}
        {collapsed && !isSettled && (gamiStatus != null || (pick.synth_odds != null && !isMiwokuri)) && (
          <span className="text-xs flex items-center gap-1.5 flex-shrink-0 tabular-nums">
            {gamiStatus === "ok" && (
              <span className="text-emerald-600 dark:text-emerald-400 font-medium">
                最低{pick.prerace_gami!.toFixed(1)}✓
              </span>
            )}
            {gamiStatus === "ng" && (
              <span className="text-orange-500 dark:text-orange-400 font-medium">
                最低{pick.prerace_gami!.toFixed(1)}⚠
              </span>
            )}
            {pick.synth_odds != null && !isMiwokuri && (
              <span className="text-gray-500 dark:text-gray-400">
                合成<span className="font-semibold text-gray-700 dark:text-gray-200">{formatRoundHalfUp(pick.synth_odds)}</span>
              </span>
            )}
          </span>
        )}
        <ChevronDown
          size={15}
          className={`flex-shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-150${collapsed ? "" : " rotate-180"}`}
        />
      </button>

      {/* 展開時コンテンツ */}
      {!collapsed && (
        <>
          {/* 買い目行（候補行はランク別買い目・それ以外は確定買い目） */}
          <div className="px-3 sm:px-4 py-1.5 border-b border-gray-50 dark:border-gray-700 flex items-center gap-2 sm:gap-3">
            {candRanks.length > 0 && candCombo ? (
              <CandBuyLines ranks={candRanks} combo={candCombo} />
            ) : (
              <span className="text-xs sm:text-sm font-medium text-gray-700 dark:text-gray-200 flex-1 min-w-0 break-words">
                {comboLabel ?? "—"}
              </span>
            )}
            {pick.synth_odds != null && !isMiwokuri && (
              <span className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">
                合成 <span className="font-semibold text-gray-700 dark:text-gray-200">{formatRoundHalfUp(pick.synth_odds)}</span>倍
              </span>
            )}
            {pick.gap23 != null && !isMiwokuri && (
              <span className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">
                {/* gap23 は DB 格納時点で pt スケール（gap12/gap34 と異なり ×100 済み） */}
                g23 <span className="font-semibold text-gray-700 dark:text-gray-200">{pick.gap23.toFixed(1)}</span>pt
              </span>
            )}
            {pick.prerace_gami != null && !isMiwokuri && !isPaperRank && (
              pick.prerace_gami >= gamiThr ? (
                <span className="text-xs flex-shrink-0 text-emerald-600 dark:text-emerald-400 font-medium">
                  最低 {pick.prerace_gami.toFixed(1)}倍✓
                </span>
              ) : (
                <span className="text-xs flex-shrink-0 text-orange-500 dark:text-orange-400 font-medium">
                  最低 {pick.prerace_gami.toFixed(1)}倍⚠
                </span>
              )
            )}
          </div>

          <EntryTable entries={pick.entries} />

          {(isSettled || pick.hit) && (
            <div className="px-3 sm:px-4 py-2 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
              <HitBadge
                hit={pick.hit}
                payout={pick.payout}
                trioPayout={pick.trio_payout}
                trifectaPayout={pick.trifecta_payout}
                bet={pick.bet_amount}
                isSettled={isSettled}
                isReference={!isPurchased && !isMiwokuri && !isGamiSkip}
                isMiwokuri={isMiwokuri}
                isGamiSkip={isGamiSkip}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

type PeriodData = KeirinSummary["today"];
type RankStats = NonNullable<PeriodData["by_rank"]>[string];

// by_rank キー: "S1"=win軸新設計 / "SS+"=SEVEN_S4のうちSSでさらに軸2車に各グレード
// 最上位クラス(S1/A1)を含まない観察用サブランク / "SS"=SEVEN_S4のうち軸2車がWT◎◯と
// 全く重ならない選出 / "S"=SEVEN_S4のうち軸2車の片方だけがWT◎◯と重なる選出
// （全てペーパー検証・名目賭金）。
// 2026-07-17 旧新S1(SIX_S1)/A(7PLUS_A) 全廃・2026-07-19 S1(SEVEN_S1)導入・2026-07-21 S4導入
// 2026-07-21 S2(7PLUS_U)/S3(7PLUS_M) 全廃、S4をgate_label(SS/S)でSS/Sの2ランクへ再編
// 2026-07-23 SS内の軸級班denyフィルター通過分をSS+として観察用に追加（買い目は変更なし）
// → トップラインは4ランク（SS+はSSの内訳のため合算には含めない）の名目合算。
const RANK_ORDER = ["SS+", "SS", "S", "S1"] as const;
const RANK_LABEL: Record<string, string> = { S1: "S1", "SS+": "SS+", SS: "SS", S: "S" };
const RANK_BADGE_STYLE: Record<string, string> = {
  S1: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400",
  "SS+": "bg-amber-200 text-amber-800 dark:bg-amber-900/60 dark:text-amber-300",
  SS: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400",
  S: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
};

/** 投資・回収・最大払戻等、モバイルでは既定で隠す列のクラス。showAll時は常時表示。 */
function mobileColClass(showAll: boolean): string {
  return showAll ? "table-cell" : "hidden sm:table-cell";
}

function RankSubRow({ rankKey, data, showAll }: { rankKey: string; data: RankStats; showAll: boolean }) {
  const roiColor = data.roi == null
    ? "text-gray-400"
    : data.roi >= 1.0
      ? "text-emerald-600 font-semibold"
      : "text-red-500";
  const hitRate = data.n_picks > 0
    ? `${((data.n_hits / data.n_picks) * 100).toFixed(0)}%`
    : "—";
  const badgeClass = RANK_BADGE_STYLE[rankKey] ?? "bg-gray-100 text-gray-600";

  return (
    <tr className="border-b border-gray-50 dark:border-gray-800 last:border-0 bg-gray-50/50 dark:bg-gray-800/30">
      <td className="py-1 px-2 sm:px-3">
        <span className="flex items-center gap-1.5 pl-3">
          <span className={`inline-flex items-center justify-center min-w-6 px-1 h-5 rounded text-xs font-bold ${badgeClass}`}>
            {RANK_LABEL[rankKey] ?? rankKey}
          </span>
        </span>
      </td>
      {/* ランク別候補数（指数条件のみ・オッズ条件前） */}
      <td className="py-1 px-1.5 sm:px-3 text-right text-xs text-gray-400 dark:text-gray-500 tabular-nums">
        {data.n_candidates ?? "—"}
      </td>
      <td className="py-1 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums">
        {data.n_picks}
      </td>
      <td className="py-1 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums">
        {data.n_hits}
        <span className="text-gray-400 dark:text-gray-500 ml-0.5">({hitRate})</span>
      </td>
      <td className={`${mobileColClass(showAll)} py-1 px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums`}>
        ¥{data.total_bet.toLocaleString()}
      </td>
      <td className={`${mobileColClass(showAll)} py-1 px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums`}>
        ¥{data.total_payout.toLocaleString()}
      </td>
      <td className={`${mobileColClass(showAll)} py-1 px-3 text-right text-xs text-gray-500 dark:text-gray-400 tabular-nums`}>
        {data.max_payout != null ? `¥${data.max_payout.toLocaleString()}` : "—"}
      </td>
      <td className={`py-1 px-1.5 sm:px-3 text-right text-xs tabular-nums ${roiColor}`}>
        {formatROI(data.roi)}
      </td>
    </tr>
  );
}

function SummaryRow({ label, sub, data, showRanks, showAll }: { label: string; sub?: string; data: PeriodData; showRanks?: boolean; showAll: boolean }) {
  const roiColor = data.roi == null
    ? "text-gray-400"
    : data.roi >= 1.0
      ? "text-emerald-600 font-semibold"
      : "text-red-500";
  const hitRate = data.n_picks > 0
    ? `${((data.n_hits / data.n_picks) * 100).toFixed(0)}%`
    : "—";
  const byRank = data.by_rank ?? {};
  // ランク別展開時は全ランク行を常に表示する（0件でも省略しない・2026-07-16）
  const hasRanks = showRanks;

  return (
    <>
      <tr className="border-b border-gray-100 dark:border-gray-700">
        {/* 期間 */}
        <td className="py-1.5 px-2 sm:px-3 text-xs sm:text-sm text-gray-700 dark:text-gray-200 font-medium">
          {label}
          {sub && <span className="block text-xs text-gray-400 dark:text-gray-500 font-normal">{sub}</span>}
        </td>
        {/* 候補（オッズ条件前の総候補レース数） */}
        <td className="py-1.5 px-1.5 sm:px-3 text-right text-xs sm:text-sm text-gray-400 dark:text-gray-500 tabular-nums">
          {data.n_candidates ?? "—"}
        </td>
        {/* 件数 */}
        <td className="py-1.5 px-1.5 sm:px-3 text-right text-xs sm:text-sm text-gray-700 dark:text-gray-200 tabular-nums">
          {data.n_picks}
        </td>
        {/* 的中 */}
        <td className="py-1.5 px-1.5 sm:px-3 text-right text-xs sm:text-sm text-gray-700 dark:text-gray-200 tabular-nums">
          {data.n_hits}
          <span className="text-xs text-gray-400 dark:text-gray-500 ml-0.5">({hitRate})</span>
        </td>
        {/* 投資・回収・最大払戻: sm以上または「すべて」表示時のみ表示 */}
        <td className={`${mobileColClass(showAll)} py-1.5 px-3 text-right text-sm text-gray-700 dark:text-gray-200 tabular-nums`}>
          ¥{data.total_bet.toLocaleString()}
        </td>
        <td className={`${mobileColClass(showAll)} py-1.5 px-3 text-right text-sm text-gray-700 dark:text-gray-200 tabular-nums`}>
          ¥{data.total_payout.toLocaleString()}
        </td>
        <td className={`${mobileColClass(showAll)} py-1.5 px-3 text-right text-sm text-gray-700 dark:text-gray-200 tabular-nums`}>
          {data.max_payout != null ? `¥${data.max_payout.toLocaleString()}` : "—"}
        </td>
        {/* 回収率 */}
        <td className={`py-1.5 px-1.5 sm:px-3 text-right text-xs sm:text-sm tabular-nums ${roiColor}`}>
          {formatROI(data.roi)}
        </td>
      </tr>
      {hasRanks && RANK_ORDER.map(rk => {
        // 0件のランクもゼロ埋めで表示する（省略しない）
        const rd = byRank[rk] ?? {
          n_picks: 0, n_hits: 0, total_bet: 0, total_payout: 0,
          roi: null, n_candidates: 0, max_payout: null,
        };
        return <RankSubRow key={rk} rankKey={rk} data={rd} showAll={showAll} />;
      })}
    </>
  );
}

function SummaryCard({ summary }: { summary: KeirinSummary }) {
  const [expanded, setExpanded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  return (
    <div className="bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden">
      <div className="px-3 sm:px-4 py-2 border-b border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex items-center gap-1">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-200 flex-1">投資・回収サマリー</h2>
        <button
          onClick={() => setShowAll(v => !v)}
          className={`sm:hidden flex items-center gap-1 text-xs px-1.5 py-0.5 rounded transition-colors ${
            showAll
              ? "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30"
              : "text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400"
          }`}
          aria-label={showAll ? "省略表示に戻す" : "すべての項目を表示"}
        >
          すべて
        </button>
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors px-1.5 py-0.5 rounded"
          aria-label={expanded ? "ランク詳細を閉じる" : "ランク詳細を開く"}
        >
          {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          <span className="hidden sm:inline">{expanded ? "閉じる" : "ランク別"}</span>
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 dark:border-gray-700">
              <th className="py-1.5 px-2 sm:px-3 text-left text-xs text-gray-500 dark:text-gray-400 font-medium">期間</th>
              <th className="py-1.5 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">候補</th>
              <th className="py-1.5 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">件数</th>
              <th className="py-1.5 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">的中</th>
              <th className={`${mobileColClass(showAll)} py-1.5 px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium`}>投資</th>
              <th className={`${mobileColClass(showAll)} py-1.5 px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium`}>回収</th>
              <th className={`${mobileColClass(showAll)} py-1.5 px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium whitespace-nowrap`}>期間最大払戻</th>
              <th className="py-1.5 px-1.5 sm:px-3 text-right text-xs text-gray-500 dark:text-gray-400 font-medium">回収率</th>
            </tr>
          </thead>
          <tbody>
            <SummaryRow label="当日" data={summary.today} showRanks={expanded} showAll={showAll} />
            <SummaryRow label="当月" data={summary.month} showRanks={expanded} showAll={showAll} />
            <SummaryRow label="当年" data={summary.year} showRanks={expanded} showAll={showAll} />
            {/* 検証期間 = 学習に使っていない期間のバックテスト（HOLD・2026-06-30以前で固定）。
                2026-07以降の本番フォワード分は当日/当月/当年サマリー側で表示 */}
            <SummaryRow
              label="検証期間"
              sub={summary.test_from && summary.test_to ? `${summary.test_from}〜${summary.test_to}` : undefined}
              data={summary.test}
              showRanks={expanded}
              showAll={showAll}
            />
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 日付ナビ（前月・前日・今日・日付指定・翌日・翌月）
// ---------------------------------------------------------------------------

const DATE_NAV_BTN_CLS =
  "px-2 sm:px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-600 text-xs sm:text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed text-center whitespace-nowrap flex-shrink-0";

function DateNav({ date, onChange }: { date: string; onChange: (d: string) => void }) {
  const dateInputRef = useRef<HTMLInputElement>(null);
  const isToday = date === todayYYYYMMDD();

  const openPicker = () => {
    const input = dateInputRef.current;
    if (!input) return;
    try { input.showPicker(); } catch { input.click(); }
  };

  return (
    <div className="flex items-center justify-between gap-1 sm:gap-2">
      <button onClick={() => onChange(addMonths(date, -1))} className={DATE_NAV_BTN_CLS} aria-label="前月">
        ≪<span className="hidden sm:inline"> 前月</span>
      </button>
      <button onClick={() => onChange(prevDay(date))} className={DATE_NAV_BTN_CLS} aria-label="前日">
        ←<span className="hidden sm:inline"> 前日</span>
      </button>
      {/* 中央: 今日ボタン（非今日時のみ）+ 日付表示（タップでピッカー） */}
      <div className="flex items-center justify-center gap-1.5 sm:gap-2 flex-1 min-w-0">
        {!isToday && (
          <button
            onClick={() => onChange(todayYYYYMMDD())}
            className="text-[11px] px-1.5 sm:px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors whitespace-nowrap flex-shrink-0"
          >
            今日
          </button>
        )}
        <div className="relative min-w-0">
          <button
            onClick={openPicker}
            className="flex items-center gap-1 text-xs sm:text-sm font-semibold text-gray-800 dark:text-gray-100 hover:text-blue-600 dark:hover:text-blue-400 transition-colors whitespace-nowrap"
            aria-label="日付を選択"
          >
            {fmtYMD(date)}
            <span className="text-sm leading-none">📅</span>
          </button>
          <input
            key={date}
            ref={dateInputRef}
            type="date"
            aria-hidden="true"
            tabIndex={-1}
            className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
            defaultValue={toISODate(date)}
            onChange={(e) => {
              const v = e.target.value.replace(/-/g, "");
              if (v.length === 8) onChange(v);
            }}
          />
        </div>
      </div>
      <button onClick={() => onChange(nextDay(date))} disabled={isToday} className={DATE_NAV_BTN_CLS} aria-label="翌日">
        <span className="hidden sm:inline">翌日 </span>→
      </button>
      <button onClick={() => onChange(clampToToday(addMonths(date, 1)))} disabled={isToday} className={DATE_NAV_BTN_CLS} aria-label="翌月">
        <span className="hidden sm:inline">翌月 </span>≫
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// メインページ
// ---------------------------------------------------------------------------

const HIDE_NOPICK_KEY = "keirin:hideNoPickRows";

export default function KeirinPage() {
  const [date, setDate] = useState(todayYYYYMMDD());
  const [picks, setPicks] = useState<KeirinPick[]>([]);
  const [summary, setSummary] = useState<KeirinSummary | null>(null);
  const [loadingPicks, setLoadingPicks] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);
  const [fetchingOdds, setFetchingOdds] = useState(false);
  const [fetchingResults, setFetchingResults] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [hideNoPickRows, setHideNoPickRows] = useState(false);
  const isToday = date === todayYYYYMMDD();
  const hasCand = picks.some((p) => p.race_key.includes("#CAND"));

  const loadData = useCallback(async (d: string) => {
    setLoadingPicks(true);
    setError(null);
    const iso = toISODate(d);
    const [picksResult, summaryResult] = await Promise.allSettled([
      fetchKeirinPicks(iso, true),
      fetchKeirinSummary(iso),
    ]);
    if (picksResult.status === "fulfilled") {
      setPicks(picksResult.value);
    } else {
      setError("ピックの取得に失敗しました。");
      setPicks([]);
    }
    if (summaryResult.status === "fulfilled") {
      setSummary(summaryResult.value);
    }
    setLoadingPicks(false);
  }, []);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshMsg(null);
    try {
      const result = await refreshKeirinPicks(toISODate(date));
      setRefreshMsg(result.message);
      await loadData(date);
    } catch {
      setRefreshMsg("採点更新に失敗しました");
    } finally {
      setRefreshing(false);
    }
  }, [date, loadData]);

  const handleFetchOdds = useCallback(async () => {
    setFetchingOdds(true);
    setActionMsg(null);
    try {
      const result = await triggerKeirinFetchOdds();
      setActionMsg(result.ok ? "オッズ更新を開始しました（約30秒後に再読込）" : `エラー: ${result.message}`);
      if (result.ok) setTimeout(() => void loadData(date), 35000);
    } catch {
      setActionMsg("オッズ更新に失敗しました");
    } finally {
      setFetchingOdds(false);
    }
  }, [date, loadData]);

  const handleFetchResults = useCallback(async () => {
    setFetchingResults(true);
    setActionMsg(null);
    try {
      const result = await triggerKeirinFetchResults();
      setActionMsg(result.ok ? "結果取得を開始しました（約60秒後に再読込）" : `エラー: ${result.message}`);
      if (result.ok) setTimeout(() => void loadData(date), 65000);
    } catch {
      setActionMsg("結果取得に失敗しました");
    } finally {
      setFetchingResults(false);
    }
  }, [date, loadData]);

  useEffect(() => {
    void loadData(date);
  }, [date, loadData]);

  useEffect(() => {
    setHideNoPickRows(localStorage.getItem(HIDE_NOPICK_KEY) === "true");
    const onStorage = (e: StorageEvent) => {
      if (e.key === HIDE_NOPICK_KEY) setHideNoPickRows(e.newValue === "true");
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return (
    <div className="w-full sm:max-w-3xl sm:mx-auto px-3 sm:px-4 py-4 pb-44 md:pb-20 space-y-4">
      {/* タイトル */}
      <div className="flex items-center gap-2">
        <Bike size={22} className="text-blue-500" />
        <h1 className="text-xl font-extrabold tracking-widest text-gray-900 dark:text-white">KEIRIN</h1>
        <div className="ml-auto flex items-center gap-3">
          <Link
            href="/keirin/stats"
            className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
            aria-label="成績グラフ"
          >
            <BarChart2 size={16} />
            <span className="hidden sm:inline">成績グラフ</span>
          </Link>
          <Link
            href="/keirin/help"
            className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
          >
            <HelpCircle size={15} />
            <span className="hidden sm:inline">推奨ガイド</span>
          </Link>
        </div>
      </div>

      {/* サマリー */}
      {summary ? (
        <SummaryCard summary={summary} />
      ) : (
        <div className="bg-white rounded-xl border border-gray-100 h-24 animate-pulse" />
      )}

      {/* 日付ナビ */}
      <DateNav date={date} onChange={setDate} />

      {/* エラー */}
      {error && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-3 py-3 text-sm text-amber-700">
          {error}
        </div>
      )}

      {/* ピック一覧 */}
      {loadingPicks ? (
        <div className="space-y-3 animate-pulse">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 h-28" />
          ))}
        </div>
      ) : !error && picks.length === 0 ? (
        <div className="text-center py-12 text-gray-400 text-sm">
          この日のピックはありません
        </div>
      ) : (
        <>
          {picks.some(p => !p.has_pick || computeGamiSkip(p)) && (
            <div className="flex items-center justify-end gap-2">
              <span className="text-xs text-gray-400">推奨外を非表示</span>
              <button
                role="switch"
                aria-checked={hideNoPickRows}
                onClick={() => {
                  const next = !hideNoPickRows;
                  setHideNoPickRows(next);
                  localStorage.setItem(HIDE_NOPICK_KEY, String(next));
                }}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${
                  hideNoPickRows ? "bg-blue-500" : "bg-gray-300"
                }`}
              >
                <span className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
                  hideNoPickRows ? "translate-x-5" : "translate-x-1"
                }`} />
              </button>
            </div>
          )}
          <div className="space-y-2">
            {picks.map((p, idx) => {
              if (!p.has_pick) {
                if (hideNoPickRows) return null;
                return <NoPickRow key={`nopick-${p.race_key}-${idx}`} pick={p} />;
              }
              // ガミ落ち（オッズ条件で推奨外確定）も「推奨外を非表示」スイッチで隠す
              if (hideNoPickRows && computeGamiSkip(p)) return null;
              return <PickCard key={`pick-${p.id}-${p.race_key}`} pick={p} cardId={`pick-${p.id}`} />;
            })}
          </div>
        </>
      )}

      {/* スティッキーボトムナビ */}
      <div
        style={{ paddingBottom: "4px" }}
        className="fixed bottom-14 left-0 right-0 z-50 bg-white/90 dark:bg-gray-900/90 backdrop-blur-sm border-t border-gray-200 dark:border-gray-700 md:bottom-0"
      >
        <div className="max-w-3xl mx-auto px-3 py-2 space-y-1.5">
          {/* 行1: 日付ナビ（前月・前日・今日・日付指定・翌日・翌月） */}
          <DateNav date={date} onChange={setDate} />
          {/* 行2: アクション（採点更新・オッズ更新・結果取得） */}
          {(hasCand || isToday) && (
            <div className="flex items-center gap-2">
              {hasCand && (
                <button
                  onClick={handleRefresh}
                  disabled={refreshing}
                  className="flex-1 px-2 py-1.5 rounded-lg border border-orange-300 dark:border-orange-600 text-xs font-semibold text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/20 hover:bg-orange-100 dark:hover:bg-orange-900/40 disabled:opacity-50 disabled:cursor-not-allowed text-center whitespace-nowrap"
                >
                  {refreshing ? "採点中…" : "⚡ 採点更新"}
                </button>
              )}
              {isToday && (
                <>
                  <button
                    onClick={handleFetchOdds}
                    disabled={fetchingOdds}
                    className="flex-1 px-2 py-1.5 rounded-lg border border-cyan-300 dark:border-cyan-600 text-xs font-semibold text-cyan-600 dark:text-cyan-400 bg-cyan-50 dark:bg-cyan-900/20 hover:bg-cyan-100 dark:hover:bg-cyan-900/40 disabled:opacity-50 disabled:cursor-not-allowed text-center whitespace-nowrap"
                  >
                    {fetchingOdds ? "更新中…" : "📊 オッズ更新"}
                  </button>
                  <button
                    onClick={handleFetchResults}
                    disabled={fetchingResults}
                    className="flex-1 px-2 py-1.5 rounded-lg border border-violet-300 dark:border-violet-600 text-xs font-semibold text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-900/20 hover:bg-violet-100 dark:hover:bg-violet-900/40 disabled:opacity-50 disabled:cursor-not-allowed text-center whitespace-nowrap"
                  >
                    {fetchingResults ? "取得中…" : "📋 結果取得"}
                  </button>
                </>
              )}
            </div>
          )}
          {/* アクション実行メッセージ */}
          {(refreshMsg || actionMsg) && (
            <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight text-center">
              {refreshMsg ?? actionMsg}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
