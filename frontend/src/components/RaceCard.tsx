"use client";

import Link from "next/link";
import { Race } from "@/lib/api";
import { gradeClass, surfaceIcon } from "@/lib/utils";

type Props = {
  race: Race;
};

export function RaceCard({ race }: Props) {
  return (
    <Link href={`/races/${race.id}`}>
      <div className="flex items-center gap-3 px-4 py-3 bg-white border border-gray-100 rounded-lg hover:border-green-300 hover:bg-green-50/30 transition-colors cursor-pointer">
        {/* R番号 */}
        <div className="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center font-bold text-white text-sm"
          style={{ background: "var(--green-deep)" }}>
          R{race.race_number}
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
          <div className="flex items-center gap-2 text-xs text-gray-500 mt-0.5">
            <span>{surfaceIcon(race.surface)} {race.surface} {race.distance}m</span>
            {race.condition && <span>馬場: {race.condition}</span>}
          </div>
        </div>

        {/* 矢印 */}
        <div className="text-gray-300 text-lg">›</div>
      </div>
    </Link>
  );
}
