import type { OddsData, RaceEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

/** 枠番 → 馬番のマッピング（n頭立て）JRA標準方式 */
function horseNumToFrame(horseNum: number, totalHorses: number): number {
  if (totalHorses <= 8) return horseNum;
  const extra = totalHorses - 8;
  const singleFrames = 8 - extra;
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

type Props = {
  entries: RaceEntry[];
  odds?: OddsData;
};

export function EntriesTable({ entries, odds }: Props) {
  const total = entries.length;
  const sorted = [...entries].sort((a, b) => a.horse_number - b.horse_number);

  return (
    <div className="bg-white rounded-lg border border-gray-100 overflow-hidden">
      <div className="px-3 py-2 border-b border-gray-100 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-700">出走馬一覧</h2>
        <span className="text-xs text-gray-400">{total}頭 · 指数算出前</span>
      </div>

      <div className="divide-y divide-gray-50">
        {sorted.map((entry) => {
          const frameNum = entry.frame_number > 0
            ? entry.frame_number
            : horseNumToFrame(entry.horse_number, total);
          const winOdds = odds?.win[String(entry.horse_number)];
          const placeOdds = odds?.place[String(entry.horse_number)];

          return (
            <div key={entry.id} className="flex items-center gap-3 px-3 py-2.5">
              {/* 馬番（枠番色） */}
              <div className={cn(
                "flex-shrink-0 w-7 h-7 rounded-full text-xs flex items-center justify-center font-bold",
                frameColorClass(frameNum)
              )}>
                {entry.horse_number}
              </div>

              {/* 馬名・騎手 */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-gray-900 truncate">{entry.horse_name}</p>
                <p className="text-[11px] text-gray-400 truncate">
                  {entry.jockey_name ?? "騎手未定"}
                  {entry.weight_carried != null && (
                    <span className="ml-1.5">{entry.weight_carried}kg</span>
                  )}
                  {entry.horse_weight != null && (
                    <span className="ml-1.5">
                      馬体{entry.horse_weight}
                      {entry.weight_change != null && (
                        <span className={entry.weight_change > 0 ? "text-red-500" : entry.weight_change < 0 ? "text-blue-500" : "text-gray-400"}>
                          ({entry.weight_change > 0 ? "+" : ""}{entry.weight_change})
                        </span>
                      )}
                    </span>
                  )}
                </p>
              </div>

              {/* オッズ */}
              {(winOdds !== undefined || placeOdds !== undefined) && (
                <div className="flex-shrink-0 flex gap-1">
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
            </div>
          );
        })}
      </div>
    </div>
  );
}
