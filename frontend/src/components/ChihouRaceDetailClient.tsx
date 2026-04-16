"use client";

import { useCallback, useState, useSyncExternalStore } from "react";
import { ChihouHorseIndex, ChihouRaceRanks, OddsData, RaceResult, buildChihouResultsWsUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import { BuySignalBadge, BUY_SIGNAL_DESC } from "./BuySignalBadge";
import { useWebSocket } from "@/hooks/useWebSocket";
import { WsStatusBadge } from "@/components/WsStatusBadge";

type Props = {
  raceId: number;
  horses: ChihouHorseIndex[];
  initialResults: RaceResult[];
  initialOdds: OddsData;
  ranks: ChihouRaceRanks | null;
  buySignal?: "buy" | "caution" | "pass" | null;
};

type SortKey = "composite" | "speed" | "last3f" | "jockey" | "rotation" | "finish";

/** 枠番 → 馬番のマッピング（n頭立て）
 *  日本中央競馬会方式: extra = n-8 頭分を枠8から逆順に2頭ずつ格納 */
function horseNumToFrame(horseNum: number, totalHorses: number): number {
  if (totalHorses <= 8) return horseNum;
  const extra = totalHorses - 8;
  const singleFrames = 8 - extra; // 1頭枠の数
  if (horseNum <= singleFrames) return horseNum;
  const remaining = horseNum - singleFrames;
  return singleFrames + Math.ceil(remaining / 2);
}

/** 枠番 → 背景・文字色クラス（JRA標準8色）*/
function frameColorClass(frame: number): string {
  switch (frame) {
    case 1: return "bg-white border border-gray-400 text-gray-800";
    case 2: return "bg-gray-800 text-white";
    case 3: return "bg-red-600 text-white";
    case 4: return "bg-blue-600 text-white";
    case 5: return "bg-yellow-400 text-gray-900";
    case 6: return "bg-green-600 text-white";
    case 7: return "bg-orange-500 text-white";
    case 8: return "bg-pink-500 text-white";
    default: return "bg-gray-200 text-gray-700";
  }
}

/** 0–100 の指数値をカラークラスに変換 */
function indexColorClass(v: number | null): string {
  if (v === null) return "text-gray-300";
  if (v >= 65) return "text-green-600 font-semibold";
  if (v >= 55) return "text-green-500";
  if (v >= 45) return "text-gray-600";
  if (v >= 35) return "text-orange-500";
  return "text-red-500";
}

/** 指数バー幅（0–100） */
function barWidth(v: number | null): string {
  if (v === null) return "0%";
  return `${Math.max(0, Math.min(100, v))}%`;
}

/** 確率を % 文字列に変換 */
function pct(v: number | null): string {
  if (v === null) return "–";
  return `${Math.round(v * 100)}%`;
}

/** 走破タイム（秒）→ m:ss.f 形式 */
function formatTime(sec: number | null): string {
  if (sec === null) return "–";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

/** 単勝オッズのカラークラス */
function winOddsColorClass(odds: number | null): string {
  if (odds === null) return "text-gray-600";
  if (odds < 10) return "text-red-600 font-semibold";
  if (odds >= 100) return "text-blue-600";
  return "text-gray-600";
}

/** 期待値のカラークラス */
function evColorClass(ev: number | null): string {
  if (ev === null) return "text-gray-400";
  if (ev >= 1.5) return "text-green-600 font-bold";
  if (ev >= 1.2) return "text-green-500 font-semibold";
  if (ev >= 1.0) return "text-gray-600";
  return "text-gray-400";
}

/** 着順バッジのクラス */
function finishBadgeClass(pos: number | null | undefined): string {
  if (pos == null) return "text-gray-400";
  if (pos === 1) return "bg-yellow-100 text-yellow-800 font-bold px-1 rounded";
  if (pos === 2) return "bg-gray-100 text-gray-700 font-bold px-1 rounded";
  if (pos === 3) return "bg-orange-100 text-orange-700 font-bold px-1 rounded";
  return "text-gray-400";
}

const RANK_CONF: Record<string, { bg: string; text: string; border: string }> = {
  S: { bg: "bg-purple-100", text: "text-purple-700", border: "border-purple-300" },
  A: { bg: "bg-green-100",  text: "text-green-700",  border: "border-green-300"  },
  B: { bg: "bg-yellow-100", text: "text-yellow-700", border: "border-yellow-300" },
  C: { bg: "bg-gray-100",   text: "text-gray-500",   border: "border-gray-200"   },
};

function RankBadge({ prefix, rank, sub }: { prefix: string; rank: string; sub?: string }) {
  const c = RANK_CONF[rank] ?? RANK_CONF.C;
  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className={`text-xs px-2 py-0.5 rounded border font-bold ${c.bg} ${c.text} ${c.border}`}>
        {prefix}<span className="text-sm">{rank}</span>
      </span>
      {sub && <span className="text-[9px] text-gray-400">{sub}</span>}
    </div>
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

export function ChihouRaceDetailClient({ raceId, horses, initialResults, initialOdds, ranks, buySignal }: Props) {
  const mounted = useIsMounted();
  const [resultsMap, setResultsMap] = useState<Map<number, number | null>>(
    () => toResultsMap(initialResults)
  );
  const hasResults = resultsMap.size > 0;

  const wsUrl = mounted ? buildChihouResultsWsUrl(raceId) : null;
  const handleWsMessage = useCallback((data: unknown) => {
    if (Array.isArray(data) && data.length > 0) {
      setResultsMap(toResultsMap(data as RaceResult[]));
    }
  }, []);
  const { isConnected: wsConnected } = useWebSocket(wsUrl, handleWsMessage);
  const totalHorses = horses.length;

  // デフォルトは常に総合（レース確定後も同様）
  const [sortKey, setSortKey] = useState<SortKey>("composite");

  const sorted = [...horses].sort((a, b) => {
    if (sortKey === "finish" && hasResults) {
      const pa = (a.horse_number !== null ? resultsMap.get(a.horse_number) : null) ?? 999;
      const pb = (b.horse_number !== null ? resultsMap.get(b.horse_number) : null) ?? 999;
      return pa - pb;
    }
    const key = sortKey === "composite" ? "composite_index" :
      sortKey === "speed" ? "speed_index" :
      sortKey === "last3f" ? "last3f_index" :
      sortKey === "jockey" ? "jockey_index" : "rotation_index";
    const av = (a[key as keyof ChihouHorseIndex] as number | null) ?? 0;
    const bv = (b[key as keyof ChihouHorseIndex] as number | null) ?? 0;
    return bv - av;
  });

  // 足切り判定: 総合指数でのランク・差を事前計算
  const maxComposite = Math.max(...horses.map((h) => h.composite_index ?? 0));
  const compositeRankMap = new Map<number, number>(
    [...horses]
      .sort((a, b) => (b.composite_index ?? 0) - (a.composite_index ?? 0))
      .map((h, i) => [h.horse_id, i + 1])
  );
  /** 指数5位以下かつトップ差15以上、またはトップ差20以上の馬を足切り対象とする */
  function isCutOff(horse: ChihouHorseIndex): boolean {
    if (horse.composite_index === null) return false;
    const gap = maxComposite - horse.composite_index;
    const rank = compositeRankMap.get(horse.horse_id) ?? 999;
    return gap >= 20 || (gap >= 15 && rank >= 5);
  }

  return (
    <>
      {/* 信頼度・推奨度ランクパネル */}
      {(ranks || buySignal) && (
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm px-4 py-3 space-y-2">
          {/* 購入指針 */}
          {buySignal !== undefined && (
            <div className="flex items-center gap-2 pb-2 border-b border-gray-50">
              <span className="text-[10px] text-gray-400 font-medium">購入指針</span>
              <BuySignalBadge signal={buySignal} size="sm" />
              {buySignal && (
                <span className="text-[10px] text-gray-500 ml-1">{BUY_SIGNAL_DESC[buySignal]}</span>
              )}
            </div>
          )}
          {ranks && (
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-3">
                <RankBadge
                  prefix="指数信頼度 "
                  rank={ranks.confidence_rank}
                  sub={`${ranks.score}pt`}
                />
                <RankBadge
                  prefix="期待値 "
                  rank={ranks.recommend_rank}
                  sub={ranks.top_win_odds != null ? `オッズ ${ranks.top_win_odds.toFixed(1)}倍` : "オッズ未取得"}
                />
              </div>
              <div className="text-[10px] text-gray-400 space-y-0.5 ml-auto">
                <p>指数差 1-2位: {ranks.gap_1_2.toFixed(1)}pt / 1-3位: {ranks.gap_1_3.toFixed(1)}pt</p>
                {ranks.win_prob_top != null && (
                  <p>予測1位 勝率: {Math.round(ranks.win_prob_top * 100)}%</p>
                )}
              </div>
            </div>
          )}
        </div>
      )}

    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
      {/* ヘッダー + ソートボタン */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
          <span className="w-1 h-4 rounded inline-block bg-green-600" />
          出馬表 指数一覧
          <span className="text-xs text-gray-400 font-normal ml-1">{horses.length}頭</span>
          {mounted && wsUrl && (
            <span className="ml-1">
              <WsStatusBadge connected={wsConnected} label="成績更新: 再接続中…" />
            </span>
          )}
        </h2>
        <div className="flex gap-1 ml-auto flex-wrap">
          <SortButton k="composite" label="総合" sortKey={sortKey} setSortKey={setSortKey} />
          <SortButton k="speed" label="速度" sortKey={sortKey} setSortKey={setSortKey} />
          <SortButton k="last3f" label="後3F" sortKey={sortKey} setSortKey={setSortKey} />
          <SortButton k="jockey" label="騎手" sortKey={sortKey} setSortKey={setSortKey} />
          <SortButton k="rotation" label="ローテ" sortKey={sortKey} setSortKey={setSortKey} />
          {hasResults && <SortButton k="finish" label="着順" sortKey={sortKey} setSortKey={setSortKey} />}
        </div>
      </div>

      {/* テーブル */}
      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-xs min-w-[480px]">
          <thead>
            <tr className="border-b border-gray-100 text-gray-400 text-[10px]">
              <th className="text-right py-1 pl-2 pr-2 w-8">馬番</th>
              <th className="text-left py-1 px-1">馬名</th>
              <th className="text-right py-1 px-1 w-20">総合</th>
              <th className="text-right py-1 px-1 w-12">速度</th>
              <th className="text-right py-1 px-1 w-12">後3F</th>
              <th className="text-right py-1 px-1 w-12">騎手</th>
              <th className="text-right py-1 px-1 w-12">ローテ</th>
              <th className="text-right py-1 px-1 w-12">勝率</th>
              <th className="text-right py-1 px-1 w-12">複率</th>
              <th className="text-right py-1 px-1 w-14">単オッズ</th>
              <th className="text-right py-1 pr-2 w-12">期待値</th>
              {hasResults && <th className="text-right py-1 pr-2 w-10">着順</th>}
            </tr>
          </thead>
          <tbody>
            {sorted.map((horse) => {
              const finishPos = horse.horse_number !== null ? resultsMap.get(horse.horse_number) : undefined;
              const isWin = finishPos === 1;
              const isPlace = finishPos !== undefined && finishPos !== null && finishPos <= 3;
              const winOdds = horse.horse_number !== null
                ? (initialOdds.win[horse.horse_number.toString()] ?? null)
                : null;
              const ev = horse.win_probability !== null && winOdds !== null
                ? horse.win_probability * winOdds
                : null;
              const frameNum = horse.horse_number !== null
                ? horseNumToFrame(horse.horse_number, totalHorses)
                : 0;
              const cutOff = isCutOff(horse);

              return (
                <tr
                  key={horse.horse_id}
                  className={cn(
                    "border-b border-gray-50 transition-colors",
                    cutOff ? "opacity-40 bg-gray-50" :
                    isWin ? "bg-yellow-50" :
                    isPlace ? "bg-orange-50/40" :
                    "hover:bg-gray-50"
                  )}
                >
                  {/* 馬番（枠番カラーバッジ） */}
                  <td className="py-1.5 pl-2 pr-2 text-right">
                    <span className={cn(
                      "inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold tabular-nums",
                      frameColorClass(frameNum)
                    )}>
                      {horse.horse_number ?? "–"}
                    </span>
                  </td>

                  {/* 馬名 + 外部コンセンサスバッジ */}
                  <td className="py-2 px-1">
                    <div className="flex items-center gap-1">
                      <span className="text-gray-800 font-medium truncate block max-w-[90px]">
                        {horse.horse_name}
                      </span>
                      {horse.external_consensus === 2 && (
                        <span className="text-[9px] bg-purple-100 text-purple-700 border border-purple-300 px-1 py-0.5 rounded font-bold whitespace-nowrap">
                          外部◎
                        </span>
                      )}
                      {horse.external_consensus === 1 && (
                        <span className="text-[9px] bg-blue-50 text-blue-600 border border-blue-200 px-1 py-0.5 rounded whitespace-nowrap">
                          外部○
                        </span>
                      )}
                    </div>
                  </td>

                  {/* 総合指数 + バー */}
                  <td className="py-2 px-1">
                    <div className="flex items-center gap-1 justify-end">
                      <span className={indexColorClass(horse.composite_index)}>
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
                  <td className={`py-2 px-1 text-right ${indexColorClass(horse.speed_index)}`}>
                    {horse.speed_index !== null ? horse.speed_index.toFixed(1) : "–"}
                  </td>

                  {/* 後3F */}
                  <td className={`py-2 px-1 text-right ${indexColorClass(horse.last3f_index)}`}>
                    {horse.last3f_index !== null ? horse.last3f_index.toFixed(1) : "–"}
                  </td>

                  {/* 騎手 */}
                  <td className={`py-2 px-1 text-right ${indexColorClass(horse.jockey_index)}`}>
                    {horse.jockey_index !== null ? horse.jockey_index.toFixed(1) : "–"}
                  </td>

                  {/* ローテ */}
                  <td className={`py-2 px-1 text-right ${indexColorClass(horse.rotation_index)}`}>
                    {horse.rotation_index !== null ? horse.rotation_index.toFixed(1) : "–"}
                  </td>

                  {/* 勝率 */}
                  <td className="py-2 px-1 text-right text-gray-600">
                    {pct(horse.win_probability)}
                  </td>

                  {/* 複勝率 */}
                  <td className="py-2 px-1 text-right text-gray-600">
                    {pct(horse.place_probability)}
                  </td>

                  {/* 単勝オッズ */}
                  <td className={`py-2 px-1 text-right ${winOddsColorClass(winOdds)}`}>
                    {winOdds !== null ? `${winOdds.toFixed(1)}倍` : "–"}
                  </td>

                  {/* 期待値 */}
                  <td className={`py-2 pr-2 text-right ${evColorClass(ev)}`}>
                    {ev !== null ? ev.toFixed(2) : "–"}
                  </td>

                  {/* 着順バッジ */}
                  {hasResults && (
                    <td className="py-2 pr-2 text-right">
                      {finishPos != null ? (
                        <span className={finishBadgeClass(finishPos)}>
                          {finishPos}着
                        </span>
                      ) : (
                        <span className="text-gray-300">–</span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 確定着順サマリ（成績あり時） */}
      {hasResults && (
        <div className="mt-4 pt-3 border-t border-gray-100">
          <h3 className="text-xs font-semibold text-gray-500 mb-2">確定着順</h3>
          <div className="space-y-1">
            {initialResults
              .filter((r) => r.finish_position !== null)
              .sort((a, b) => (a.finish_position ?? 99) - (b.finish_position ?? 99))
              .slice(0, 5)
              .map((r) => (
                <div key={r.horse_name} className="flex items-center gap-2 text-xs">
                  <span className={cn(
                    "min-w-[2.5rem] text-center text-[11px] py-0.5 rounded font-bold",
                    r.finish_position === 1 ? "bg-yellow-100 text-yellow-800" :
                    r.finish_position === 2 ? "bg-gray-100 text-gray-700" :
                    r.finish_position === 3 ? "bg-orange-100 text-orange-700" :
                    "text-gray-500"
                  )}>
                    {r.finish_position}着
                  </span>
                  <span className="font-medium text-gray-800">{r.horse_name}</span>
                  {r.finish_time !== null && (
                    <span className="text-gray-400 tabular-nums">{formatTime(r.finish_time)}</span>
                  )}
                  {r.last_3f !== null && (
                    <span className="text-gray-400 tabular-nums">後3F {r.last_3f.toFixed(1)}</span>
                  )}
                </div>
              ))}
          </div>
        </div>
      )}

      {/* 凡例 */}
      <div className="mt-3 text-[10px] text-gray-400 border-t border-gray-50 pt-2 space-y-0.5">
        <p><span className="text-green-600">緑</span>=高評価 / <span className="text-red-500">赤</span>=低評価（65↑: 強 / 55–65: 良 / 45–55: 並 / 35–45: 劣 / ↓35: 弱）</p>
        <p><span className="opacity-50">グレー</span>=足切り候補（トップ差20以上、または差15以上かつ5位以下）</p>
      </div>
    </section>
    </>
  );
}
