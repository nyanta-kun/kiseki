"use client";

import { RaceConfidence } from "@/lib/api";

type Props = {
  confidence: RaceConfidence;
};

const RANK_CONF: Record<string, { bg: string; text: string; border: string }> = {
  S: { bg: "bg-purple-100", text: "text-purple-700", border: "border-purple-300" },
  A: { bg: "bg-green-100",  text: "text-green-700",  border: "border-green-300"  },
  B: { bg: "bg-yellow-100", text: "text-yellow-700", border: "border-yellow-300" },
  "C+": { bg: "bg-slate-100", text: "text-slate-600", border: "border-slate-300" },
  C: { bg: "bg-gray-100",   text: "text-gray-500",   border: "border-gray-200"   },
};

// recommend_rank(=市場一致×指数信頼度で再設計・2026-07-25、C+はPhase3市場混戦度分析で追加)の意味
// [[jra_axis_market_agree_redesign]]: S/A/B は指数1位が単勝1番人気と一致する馬のみ、
// Cは市場が指数1位を支持していない「見送り」。ROI保証ではなく的中率tier。
const RECOMMEND_MEANING: Record<string, string> = {
  S: "最強軸（断然人気 または 市場一致×高信頼、1位馬勝率45-51%）",
  A: "信頼軸（市場一致×中信頼、1位馬勝率33-40%）",
  B: "準軸（市場一致×通常信頼、1位馬勝率27-35%、複勝向き）",
  "C+": "準見送り（市場乖離だがまだ拮抗、複勝的中率約55%、複勝向き）",
  C: "混戦・見送り（市場が指数1位を支持せず、1位馬勝率15-26%）",
};

function RankBadge({ rank }: { rank: string }) {
  const c = RANK_CONF[rank] ?? RANK_CONF.C;
  return (
    <span className={`inline-flex items-center justify-center w-6 h-6 rounded border font-bold text-sm flex-shrink-0 ${c.bg} ${c.text} ${c.border}`}>
      {rank}
    </span>
  );
}

export function ConfidencePanel({ confidence }: Props) {
  const confRank = confidence.rank ?? "C";
  const recRank  = confidence.recommend_rank ?? "C";

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm px-3 py-2.5 space-y-1.5">
      {/* 購入指針: 推奨tier（指数1位馬の市場一致×信頼度、的中重視） */}
      <div className="flex items-start gap-2">
        <span className="text-[10px] text-gray-400 whitespace-nowrap pt-0.5">購入指針</span>
        <RankBadge rank={recRank} />
        <span className="text-[10px] text-gray-500 leading-tight">{RECOMMEND_MEANING[recRank] ?? RECOMMEND_MEANING.C}</span>
      </div>

      {/* 指数信頼度（参考値: gapベースの生スコア） */}
      <div className="flex items-center gap-1.5 pt-1.5 border-t border-gray-50 text-[10px]">
        <span className="text-gray-400 whitespace-nowrap">指数信頼度（参考）</span>
        <RankBadge rank={confRank} />
        <span className="text-gray-600 whitespace-nowrap">{confidence.score}pt</span>
        <span className="text-gray-400 whitespace-nowrap">
          差{confidence.gap_1_2.toFixed(1)}/{confidence.gap_1_3.toFixed(1)}
        </span>
      </div>
    </div>
  );
}
