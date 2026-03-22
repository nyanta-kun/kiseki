"use client";

import { useState } from "react";
import { HorseIndex } from "@/lib/api";
import { IndexBar } from "./IndexBar";
import { cn, indexColor, evClass, evLabel } from "@/lib/utils";

type Props = {
  indices: HorseIndex[];
};

type SortKey = "composite_index" | "win_probability" | "horse_number";

const SUB_INDICES: { key: keyof HorseIndex; label: string }[] = [
  { key: "speed_index", label: "速度" },
  { key: "last3f_index", label: "後3F" },
  { key: "course_aptitude", label: "コース" },
  { key: "jockey_index", label: "騎手" },
  { key: "pace_index", label: "展開" },
  { key: "rotation_index", label: "ローテ" },
  { key: "pedigree_index", label: "血統" },
  { key: "position_advantage", label: "枠順" },
];

export function IndicesTable({ indices }: Props) {
  const [sort, setSort] = useState<SortKey>("composite_index");
  const [expandedHorse, setExpandedHorse] = useState<number | null>(null);

  const sorted = [...indices].sort((a, b) => {
    if (sort === "horse_number") return a.horse_number - b.horse_number;
    if (sort === "win_probability") {
      return (b.win_probability ?? 0) - (a.win_probability ?? 0);
    }
    return b.composite_index - a.composite_index;
  });

  return (
    <div>
      {/* ソートタブ */}
      <div className="flex gap-1 mb-3">
        {(["composite_index", "win_probability", "horse_number"] as SortKey[]).map((key) => {
          const labels: Record<SortKey, string> = {
            composite_index: "指数順",
            win_probability: "勝率順",
            horse_number: "馬番順",
          };
          return (
            <button
              key={key}
              onClick={() => setSort(key)}
              className={cn(
                "text-xs px-3 py-1 rounded-full border transition-colors",
                sort === key
                  ? "border-green-600 bg-green-700 text-white"
                  : "border-gray-200 text-gray-600 hover:border-green-300"
              )}
            >
              {labels[key]}
            </button>
          );
        })}
      </div>

      {/* 馬カード一覧 */}
      <div className="space-y-2">
        {sorted.map((horse, rank) => {
          const isExpanded = expandedHorse === horse.horse_number;
          const winPct = horse.win_probability !== null
            ? (horse.win_probability * 100).toFixed(1)
            : null;
          const placePct = horse.place_probability !== null
            ? (horse.place_probability * 100).toFixed(1)
            : null;

          // 指数1位は強調
          const isTop = rank === 0 && sort !== "horse_number";

          return (
            <div
              key={horse.horse_number}
              className={cn(
                "rounded-lg border overflow-hidden transition-all",
                isTop
                  ? "border-green-400 shadow-sm"
                  : "border-gray-100"
              )}
            >
              {/* メイン行 */}
              <button
                onClick={() => setExpandedHorse(isExpanded ? null : horse.horse_number)}
                className={cn(
                  "w-full text-left px-3 py-2.5 flex items-center gap-3",
                  isTop ? "bg-green-50" : "bg-white hover:bg-gray-50"
                )}
              >
                {/* 馬番 */}
                <div className="flex-shrink-0 w-7 h-7 rounded-full bg-gray-800 text-white text-xs flex items-center justify-center font-bold">
                  {horse.horse_number}
                </div>

                {/* 馬名 */}
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-sm text-gray-900 truncate">
                    {horse.horse_name}
                    {isTop && <span className="ml-1 text-[10px] text-green-600 font-normal">◎ 本命</span>}
                  </div>

                  {/* 総合指数バー */}
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className={cn("text-xs font-bold tabular-nums", indexColor(horse.composite_index))}>
                      {horse.composite_index.toFixed(1)}
                    </span>
                    <div className="flex-1">
                      <IndexBar value={horse.composite_index} />
                    </div>
                  </div>
                </div>

                {/* 確率・期待値 */}
                <div className="flex-shrink-0 text-right space-y-1">
                  <div className="flex gap-1 justify-end">
                    {winPct && (
                      <span className="text-[10px] bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">
                        単{winPct}%
                      </span>
                    )}
                    {placePct && (
                      <span className="text-[10px] bg-purple-50 text-purple-700 px-1.5 py-0.5 rounded">
                        複{placePct}%
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-gray-400 flex items-center justify-end gap-0.5">
                    {isExpanded ? "▲ 閉じる" : "▼ 詳細"}
                  </div>
                </div>
              </button>

              {/* 展開: 指数内訳 */}
              {isExpanded && (
                <div className="border-t border-gray-100 bg-gray-50 px-3 py-3">
                  <p className="text-[10px] text-gray-400 mb-2">指数内訳</p>
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
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
