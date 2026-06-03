"use client";

import { ProjectedEntry } from "@/lib/api";

type Props = {
  entries: ProjectedEntry[];
  raceName?: string | null;
};

export function ProjectedEntriesTable({ entries, raceName }: Props) {
  if (entries.length === 0) {
    return (
      <div className="py-4 text-center text-gray-400 text-sm">
        出走想定データがありません
      </div>
    );
  }

  const displayRaceName = raceName ?? entries[0].race_name;
  const jockeyCount = entries.filter((e) => e.expected_jockey_name).length;

  return (
    <div className="space-y-2">
      {/* レース情報ヘッダー */}
      <div className="flex items-center gap-2 text-xs text-gray-500">
        {displayRaceName && (
          <span className="font-medium text-gray-700">{displayRaceName}</span>
        )}
        <span className="ml-auto text-gray-400">
          想定 {entries.length}頭（騎手 {jockeyCount}）
        </span>
      </div>

      {/* 馬一覧テーブル */}
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-left px-3 py-2 font-medium text-gray-600">馬名</th>
              <th className="text-center px-2 py-2 font-medium text-gray-600 whitespace-nowrap">性齢</th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">想定騎手</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, idx) => (
              <tr
                key={entry.horse_name}
                className={idx % 2 === 0 ? "bg-white" : "bg-gray-50/50"}
              >
                <td className="px-3 py-2 font-medium text-gray-800">
                  {entry.horse_name}
                </td>
                <td className="px-2 py-2 text-center text-gray-600 whitespace-nowrap">
                  {entry.sex_age ?? ""}
                </td>
                <td className="px-3 py-2 text-gray-500 text-xs">
                  {entry.expected_jockey_name ?? "未定"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
