"use client";

type BuySignal = "buy" | "caution" | "pass" | null | undefined;

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

// ---------------------------------------------------------------------------
// フロントエンド購入指針算出ユーティリティ
// ---------------------------------------------------------------------------

/**
 * JRA購入指針をフロントエンドで算出（詳細ページ用）
 * バックエンドのjra_buy_signalと同一ロジック
 */
export function computeJraBuySignal(
  distance: number,
  topOdds: number | null,
): BuySignal {
  if (topOdds === null) return null;
  if (distance <= 1400) return "pass";
  if (topOdds < 3.0) return "pass";
  if (topOdds >= 4.0) return "buy";
  return "caution"; // 3.0 <= odds < 4.0
}

const _CHIHOU_COURSE_GRADE: Record<string, NonNullable<BuySignal>> = {
  高知: "buy", 盛岡: "buy", 園田: "buy",
  佐賀: "caution", 門別: "caution", 名古屋: "caution",
  金沢: "caution", 笠松: "caution", 大井: "caution", 川崎: "caution",
  水沢: "pass", 姫路: "pass", 船橋: "pass", 浦和: "pass",
};

/**
 * 地方競馬購入指針をフロントエンドで算出（詳細ページ用）
 * バックエンドのchihou_buy_signalと同一ロジック
 */
export function computeChihouBuySignal(courseName: string): NonNullable<BuySignal> {
  return _CHIHOU_COURSE_GRADE[courseName] ?? "caution";
}
