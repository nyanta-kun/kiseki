export type BuySignal = "buy" | "caution" | "pass" | null | undefined;

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
