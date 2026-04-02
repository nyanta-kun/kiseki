"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { HorseIndex, OddsData, RaceHistoryEntry, buildOddsWsUrl, fetchHorseHistory } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { WsStatusBadge } from "@/components/WsStatusBadge";
import { IndexBar } from "./IndexBar";
import { cn, indexColor } from "@/lib/utils";

type Props = {
  indices: HorseIndex[];
  /** horse_number → finish_position のマップ（成績あり時） */
  results?: Map<number, number | null>;
  /** 初期オッズデータ（サーバーサイドで取得済み） */
  initialOdds?: OddsData;
  /** レースID（WebSocket接続用） */
  raceId?: number;
};

type SortKey = "composite_index" | "win_probability" | "horse_number" | "finish_position" | "upside_score";

const SUB_INDICES: { key: keyof HorseIndex; label: string }[] = [
  { key: "speed_index", label: "速度" },
  { key: "last3f_index", label: "後3F" },
  { key: "course_aptitude", label: "コース" },
  { key: "jockey_index", label: "騎手" },
  { key: "pace_index", label: "展開" },
  { key: "rotation_index", label: "ローテ" },
  { key: "pedigree_index", label: "血統" },
  { key: "position_advantage", label: "枠順" },
  { key: "training_index", label: "調教" },
  { key: "paddock_index", label: "パドック" },
];

const ANAGUSA_RANK_COLOR: Record<string, string> = {
  A: "bg-red-50 text-red-600 border-red-200",
  B: "bg-orange-50 text-orange-600 border-orange-200",
  C: "bg-yellow-50 text-yellow-700 border-yellow-200",
};

function finishLabel(pos: number | null | undefined): string {
  if (pos == null) return "";
  if (pos === 1) return "1着";
  if (pos === 2) return "2着";
  if (pos === 3) return "3着";
  return `${pos}着`;
}

function finishBadgeClass(pos: number | null | undefined): string {
  if (pos == null) return "";
  if (pos === 1) return "bg-yellow-100 text-yellow-800 font-bold";
  if (pos === 2) return "bg-gray-100 text-gray-700 font-bold";
  if (pos === 3) return "bg-orange-100 text-orange-700 font-bold";
  return "bg-gray-50 text-gray-500";
}

function formatTime(sec: number | null): string {
  if (sec == null) return "-";
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(1).padStart(4, "0");
  return m > 0 ? `${m}:${s}` : `${s}`;
}

