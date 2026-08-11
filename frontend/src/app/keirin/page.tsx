"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { formatMultiBetComboLines } from "@/lib/keirinCombo";
import { makeRaceNormalizer } from "@/lib/keirinProb";
import Link from "next/link";
import { Bike, HelpCircle, ChevronDown, ChevronUp, BarChart2, ClipboardCheck, Settings, Send } from "lucide-react";
import { fetchKeirinPicks, fetchKeirinSummary, fetchKeirinApprovalMode, fetchKeirinProposals, type KeirinPick, type KeirinSummary, type ManualKeirinRankKey } from "@/lib/api";
// 副作用のある操作は Server Action 経由（APIキーをブラウザへ出さないため）。
// 詳細は app/keirin/actions.ts の冒頭コメント参照。
import {
  refreshKeirinPicksAction as refreshKeirinPicks,
  triggerKeirinSubmitRaceAction as triggerKeirinSubmitRace,
} from "./actions";
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

// pred_win_pct/pred_top2_pct/pred_top3_pct（選手ごと独立モデルの生確率）を
// レース内合計が一定値になるよう補正する。実装は `@/lib/keirinProb`（review
// 画面・netkeirin 入稿コメントと同じ正規化を使うための単一正本）。

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

// 三連複2軸総流しの「相手（3着候補）」を複勝(3着内)予測確率の降順に並べ替える。
//
// keirin 側が組み立てる並びは車番昇順で優先順位の意味を持たない
// （write_candidates_wt.py::_third_list / notify_prerace_wt.py::_u_third_list）。
// 2軸固定の三連複は「残り1枠を誰が埋めるか」なので、各車の pred_top3_pct 降順が
// そのまま買い目ごとの的中寄与の順になる。購入は均等のため並び順は買い方に影響せず
// 表示上の情報付与のみ（点数・組み合わせの中身は不変）。
// EntryTable のロジットシフト補正は単調変換のため順位は生確率のままで一致する。
// 確率欠損車は末尾へ回し、同値・全欠損時は元の車番昇順を保つ。
function sortThirdsByTop3(thirds: number[], entries: KeirinPick["entries"]): number[] {
  const top3 = new Map(entries.map((e) => [e.frame_no, e.pred_top3_pct]));
  return thirds
    .map((car, i) => ({ car, i, p: top3.get(car) ?? null }))
    .sort((a, b) => {
      if (a.p == null && b.p == null) return a.i - b.i;
      if (a.p == null) return 1;
      if (b.p == null) return -1;
      return b.p - a.p || a.i - b.i;
    })
    .map((x) => x.car);
}

