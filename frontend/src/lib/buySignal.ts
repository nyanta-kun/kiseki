export type BuySignal = "buy" | "caution" | "pass" | null | undefined;
export type PurchaseSignal = "super_buy" | "buy" | "watch" | null | undefined;

/**
 * JRA レースレベル購入指針をフロントエンドで算出（詳細ページ用）。
 * バックエンドの jra_buy_signal と同一ロジック。
 *
 * v26 ensemble 検証 (2026-05-02) ベース:
 *   オッズ ≥ 10 → buy (単勝ROI 1.237)
 *   6 ≤ オッズ < 10 → caution (~1.0)
 *   オッズ < 6 → pass (0.85-0.89, 鉄板買いはマイナス)
 */
export function computeJraBuySignal(
  _distance: number,
  topOdds: number | null,
): BuySignal {
  if (topOdds === null) return null;
  if (topOdds >= 10.0) return "buy";
  if (topOdds >= 6.0) return "caution";
  return "pass";
}

/**
 * 個別馬の購入シグナルをフロントエンドで算出。
 * バックエンドの jra_horse_purchase_signal と同一ロジック。
 *
 * v26 breakaway 検証 (2026-05-02):
 *   super_buy: rank≤2 ∧ top2_t3_gap≥7 ∧ オッズ≥10 → 単勝ROI 1.593 (年46R)
 *   buy:       rank≤2 ∧ top2_t3_gap≥5 ∧ オッズ≥10 → 単勝ROI 1.290 (年79R)
 *   watch:     rank≤3 ∧ オッズ≥10               → 単勝ROI 1.042 (年1786R)
 */
export function computeHorsePurchaseSignal(
  rank: number,
  top2T3Gap: number | null,
  winOdds: number | null,
): PurchaseSignal {
  if (winOdds === null || winOdds < 10.0) return null;
  if (rank <= 2 && top2T3Gap !== null && top2T3Gap >= 7.0) return "super_buy";
  if (rank <= 2 && top2T3Gap !== null && top2T3Gap >= 5.0) return "buy";
  if (rank <= 3) return "watch";
  return null;
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
