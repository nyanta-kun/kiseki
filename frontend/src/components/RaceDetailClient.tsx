"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  HorseIndex,
  OddsData,
  RaceResult,
  buildOddsWsUrl,
  buildResultsWsUrl,
  fetchHorseHistory,
  fetchOddsBrowser,
  fetchResultsBrowser,
} from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { WsStatusBadge } from "@/components/WsStatusBadge";
import { DmSignalBadges, DM_SIGNAL_META } from "@/components/DmSignalBadges";
import { SIGNAL_HEIHACHI, matchesHeihachi } from "@/lib/heihachi";
import { useHeihachiThresholds } from "@/lib/useHeihachiThresholds";
import { IndexBar } from "./IndexBar";
import { cn, indexColor, horseNumToFrame, frameColorClass, EV_HIGHLIGHT_THRESHOLD } from "@/lib/utils";
import { HorseHistorySection } from "./HorseHistorySection";
import { PaywallGate } from "@/components/PaywallGate";

type Props = {
  raceId: number;
  indices: HorseIndex[];
  initialOdds: OddsData;
  initialResults: RaceResult[];
  isPremium?: boolean;
  raceNumber?: number;
  paywallEnabled?: boolean;
  /** 平八バッジのレース選定に使う。keiba.races.grade をそのまま渡す。 */
  grade?: string | null;
};

type SortKey = "composite" | "speed" | "last3f" | "jockey" | "rotation" | "finish";

const SUB_INDICES: { key: keyof HorseIndex; label: string }[] = [
  { key: "speed_index",       label: "速度"   },
  { key: "last3f_index",      label: "後3F"   },
  { key: "course_aptitude",   label: "コース" },
  { key: "jockey_index",      label: "騎手"   },
  { key: "pace_index",        label: "展開"   },
  { key: "rotation_index",    label: "ローテ" },
  { key: "pedigree_index",    label: "血統"   },
  { key: "position_advantage",label: "枠順"   },
  { key: "training_index",    label: "調教"   },
  { key: "paddock_index",     label: "パドック"},
];

const ANAGUSA_RANK_COLOR: Record<string, string> = {
  A: "bg-red-50 text-red-600 border-red-200",
  B: "bg-orange-50 text-orange-600 border-orange-200",
  C: "bg-yellow-50 text-yellow-700 border-yellow-200",
};

function barWidth(v: number | null): string {
  if (v === null) return "0%";
  return `${Math.max(0, Math.min(100, v))}%`;
}

function pct(v: number | null): string {
  if (v === null) return "–";
  return `${Math.round(v * 100)}%`;
}

function winOddsColorClass(odds: number | null): string {
  if (odds === null) return "text-gray-600";
  if (odds < 10) return "text-red-600 font-semibold";
  if (odds >= 100) return "text-blue-600";
  return "text-gray-600";
}

function evColorClass(ev: number | null): string {
  if (ev === null) return "text-gray-400";
  if (ev >= 1.5) return "text-green-600 font-bold";
  if (ev >= EV_HIGHLIGHT_THRESHOLD) return "text-green-500 font-semibold";
  if (ev >= 1.0) return "text-gray-600";
  return "text-gray-400";
}

function finishBadgeClass(pos: number | null | undefined): string {
  if (pos == null) return "text-gray-400";
  if (pos === 1) return "bg-yellow-100 text-yellow-800 font-bold px-1 rounded";
  if (pos === 2) return "bg-gray-100 text-gray-700 font-bold px-1 rounded";
  if (pos === 3) return "bg-orange-100 text-orange-700 font-bold px-1 rounded";
  return "text-gray-400";
}

function toResultsMap(results: RaceResult[]): Map<number, number | null> {
  return new Map(
    results
      .filter((r) => r.horse_number !== null)
      .map((r) => [r.horse_number as number, r.finish_position])
  );
}

