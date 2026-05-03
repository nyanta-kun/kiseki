"use client";

import { SpecialRegistration } from "@/lib/api";

const TRACK_LABELS: Record<string, string> = {
  "11": "芝左", "12": "芝右", "13": "芝直",
  "21": "ダ左", "22": "ダ右", "23": "ダ直",
  "51": "障芝", "52": "障ダ",
};

const GRADE_LABELS: Record<string, string> = {
  "A": "G1", "B": "G2", "C": "G3", "D": "J.G1", "E": "J.G2", "F": "J.G3",
  "L": "L", "OP": "OP",
};

type Props = {
  entries: SpecialRegistration[];
  raceName?: string | null;
  distance?: number | null;
  trackCode?: string | null;
};

export function SpecialRegistrationsTable({ entries, raceName, distance, trackCode }: Props) {
  if (entries.length === 0) {
    return (
      <div className="py-4 text-center text-gray-400 text-sm">
        特別登録馬データがありません
      </div>
    );
  }

  const firstEntry = entries[0];
  const displayRaceName = raceName ?? firstEntry.race_name;
  const displayDistance = distance ?? firstEntry.distance;
  const displayTrack = trackCode ?? firstEntry.track_code;
  const gradeCode = firstEntry.grade_code;

  return (
    <div className="space-y-2">
      {/* レース情報ヘッダー */}
      <div className="flex items-center gap-2 text-xs text-gray-500">
        {displayRaceName && (
          <span className="font-medium text-gray-700">{displayRaceName}</span>
        )}
        {gradeCode && GRADE_LABELS[gradeCode] && (
          <span className="bg-red-100 text-red-700 px-1.5 py-0.5 rounded text-[10px] font-bold">
            {GRADE_LABELS[gradeCode]}
          </span>
        )}
        {displayDistance && displayTrack && (
          <span>{TRACK_LABELS[displayTrack] ?? displayTrack} {displayDistance}m</span>
        )}
        <span className="ml-auto text-gray-400">{entries.length}頭登録</span>
      </div>

      {/* 馬一覧テーブル */}
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-left px-3 py-2 font-medium text-gray-600">馬名</th>
              <th className="text-center px-2 py-2 font-medium text-gray-600 whitespace-nowrap">性齢</th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">調教師</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, idx) => (
              <tr
                key={entry.jravan_horse_code}
                className={idx % 2 === 0 ? "bg-white" : "bg-gray-50/50"}
              >
                <td className="px-3 py-2 font-medium text-gray-800">
                  {entry.horse_name}
                </td>
                <td className="px-2 py-2 text-center text-gray-600 whitespace-nowrap">
                  {entry.sex ?? ""}{entry.age != null ? `${entry.age}歳` : ""}
                </td>
                <td className="px-3 py-2 text-gray-500 text-xs">
                  {entry.trainer_name ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