/** 指数推移スパークライン (SVG) */
function Sparkline({ values }: { values: (number | null)[] }) {
  const valid = values.filter((v): v is number => v !== null);
  if (valid.length < 2) return null;

  const min = Math.min(...valid) - 2;
  const max = Math.max(...valid) + 2;
  const range = max - min || 1;
  const w = 80;
  const h = 28;
  const pts = valid.map((v, i) => {
    const x = (i / (valid.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const last = valid[valid.length - 1];
  const prev = valid[valid.length - 2];
  const color = last >= prev ? "#16a34a" : "#f97316";

  return (
    <svg width={w} height={h} className="overflow-visible" aria-hidden="true">
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {valid.map((v, i) => {
        const x = (i / (valid.length - 1)) * w;
        const y = h - ((v - min) / range) * h;
        return (
          <circle key={i} cx={x} cy={y} r="2" fill={color} />
        );
      })}
    </svg>
  );
}

/** 近走成績テーブル */
function HistorySection({ horseId }: { horseId: number }) {
  const [history, setHistory] = useState<RaceHistoryEntry[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (history !== null || loading) return;
    setLoading(true);
    try {
      const data = await fetchHorseHistory(horseId);
      setHistory(data);
    } catch {
      setHistory([]);
    } finally {
      setLoading(false);
    }
  }, [horseId, history, loading]);

  useEffect(() => {
    load();
  }, [load]);

  const indexValues = history
    ? history.map((h) => h.composite_index).reverse()
    : null;

  if (loading) {
    return (
      <div className="animate-pulse flex gap-2 mt-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-8 flex-1 bg-gray-100 rounded" />
        ))}
      </div>
    );
  }

  if (!history || history.length === 0) {
    return (
      <div className="mt-3 pt-3 border-t border-gray-100 text-[10px] text-gray-400">
        近走成績なし
      </div>
    );
  }

  return (
    <div className="mt-3 pt-3 border-t border-gray-100">
      <div className="flex items-center justify-between mb-2">
        <p className="text-[10px] text-gray-400">近走成績</p>
        {indexValues && indexValues.some((v) => v !== null) && (
          <div className="flex items-center gap-2">
            <p className="text-[10px] text-gray-400">指数推移</p>
            <Sparkline values={indexValues} />
          </div>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[10px] text-gray-600">
          <caption className="sr-only">近走成績</caption>
          <thead>
            <tr className="text-gray-400 border-b border-gray-100">
              <th scope="col" className="text-left pb-1 pr-2 font-normal whitespace-nowrap">日付</th>
              <th scope="col" className="text-left pb-1 pr-2 font-normal whitespace-nowrap">開催</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">着順</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">タイム</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">後3F</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">人気</th>
              <th scope="col" className="text-right pb-1 pr-2 font-normal whitespace-nowrap">指数</th>
              <th scope="col" className="text-left pb-1 font-normal whitespace-nowrap">不利</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h, i) => (
              <tr key={i} className="border-b border-gray-50 last:border-0">
                <td className="py-1 pr-2 whitespace-nowrap">
                  {h.date.slice(4, 6)}/{h.date.slice(6, 8)}
                </td>
                <td className="py-1 pr-2 whitespace-nowrap">
                  {h.course_name} {h.distance}m{h.surface === "芝" ? "芝" : "ダ"}
                </td>
                <td className="py-1 pr-2 text-right font-bold whitespace-nowrap">
                  {h.finish_position != null ? (
                    <span className={cn(
                      "px-1 rounded",
                      h.finish_position === 1 ? "bg-yellow-100 text-yellow-800" :
                      h.finish_position === 2 ? "bg-gray-100 text-gray-700" :
                      h.finish_position === 3 ? "bg-orange-100 text-orange-700" :
                      "text-gray-500"
                    )}>
                      {h.finish_position}着
                    </span>
                  ) : "-"}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums whitespace-nowrap">
                  {formatTime(h.finish_time)}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums whitespace-nowrap">
                  {h.last_3f != null ? h.last_3f.toFixed(1) : "-"}
                </td>
                <td className="py-1 pr-2 text-right whitespace-nowrap">
                  {h.win_popularity != null ? `${h.win_popularity}番人気` : "-"}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums whitespace-nowrap">
                  {h.composite_index != null ? (
                    <span className={cn("font-medium", indexColor(h.composite_index))}>
                      {h.composite_index.toFixed(1)}
                    </span>
                  ) : "-"}
                </td>
                <td className="py-1 whitespace-nowrap">
                  {h.remarks ? (
                    <span className="text-[10px] text-orange-600 bg-orange-50 px-1 py-0.5 rounded border border-orange-200">
                      {h.remarks}
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function IndicesTable({ indices, results, initialOdds, raceId }: Props) {
  const hasResults = results && results.size > 0;
  const defaultSort: SortKey = hasResults ? "finish_position" : "composite_index";
  const [sort, setSort] = useState<SortKey>(defaultSort);
  const [expandedHorse, setExpandedHorse] = useState<number | null>(null);
  const [odds, setOdds] = useState<OddsData>(initialOdds ?? { win: {}, place: {} });
  const liveRegionRef = useRef<HTMLDivElement | null>(null);

  // WebSocket接続 - オッズリアルタイム更新
  const wsUrl = raceId ? buildOddsWsUrl(raceId) : null;

  const handleOddsMessage = useCallback((data: unknown) => {
    setOdds(data as OddsData);
    if (liveRegionRef.current) {
      liveRegionRef.current.textContent = "オッズが更新されました";
      setTimeout(() => {
        if (liveRegionRef.current) liveRegionRef.current.textContent = "";
      }, 3000);
    }
  }, []);

  const { isConnected: wsConnected } = useWebSocket(wsUrl, handleOddsMessage, {
    reconnectInterval: 30_000,
  });

  // 総合指数1位の馬番（ソート不問で固定）
  const topHorseNumber = useMemo(
    () =>
      indices.reduce(
        (best, h) => h.composite_index > best.composite_index ? h : best,
        indices[0],
      )?.horse_number,
    [indices],
  );

  // 総合指数ランクマップ（O(n log n) → O(1) lookup）
  const compositeRankMap = useMemo(() => {
    const sortedByIndex = [...indices].sort((a, b) => b.composite_index - a.composite_index);
    return new Map(sortedByIndex.map((h, i) => [h.horse_number, i + 1]));
  }, [indices]);

  const sorted = useMemo(() => {
    return [...indices].sort((a, b) => {
      if (sort === "horse_number") return a.horse_number - b.horse_number;
      if (sort === "win_probability") {
        return (b.win_probability ?? 0) - (a.win_probability ?? 0);
      }
      if (sort === "finish_position" && results) {
        const pa = results.get(a.horse_number) ?? 999;
        const pb = results.get(b.horse_number) ?? 999;
        return pa - pb;
      }
      if (sort === "upside_score") {
        return (b.upside_score ?? 0) - (a.upside_score ?? 0);
      }
      return b.composite_index - a.composite_index;
    });
  }, [indices, sort, results]);

  const sortKeys: SortKey[] = hasResults
    ? ["finish_position", "composite_index", "win_probability", "upside_score", "horse_number"]
    : ["composite_index", "win_probability", "upside_score", "horse_number"];

  const sortLabels: Record<SortKey, string> = {
    composite_index: "指数順",
    win_probability: "勝率順",
    horse_number: "馬番順",
    finish_position: "着順",
    upside_score: "穴スコア順",
  };

  return (
    <div>
      {/* スクリーンリーダー向けライブリージョン */}
      <div ref={liveRegionRef} aria-live="polite" aria-atomic="true" className="sr-only" />

      {/* WebSocket切断通知 */}
      {raceId && (
        <div className="mb-2">
          <WsStatusBadge
            connected={wsConnected}
            label="リアルタイム更新停止中（再接続を試みています...）"
          />
        </div>
      )}

      {/* ソートタブ */}
      <div className="flex gap-1 mb-3 flex-wrap" role="group" aria-label="ソート順">
        {sortKeys.map((key) => (
          <button
            key={key}
            aria-pressed={sort === key}
            onClick={() => setSort(key)}
            className={cn(
              "text-xs px-3 py-1 min-h-[32px] rounded-full border transition-colors",
              sort === key
                ? "border-green-600 bg-green-700 text-white"
                : "border-gray-200 text-gray-600 hover:border-green-300"
            )}
          >
            {sortLabels[key]}
          </button>
        ))}
      </div>

      {/* 馬カード一覧 */}
      <div className="space-y-2">
        {sorted.map((horse) => {
          const isExpanded = expandedHorse === horse.horse_number;
          const winPct = horse.win_probability !== null
            ? (horse.win_probability * 100).toFixed(1)
            : null;
          const placePct = horse.place_probability !== null
            ? (horse.place_probability * 100).toFixed(1)
            : null;

          const isTop = horse.horse_number === topHorseNumber;
          const isAnagusa = horse.anagusa_rank !== null && !isTop;
          // 指数4位以降でupsideスコアが高い = 穴候補
          const compositeRank = compositeRankMap.get(horse.horse_number) ?? 99;
          const isUpsideCandidate = !isTop && compositeRank >= 4 && (horse.upside_score ?? 0) >= 0.6;

          const finishPos = results?.get(horse.horse_number);
          const finishLabel_ = finishLabel(finishPos);
          const finishClass = finishBadgeClass(finishPos);

          const hn = String(horse.horse_number);
          const winOdds = odds.win[hn];
          const placeOdds = odds.place[hn];

          return (
            <div
              key={horse.horse_number}
              className={cn(
                "rounded-lg border overflow-hidden transition-all",
                isTop ? "border-green-400 shadow-sm" : "border-gray-100"
              )}
            >
              {/* メイン行 */}
              <button
                aria-expanded={isExpanded}
                aria-controls={`horse-detail-${horse.horse_number}`}
                aria-label={`${horse.horse_name}の詳細を${isExpanded ? "閉じる" : "表示する"}`}
                onClick={() => setExpandedHorse(isExpanded ? null : horse.horse_number)}
                className={cn(
                  "w-full text-left px-3 py-2.5 flex items-center gap-3 focus-visible:ring-2 focus-visible:ring-green-500 focus-visible:ring-offset-1",
                  isTop ? "bg-green-50" : "bg-white hover:bg-gray-50"
                )}
              >
                {/* 馬番 */}
                <div className="flex-shrink-0 w-7 h-7 rounded-full bg-gray-800 text-white text-xs flex items-center justify-center font-bold">
                  {horse.horse_number}
                </div>

                {/* 馬名・指数バー */}
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-sm text-gray-900 truncate">
                    {horse.horse_name}
                    {isTop && <span className="ml-1 text-[10px] text-green-600 font-normal">◎ 本命</span>}
                  </div>
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className={cn("text-xs font-bold tabular-nums", indexColor(horse.composite_index))}>
                      {horse.composite_index.toFixed(1)}
                    </span>
                    <div className="flex-1">
                      <IndexBar value={horse.composite_index} />
                    </div>
                  </div>
                </div>

                {/* 右側: オッズ + 着順 + 確率 */}
                <div className="flex-shrink-0 text-right space-y-1">
                  {finishLabel_ && (
                    <div className={cn("text-[11px] px-1.5 py-0.5 rounded text-center", finishClass)}>
                      {finishLabel_}
                    </div>
                  )}
                  {/* 単勝・複勝オッズ */}
                  {(winOdds !== undefined || placeOdds !== undefined) && (
                    <div className="flex gap-1 justify-end">
                      {winOdds !== undefined && (
                        <span className="text-[11px] font-mono tabular-nums bg-amber-50 text-amber-800 px-1.5 py-0.5 rounded border border-amber-200">
                          単{winOdds.toFixed(1)}
                        </span>
                      )}
                      {placeOdds !== undefined && (
                        <span className="text-[11px] font-mono tabular-nums bg-sky-50 text-sky-700 px-1.5 py-0.5 rounded border border-sky-200">
                          複{placeOdds.toFixed(1)}
                        </span>
                      )}
                    </div>
                  )}
                  <div className="flex gap-1 justify-end items-center">
                    {/* 穴ぐさバッジ: 常にスペース確保し、非該当時は invisible で隠す */}
                    <span className={cn(
                      "text-[10px] px-1 py-0.5 rounded border font-bold",
                      isAnagusa
                        ? ANAGUSA_RANK_COLOR[horse.anagusa_rank!] ?? "bg-yellow-50 text-yellow-700 border-yellow-200"
                        : "invisible"
                    )}>
                      ☆{horse.anagusa_rank || "A"}
                    </span>
                    {/* 穴候補バッジ */}
                    {isUpsideCandidate && (
                      <span className="text-[10px] px-1 py-0.5 rounded border font-bold bg-purple-50 text-purple-700 border-purple-200">
                        穴{Math.round((horse.upside_score ?? 0) * 100)}
                      </span>
                    )}
                    {winPct && (
                      <span className="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
                        単{winPct}%
                      </span>
                    )}
                    {placePct && !finishLabel_ && (
                      <span className="text-[10px] bg-purple-50 text-purple-700 px-1.5 py-0.5 rounded">
                        複{placePct}%
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-gray-400">
                    {isExpanded ? "▲ 閉じる" : "▼ 詳細"}
                  </div>
                </div>
              </button>

              {/* 展開: 指数内訳 + 近走成績 */}
              {isExpanded && (
                <div id={`horse-detail-${horse.horse_number}`} className="border-t border-gray-100 bg-gray-50 px-3 py-3">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-[10px] text-gray-400">指数内訳</p>
                    {horse.upside_score !== null && horse.upside_score !== undefined && (
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] text-gray-400">穴馬スコア</span>
                        <span className={cn(
                          "text-[11px] font-bold px-1.5 py-0.5 rounded border",
                          horse.upside_score >= 0.7 ? "bg-purple-100 text-purple-800 border-purple-300" :
                          horse.upside_score >= 0.5 ? "bg-purple-50 text-purple-700 border-purple-200" :
                          "bg-gray-50 text-gray-500 border-gray-200"
                        )}>
                          {(horse.upside_score * 100).toFixed(0)}
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                    {SUB_INDICES.map(({ key, label }) => {
                      const val = horse[key] as number | null;
                      return (
                        <div key={key} className="flex items-center gap-1.5">
                          <span className="text-[10px] text-gray-500 w-10 flex-shrink-0">{label}</span>
                          <span className={cn("text-[11px] font-mono tabular-nums w-7 text-right flex-shrink-0", indexColor(val))}>
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
                  <HistorySection horseId={horse.horse_id} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
