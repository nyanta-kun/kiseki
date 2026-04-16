"use client";

import type { BuySignal } from "@/lib/buySignal";
export type { BuySignal } from "@/lib/buySignal";
export { computeJraBuySignal, computeChihouBuySignal } from "@/lib/buySignal";

type Size = "sm" | "md";

type Props = {
  signal: BuySignal;
  size?: Size;
  showLabel?: boolean;
};

const SIGNAL_CONFIG = {
  buy: {
    label: "購入推奨",
    shortLabel: "推奨",
    icon: "▶",
    cls: "bg-green-100 text-green-700 border-green-300",
  },
  caution: {
    label: "要注意",
    shortLabel: "注意",
    icon: "◆",
    cls: "bg-yellow-100 text-yellow-700 border-yellow-300",
  },
  pass: {
    label: "見送り推奨",
    shortLabel: "見送り",
    icon: "✕",
    cls: "bg-red-50 text-red-500 border-red-200",
  },
} as const;

export function BuySignalBadge({ signal, size = "sm", showLabel = false }: Props) {
  if (!signal) return null;

  const cfg = SIGNAL_CONFIG[signal];
  const isSmall = size === "sm";
  const textSize = isSmall ? "text-[10px]" : "text-xs";
  const padding = isSmall ? "px-1.5 py-0.5" : "px-2.5 py-1";

  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded border font-semibold whitespace-nowrap ${textSize} ${padding} ${cfg.cls}`}
      title={cfg.label}
    >
      <span>{cfg.icon}</span>
      <span>{showLabel ? cfg.label : cfg.shortLabel}</span>
    </span>
  );
}

/** 購入指針の説明文（ConfidencePanel内などで使用） */
export const BUY_SIGNAL_DESC: Record<NonNullable<BuySignal>, string> = {
  buy:     "過去実績に基づき積極的な購入を推奨します（単勝ROI 100%超）。",
  caution: "購入可能圏内ですが収益は限定的です（単勝ROI ±0付近）。",
  pass:    "過去実績から見送りを推奨します（単勝ROI 100%未満）。",
};

