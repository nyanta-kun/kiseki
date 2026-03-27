"use client";

import Link from "next/link";
import { Race } from "@/lib/api";
import { gradeClass, surfaceIcon } from "@/lib/utils";

type Props = {
  race: Race;
};

const CONFIDENCE_CONFIG = {
  HIGH: { text: "高信頼", class: "bg-green-100 text-green-700 border-green-200" },
  MID:  { text: "中信頼", class: "bg-yellow-100 text-yellow-700 border-yellow-200" },
  LOW:  { text: "低信頼", class: "bg-red-100 text-red-600 border-red-200" },
} as const;

export function RaceCard({ race }: Props) {
  const conf = race.confidence_label ? CONFIDENCE_CONFIG[race.confidence_label] : null;

  return (
    <Link href={`/races/${race.id}`}>
      <div className="flex items-center gap-3 px-4 py-3 bg-white border border-gray-100 rounded-lg hover:border-green-300 hover:bg-green-50/30 transition-colors cursor-pointer">
        {/* R番号 */}
        <div className="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center font-bold text-white text-sm"
          style={{ background: "var(--green-deep)" }}>
          {race.race_number}R
        </div>

        {/* レース情報 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-semibold text-gray-800 truncate">
              {race.race_name ?? `${race.race_number}R`}
            </span>
            {race.grade && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${gradeClass(race.grade)}`}>
                {race.grade}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mt-0.5 flex-wrap">
            <span>{surfaceIcon(race.surface)} {race.surface} {race.distance}m</span>
            {race.head_count && <span>{race.head_count}頭</span>}
            {race.condition && <span>馬場: {race.condition}</span>}
          </div>
        </div>

        {/* 右側: 信頼度 + 算出済みバッジ + 矢印 */}
        <div className="flex-shrink-0 flex items-center gap-1.5">
          {conf && (
            <div className="flex flex-col items-center gap-0.5">
              <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium whitespace-nowrap ${conf.class}`}>
                {conf.text}
              </span>
              <span className="text-[9px] text-gray-400 tabular-nums">
                {race.confidence_score}pt
              </span>
            </div>
          )}
          {race.has_indices && !conf && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-50 text-green-700 border border-green-200 font-medium whitespace-nowrap">
              指数✓
            </span>
          )}
          {!race.has_indices && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-50 text-gray-400 border border-gray-100 whitespace-nowrap">
              未算出
            </span>
          )}
          <div className="text-gray-300 text-lg">›</div>
        </div>
      </div>
    </Link>
  );
}
