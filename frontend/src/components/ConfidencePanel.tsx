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

export function ConfidencePanel({ confidence }: Props) {
  const cfg = LABEL_CONFIG[confidence.label];

  return (
    <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-gray-700 flex items-center gap-1.5">
          <span
            className="w-1 h-4 rounded inline-block"
            style={{ background: "var(--green-mid)" }}
          />
          レース信頼度
        </h2>
        <span className={`text-[11px] px-2 py-0.5 rounded-full border font-medium ${cfg.badge}`}>
          {cfg.text}
        </span>
      </div>

      {/* スコアバー */}
      <div className="flex items-center gap-3 mb-3">
        <div className="flex-shrink-0 flex items-baseline gap-0.5">
          <span className="text-3xl font-bold text-gray-800 tabular-nums">{confidence.score}</span>
          <span className="text-sm text-gray-400">/100</span>
        </div>
        <div className="flex-1">
          <div className="w-full bg-gray-100 rounded-full h-2.5 overflow-hidden">
            <div
              className={`h-2.5 rounded-full transition-all ${cfg.bar}`}
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
        <div className="bg-gray-50 rounded-lg px-2.5 py-1.5 col-span-2">
          <span className="text-gray-400">出走頭数</span>
          <span className="float-right font-bold text-gray-700 tabular-nums">
            {confidence.head_count}頭
          </span>
        </div>
      </div>

      {/* 説明文 */}
      <p className="text-[11px] text-gray-500 leading-relaxed">{cfg.desc}</p>
    </section>
  );
}
