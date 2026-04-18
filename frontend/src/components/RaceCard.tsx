"use client";

import Link from "next/link";
import { Race } from "@/lib/api";
import { gradeClass, raceClassBadgeClass, raceClassShort, surfaceIcon } from "@/lib/utils";
import { BuySignalBadge } from "./BuySignalBadge";

type Props = {
  race: Race;
  basePath?: string;
};

const RANK_CONFIG = {
  S: { cls: "bg-purple-100 text-purple-700 border-purple-300 font-bold" },
  A: { cls: "bg-green-100 text-green-700 border-green-300 font-bold" },
  B: { cls: "bg-yellow-100 text-yellow-700 border-yellow-300 font-semibold" },
  C: { cls: "bg-gray-100 text-gray-500 border-gray-200" },
} as const;

/** "1025" → "10:25" */
function formatPostTime(t: string | null): string | null {
  if (!t || t.length !== 4) return null;
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}


export function RaceCard({ race, basePath = "/races" }: Props) {
  const confRank   = race.confidence_rank ?? null;
  const recRank    = race.recommend_rank  ?? null;
  const buySignal  = race.buy_signal ?? null;
  const postTime   = formatPostTime(race.post_time);

  return (
    <Link
      href={`${basePath}/${race.id}`}
      aria-label={`${race.course_name} ${race.race_number}R ${race.race_name ?? ''} 詳細へ`}
    >
      <div className="flex items-center gap-3 px-4 py-3 bg-white border border-gray-100 rounded-lg hover:border-blue-300 hover:bg-blue-50/30 transition-colors cursor-pointer">
        {/* R番号: 結果確定後はグレー */}
        <div
          className="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center font-bold text-white text-sm"
          style={{ background: race.result_confirmed ? "#9ca3af" : "var(--primary)" }}
        >
          {race.race_number}R
        </div>

        {/* レース情報 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-semibold text-gray-800 truncate">
              {race.race_name ?? race.race_class_label ?? `${race.race_number}R`}
            </span>
            {race.grade && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${gradeClass(race.grade)}`}>
                {race.grade}
              </span>
            )}
            {!race.grade && raceClassShort(race.race_class_label) && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${raceClassBadgeClass(race.race_class_label)}`}>
                {raceClassShort(race.race_class_label)}
              </span>
            )}
          </div>
          {/* 2行目: 時刻・距離・頭数・馬場 */}
          <div className="flex items-center gap-1.5 text-xs text-gray-500 mt-0.5 flex-wrap">
            {postTime && <span className="font-medium text-gray-700">{postTime}</span>}
            <span>{surfaceIcon(race.surface)} {race.surface} {race.distance}m</span>
            {race.head_count && <span>{race.head_count}頭</span>}
            {race.condition && <span>馬場:{race.condition}</span>}
          </div>
        </div>

        {/* 右側: 上段=購入指針+穴ぐさ / 下段=ランク */}
        <div className="flex-shrink-0 flex items-center gap-1.5">
          <div className="flex flex-col items-end gap-0.5">
            {/* 上段: 購入指針・穴ぐさを横並び */}
            {(buySignal || race.has_anagusa) && (
              <div className="flex items-center gap-0.5">
                {buySignal && <BuySignalBadge signal={buySignal} size="sm" />}
                {race.has_anagusa && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-50 text-yellow-700 border border-yellow-200 font-medium whitespace-nowrap">
                    ☆穴
                  </span>
                )}
              </div>
            )}
            {/* 下段: 信頼度・推奨度ランク */}
            {confRank && recRank ? (
              <div className="flex items-center gap-0.5">
                <span className={`text-[9px] px-1 py-0.5 rounded border whitespace-nowrap ${RANK_CONFIG[confRank].cls}`}>
                  信{confRank}
                </span>
                <span className={`text-[9px] px-1 py-0.5 rounded border whitespace-nowrap ${RANK_CONFIG[recRank].cls}`}>
                  EV{recRank}
                </span>
                <span className="text-[9px] text-gray-400 tabular-nums">{race.confidence_score}pt</span>
              </div>
            ) : race.has_indices ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200 font-medium whitespace-nowrap">
                指数✓
              </span>
            ) : (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-50 text-gray-400 border border-gray-100 whitespace-nowrap">
                未算出
              </span>
            )}
          </div>
          <div className="text-gray-300 text-lg">›</div>
        </div>
      </div>
    </Link>
  );
}
