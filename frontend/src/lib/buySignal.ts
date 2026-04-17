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

// v8 P1実績（2023-04-16〜2024-04-16, 3,373R）に基づくコースグレード
const _CHIHOU_COURSE_GRADE: Record<string, "buy" | "caution" | "pass"> = {
  // buy: ROI ≥ 85%
  高知: "buy",  // 94.7%
  園田: "buy",  // 91.0%
  盛岡: "buy",  // 過去実績から維持
  // caution: 60% ≤ ROI < 85%
  佐賀: "caution",   // 83.7%
  名古屋: "caution", // 78.6%
  水沢: "caution",   // 77.9%（旧pass）
  大井: "caution",   // 77.5%
  姫路: "caution",   // 71.4%（旧pass）
  船橋: "caution",   // 71.1%（旧pass）
  川崎: "caution",   // 64.9%
  笠松: "caution",   // 64.1%
  浦和: "caution",   // 61.6%
  門別: "caution",   // 暫定
  // pass: ROI < 60%
  金沢: "pass",      // 48.3%（旧caution）
};

/**
 * 地方競馬購入指針をフロントエンドで算出（詳細ページ用）
 * バックエンドの chihou_buy_signal と同一ロジック。
 *
 * recommend_rank（EV期待値ランク）が得られている場合はコース × EV で判定:
 *   buy-course + S/A → "buy"     buy-course + B/C → "caution"
 *   caution-course + S/A → "caution"  caution-course + B/C → "pass"
 *   pass-course → "pass"
 */
export function computeChihouBuySignal(
  courseName: string,
  recommendRank?: "S" | "A" | "B" | "C" | null,
): NonNullable<BuySignal> {
  const grade = _CHIHOU_COURSE_GRADE[courseName] ?? "caution";
  if (!recommendRank) return grade; // オッズ未取得: コースのみ

  if (grade === "buy") {
    return recommendRank === "S" || recommendRank === "A" ? "buy" : "caution";
  }
  if (grade === "caution") {
    return recommendRank === "S" || recommendRank === "A" ? "caution" : "pass";
  }
  return "pass";
}
