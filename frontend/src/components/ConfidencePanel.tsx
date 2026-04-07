"use client";

import { RaceConfidence } from "@/lib/api";

type Props = {
  confidence: RaceConfidence;
};

const LABEL_CONFIG = {
  HIGH: {
    text: "高信頼",
    badge: "bg-green-100 text-green-700 border-green-200",
    bar: "bg-green-500",
    desc: "本命軸が機能しやすいレースです。指数上位馬を中心に狙えます。",
  },
  MID: {
    text: "中信頼",
    badge: "bg-yellow-100 text-yellow-700 border-yellow-200",
    bar: "bg-yellow-400",
    desc: "相手選びが鍵になるレースです。2〜3頭の軸から馬連・三連複が有効です。",
  },
  LOW: {
    text: "低信頼",
    badge: "bg-red-100 text-red-600 border-red-200",
    bar: "bg-red-400",
    desc: "荒れる可能性が高いレースです。指数外からの穴馬にも注意が必要です。",
  },
} as const;

const RANK_CONFIG = {
  S: { bg: "bg-purple-100", text: "text-purple-700", border: "border-purple-300" },
  A: { bg: "bg-green-100",  text: "text-green-700",  border: "border-green-300"  },
  B: { bg: "bg-yellow-100", text: "text-yellow-700", border: "border-yellow-300" },
  C: { bg: "bg-gray-100",   text: "text-gray-500",   border: "border-gray-200"   },
} as const;

const RECOMMEND_DESC: Record<string, string> = {
  S: "期待値が高く積極的に購入を推奨します。",
  A: "期待値がプラス圏内で購入を推奨します。",
  B: "収支ほぼ±0圏内。慎重な判断が必要です。",
  C: "期待値が低いか、人気すぎて旨みが少ないレースです。",
};

export function ConfidencePanel({ confidence }: Props) {
  const cfg = LABEL_CONFIG[confidence.label];
  const confRank = confidence.rank ?? "C";
  const recRank  = confidence.recommend_rank ?? "C";
  const confCls  = RANK_CONFIG[confRank as keyof typeof RANK_CONFIG] ?? RANK_CONFIG.C;
  const recCls   = RANK_CONFIG[recRank  as keyof typeof RANK_CONFIG] ?? RANK_CONFIG.C;

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
      {/* ヘッダー */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
          <span
            className="w-1 h-4 rounded inline-block"
            style={{ background: "var(--primary)" }}
          />
          レース評価
        </h2>
      </div>

      {/* 信頼度・推奨度ランク */}
      <div className="flex items-center gap-4 mb-3">
        <div className="flex flex-col items-center gap-0.5">
          <span className={`text-xs px-2.5 py-0.5 rounded border font-bold ${confCls.bg} ${confCls.text} ${confCls.border}`}>
            信頼度 <span className="text-base">{confRank}</span>
          </span>
          <span className="text-[10px] text-gray-400">{confidence.score}pt</span>
        </div>
        <div className="flex flex-col items-center gap-0.5">
          <span className={`text-xs px-2.5 py-0.5 rounded border font-bold ${recCls.bg} ${recCls.text} ${recCls.border}`}>
            推奨度 <span className="text-base">{recRank}</span>
          </span>
          <span className="text-[10px] text-gray-400">
            {confidence.top_win_odds != null ? `${confidence.top_win_odds.toFixed(1)}倍` : "オッズ待ち"}
          </span>
        </div>
        <div className="ml-auto">
          <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${cfg.badge}`}>
            {cfg.text}
          </span>
        </div>
      </div>

      {/* スコアバー */}
      <div className="flex items-center gap-3 mb-3">
        <div className="flex-shrink-0 flex items-baseline gap-0.5">
          <span className="text-2xl font-bold text-gray-800 tabular-nums">{confidence.score}</span>
          <span className="text-xs text-gray-400">/100</span>
        </div>
        <div className="flex-1">
          <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
            <div
              role="progressbar"
              aria-valuenow={confidence.score}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="信頼度スコア"
              className={`h-2 rounded-full transition-all ${cfg.bar}`}
              style={{ width: `${confidence.score}%` }}
            />
          </div>
        </div>
      </div>

      {/* 内訳 */}
      <div className="grid grid-cols-2 gap-2 mb-3 text-[11px]">
        <div className="bg-gray-50 rounded-lg px-2.5 py-1.5">
          <span className="text-gray-400">1〜2位差</span>
          <span className="float-right font-bold text-gray-700 tabular-nums">
            {confidence.gap_1_2.toFixed(1)}
          </span>
        </div>
        <div className="bg-gray-50 rounded-lg px-2.5 py-1.5">
          <span className="text-gray-400">1〜3位差</span>
          <span className="float-right font-bold text-gray-700 tabular-nums">
            {confidence.gap_1_3.toFixed(1)}
          </span>
        </div>
        <div className="bg-gray-50 rounded-lg px-2.5 py-1.5">
          <span className="text-gray-400">出走頭数</span>
          <span className="float-right font-bold text-gray-700 tabular-nums">
            {confidence.head_count}頭
          </span>
        </div>
        {confidence.win_prob_top != null && (
          <div className="bg-gray-50 rounded-lg px-2.5 py-1.5">
            <span className="text-gray-400">本命勝率</span>
            <span className="float-right font-bold text-gray-700 tabular-nums">
              {Math.round(confidence.win_prob_top * 100)}%
            </span>
          </div>
        )}
      </div>

      {/* 推奨度説明 */}
      <p className="text-[11px] text-gray-500 leading-relaxed">{RECOMMEND_DESC[recRank]}</p>
    </section>
  );
}