// pred_combo「a1=a2-t1,t2,..」の相手部分を sortThirdsByTop3 で並べ替えて返す。
// 旧ランクの別形式（S1の "p1-p2-t.."・旧Aの "axis>t.."）や欠損はそのまま返す。
function reorderComboByTop3(pred: string, entries: KeirinPick["entries"]): string {
  const m = /^(\d+)=(\d+)-(\d+(?:,\d+)*)$/.exec(pred.trim());
  if (!m) return pred;
  const thirds = m[3].split(",").map(Number);
  return `${m[1]}=${m[2]}-${sortThirdsByTop3(thirds, entries).join(",")}`;
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

// race_key は "YYYYMMDD_場コード_レース番号" 形式（keirinリポジトリ側と共通仕様）。
// API側（/keirin/picks等）は候補種別を示す"#CAND"/"#7S7"等のサフィックスを付けて返す
// ことがあるため、netkeirin側の物理レースキーと突き合わせる際は先に剥がす。
function baseRaceKey(raceKey: string): string {
  return raceKey.split("#")[0];
}

function raceKeyToISODate(raceKey: string): string {
  const base = baseRaceKey(raceKey);
  return `${base.slice(0, 4)}-${base.slice(4, 6)}-${base.slice(6, 8)}`;
}

// netkeirin入稿は朝バッチ(19時未満の発走)/夕バッチ(19時以降の発走)で候補ファイルが
// 分かれる（evening_picks_wt.sh --start-from-hour 19）ため、発走時刻からsessionを判定する
function submitSessionFromStartAt(startAt: number | string | null): "morning" | "evening" {
  if (startAt == null) return "morning";
  const ts = typeof startAt === "number" ? startAt : parseInt(String(startAt), 10);
  if (isNaN(ts)) return "morning";
  const hour = parseInt(
    new Date(ts * 1000).toLocaleTimeString("ja-JP", { timeZone: "Asia/Tokyo", hour: "2-digit", hour12: false }),
    10,
  );
  return hour >= 19 ? "evening" : "morning";
}

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

// 現行ランク体系（2026-08-01〜: RANK_7S/RANK_7A/RANK_9S/RANK_9A/RANK_7SS の
// 5ペーパーランク。表示ラベルは 7S/7A/9S/9A/7SS）。
//
// 【2026-08-01 是正】keirin リポジトリ（別リポジトリ・commit f31f84b, 2026-07-31）が
// 内部rank名を "RANK_" + 表示ラベル方式へ全面改名した（旧 SEVEN_S7→RANK_7S・
// SEVEN_7A→RANK_7A・NINE_S9→RANK_9S・NINE_9A→RANK_9A。表示ラベル自体は変更なし）。
// kiseki backendはこの改名に追随済み（backend/src/api/keirin_router.pyの
// _PAPER_RANK_LABELSが単一正本）。バックエンドが計算した pick.display_rank
// （7S/7A/9S/9A/7SS）をそのままキーに使う設計は変更なし。
//
// 同時に、旧 SEVEN_S7/NINE_S9 の gate_label('SS'/'S')による7SS/9SS・7S/9Sの
// 分岐は keirin側commit e994758（2026-07-31）で廃止された（rank_7s_gate_label()は
// 常に"S"のみを返す。既存行のgate_labelも'S'へ一括更新済み）。7S/9Sはそれぞれ
// 単一ランクとなり、"9SS"という表示は現在は存在しない。
//
// 【2026-08-02: 表示ラベル"7SS"は現在どのランクも指さない】上記の分岐廃止で
// 旧7SS（SEVEN_S7のgate_label='SS'内訳）が消えた後、同じラベルを再利用して
// RANK_7SS（波乱軸選出・穴レース検知・モデル非依存）を導入していたが、
// live実績 n=16,298・ROI73.5%（2026年の月次も1月以外すべて70%以下）と
// 控除率75%を下回り続けたためユーザー判断で全廃した。picks_history の
// RANK_7SS 行も削除済み。将来の再設定に備え keirin 側の判定ロジックは残置。
//
// S1（win軸1着固定×3着内モデル相手2車・三連単2点流し・旧SEVEN_S1）は
// 2026-07-31にkeirin側で全廃済み（このためRANK_STYLE/RANK_ORDER等からも除去）。
// S2(7PLUS_U)/S3(7PLUS_M)は2026-07-21全廃・旧新S1（SIX_S1・6車三連単）とA
// （7PLUS_A・一致波乱二連単）は2026-07-17全廃・旧S1(7PLUS_R・7車三連複)・
// S/S+（7PLUS_ST/STP）は2026-07-15〜16全廃（いずれも行はアーカイブ退避済み）。
// 未知 rank は RankBadge が「非」フォールバック表示する。
const RANK_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  // 7S=RANK_7S（単勝×複勝指数トップ3重なり軸×波乱度選出・三連複2軸総流し）。
  // 2026-07-31にgate_label('SS'/'S')による分岐が廃止され単一ランクへ統合済み
  // （旧7SSはSへ吸収）。同じ色を維持（旧"7S"の色をそのまま踏襲）。
  "7S":         { bg: "#16a34a", text: "#fff", label: "7S" },
  "7PLUS_CAND": { bg: "#9ca3af", text: "#fff", label: "候補" },
  // 9S=RANK_9S（S7の9車立て版・独立ランク）。7Sと同様2026-07-31にgate_label分岐
  // 廃止・単一ランク化済み。買い目コスト(7点流し=700円)・母集団が異なるため
  // 色調は別系統（青系）のまま区別する。
  "9S":         { bg: "#0891b2", text: "#fff", label: "9S" },
  // 7A/9A=RANK_7S/RANK_9Sの境界ランク（2ゲート中1つだけ不合格・2026-07-27導入・
  // 2026-07-31にゲート数を3→2へ簡素化）。彩度を落とした色で7S/9Sとはやや区別する。
  "7A":         { bg: "#78716c", text: "#fff", label: "7A" },
  "9A":         { bg: "#64748b", text: "#fff", label: "9A" },
  // 7B=RANK_7B（◎◯一致だが順序・相手で不一致・三連複3点・2026-08-03導入）。
  // 7S/7Aとは母集団が排他（7B=overlap2 / 7S・7A=overlap0,1）で買い目点数も
  // 異なる（3点 vs 5点）ため、独立した色調（琥珀系）で区別する。
  "7B":         { bg: "#b45309", text: "#fff", label: "7B" },
  // 7SS=RANK_7SS（entropy不合格×軸2車が同一ライン・2026-08-05新設）。
  // ⚠️ 2026-08-02に全廃した旧RANK_7SS（波乱軸選出/穴レース検知・ROI73.5%）とは
  // 無関係の別戦略で、名前だけを引き継いでいる（picks_historyの旧7SS行は0件）。
  // 買い目は7S/7Aと同じ三連複2軸+総流し5点だが、確認窓ROI 85.9%と現行最良のため
  // 最上位ランク。7Sと同系（緑）でより濃い色にして「7Sの上」であることを示す。
  "7SS":        { bg: "#15803d", text: "#fff", label: "7SS" },
  // 7H1=RANK_7H1（穴推奨・本命バスト型・2026-08-06新設）。既存6ランクとは系統が
  // 違う（S/A/B＝的中率重視の予想ベース、H＝穴狙い）ため色系統も分ける（紫）。
  // **唯一の2券種ランク**（三連単フォーメーション8点 + 三連複BOX 4〜10点）で、
  // 本命とその同ラインを買い目から丸ごと落とすため他ランクと買い方が正反対。
  "7H1":        { bg: "#7e22ce", text: "#fff", label: "7H1" },
  // 7H2=RANK_7H2（穴推奨・印なし2軸・2026-08-10新設）。7H1 と同じ穴狙い系なので
  // 同系色（紫）だが、**7H1 とは母集団が排他ではない**（同じ7車立てで重なる）。
  "7H2":        { bg: "#6d28d9", text: "#fff", label: "7H2" },
  // 9H1 も穴推奨系なので 7H1 と同系色。車数が違うので明度で分ける。
  "9H1":        { bg: "#a855f7", text: "#fff", label: "9H1" },
  // 7H3=RANK_7H3（穴推奨・本命連対どまり型・2026-08-12新設）。穴狙い系なので同系色。
  "7H3":        { bg: "#4f46e5", text: "#fff", label: "7H3" },
  // 7C=RANK_7C（ベースモデル・終日の二軸・2026-08-07新設）。件数が最多になる。
  "7C":         { bg: "#0d9488", text: "#fff", label: "7C" },
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


// WINTICKET公式予想印: 1=◎ 2=◯ 3=△ 4=× 0(または未設定)=無印
function wtMarkSymbol(mark: number | null): string {
  switch (mark) {
    case 1: return "◎";
    case 2: return "◯";
    case 3: return "△";
    case 4: return "×";
    default: return "—";
  }
}

// ---------------------------------------------------------------------------
// ライン構成（winticket の linePrediction 由来 wt_entries.line_group）
//
// 表に列を足すのではなく1行に畳む。競輪のラインは「1-4-7 / 2-5 / 3」という
// 並びで読むものであり、行ごとに散らすと隊列が読めない。
// ---------------------------------------------------------------------------
function buildLines(entries: KeirinPick["entries"]): number[][] {
  const groups = new Map<string, KeirinPick["entries"]>();
  const solo: number[][] = [];
  for (const e of entries) {
    const g = e.line_group == null || e.line_group === "" ? null : String(e.line_group);
    if (g == null) {
      solo.push([e.frame_no]);
      continue;
    }
    const cur = groups.get(g);
    if (cur) cur.push(e);
    else groups.set(g, [e]);
  }
  const lines = [...groups.values()].map((members) =>
    [...members]
      // 隊列は line_pos（先頭=1）順。欠けている場合だけ車番で代用する。
      .sort((a, b) => (a.line_pos ?? 99) - (b.line_pos ?? 99) || a.frame_no - b.frame_no)
      .map((m) => m.frame_no),
  );
  // 単騎も1車のラインとして同列に並べる（先頭車番順）。
  return [...lines, ...solo].sort((a, b) => a[0] - b[0]);
}

function LineRow({ entries }: { entries: KeirinPick["entries"] }) {
  const lines = buildLines(entries);
  // line_group が全車未取得のレース（ライン予想が未公開）は行ごと出さない。
  if (!lines.length || lines.every((l) => l.length === 1)) return null;
  return (
    <div className="px-3 sm:px-4 py-1.5 border-b border-gray-50 dark:border-gray-700 flex items-center gap-2 text-xs">
      <span className="text-gray-400 dark:text-gray-500 flex-shrink-0">ライン</span>
      <span className="font-medium text-gray-700 dark:text-gray-200 break-words">
        {lines.map((l) => l.join("-")).join(" / ")}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 入稿した買い目と金額配分
//
// 🔴 **表示するのは記録であって再計算ではない。** 傾斜配分の金額は入稿時点の
//    想定オッズから決まるため、あとから同じ値を出すことはできない。
//    keirin 側が入稿の瞬間に保存した bet_detail をそのまま並べる。
// ---------------------------------------------------------------------------
const BET_SOURCE_JP: Record<string, string> = {
  blend: "朝オッズ×モデル",
  odds: "朝オッズ",
  model: "モデルのみ",
  equal: "均等",
};

// 確定した着順から「当たった目」の表記を作る。3連複は昇順 "a=b=c"、
// 3連単は着順どおり "1着-2着-3着"。`bet_detail.combo` と同じ書き方に揃える。
// ⚠️ **着順が入っていないうちは null を返す**。未確定のまま色を付けると
//    「外れた」と読めてしまう（I-34 と同じ理由）。
function winningCombos(entries: KeirinPick["entries"]): Set<string> | null {
  const byOrder = new Map<number, number>();
  for (const e of entries) {
    const o = e.finish_order ?? 0;
    if (o >= 1 && o <= 3) byOrder.set(o, e.frame_no);
  }
  if (byOrder.size !== 3) return null;
  const [a, b, c] = [byOrder.get(1)!, byOrder.get(2)!, byOrder.get(3)!];
  return new Set([
    [a, b, c].slice().sort((x, y) => x - y).join("="),   // 3連複
    `${a}-${b}-${c}`,                                     // 3連単
  ]);
}

function SubmittedBetBlock({ bet, entries }: {
  bet: NonNullable<KeirinPick["submitted_bet"]>;
  entries: KeirinPick["entries"];
}) {
  // 金額の大きい順。傾斜配分では「どこに厚く置いたか」が読みたい情報なので、
  // 車番順よりも配分順のほうが目的に合う。
  const lines = [...bet.lines].sort((a, b) => b.stake - a.stake || a.combo.localeCompare(b.combo));
  const multiType = new Set(lines.map((l) => l.bet_type)).size > 1;
  const winners = winningCombos(entries);
  return (
    <div className="px-3 sm:px-4 py-2 border-t border-gray-100 dark:border-gray-700">
      <div className="flex items-baseline gap-2 mb-1">
        <span className="text-xs font-medium text-gray-600 dark:text-gray-300">入稿した買い目</span>
        <span className="text-xs text-gray-500 dark:text-gray-400 tabular-nums">
          計 {bet.total.toLocaleString()}円 / {lines.length}点
        </span>
        {bet.source && BET_SOURCE_JP[bet.source] && (
          <span className="text-[10px] text-gray-400 dark:text-gray-500">
            配分: {BET_SOURCE_JP[bet.source]}
          </span>
        )}
      </div>
      {/* 買い目・オッズ・金額を**列として揃える**。
          以前は1点ぶんを flex の justify-between で組んでいたため、オッズと金額の
          境目が桁数（1.5倍 / 23.2倍）でずれて縦に読めなかった。
          `li` を `display:contents` にして「買い目 / オッズ / 金額」の3セルを
          外側の grid へ直接流し込み、オッズ・金額列を右寄せで固定する。 */}
      <ul className="grid grid-cols-[repeat(2,minmax(0,1fr)_auto_auto)] sm:grid-cols-[repeat(3,minmax(0,1fr)_auto_auto)] gap-x-2 sm:gap-x-3 gap-y-0.5 text-xs tabular-nums">
        {lines.map((l) => (
          <li key={`${l.bet_type}:${l.combo}`} className="contents">
            <span
              className={`truncate ${winners?.has(l.combo)
                ? "text-red-600 dark:text-red-400 font-bold"
                : "text-gray-700 dark:text-gray-200"}`}
            >
              {multiType && <span className="text-gray-400 dark:text-gray-500 mr-1">{l.bet_type}</span>}
              {l.combo}
            </span>
            {/* 🔴 予測オッズは板と区別して出す。同じ顔で並べると
                「実際に付いていたオッズ」と読まれる。 */}
            <span
              className={`text-right ${l.odds_source === "predicted"
                ? "text-indigo-400 dark:text-indigo-300"
                : "text-gray-400 dark:text-gray-500"}`}
              title={l.odds != null && l.odds_source === "predicted"
                ? "板に無かったため、オッズ生成モデルの予測値を表示しています"
                : undefined}
            >
              {l.odds != null && `${l.odds.toFixed(1)}倍${l.odds_source === "predicted" ? "*" : ""}`}
            </span>
            <span className="text-right text-gray-600 dark:text-gray-300">
              {l.stake.toLocaleString()}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EntryTable({ entries }: { entries: KeirinPick["entries"] }) {
  if (!entries.length) return <p className="text-xs text-gray-400 dark:text-gray-500 px-3 py-2">出走情報なし</p>;
  const sorted = [...entries].sort((a, b) => {
    const winDiff = (b.pred_win_pct ?? -Infinity) - (a.pred_win_pct ?? -Infinity);
    if (winDiff !== 0) return winDiff;
    const top2Diff = (b.pred_top2_pct ?? -Infinity) - (a.pred_top2_pct ?? -Infinity);
    if (top2Diff !== 0) return top2Diff;
    const top3Diff = (b.pred_top3_pct ?? -Infinity) - (a.pred_top3_pct ?? -Infinity);
    if (top3Diff !== 0) return top3Diff;
    return (b.race_point ?? -Infinity) - (a.race_point ?? -Infinity);
  });
  // レース内合計を 1着=100% / 2着内=min(出走数,2)*100% / 3着内=min(出走数,3)*100%
  // に揃える（生確率のままだと単勝合計9.7%等になり読めない）。詳細は lib/keirinProb。
  const normWin = makeRaceNormalizer(entries.map((e) => e.pred_win_pct), 1);
  const normTop2 = makeRaceNormalizer(
    entries.map((e) => e.pred_top2_pct), Math.min(entries.length, 2));
  const normTop3 = makeRaceNormalizer(
    entries.map((e) => e.pred_top3_pct), Math.min(entries.length, 3));
  return (
    // 🔴 数値列が4本（単勝率・2着内率・複勝率・競走得点）になり、狭い端末では
    //    テーブルが card 幅を超える。**ページごと横スクロールさせない**ため、
    //    ここで内側スクロールに閉じ込める（`feedback_fixed_layout`）。
    <div className="overflow-x-auto">
    <table className="w-full">
      <thead>
        <tr className="border-b border-gray-100 dark:border-gray-700">
          <th className="text-center px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-7 sm:w-8">車</th>
          <th className="text-left px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs">選手名</th>
          <th className="text-center px-1 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-6 sm:w-8">W</th>
          <th className="text-center px-1 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-9 sm:w-12">戦法</th>
          <th className="text-right px-1.5 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14 whitespace-nowrap">単勝率</th>
          {/* 2着内率（連対率）。1着率・3着内率と同じ経路のモデル出力（lgbm_wt_top2）。
              2026-08-12 以前のレースは列が無かったので「—」になる。 */}
          <th className="text-right px-1.5 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14 whitespace-nowrap">2着内率</th>
          <th className="text-right px-1.5 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14 whitespace-nowrap">複勝率</th>
          <th className="text-right px-2 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-11 sm:w-14 whitespace-nowrap">競走得点</th>
          <th className="text-center px-1 sm:px-3 py-1 font-medium text-gray-500 dark:text-gray-400 text-xs w-8 sm:w-10">着</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((e) => (
          <tr key={e.frame_no} className="border-b border-gray-50 dark:border-gray-700 last:border-0">
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 font-bold text-center text-xs sm:text-sm text-gray-700 dark:text-gray-200">{e.frame_no}</td>
            <td className="px-2 sm:px-3 py-1 sm:py-1.5 text-xs sm:text-sm text-gray-800 dark:text-gray-100">{e.name ?? "—"}</td>
            <td className="px-1 sm:px-3 py-1 sm:py-1.5 text-center text-gray-600 dark:text-gray-300 text-xs sm:text-sm">{wtMarkSymbol(e.prediction_mark)}</td>
            <td className="px-1 sm:px-3 py-1 sm:py-1.5 text-center text-gray-500 dark:text-gray-400 text-xs">{e.style ?? "—"}</td>
            <td className="px-1.5 sm:px-3 py-1 sm:py-1.5 text-right font-mono text-xs sm:text-sm text-gray-700 dark:text-gray-200">
              {normWin(e.pred_win_pct) != null ? `${normWin(e.pred_win_pct)!.toFixed(1)}%` : "—"}
            </td>
            <td className="px-1.5 sm:px-3 py-1 sm:py-1.5 text-right font-mono text-xs sm:text-sm text-gray-700 dark:text-gray-200">
              {normTop2(e.pred_top2_pct) != null ? `${normTop2(e.pred_top2_pct)!.toFixed(1)}%` : "—"}
            </td>
            <td className="px-1.5 sm:px-3 py-1 sm:py-1.5 text-right font-mono text-xs sm:text-sm text-gray-700 dark:text-gray-200">
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
    </div>
  );
}

// コンポーネント外に置くことで react-hooks/purity を回避
// 🔴 **結果が取り込めているか**。発走した／時間が過ぎた だけでは「不的中」と
//    言い切れない（確定〜こちらの取込までの間は結果が空なので、そのまま出すと
//    当たったレースまで「不的中」と表示される。ユーザー指摘 2026-08-07）。
//    着順が入っているかで判定するのが唯一確実。
function hasResult(entries: KeirinPick["entries"]): boolean {
  return entries.some((e) => (e.finish_order ?? 0) >= 1);
}

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

// 推奨外レースの手動入稿で選べるランク。
// S1は2026-07-31全廃・買い目構造が異なるため対象外。旧gate_label分岐由来の
// 7SS/9SS（同日廃止・SはSへ統合済み）も対象外。
// RANK_7SS（2026-08-05新設）は軸選定・買い目とも7S/7Aと同一なので技術的には
// 手動入稿できるが、netkeirinの「自信あり」タグ（7SSのみ付与・上限1件/日と推定）を
// 手動分で消費すると自動入稿側が落ちるため、あえて含めていない
// （api.ts の ManualKeirinRankKey 参照）。
// 車数(n_entries)ごとに候補を絞り込む。
// 賭け金は 2026-08-07 に全ランク「1レース10,000円を点数で均等割り」へ統一した
// （keirin 側 strategy_wt.unit_stake が単一正本）。7車5点=2,000円/点・
// 9車7点=1,400円/点となり、統一前の固定単価と同じ値になる。
// ⚠️ 7B は 2026-08-03 の新設以来ここに載っていたが、backend の
// `_MANUAL_RANK_KEYS` には無いため**選ぶと必ず 400 で落ちていた**（2026-08-08 是正）。
// backend 側が意図的に外している（hypo軸と本番の 7B 軸選定が一致するか未確認）ので、
// 選択肢の方を落とす。7B を手動入稿したくなったら先に軸の一致を確認し、
// backend の `_MANUAL_RANK_KEYS` と両方へ足すこと。
const MANUAL_SUBMIT_RANKS: Record<7 | 9, { key: ManualKeirinRankKey; label: string }[]> = {
  7: [
    { key: "7S", label: "7S" },
    { key: "7A", label: "7A" },
  ],
  9: [
    { key: "9S", label: "9S" },
    { key: "9A", label: "9A" },
  ],
};

function SubmitRankDialog({ pick, onClose }: { pick: KeirinPick; onClose: () => void }) {
  const nCars = pick.n_entries === 9 ? 9 : 7;
  const options = MANUAL_SUBMIT_RANKS[nCars];
  const [rankKey, setRankKey] = useState<ManualKeirinRankKey>(options[0].key);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const handleSubmit = async () => {
    if (pick.hypo_axis1 == null || pick.hypo_axis2 == null || submitting) return;
    setSubmitting(true);
    setResult(null);
    try {
      const r = await triggerKeirinSubmitRace(
        baseRaceKey(pick.race_key),
        raceKeyToISODate(pick.race_key),
        submitSessionFromStartAt(pick.start_at),
        { rankKey, axis1: pick.hypo_axis1, axis2: pick.hypo_axis2 },
      );
      setResult(r);
    } catch {
      setResult({ ok: false, message: "入稿リクエストに失敗しました" });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-900 rounded-xl shadow-lg w-full max-w-sm p-4 sm:p-5"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-1">
          推奨外レースの入稿ランクを選択
        </h3>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          {pick.venue_name}{pick.race_no}R（軸 {pick.hypo_axis1}-{pick.hypo_axis2}・{nCars}車立て）。
          選んだランクのテンプレート文言で入稿します（賭け方・点数はランクにより変わりません）。
        </p>
        <div className="flex gap-2 mb-4">
          {options.map(o => (
            <button
              key={o.key}
              type="button"
              onClick={() => setRankKey(o.key)}
              className={`flex-1 py-1.5 rounded-lg text-sm font-semibold border transition-colors ${
                rankKey === o.key
                  ? "bg-blue-500 border-blue-500 text-white"
                  : "border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
        {result && (
          <p className={`text-xs mb-3 ${result.ok ? "text-gray-500 dark:text-gray-400" : "text-red-500"}`}>
            {result.ok ? "このレースの入稿を開始しました（結果はDiscordで確認してください）" : `エラー: ${result.message}`}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-sm text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
          >
            閉じる
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || result?.ok}
            className="px-3 py-1.5 rounded-lg text-sm font-semibold bg-blue-500 text-white disabled:opacity-40 disabled:cursor-not-allowed hover:bg-blue-600"
          >
            {submitting ? "送信中…" : "このランクで入稿"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 開催（会場×日）種別ごとのカード背景
//
// ユーザー要望「netkeirin入稿単位の開催に合わせ、カード背景を分ける。
// モーニング・通常・ミッドナイト」。**デイとナイターは「通常」としてまとめる**
// （4種のうち色は3系統）。種別の判定は backend `keirin_meeting.py` が正本で、
// netkeirin 入稿の波（朝7:00 / 昼13:00 / 夕18:00）と境界を揃えてある。
//
// ⚠️ 発走時刻が取れない開催は null で来る。**その場合は色を付けない**
//    （分からないものをどれかに倒すと実際と違う色が付いて誤読の元になる）。
// ⚠️ 薄い色にとどめる。カードは見送り(opacity-55)・結果バッジ・足切りなど
//    既に状態を色で表しており、背景を濃くするとそちらが読めなくなる。
const MEETING_CARD_BG: Record<string, string> = {
  morning: "bg-amber-50/70 dark:bg-amber-950/20",      // モーニング
  day: "bg-white dark:bg-gray-900",                     // 通常（デイ）
  nighter: "bg-white dark:bg-gray-900",                 // 通常（ナイター）
  midnight: "bg-indigo-50/70 dark:bg-indigo-950/25",    // ミッドナイト
};

function meetingBg(t: KeirinPick["meeting_type"]): string {
  return (t && MEETING_CARD_BG[t]) || "bg-white dark:bg-gray-900";
}

// ヘッダー行の背景。⚠️ **ここを塗らないと開催種別の色は見えない。**
// カード外側に MEETING_CARD_BG を当てても、ヘッダーが bg-gray-50 を
// 上から塗るため色が完全に隠れていた（2026-08-07 の色分けが効いていなかった原因）。
// 本文より一段濃くして、折りたたみ時でも種別が分かるようにする。
const MEETING_HEADER_BG: Record<string, string> = {
  morning: "bg-amber-100/80 dark:bg-amber-950/40",     // モーニング
  day: "bg-gray-50 dark:bg-gray-800",                   // 通常（デイ）
  nighter: "bg-gray-50 dark:bg-gray-800",               // 通常（ナイター）
  midnight: "bg-indigo-100/80 dark:bg-indigo-950/45",   // ミッドナイト
};

function meetingHeaderBg(t: KeirinPick["meeting_type"]): string {
  return (t && MEETING_HEADER_BG[t]) || "bg-gray-50 dark:bg-gray-800";
}

function NoPickRow({ pick }: { pick: KeirinPick }) {
  const [collapsed, setCollapsed] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const startTime = fmtStartAt(pick.start_at);
  // 発走済みでも結果未取込のうちは「未確定」。不的中を出すのは確定後だけ。
  const isSettled = computeIsSettled(pick.status, pick.start_at) && hasResult(pick.entries);
  const hasPayout = pick.trio_payout > 0 || (pick.trifecta_payout ?? 0) > 0;
  const hasHypo = pick.hypo_axis1 != null && pick.hypo_axis2 != null;
  return (
    <div className={`${meetingBg(pick.meeting_type)} rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden opacity-75`}>
      <div className={`w-full flex items-center gap-1 px-1 sm:px-2 ${meetingHeaderBg(pick.meeting_type)}${collapsed ? "" : " border-b border-gray-100 dark:border-gray-700"}`}>
        <button
          type="button"
          onClick={() => setCollapsed(v => !v)}
          className="flex-1 min-w-0 flex items-center gap-2 px-2 sm:px-3 py-2 text-left"
        >
          <span className="inline-flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold flex-shrink-0 bg-gray-200 dark:bg-gray-700 text-gray-400 dark:text-gray-500">—</span>
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-1.5 sm:gap-2 flex-wrap">
              <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{pick.venue_name}</span>
              <span className="font-semibold text-gray-600 dark:text-gray-300 text-sm">{pick.race_no}R</span>
              {pick.is_marquee && (
                <span className="text-amber-500/70 dark:text-amber-400/70 text-sm" title="看板レース（決勝・特選クラス）">★</span>
              )}
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
        {hasHypo && !isSettled && (
          <button
            type="button"
            onClick={() => setDialogOpen(true)}
            title="ランクを選んでnetkeirinへ入稿"
            aria-label="ランクを選んでnetkeirinへ入稿"
            className="flex-shrink-0 p-1 mr-1 rounded text-gray-400 hover:text-blue-500 dark:text-gray-500 dark:hover:text-blue-400"
          >
            <Send size={14} />
          </button>
        )}
      </div>
      {!collapsed && (
        <>
          {hasHypo && (
            <div className="px-3 sm:px-4 py-1.5 border-b border-gray-50 dark:border-gray-700 flex items-center gap-2 text-xs">
              <span className="text-gray-400 dark:text-gray-500 flex-shrink-0">参考買い目</span>
              <span className="text-gray-500 dark:text-gray-400 tabular-nums">
                3連複: {pick.hypo_axis1}={pick.hypo_axis2}-{sortThirdsByTop3(pick.hypo_others ?? [], pick.entries).join(",")}
                {" "}({(pick.hypo_others ?? []).length}点)
              </span>
            </div>
          )}
          <LineRow entries={pick.entries} />

          <EntryTable entries={pick.entries} />
          {isSettled && (
            <div className="px-3 sm:px-4 py-2 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex items-center justify-between gap-2">
              <span className="text-xs text-gray-400 dark:text-gray-500">推奨外</span>
              <PayoutInfo trio={pick.trio_payout} trifecta={pick.trifecta_payout} />
            </div>
          )}
        </>
      )}
      {dialogOpen && <SubmitRankDialog pick={pick} onClose={() => setDialogOpen(false)} />}
    </div>
  );
}

function PickCard({ pick, cardId }: { pick: KeirinPick; cardId?: string }) {
  // 発走済みでも結果未取込のうちは「未確定」。不的中を出すのは確定後だけ
  // （ユーザー指摘 2026-08-07。サマリー側の集計は従来どおりで変えない）。
  const isRaceOver = computeIsSettled(pick.status, pick.start_at);
  const isSettled = isRaceOver && hasResult(pick.entries);
  const isPendingResult = isRaceOver && !isSettled;
  const [collapsed, setCollapsed] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitMsg, setSubmitMsg] = useState<string | null>(null);
  const isMiwokuri = pick.miwokuri;
  const isPurchased = !isMiwokuri && pick.bet_amount > 0;
  const gamiThr = GAMI_THRESHOLD;
  const isGamiSkip = computeGamiSkip(pick);
  // ペーパー検証ランク（RANK_7SS/RANK_7S/RANK_7A/RANK_9S/RANK_9A。2026-08-01〜
  // 内部rank名の全面改名に追随・旧S1(SEVEN_S1)は2026-07-31全廃）は旧S1（廃止済み）
  // の三連複ガミ閾値と無関係のためガミ判定チップ（✓/⚠）を表示しない。
  // RANK_7B は買い目を3点に絞る性質上ガミ判定を使う運用のため意図的に含めない。
  // ⚠️ RANK_7SS は 2026-08-02 に全廃した旧同名ランクではなく 2026-08-05 新設の
  // 別戦略（entropy不合格×同一ライン）。買い目は7S/7Aと同じ5点流し。
  // RANK_7H1（穴推奨）も対象。ガミ閾値は三連複の最低倍率に対する条件で、
  // 三連単フォーメーションとの併せ買いには意味を持たない。
  const isPaperRank = pick.rank === "RANK_7SS" || pick.rank === "RANK_7S"
    || pick.rank === "RANK_7A"
    || pick.rank === "RANK_9S" || pick.rank === "RANK_9A"
    || pick.rank === "RANK_7H1";
  const gamiStatus: "ok" | "ng" | null = !isPaperRank && pick.prerace_gami != null && (!isMiwokuri || isGamiSkip)
    ? pick.prerace_gami >= gamiThr ? "ok" : "ng"
    : null;

  const rankStr = pick.rank ?? "";
  // 候補行: 指数条件上なり得るランク（S1）と買い目（三連複全目）。
  // ガミ落ち確定行は「ガミ落ち」表示を優先し候補ランクは出さない。
  const candRanks = isGamiSkip ? [] : candPossibleRanks(pick);
  const candCombo = candRanks.length > 0 ? parseCandCombo(pick.pred_combo) : null;
  // バッジ表示はランク名を直接出す（2026-07-16に導入・2026-07-21にSS/S再編でAPI側の
  // display_rank(S1/7SS/7S)をそのまま使う方式へ変更）:
  // S1候補（7PLUS_CAND ∧ gap条件成立）は「候補」ではなく S1 バッジで表示し、
  // 直前オッズ判定で購入対象になったら緑○で囲う（RankBadge purchased）。
  const badgeRank = rankStr === "7PLUS_CAND" && candRanks.includes("S1")
    ? "7PLUS_R"
    : (pick.display_rank ?? rankStr);
  // 購入対象判定: 採点済みは bet_amount>0。当日の S1 買い成立は #CAND 行の
  // rank が 7PLUS_R に昇格した時点（bet_amount は翌朝採点まで 0 のため）。
  const isBuyConfirmed = !isMiwokuri && !isGamiSkip && (pick.bet_amount > 0 || rankStr === "7PLUS_R");
  // 券種ラベル: 旧S1（win軸新設計・三連単）は2026-07-31全廃済み。三連複ランク
  // （RANK_7S/RANK_7A/RANK_7B/RANK_9S/RANK_9A/RANK_7SS）は固定表示でよい
  // （API側の_VALID_PICK_RANKSからもSEVEN_S1は除外済みのため到達し得ない）。
  //
  // RANK_7H1（穴推奨）だけは**2券種**（三連単フォーメーション + 三連複BOX）で、
  // pred_combo が既に "三複:… / 三単:…" と券種名込みの形で入っている。
  // ここで "3連複:" を前置すると三連単の目まで三連複と表示され**買い目を偽る**ので、
  // 7H1 は前置せずそのまま出す（reorderComboByTop3 も形式不一致で素通しになる）。
  //
  // さらに全目の列挙（1レース18目）は画面上ほぼ読めないため、券種ごとに
  // フォーメーション表記へ畳んで**行を分けて**出す（multiBetLines）。
  // 畳めない構造や "見送り" などは null が返るので、下の従来表示へ落ちる。
  const is7h1 = pick.rank === "RANK_7H1";
  const nCombosSuffix = pick.n_combos && pick.n_combos > 1 ? ` (${pick.n_combos}点)` : "";
  const multiBetLines = pick.pred_combo ? formatMultiBetComboLines(pick.pred_combo) : null;
  const comboLabel = pick.pred_combo && !multiBetLines
    ? (is7h1
      ? `${pick.pred_combo}${nCombosSuffix}`
      : `3連複: ${reorderComboByTop3(pick.pred_combo, pick.entries)}${nCombosSuffix}`)
    : undefined;

  const startTime = fmtStartAt(pick.start_at);

  const handleSubmitRace = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (submitting) return;
    setSubmitting(true);
    setSubmitMsg(null);
    try {
      const result = await triggerKeirinSubmitRace(
        baseRaceKey(pick.race_key),
        raceKeyToISODate(pick.race_key),
        submitSessionFromStartAt(pick.start_at),
      );
      setSubmitMsg(result.ok ? "このレースの入稿を開始しました（結果はDiscordで確認してください）" : `エラー: ${result.message}`);
    } catch {
      setSubmitMsg("入稿リクエストに失敗しました");
    } finally {
      setSubmitting(false);
    }
  }, [pick.race_key, pick.start_at, submitting]);

  useEffect(() => {
    if (!submitMsg) return;
    const t = setTimeout(() => setSubmitMsg(null), 6000);
    return () => clearTimeout(t);
  }, [submitMsg]);

  return (
    <div id={cardId} className={`${meetingBg(pick.meeting_type)} rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm overflow-hidden${isMiwokuri || isGamiSkip ? " opacity-55" : ""}`}>
      {/* ヘッダー行（クリックで折りたたみトグル + 右端にピンポイント入稿アイコン） */}
      <div className={`w-full flex items-center gap-1 px-3 sm:px-4 py-2 ${meetingHeaderBg(pick.meeting_type)}${collapsed ? "" : " border-b border-gray-100 dark:border-gray-700"}`}>
        <button
          type="button"
          onClick={() => setCollapsed(v => !v)}
          className="flex-1 min-w-0 flex items-center gap-2 text-left"
        >
          {/* 左バッジ = display_rank(7S/7A/9S/9A/7SS)の直接表示（全ランク統一）。購入対象は緑○で囲う */}
          <RankBadge rank={badgeRank} purchased={isBuyConfirmed} />
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-1.5 sm:gap-2 flex-wrap">
              <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{pick.venue_name}</span>
              <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{pick.race_no}R</span>
              {pick.is_marquee && (
                <span className="text-amber-500 dark:text-amber-400 text-sm" title="看板レース（決勝・特選クラス）">★</span>
              )}
              {/* ランクのゲートを通らず入稿したレース（手動入稿・看板の穴埋め）。
                  同じ 7A でも経路が違うので、混ぜたまま出すと「ランクの成績」と
                  読まれてしまう（実測でゲート通過と回収率が倍近く違う）。 */}
              {pick.submission_only && (
                <span
                  className="px-1 py-0.5 rounded text-[10px] font-semibold bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300"
                  title="ランクのゲートを通っていない入稿（手動・看板の穴埋め）。買い目と的中は入稿記録から表示しています"
                >
                  手動
                </span>
              )}
              {startTime && (
                <span className="font-semibold text-gray-800 dark:text-gray-100 text-sm">{startTime}</span>
              )}
              {(pick.grade || pick.race_type) && (
                <span className="text-gray-500 dark:text-gray-400 text-xs">{pick.grade ?? ""} {pick.race_type ?? ""}</span>
              )}
            </div>
          </div>
          {/* 折りたたみ時: 結果サマリー or オッズ（最低=ガミ判定値・合成）をインライン表示 */}
          {collapsed && isPendingResult && (
            <span className="text-xs text-gray-400 dark:text-gray-500 flex-shrink-0">未確定</span>
          )}
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
        <button
          type="button"
          onClick={handleSubmitRace}
          disabled={submitting}
          title="このレースのみnetkeirinへピンポイント入稿"
          aria-label="このレースのみnetkeirinへピンポイント入稿"
          className="flex-shrink-0 p-1 rounded text-gray-400 hover:text-blue-500 dark:text-gray-500 dark:hover:text-blue-400 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Send size={14} className={submitting ? "animate-pulse" : ""} />
        </button>
      </div>
      {submitMsg && (
        <p className="px-3 sm:px-4 py-1 text-[11px] text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 border-b border-gray-100 dark:border-gray-700">
          {submitMsg}
        </p>
      )}

      {/* 展開時コンテンツ */}
      {!collapsed && (
        <>
          {/* 買い目行（候補行はランク別買い目・それ以外は確定買い目） */}
          <div className="px-3 sm:px-4 py-1.5 border-b border-gray-50 dark:border-gray-700 flex items-center gap-2 sm:gap-3">
            {candRanks.length > 0 && candCombo ? (
              <CandBuyLines ranks={candRanks} combo={candCombo} />
            ) : multiBetLines ? (
              // 2券種併買（7H1）は券種ごとに改行する。横に繋げると折り返した時に
              // どちらの券種の買い目か読めなくなるため。
              <span className="text-xs sm:text-sm font-medium text-gray-700 dark:text-gray-200 flex-1 min-w-0 break-words">
                {multiBetLines.map((l) => (
                  <span key={l} className="block">{l}</span>
                ))}
              </span>
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

          {/* 🔴 ライン構成は**確定前後を問わず**出す（2026-08-10）。
              NoPickRow には最初から入っていたが PickCard には無く、
              推奨レースだけ隊列が読めない状態だった。line_group は
              wt_entries に残るので確定後も同じ内容を出せる。 */}
          <LineRow entries={pick.entries} />

          <EntryTable entries={pick.entries} />

          {isPendingResult && !pick.hit && (
            <div className="px-3 sm:px-4 py-2 border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
              <span className="text-xs text-gray-400 dark:text-gray-500">未確定（結果の取込待ち）</span>
            </div>
          )}
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

          {pick.submitted_bet && (
            <SubmittedBetBlock bet={pick.submitted_bet} entries={pick.entries} />
          )}
        </>
      )}
    </div>
  );
}

type PeriodData = KeirinSummary["today"];
type RankStats = NonNullable<PeriodData["by_rank"]>[string];

// by_rank キー: "7S"=RANK_7S（単勝×複勝指数トップ3重なり軸×波乱度選出）/
// "7A"=RANK_7Sの境界ランク / "9S"=RANK_9S（7Sの9車立て版） / "9A"=RANK_9Sの境界
// ランク（全てペーパー検証・名目賭金）。
// 2026-07-17 旧新S1(SIX_S1)/A(7PLUS_A) 全廃・2026-07-19 S1(SEVEN_S1)導入・2026-07-21 S7導入
// 2026-07-21 S2(7PLUS_U)/S3(7PLUS_M) 全廃、S7をgate_label(SS/S)で7SS/7Sの2ランクへ再編
// 2026-07-23 7SS内の軸級班denyフィルター通過分を7SS+として観察用に追加していたが、
// サンプル数不足のため2026-07-27にSSへ統合・廃止した。
// 2026-07-27 S9(9車立て・独立ランク)をトップライン（当日/当月/当年）に統合。
// 同日、内部名S4→S7へ統一し表示ランクにも対象車数の接頭辞（7 or 9）を揃えて付与。
// 同日、7A/9A（S7/S9の境界ランク）を新設。当初はROIの違いを踏まえ専用の別テーブルに
// 分離していたが、表示が煩雑とのユーザー要望により同日中にトップラインへ統合した。
// 2026-07-31: keirin側でgate_label('SS'/'S')による7SS/9SS・7S/9Sの分岐が廃止され
// 単一ランク化（旧7SS/9SSはS/9Sへ吸収）。同日、旧S1(SEVEN_S1)は全廃。
// 2026-08-01: 内部rank名の全面改名（RANK_*方式）に追随するとともに、独立ランク
// RANK_7SS をランク別展開へ追加した。
// 2026-08-02: その RANK_7SS を全廃（ROI73.5%・n=16,298）。並び順は 7S/7A/9S/9A。
// 表示ラベルの辞書順・車数まとまり（7車系→9車系）と揃え、サマリー/ランク別展開/
// 入稿設定など Web上の全ランク列挙でこの順序を単一の基準とする。
// ランク別展開では7車(7S/7A)・9車(9S/9A)を同じ一覧内に並べて確認できる
// ようにする（表示ラベルの先頭数字が対象車数を表し混同を防ぐ）。
// 2026-08-02: 7SS（波乱軸選出）を全廃したため一覧から除去した。
// 2026-08-05: 同じ "7SS" ラベルに**別戦略**（entropy不合格×軸2車が同一ライン）を
// 新設したため先頭へ戻した（keirin PR#10 `cb419d4`。旧7SSとは無関係で
// picks_history の旧7SS行は0件のため成績は混ざらない）。7SS>7S>7A の順で
// 確認窓ROIが単調（85.9 / 84.4 / 80.8%）なので、この並びがそのまま期待値順になる。
// 2026-08-06: 7H1（穴推奨・本命バスト型）を末尾へ追加した。S/A/B（的中率重視の
// 予想ベース）とは系統が違い期待値順に並べられないため、末尾に置いて区別する。
const RANK_ORDER = ["7SS", "7S", "7A", "7B", "9S", "9A", "7H1", "7H2", "9H1", "7H3", "7C"] as const;
const RANK_LABEL: Record<string, string> = {
  "7SS": "7SS", "7S": "7S", "7A": "7A", "7B": "7B", "9S": "9S", "9A": "9A",
  "7H1": "7H1",
  "7H2": "7H2",
  "9H1": "9H1",
  "7H3": "7H3",
  "7C": "7C",
};
const RANK_BADGE_STYLE: Record<string, string> = {
  "7SS": "bg-green-200 text-green-900 dark:bg-green-800/60 dark:text-green-200",
  "7S": "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
  "7A": "bg-stone-100 text-stone-700 dark:bg-stone-800/60 dark:text-stone-300",
  "7B": "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400",
  "9S": "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-400",
  "9A": "bg-slate-100 text-slate-700 dark:bg-slate-800/60 dark:text-slate-300",
  "7H1": "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400",
  "7H2": "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-400",
  "9H1": "bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/40 dark:text-fuchsia-400",
  "7H3": "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-400",
  "7C": "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-400",
};

/** 投資・回収・最大払戻等、モバイルでは既定で隠す列のクラス。showAll時は常時表示。 */
function mobileColClass(showAll: boolean): string {
  return showAll ? "table-cell" : "hidden sm:table-cell";
}

function RankSubRow({ rankKey, data, showAll, labelMap = RANK_LABEL, badgeStyleMap = RANK_BADGE_STYLE }: {
  rankKey: string; data: RankStats; showAll: boolean;
  labelMap?: Record<string, string>; badgeStyleMap?: Record<string, string>;
}) {
  const roiColor = data.roi == null
    ? "text-gray-400"
    : data.roi >= 1.0
      ? "text-emerald-600 font-semibold"
      : "text-red-500";
  const hitRate = data.n_picks > 0
    ? `${((data.n_hits / data.n_picks) * 100).toFixed(0)}%`
    : "—";
  const badgeClass = badgeStyleMap[rankKey] ?? "bg-gray-100 text-gray-600";

  return (
    <tr className="border-b border-gray-50 dark:border-gray-800 last:border-0 bg-gray-50/50 dark:bg-gray-800/30">
      <td className="py-1 px-2 sm:px-3">
        <span className="flex items-center gap-1.5 pl-3">
          <span className={`inline-flex items-center justify-center min-w-6 px-1 h-5 rounded text-xs font-bold ${badgeClass}`}>
            {labelMap[rankKey] ?? rankKey}
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

function SummaryRow({ label, sub, data, showRanks, showAll, rankOrder = RANK_ORDER, rankLabelMap = RANK_LABEL, rankBadgeStyleMap = RANK_BADGE_STYLE }: {
  label: string; sub?: string; data: PeriodData; showRanks?: boolean; showAll: boolean;
  rankOrder?: readonly string[]; rankLabelMap?: Record<string, string>; rankBadgeStyleMap?: Record<string, string>;
}) {
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
      {hasRanks && rankOrder.map(rk => {
        // 0件のランクもゼロ埋めで表示する（省略しない）
        const rd = byRank[rk] ?? {
          n_picks: 0, n_hits: 0, total_bet: 0, total_payout: 0,
          roi: null, n_candidates: 0, max_payout: null,
        };
        return <RankSubRow key={rk} rankKey={rk} data={rd} showAll={showAll} labelMap={rankLabelMap} badgeStyleMap={rankBadgeStyleMap} />;
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
  // 表示中の日付に開催がある場だけを出すトグル。null = 全て。
  // ⚠️ 日付を変えたら選択を解除する（前日に選んでいた場が翌日は無いことがあり、
  //    そのままだと**1件も出ない画面**になって「壊れた」ように見える）。
  const [venueFilter, setVenueFilter] = useState<string | null>(null);
  const [hideNoPickRows, setHideNoPickRows] = useState(false);
  // 承認制（netkeirin_settings._global.require_approval）と未承認の入稿案件数。
  // ⚠️ 承認制が ON のときに承認を忘れると **その日の商品が1件も出ない**。
  //    確認画面への導線と件数バッジをここに出すのがその唯一の常時サインになる
  //    （Discord の催促は締切前に一度だけ・見落としうる）。
  const [requireApproval, setRequireApproval] = useState(false);
  const [nProposed, setNProposed] = useState(0);

  /** 表示中の日付に開催がある場（picks の出現順＝発走順）。 */
  const venues = useMemo(() => {
    const seen: string[] = [];
    for (const p of picks) {
      if (p.venue_name && !seen.includes(p.venue_name)) seen.push(p.venue_name);
    }
    return seen;
  }, [picks]);

  /** 場フィルタ適用後のピック。null（全て）ならそのまま。 */
  const shownPicks = useMemo(
    () => (venueFilter ? picks.filter((p) => p.venue_name === venueFilter) : picks),
    [picks, venueFilter],
  );
  const hasCand = picks.some((p) => p.race_key.includes("#CAND"));
  // 隠せる行（ピック無し・ガミ落ちで推奨外が確定した行）がある日だけ切替を出す。
  // 判定は一覧側の描画条件と**同じ式**にすること（片方だけ直すと、ボタンは
  // 出るのに何も隠れない／隠れるのにボタンが無い、という食い違いになる）。
  const hasHideableRows = picks.some((p) => !p.has_pick || computeGamiSkip(p));

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

  useEffect(() => {
    void loadData(date);
  }, [date, loadData]);

  // 日付が変わったら場の選択を解除する（上記の理由）
  useEffect(() => {
    setVenueFilter(null);
  }, [date]);

  // 未承認バッジ。まず承認制かどうかだけを引き（軽い）、**ON のときだけ**
  // 入稿案を取りに行く（/proposals は全レースの買い目・出走表を含んで重いため、
  // 承認制 OFF の平常時にトップページへ乗せてはいけない）。
  useEffect(() => {
    let alive = true;
    void (async () => {
      const mode = await fetchKeirinApprovalMode().catch(() => ({ require_approval: false }));
      if (!alive) return;
      setRequireApproval(mode.require_approval);
      if (!mode.require_approval) {
        setNProposed(0);
        return;
      }
      const p = await fetchKeirinProposals(toISODate(date)).catch(() => ({ n_proposed: 0 }));
      if (alive) setNProposed(p.n_proposed);
    })();
    return () => { alive = false; };
  }, [date]);

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
          {/* 2026-08-03: netkeirin売上推移を /keirin/stats に追加したのに合わせ、
              ラベルを「成績グラフ」→「成績・売上」へ変更し、**モバイルでも常時表示**する
              （他2つと同じ hidden sm:inline だとモバイルではアイコンのみになり、
              売上ページへの導線が事実上見つけられなかった＝ユーザー指摘）。 */}
          <Link
            href="/keirin/stats"
            className="flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:text-blue-500 dark:hover:text-blue-300 transition-colors"
            aria-label="成績・売上グラフ"
          >
            <BarChart2 size={16} />
            <span>成績・売上</span>
          </Link>
          {/* 入稿の確認・承認。2026-08-11 の新設時に導線を付け忘れており、URL を
              直接打たないと辿り着けなかった（ユーザー指摘）。
              ⚠️ 未承認が残っているときは**モバイルでもラベルを出す**。
                 他と同じ hidden sm:inline にすると、承認しないとその日の商品が
                 出ないという最も重い状態がアイコン1つに潰れる
                 （成績・売上リンクで同じ失敗をしている）。 */}
          <Link
            href={`/keirin/review?date=${toISODate(date)}`}
            className={`flex items-center gap-1 text-xs transition-colors ${
              nProposed > 0
                ? "font-semibold text-red-600 dark:text-red-400 hover:text-red-500"
                : "text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400"
            }`}
            aria-label={nProposed > 0 ? `入稿確認（未承認 ${nProposed} 件）` : "入稿確認"}
          >
            <ClipboardCheck size={15} />
            <span className={nProposed > 0 ? "" : "hidden sm:inline"}>入稿確認</span>
            {nProposed > 0 && (
              <span className="rounded-full bg-red-600 px-1.5 py-0.5 text-[10px] font-bold leading-none text-white">
                {nProposed}
              </span>
            )}
            {/* 承認制 ON で未承認ゼロ＝全部さばけている、と分かるようにする */}
            {requireApproval && nProposed === 0 && (
              <span className="hidden sm:inline text-[10px] text-emerald-600 dark:text-emerald-400">承認済</span>
            )}
          </Link>
          <Link
            href="/keirin/help"
            className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
          >
            <HelpCircle size={15} />
            <span className="hidden sm:inline">推奨ガイド</span>
          </Link>
          <Link
            href="/keirin/settings"
            className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
            aria-label="入稿設定"
          >
            <Settings size={15} />
            <span className="hidden sm:inline">入稿設定</span>
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
          <div className="space-y-2">
            {shownPicks.map((p, idx) => {
              if (!p.has_pick) {
                if (hideNoPickRows) return null;
                return <NoPickRow key={`nopick-${p.race_key}-${idx}`} pick={p} />;
              }
              // ガミ落ち（オッズ条件で推奨外確定）も「推奨外を非表示」スイッチで隠す
              if (hideNoPickRows && computeGamiSkip(p)) return null;
              // 入稿だけの行は picks_history の id を持たない。キーは race_key で作る
              // （id=null のまま並べると全部同じキーになり React が行を取り違える）。
              const rowId = p.id ?? p.race_key;
              return <PickCard key={`pick-${rowId}-${p.race_key}`} pick={p} cardId={`pick-${rowId}`} />;
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
          {/* 行2: アクション（採点更新・推奨外の表示切替・場フィルタ）
              ⚠️ 場の数は日によって変わる（当日6場など）ので **折り返す**。
                 横スクロールにすると右側の場が画面外に隠れ、押せることに気づけない。
                 折り返す都合で既存2ボタンの flex-1（横幅いっぱいに伸ばす）は外し、
                 内容ぶんの自然幅にしてある。 */}
          {(hasCand || hasHideableRows || venues.length > 1) && (
            <div className="flex flex-wrap items-center gap-1.5">
              {hasCand && (
                <button
                  onClick={handleRefresh}
                  disabled={refreshing}
                  className="shrink-0 px-2.5 py-1.5 rounded-lg border border-orange-300 dark:border-orange-600 text-xs font-semibold text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/20 hover:bg-orange-100 dark:hover:bg-orange-900/40 disabled:opacity-50 disabled:cursor-not-allowed text-center whitespace-nowrap"
                >
                  {refreshing ? "採点中…" : "⚡ 採点更新"}
                </button>
              )}
              {/* 推奨外（ピック無し・ガミ落ち）の表示切替。
                  ⚠️ ラベルは**現在の状態**を書く（「非表示にする」ではなく「非表示中」）。
                     トグルは押した後の状態を書くか今の状態を書くかで意味が反転するため。 */}
              {hasHideableRows && (
                <button
                  type="button"
                  aria-pressed={hideNoPickRows}
                  onClick={() => {
                    const next = !hideNoPickRows;
                    setHideNoPickRows(next);
                    localStorage.setItem(HIDE_NOPICK_KEY, String(next));
                  }}
                  className={`shrink-0 px-2.5 py-1.5 rounded-lg border text-xs font-semibold text-center whitespace-nowrap transition-colors ${
                    hideNoPickRows
                      ? "border-blue-500 dark:border-blue-400 text-white bg-blue-500 dark:bg-blue-600 hover:bg-blue-600 dark:hover:bg-blue-700"
                      : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700"
                  }`}
                >
                  {hideNoPickRows ? "🙈 推奨外 非表示中" : "👁 推奨外 表示中"}
                </button>
              )}
              {/* 場フィルタ。**一番左が「全て」**。開催が2場以上ある日だけ出す
                  （1場しかない日は選択肢にならない）。 */}
              {venues.length > 1 && (
                <>
                  <button
                    type="button"
                    aria-pressed={venueFilter === null}
                    onClick={() => setVenueFilter(null)}
                    className={`shrink-0 px-2.5 py-1.5 rounded-lg border text-xs font-semibold whitespace-nowrap transition-colors ${
                      venueFilter === null
                        ? "border-blue-500 dark:border-blue-400 text-white bg-blue-500 dark:bg-blue-600"
                        : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700"
                    }`}
                  >
                    全て
                  </button>
                  {venues.map((v) => (
                    <button
                      key={v}
                      type="button"
                      aria-pressed={venueFilter === v}
                      onClick={() => setVenueFilter((cur) => (cur === v ? null : v))}
                      className={`shrink-0 px-2.5 py-1.5 rounded-lg border text-xs font-semibold whitespace-nowrap transition-colors ${
                        venueFilter === v
                          ? "border-blue-500 dark:border-blue-400 text-white bg-blue-500 dark:bg-blue-600"
                          : "border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700"
                      }`}
                    >
                      {v}
                    </button>
                  ))}
                </>
              )}
            </div>
          )}
          {/* アクション実行メッセージ */}
          {refreshMsg && (
            <p className="text-[11px] text-gray-500 dark:text-gray-400 leading-tight text-center">
              {refreshMsg}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
