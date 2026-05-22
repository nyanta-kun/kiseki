"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AnagusaRuleItem, fetchAnagusaRulesBrowser } from "@/lib/api";
import { surfaceIcon } from "@/lib/utils";

type Props = {
  initialItems: AnagusaRuleItem[];
  date: string;
};

const BET_LABEL: Record<string, string> = {
  place: "複勝",
  win_place: "単+複",
};

function fmtTime(t: string | null) {
  if (!t || t.length !== 4) return "";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function fmtOdds(v: number | null | undefined) {
  return v != null ? `${v.toFixed(1)}` : "—";
}

function posColor(p: number | null | undefined) {
  if (p == null) return "bg-gray-100 text-gray-400";
  if (p === 1) return "bg-amber-100 text-amber-700 font-bold";
  if (p <= 3) return "bg-blue-100 text-blue-700 font-bold";
  return "bg-gray-100 text-gray-500";
}

const RULE_COLORS: Record<string, string> = {
  Rule1: "bg-blue-50 text-blue-700 border-blue-200",
  Rule2: "bg-emerald-50 text-emerald-700 border-emerald-200",
  Rule3: "bg-purple-50 text-purple-700 border-purple-200",
  Rule4: "bg-orange-50 text-orange-700 border-orange-200",
};

export function AnagusaRulePanel({ initialItems, date }: Props) {
  const [items, setItems] = useState<AnagusaRuleItem[]>(initialItems);

  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const data = await fetchAnagusaRulesBrowser(date);
        setItems(data);
      } catch {
        // 無視（次回ポーリングで回復）
      }
    }, 30_000);
    return () => clearInterval(timer);
  }, [date]);

  if (items.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-100 shadow-sm px-4 py-3">
        <SectionHeader />
        <p className="text-xs text-gray-400 text-center py-3">
          該当馬なし（穴ぐさデータ未取得 or 対象レースなし）
        </p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
      <SectionHeader count={items.length} />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 border-b border-gray-100 bg-gray-50/50">
              <th className="text-left py-1.5 px-3 font-medium">発走</th>
              <th className="text-left py-1.5 pr-2 font-medium">レース</th>
              <th className="text-left py-1.5 pr-2 font-medium">馬名</th>
              <th className="text-center py-1.5 pr-2 font-medium">条件</th>
              <th className="text-right py-1.5 pr-2 font-medium">人気</th>
              <th className="text-right py-1.5 pr-2 font-medium">単勝</th>
              <th className="text-right py-1.5 pr-2 font-medium">複勝</th>
              <th className="text-center py-1.5 pr-2 font-medium">bet</th>
              <th className="text-right py-1.5 pr-3 font-medium">複ROI参考</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => {
              const ruleColor = RULE_COLORS[item.rule_label] ?? "bg-gray-50 text-gray-600 border-gray-200";
              const isPreferred = item.is_preferred_pop;
              const hasResult = item.finish_position != null;
              const isHit = hasResult && item.finish_position! <= 3;
              const isWin = hasResult && item.finish_position === 1;

              return (
                <tr
                  key={`${item.race_id}-${item.horse_number}`}
                  className={`border-b border-gray-50 last:border-0 ${
                    isPreferred ? "bg-yellow-50/40" : ""
                  }`}
                >
                  {/* 発走時刻 */}
                  <td className="py-2 px-3 text-gray-500 whitespace-nowrap">
                    {fmtTime(item.post_time)}
                  </td>

                  {/* レース */}
                  <td className="py-2 pr-2 whitespace-nowrap">
                    <Link
                      href={`/races/${item.race_id}`}
                      className="font-bold text-gray-800 hover:text-blue-600 transition-colors"
                    >
                      {item.course_name} {item.race_number}R
                    </Link>
                    <span className="text-gray-400 ml-1">
                      {surfaceIcon(item.surface)}{item.distance}m
                    </span>
                  </td>

                  {/* 馬名 */}
                  <td className="py-2 pr-2 font-medium text-gray-800 whitespace-nowrap">
                    {isPreferred && (
                      <span className="mr-1 text-yellow-600 text-[10px] font-bold">★</span>
                    )}
                    {item.horse_name ?? "—"}
                    <span className="text-gray-400 ml-1 font-normal">#{item.horse_number}</span>
                  </td>

                  {/* 条件ルール */}
                  <td className="py-2 pr-2">
                    <span className={`px-1.5 py-0.5 rounded border text-[10px] font-bold whitespace-nowrap ${ruleColor}`}>
                      {item.rule_label}
                    </span>
                  </td>

                  {/* 人気 */}
                  <td className="py-2 pr-2 text-right">
                    {item.popularity != null ? (
                      <span
                        className={`px-1.5 py-0.5 rounded text-[11px] font-bold ${
                          isPreferred
                            ? "bg-yellow-100 text-yellow-700"
                            : "text-gray-600"
                        }`}
                      >
                        {item.popularity}番人気
                      </span>
                    ) : "—"}
                  </td>

                  {/* 単勝オッズ */}
                  <td className="py-2 pr-2 text-right text-gray-600">
                    {fmtOdds(item.win_odds)}
                  </td>

                  {/* 複勝オッズ */}
                  <td className="py-2 pr-2 text-right text-gray-600">
                    {fmtOdds(item.place_odds)}
                  </td>

                  {/* bet種別 */}
                  <td className="py-2 pr-2 text-center">
                    <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold ${
                      item.bet_type === "win_place"
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-blue-100 text-blue-700"
                    }`}>
                      {BET_LABEL[item.bet_type] ?? item.bet_type}
                    </span>
                  </td>

                  {/* 複ROI参考 + 結果 */}
                  <td className="py-2 pr-3 text-right whitespace-nowrap">
                    {hasResult ? (
                      <span className={`px-1.5 py-0.5 rounded text-[11px] font-bold ${posColor(item.finish_position)}`}>
                        {item.finish_position}着
                        {isWin ? " 単★" : isHit ? " 複★" : ""}
                      </span>
                    ) : (
                      <span className="text-gray-500">
                        {item.backtest_place_roi.toFixed(3)}
                        <span className="text-gray-400 text-[10px] ml-0.5">({item.backtest_n})</span>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-2 bg-gray-50/50 border-t border-gray-100">
        <p className="text-[10px] text-gray-400">
          ★=人気4〜6番（最優先）　複ROI参考: 3年バックテスト値（n=件数）
          　Rule1:東京芝1201-1800 / Rule2:新潟芝1601-1800 / Rule3:京都芝〜1200 / Rule4:京都ダ1601-1800
        </p>
      </div>
    </div>
  );
}

function SectionHeader({ count }: { count?: number }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100">
      <span className="text-sm font-bold text-gray-700">穴ぐさ条件推奨</span>
      {count != null && count > 0 && (
        <span className="px-1.5 py-0.5 rounded-full text-[11px] font-bold bg-orange-100 text-orange-700">
          {count}頭
        </span>
      )}
      <span className="ml-auto text-[10px] text-gray-400">rank_A × 場/面/距離</span>
    </div>
  );
}
