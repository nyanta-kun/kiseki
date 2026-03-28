"use client";

import { HorseIndex } from "@/lib/api";

type Props = {
  indices: HorseIndex[];
};

export function EVSummary({ indices }: Props) {
  // 総合指数上位3頭を抽出
  const top3 = [...indices]
    .sort((a, b) => b.composite_index - a.composite_index)
    .slice(0, 3);

  if (top3.length === 0) return null;

  const marks = ["◎", "○", "▲"];
  const markColors = ["text-red-600", "text-blue-600", "text-yellow-600"];

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
      <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
        <span
          className="w-1 h-4 rounded inline-block"
          style={{ background: "var(--gold)" }}
        />
        指数本命予想
      </h2>

      {/* 3カードが収まらない場合は横スクロール。各カード最低132px（9文字馬名+padding） */}
      <div className="flex gap-3 overflow-x-auto pb-1">
        {top3.map((horse, i) => {
          const winPct = horse.win_probability !== null
            ? (horse.win_probability * 100).toFixed(1)
            : null;
          const placePct = horse.place_probability !== null
            ? (horse.place_probability * 100).toFixed(1)
            : null;

          return (
            <div
              key={horse.horse_number}
              className="flex-shrink-0 w-[calc((100%-1.5rem)/3)] min-w-[132px] rounded-lg border p-3 text-center"
              style={{
                borderColor: i === 0 ? "var(--gold)" : "#e5e7eb",
                background: i === 0 ? "var(--gold-light)" : "#f9fafb",
              }}
            >
              <div className={`text-xl font-bold ${markColors[i]}`}>{marks[i]}</div>
              <div className="text-[10px] text-gray-500 mt-0.5">
                {horse.horse_number}番
              </div>
              <div className="font-semibold text-xs text-gray-800 mt-1 truncate">
                {horse.horse_name}
              </div>
              <div className="mt-2 space-y-0.5">
                <div className="text-[11px] text-gray-600">
                  指数 <span className="font-bold text-green-700">{horse.composite_index.toFixed(1)}</span>
                </div>
                {winPct && (
                  <div className="text-[11px] text-gray-600">
                    単勝率 <span className="font-bold text-blue-700">{winPct}%</span>
                  </div>
                )}
                {placePct && (
                  <div className="text-[11px] text-gray-600">
                    複勝率 <span className="font-bold text-purple-700">{placePct}%</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-[10px] text-gray-400 mt-2">
        ※ 指数に基づく予測です。投票は自己責任でお願いします。
      </p>
    </section>
  );
}
