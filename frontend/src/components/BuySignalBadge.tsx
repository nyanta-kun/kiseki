"use client";

// 購入指針はバックエンド（src/indices/buy_signal.py）が単一の真実源。
// フロントは API の race.buy_signal をそのまま表示する（判定ロジックの再実装禁止）。
export type BuySignal = "buy" | "caution" | "pass" | null | undefined;

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

/** 購入指針の説明文（レース詳細パネル内で使用） */
export const BUY_SIGNAL_DESC: Record<NonNullable<BuySignal>, string> = {
  buy:     "バックテストでROI良好な条件（JRA: 指数1位が単勝10倍以上 / 地方: 好ROI場×推奨ランク上位）。",
  caution: "条件が最適帯から外れています。詳細を確認の上判断してください。",
  pass:    "バックテストでROI不良な条件（低オッズ本命買い・低ROI場など）。見送り推奨。",
};

