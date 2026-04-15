"use client";

import { RaceConfidence } from "@/lib/api";
import { BuySignalBadge, BUY_SIGNAL_DESC } from "./BuySignalBadge";

type Props = {
  confidence: RaceConfidence;
  buySignal?: "buy" | "caution" | "pass" | null;
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

export function ConfidencePanel({ confidence, buySignal }: Props) {
  const cfg = LABEL_CONFIG[confidence.label];
  const confRank = confidence.rank ?? "C";
  const recRank  = confidence.recommend_rank ?? "C";
  const confCls  = RANK_CONFIG[confRank as keyof typeof RANK_CONFIG] ?? RANK_CONFIG.C;
  const recCls   = RANK_CONFIG[recRank  as keyof typeof RANK_CONFIG] ?? RANK_CONFIG.C;

  const signalDesc = buySignal ? BUY_SIGNAL_DESC[buySignal] : null;

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm space-y-3">
      {/* ヘッダー */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
          <span
            className="w-1 h-4 rounded inline-block"
            style={{ background: "var(--primary)" }}
          />
          レース評価
        </h2>
        <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${cfg.badge}`}>
          {cfg.text}
        </span>
      </div>

      {/* ── 購入指針（過去実績ベース）── */}
      {buySignal !== undefined && (
        <div className="rounded-lg border px-3 py-2 bg-gray-50">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[10px] text-gray-400 font-medium">購入指針</span>
            <BuySignalBadge signal={buySignal} size="sm" />
          </div>
          {signalDesc && (
            <p className="text-[11px] text-gray-500 leading-relaxed">{signalDesc}</p>
          )}
          {!buySignal && (
            <p className="text-[11px] text-gray-400">オッズ取得後に更新されます。</p>
          )}
        </div>
      )}

      {/* ── 指数信頼度 + 期待値ランク ── */}
      <div className="flex items-center gap-3">
        <div className="flex flex-col items-center gap-0.5">
          <span className={`text-xs px-2.5 py-0.5 rounded border font-bold ${confCls.bg} ${confCls.text} ${confCls.border}`}>
            指数信頼度 <span className="text-base">{confRank}</span>
          </span>
          <span className="text-[10px] text-gray-400">{confidence.score}pt</span>
        </div>
        <div className="flex flex-col items-center gap-0.5">
          <span className={`text-xs px-2.5 py-0.5 rounded border font-bold ${recCls.bg} ${recCls.text} ${recCls.border}`}>
            期待値 <span className="text-base">{recRank}</span>
          </span>
          <span className="text-[10px] text-gray-400">
            {confidence.top_win_odds != null ? `${confidence.top_win_odds.toFixed(1)}倍` : "オッズ待ち"}
          </span>
        </div>
      </div>

      {/* スコアバー */}
      <div className="flex items-center gap-3">
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
              aria-label="指数信頼度スコア"
              className={`h-2 rounded-full transition-all ${cfg.bar}`}
              style={{ width: `${confidence.score}%` }}
            />
          </div>
        </div>
      </div>

      {/* 内訳 */}
      <div className="grid grid-cols-2 gap-2 text-[11px]">
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

      {/* 指数信頼度説明 */}
      <p className="text-[11px] text-gray-500 leading-relaxed">{cfg.desc}</p>
    </section>
  );
}
