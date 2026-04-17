"use client";

import { RaceConfidence } from "@/lib/api";
import { BuySignalBadge, BUY_SIGNAL_DESC } from "./BuySignalBadge";

type Props = {
  confidence: RaceConfidence;
  buySignal?: "buy" | "caution" | "pass" | null;
};

const RANK_CONF: Record<string, { bg: string; text: string; border: string }> = {
  S: { bg: "bg-purple-100", text: "text-purple-700", border: "border-purple-300" },
  A: { bg: "bg-green-100",  text: "text-green-700",  border: "border-green-300"  },
  B: { bg: "bg-yellow-100", text: "text-yellow-700", border: "border-yellow-300" },
  C: { bg: "bg-gray-100",   text: "text-gray-500",   border: "border-gray-200"   },
};

function RankBadge({ rank }: { rank: string }) {
  const c = RANK_CONF[rank] ?? RANK_CONF.C;
  return (
    <span className={`inline-flex items-center justify-center w-6 h-6 rounded border font-bold text-sm flex-shrink-0 ${c.bg} ${c.text} ${c.border}`}>
      {rank}
    </span>
  );
}

export function ConfidencePanel({ confidence, buySignal }: Props) {
  const confRank = confidence.rank ?? "C";
  const recRank  = confidence.recommend_rank ?? "C";

  const ev =
    confidence.win_prob_top != null && confidence.top_win_odds != null
      ? confidence.win_prob_top * confidence.top_win_odds
      : null;
  const evZone =
    ev === null ? null
    : ev >= 2.0 ? { label: "大穴注意", cls: "text-orange-500" }
    : ev >= 1.0 ? { label: "最適帯",   cls: "text-green-600"  }
    : ev >= 0.8 ? { label: "過剰人気", cls: "text-yellow-600" }
    :             { label: "過剰人気", cls: "text-red-500"     };

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm px-3 py-2.5 space-y-1.5">
      {/* 購入指針 */}
      {buySignal !== undefined && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-400 whitespace-nowrap">購入指針</span>
          <BuySignalBadge signal={buySignal} size="sm" />
          {buySignal && (
            <span className="text-[10px] text-gray-400 leading-tight">{BUY_SIGNAL_DESC[buySignal]}</span>
          )}
        </div>
      )}

      {/* 指数信頼度 + EV 横並び */}
      <div className="flex items-center gap-3 pt-1.5 border-t border-gray-50 flex-wrap">
        {/* 信頼度 */}
        <div className="flex items-center gap-1.5 text-[10px]">
          <span className="text-gray-400 whitespace-nowrap">指数信頼度</span>
          <RankBadge rank={confRank} />
          <span className="text-gray-600 whitespace-nowrap">{confidence.score}pt</span>
          <span className="text-gray-400 whitespace-nowrap">
            差{confidence.gap_1_2.toFixed(1)}/{confidence.gap_1_3.toFixed(1)}
          </span>
        </div>

        <div className="w-px h-4 bg-gray-200 flex-shrink-0" />

        {/* EV */}
        <div className="flex items-center gap-1.5 text-[10px]">
          <span className="text-gray-400 whitespace-nowrap">期待値 EV</span>
          <RankBadge rank={recRank} />
          {ev !== null ? (
            <>
              <span className={`font-bold whitespace-nowrap ${evZone?.cls ?? ""}`}>
                {ev.toFixed(2)}
              </span>
              {evZone && (
                <span className={`whitespace-nowrap ${evZone.cls}`}>{evZone.label}</span>
              )}
              {confidence.win_prob_top != null && confidence.top_win_odds != null && (
                <span className="text-gray-400 whitespace-nowrap">
                  ({Math.round(confidence.win_prob_top * 100)}%×{confidence.top_win_odds.toFixed(1)}倍)
                </span>
              )}
            </>
          ) : (
            <span className="text-gray-400">オッズ未取得</span>
          )}
        </div>
      </div>
    </div>
  );
}