function useIsMounted() {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

function SortButton({
  k, label, sortKey, setSortKey,
}: {
  k: SortKey; label: string; sortKey: SortKey; setSortKey: (k: SortKey) => void;
}) {
  return (
    <button
      onClick={() => setSortKey(k)}
      className={cn(
        "text-[10px] px-2 py-0.5 rounded-full border transition-colors whitespace-nowrap",
        sortKey === k
          ? "text-white border-green-600 bg-green-700"
          : "text-gray-500 border-gray-200 hover:border-green-400 bg-white"
      )}
    >
      {label}
    </button>
  );
}

function formatTime(sec: number | null): string {
  if (sec === null) return "–";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export function RaceDetailClient({
  raceId,
  indices,
  initialOdds,
  initialResults,
  isPremium = false,
  raceNumber = 1,
  paywallEnabled = false,
  grade = null,
}: Props) {
  // 平八バッジは推奨ページのスライダーで動かしたしきい値に追随する
  // （判定は lib/heihachi.ts が単一真実源。サーバー側 dm_signals の "平八" は
  //  既定値ベースなので表示からは外し、ここで付け直す）。
  const { thresholds: heihachi } = useHeihachiThresholds();
  const mounted = useIsMounted();
  const [resultsMap, setResultsMap] = useState<Map<number, number | null>>(
    () => toResultsMap(initialResults)
  );
  const [odds, setOdds] = useState<OddsData>(initialOdds ?? { win: {}, place: {} });
  const liveRegionRef = useRef<HTMLDivElement | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("composite");
  const [expandedHorse, setExpandedHorse] = useState<number | null>(null);
  const hasResults = resultsMap.size > 0;

  // 成績 WebSocket
  const resultsWsUrl = mounted ? buildResultsWsUrl(raceId) : null;
  const handleResultsMessage = useCallback((data: unknown) => {
    if (Array.isArray(data) && data.length > 0) {
      setResultsMap(toResultsMap(data as RaceResult[]));
    }
  }, []);
  const { isConnected: wsConnected } = useWebSocket(resultsWsUrl, handleResultsMessage);

  // オッズ WebSocket
  const oddsWsUrl = mounted ? buildOddsWsUrl(raceId) : null;
  const handleOddsMessage = useCallback((data: unknown) => {
    setOdds(data as OddsData);
    if (liveRegionRef.current) {
      liveRegionRef.current.textContent = "オッズが更新されました";
      setTimeout(() => {
        if (liveRegionRef.current) liveRegionRef.current.textContent = "";
      }, 3000);
    }
  }, []);
  useWebSocket(oddsWsUrl, handleOddsMessage, { reconnectInterval: 30_000 });

  // HTTP ポーリング（30秒間隔でオッズ・成績のみ更新、画面全体の再レンダリングなし）
  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const [newOdds, newResults] = await Promise.all([
          fetchOddsBrowser(raceId),
          fetchResultsBrowser(raceId),
        ]);
        setOdds(newOdds);
        if (newResults.length > 0) setResultsMap(toResultsMap(newResults));
      } catch {
        // ネットワーク障害時は無視（次回ポーリングで回復）
      }
    }, 30_000);
    return () => clearInterval(timer);
  }, [raceId]);

  const totalHorses = indices.length;

  const topHorseNumber = useMemo(
    () =>
      indices.reduce(
        (best, h) => (h.composite_index > best.composite_index ? h : best),
        indices[0]
      )?.horse_number,
    [indices]
  );

  const sorted = useMemo(() => {
    return [...indices].sort((a, b) => {
      if (sortKey === "finish" && hasResults) {
        const pa = resultsMap.get(a.horse_number) ?? 999;
        const pb = resultsMap.get(b.horse_number) ?? 999;
        return pa - pb;
      }
      const keyMap: Partial<Record<SortKey, keyof HorseIndex>> = {
        composite: "composite_index",
        speed:     "speed_index",
        last3f:    "last3f_index",
        jockey:    "jockey_index",
        rotation:  "rotation_index",
      };
      const k = keyMap[sortKey] ?? "composite_index";
      const av = (a[k] as number | null) ?? 0;
      const bv = (b[k] as number | null) ?? 0;
      return bv - av;
    });
  }, [indices, sortKey, hasResults, resultsMap]);

  /** 足切り判定は着外率（6着以下確率）ベース。単一真実源はバックエンド（is_cut_off）。 */
  function isCutOff(horse: HorseIndex): boolean {
    return horse.is_cut_off ?? false;
  }

  const colSpan = hasResults ? 12 : 11;

  return (
    <PaywallGate isPremium={isPremium} raceNumber={raceNumber} paywallEnabled={paywallEnabled ?? false}>
      <>
        <div ref={liveRegionRef} aria-live="polite" aria-atomic="true" className="sr-only" />

        <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
          {/* ヘッダー + ソートボタン */}
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
              <span className="w-1 h-4 rounded inline-block bg-green-600" />
              出馬表 指数一覧
              <span className="text-xs text-gray-400 font-normal ml-1">{indices.length}頭</span>
              {mounted && wsConnected !== undefined && (
                <span className="ml-1">
                  <WsStatusBadge connected={wsConnected} label="成績更新: 再接続中…" />
                </span>
              )}
            </h2>
            <div className="flex gap-1 ml-auto flex-wrap">
              <SortButton k="composite" label="総合" sortKey={sortKey} setSortKey={setSortKey} />
              <SortButton k="speed" label="速度" sortKey={sortKey} setSortKey={setSortKey} />
              <span className="hidden sm:contents">
                <SortButton k="last3f" label="後3F" sortKey={sortKey} setSortKey={setSortKey} />
                <SortButton k="jockey" label="騎手" sortKey={sortKey} setSortKey={setSortKey} />
                <SortButton k="rotation" label="ローテ" sortKey={sortKey} setSortKey={setSortKey} />
              </span>
              {hasResults && (
                <SortButton k="finish" label="着順" sortKey={sortKey} setSortKey={setSortKey} />
              )}
            </div>
          </div>

          {/* テーブル */}
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-xs min-w-[320px]">
              <thead>
                <tr className="border-b border-gray-100 text-gray-400 text-[10px]">
                  <th className="text-right py-1 pl-2 pr-2 w-8">馬番</th>
                  <th className="text-left py-1 px-1">馬名</th>
                  <th className="text-right py-1 px-1 w-20">総合</th>
                  <th className="text-right py-1 px-1 w-12">速度</th>
                  <th className="hidden sm:table-cell text-right py-1 px-1 w-12">後3F</th>
                  <th className="hidden sm:table-cell text-right py-1 px-1 w-12">騎手</th>
                  <th className="hidden sm:table-cell text-right py-1 px-1 w-12">ローテ</th>
                  <th className="text-right py-1 px-1 w-12">勝率</th>
                  <th className="text-right py-1 px-1 w-12">複率</th>
                  <th className="text-right py-1 px-1 w-14">単オッズ</th>
                  <th className="text-right py-1 pr-2 w-12">期待値</th>
                  {hasResults && <th className="text-right py-1 pr-2 w-10">着順</th>}
                </tr>
              </thead>
              <tbody>
                {sorted.flatMap((horse) => {
                  const finishPos = resultsMap.get(horse.horse_number);
                  const isWin = finishPos === 1;
                  const isPlace = finishPos !== undefined && finishPos !== null && finishPos <= 3;
                  const winOdds = odds.win[String(horse.horse_number)] ?? null;
                  const ev =
                    horse.win_probability !== null && winOdds !== null
                      ? horse.win_probability * winOdds
                      : null;
                  const frameNum = horseNumToFrame(horse.horse_number, totalHorses);
                  const cutOff = isCutOff(horse);
                  const isTop = horse.horse_number === topHorseNumber;
                  const isAnagusa = horse.anagusa_rank !== null && !isTop;
                  const isExpanded = expandedHorse === horse.horse_number;

                  const rows = [
                    <tr
                      key={horse.horse_number}
                      onClick={() => setExpandedHorse(isExpanded ? null : horse.horse_number)}
                      className={cn(
                        "border-b border-gray-50 transition-colors whitespace-nowrap cursor-pointer",
                        cutOff ? "opacity-40 bg-gray-50" :
                        isWin ? "bg-yellow-50" :
                        isPlace ? "bg-orange-50/40" :
                        isTop ? "bg-green-50/40 hover:bg-green-50" :
                        "hover:bg-gray-50"
                      )}
                    >
                      {/* 馬番 */}
                      <td className="py-1.5 pl-2 pr-2 text-right">
                        <span className={cn(
                          "inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold tabular-nums",
                          frameColorClass(frameNum)
                        )}>
                          {horse.horse_number}
                        </span>
                      </td>

                      {/* 馬名 + バッジ */}
                      <td className="py-2 px-1 whitespace-normal">
                        <div className="flex items-center gap-1 flex-wrap">
                          {/*
                            スイートスポット該当馬は馬名を赤字にする。

                            🔴 2026-09-01 復元。この表示は旧 IndicesTable.tsx にあったが、
                               同コンポーネントが RaceDetailClient に置き換えられた際に
                               描画だけが落ちていた（IndicesTable への参照は 0 件）。
                               バックエンドは races.py で is_sweet_spot を毎回算出して
                               返し続けていたため、エラーもログも出ないまま
                               「計算しているのに画面に出ない」状態になっていた。
                          */}
                          <span
                            className={cn(
                              "font-medium truncate block max-w-[110px]",
                              horse.is_sweet_spot ? "text-red-600 font-semibold" : "text-gray-800"
                            )}
                            title={
                              horse.is_sweet_spot
                                ? "スイートスポット該当: 単勝10倍以上 ∧ 期待値1.2〜5.0 ∧ バッジあり ∧ レース内3頭未満。"
                                  + "表示のみの目安で、推奨（軸の信頼度）には使っていない。OOS 検証では脆弱。"
                                : undefined
                            }
                          >
                            {horse.horse_name}
                          </span>
                          {isAnagusa && (
                            <span className={cn(
                              "text-[9px] px-1 py-0.5 rounded border font-bold",
                              ANAGUSA_RANK_COLOR[horse.anagusa_rank!] ?? "bg-yellow-50 text-yellow-700 border-yellow-200"
                            )}>
                              ☆{horse.anagusa_rank}
                            </span>
                          )}
                          {/* 穴候補（レース内最有力1頭のみ・軸信頼度は購入指針パネルのrecommend_rankに一本化） */}
                          {/* 平八はユーザーのしきい値で判定し直すので、サーバー側のタグは落とす */}
                          <DmSignalBadges
                            signals={[
                              ...(horse.dm_signals ?? []).filter((t) => t !== SIGNAL_HEIHACHI),
                              ...(matchesHeihachi(
                                {
                                  grade,
                                  indexRank: horse.composite_rank,
                                  winOdds: winOdds,
                                  placeProbability: horse.place_probability,
                                },
                                heihachi,
                              )
                                ? [SIGNAL_HEIHACHI]
                                : []),
                            ]}
                            compact
                          />
                        </div>
                      </td>

                      {/* 総合 + バー */}
                      <td className="py-2 px-1">
                        <div className="flex items-center gap-1 justify-end">
                          <span className={indexColor(horse.composite_index)}>
                            {horse.composite_index.toFixed(1)}
                          </span>
                          <div className="w-12 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-green-500 rounded-full"
                              style={{ width: barWidth(horse.composite_index) }}
                            />
                          </div>
                        </div>
                      </td>

                      {/* 速度 */}
                      <td className={`py-2 px-1 text-right ${indexColor(horse.speed_index)}`}>
                        {horse.speed_index !== null ? horse.speed_index.toFixed(1) : "–"}
                      </td>

                      {/* 後3F */}
                      <td className={`hidden sm:table-cell py-2 px-1 text-right ${indexColor(horse.last3f_index)}`}>
                        {horse.last3f_index !== null ? horse.last3f_index.toFixed(1) : "–"}
                      </td>

                      {/* 騎手 */}
                      <td className={`hidden sm:table-cell py-2 px-1 text-right ${indexColor(horse.jockey_index)}`}>
                        {horse.jockey_index !== null ? horse.jockey_index.toFixed(1) : "–"}
                      </td>

                      {/* ローテ */}
                      <td className={`hidden sm:table-cell py-2 px-1 text-right ${indexColor(horse.rotation_index)}`}>
                        {horse.rotation_index !== null ? horse.rotation_index.toFixed(1) : "–"}
                      </td>

                      {/* 勝率 */}
                      <td className="py-2 px-1 text-right text-gray-600">
                        {pct(horse.win_probability)}
                      </td>

                      {/* 複率 */}
                      <td className="py-2 px-1 text-right text-gray-600">
                        {pct(horse.place_probability)}
                      </td>

                      {/* 単オッズ */}
                      <td className={`py-2 px-1 text-right ${winOddsColorClass(winOdds)}`}>
                        {winOdds !== null ? `${winOdds.toFixed(1)}倍` : "–"}
                      </td>

                      {/* 期待値 */}
                      <td className={`py-2 pr-2 text-right ${evColorClass(ev)}`}>
                        {ev !== null ? ev.toFixed(2) : "–"}
                      </td>

                      {/* 着順 */}
                      {hasResults && (
                        <td className="py-2 pr-2 text-right">
                          {finishPos != null ? (
                            <span className={finishBadgeClass(finishPos)}>{finishPos}着</span>
                          ) : (
                            <span className="text-gray-300">–</span>
                          )}
                        </td>
                      )}
                    </tr>,
                  ];

                  if (isExpanded) {
                    rows.push(
                      <tr key={`${horse.horse_number}-detail`}>
                        <td colSpan={colSpan} className="border-b border-gray-100 bg-gray-50 px-3 py-3">
                          {/* 指数内訳ヘッダー */}
                          <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                            <p className="text-[10px] text-gray-400">指数内訳</p>
                            <div className="flex items-center gap-2 flex-wrap">
                              {/* 外部指数ランク */}
                              {(horse.nb_course_rank !== null || horse.nb_ave_rank !== null || horse.km_rank !== null) && (
                                <div className="flex items-center gap-1 flex-wrap">
                                  <span className="text-[10px] text-gray-400">外部指数</span>
                                  {horse.nb_course_rank !== null && (
                                    <span className={cn(
                                      "text-[10px] px-1 py-0.5 rounded border",
                                      horse.nb_course_rank === 1
                                        ? "bg-teal-50 text-teal-700 border-teal-200 font-bold"
                                        : "bg-gray-50 text-gray-500 border-gray-200"
                                    )}>
                                      コース{horse.nb_course_rank}位
                                    </span>
                                  )}
                                  {horse.nb_ave_rank !== null && (
                                    <span className={cn(
                                      "text-[10px] px-1 py-0.5 rounded border",
                                      horse.nb_ave_rank <= 2
                                        ? "bg-teal-50 text-teal-700 border-teal-200 font-bold"
                                        : "bg-gray-50 text-gray-500 border-gray-200"
                                    )}>
                                      NB{horse.nb_ave_rank}位
                                    </span>
                                  )}
                                  {horse.km_rank !== null && (
                                    <span className={cn(
                                      "text-[10px] px-1 py-0.5 rounded border",
                                      horse.km_rank === 1
                                        ? "bg-teal-50 text-teal-700 border-teal-200 font-bold"
                                        : "bg-gray-50 text-gray-500 border-gray-200"
                                    )}>
                                      KM{horse.km_rank}位
                                    </span>
                                  )}
                                </div>
                              )}
                              {/* 穴スコア */}
                              {horse.upside_score !== null && horse.upside_score !== undefined && (
                                <div className="flex items-center gap-1">
                                  <span className="text-[10px] text-gray-400">穴スコア</span>
                                  <span className={cn(
                                    "text-[11px] font-bold px-1.5 py-0.5 rounded border",
                                    horse.upside_score >= 0.7
                                      ? "bg-purple-100 text-purple-800 border-purple-300"
                                      : horse.upside_score >= 0.5
                                      ? "bg-purple-50 text-purple-700 border-purple-200"
                                      : "bg-gray-50 text-gray-500 border-gray-200"
                                  )}>
                                    {(horse.upside_score * 100).toFixed(0)}
                                  </span>
                                </div>
                              )}
                            </div>
                          </div>

                          {/* 穴ぐさコメント（専門紙ピック時のみ） */}
                          {horse.anagusa_rank && horse.anagusa_comment && (
                            <div className={cn(
                              "mb-3 px-2.5 py-2 rounded border text-[11px] leading-relaxed",
                              ANAGUSA_RANK_COLOR[horse.anagusa_rank] ?? "bg-yellow-50 text-yellow-700 border-yellow-200"
                            )}>
                              <span className="font-bold mr-1">☆{horse.anagusa_rank} 穴ぐさコメント</span>
                              <span className="text-gray-700">{horse.anagusa_comment}</span>
                            </div>
                          )}

                          {/* 指数グリッド */}
                          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                            {SUB_INDICES.map(({ key, label }) => {
                              const val = horse[key] as number | null;
                              return (
                                <div key={key} className="flex items-center gap-1.5">
                                  <span className="text-[10px] text-gray-500 w-10 flex-shrink-0">
                                    {label}
                                  </span>
                                  <span className={cn(
                                    "text-[11px] font-mono tabular-nums w-7 text-right flex-shrink-0",
                                    indexColor(val)
                                  )}>
                                    {val !== null ? val.toFixed(0) : "-"}
                                  </span>
                                  <div className="flex-1">
                                    <IndexBar value={val} />
                                  </div>
                                </div>
                              );
                            })}
                          </div>

                          {/* 近走成績 */}
                          <HorseHistorySection
                            horseId={horse.horse_id}
                            fetchHistory={fetchHorseHistory}
                          />
                        </td>
                      </tr>
                    );
                  }

                  return rows;
                })}
              </tbody>
            </table>
          </div>

          {/* 確定着順サマリ */}
          {hasResults && (
            <div className="mt-4 pt-3 border-t border-gray-100">
              <h3 className="text-xs font-semibold text-gray-500 mb-2">確定着順</h3>
              <div className="space-y-1">
                {initialResults
                  .filter((r) => r.finish_position !== null)
                  .sort((a, b) => (a.finish_position ?? 99) - (b.finish_position ?? 99))
                  .slice(0, 5)
                  .map((r) => (
                    <div key={r.horse_name} className="grid text-xs" style={{ gridTemplateColumns: "2.5rem 7rem 1fr 1fr" }}>
                      <span className={cn(
                        "text-center text-[11px] py-0.5 rounded font-bold",
                        r.finish_position === 1 ? "bg-yellow-100 text-yellow-800" :
                        r.finish_position === 2 ? "bg-gray-100 text-gray-700" :
                        r.finish_position === 3 ? "bg-orange-100 text-orange-700" :
                        "text-gray-500"
                      )}>
                        {r.finish_position}着
                      </span>
                      <span className="font-medium text-gray-800 truncate px-1">{r.horse_name}</span>
                      <span className="text-gray-400 tabular-nums">
                        {r.finish_time !== null ? formatTime(r.finish_time) : ""}
                      </span>
                      <span className="text-gray-400 tabular-nums">
                        {r.last_3f !== null ? `後3F ${r.last_3f.toFixed(1)}` : ""}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* 凡例 */}
          <div className="mt-3 text-[10px] text-gray-400 border-t border-gray-50 pt-2 space-y-0.5">
            <p>
              <span className="text-green-600">緑</span>=高評価 / <span className="text-red-500">赤</span>=低評価（65↑: 強 / 55–65: 良 / 45–55: 並 / 35–45: 劣 / ↓35: 弱）
            </p>
            <p>
              <span className="opacity-50">グレー</span>=足切り候補（着外率80%以上）
            </p>
            <p>行クリックで指数内訳・近走成績を表示</p>
          </div>

          {/* バッジ凡例 */}
          <details className="mt-4 text-[11px] text-gray-600 border border-gray-200 rounded-md bg-gray-50">
            <summary className="cursor-pointer font-bold px-3 py-2 select-none">
              バッジ凡例（クリックで展開）
            </summary>
            <div className="px-3 pb-3 pt-1 space-y-3">

              {/* 穴候補 */}
              <div>
                <p className="font-bold text-gray-700 mb-1">
                  📊 穴候補 <span className="font-normal text-gray-500 text-[10px]">(JV-Next DM × 穴ぐさ × 既存指数、レース1頭のみ)</span>
                </p>
                <ul className="space-y-1 ml-1">
                  {Object.entries(DM_SIGNAL_META).map(([name, meta]) => (
                    <li key={name} className="flex items-start gap-2">
                      <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", meta.cls)}>
                        {meta.label}
                      </span>
                      <span>{meta.title}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* 穴ぐさランク */}
              <div>
                <p className="font-bold text-gray-700 mb-1">⭐ 穴ぐさランク <span className="font-normal text-gray-500 text-[10px]">(専門紙 sekito.anagusa ピック)</span></p>
                <ul className="space-y-1 ml-1">
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", ANAGUSA_RANK_COLOR.A)}>
                      ☆A
                    </span>
                    <span>最高評価ピック（穴推し本命）</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", ANAGUSA_RANK_COLOR.B)}>
                      ☆B
                    </span>
                    <span>準本命の穴推し</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className={cn("text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap shrink-0", ANAGUSA_RANK_COLOR.C)}>
                      ☆C
                    </span>
                    <span>注目穴馬</span>
                  </li>
                </ul>
              </div>

              <p className="text-[10px] text-gray-500 italic pt-1 border-t border-gray-200">
                ※ ROI = 100円賭けた時の平均回収額 / 100。1.0以上で期待値プラス。
                各バッジにマウスを合わせるとツールチップで詳細条件が表示されます。
                表は composite_index 降順（上から指数1位）で表示されます。
              </p>
            </div>
          </details>
        </section>
      </>
    </PaywallGate>
  );
}
