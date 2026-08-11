// 成績／売上ページ共通の表示フォーマッタ。
// page.tsx と AnalysisTab.tsx の両方が使うためファイルを分けている。

/**
 * 金額を短く表示する（¥1,234 / ¥1.2万 / ¥123万）。
 *
 * ⚠️ **絶対値で桁を判定して符号を後付けすること。**
 *    負数をそのまま比較すると 10,000 以上の分岐に一度も入らず、
 *    損益だけが `¥-1,095,170` のようにフル桁で出てカードからはみ出す
 *    （2026-08-11 に成績カードで実際に起きていた）。
 */
export function formatYen(val: number): string {
  const sign = val < 0 ? "-" : "";
  const abs = Math.abs(val);
  if (abs >= 1_000_000) return `${sign}¥${(abs / 10000).toFixed(0)}万`;
  if (abs >= 10_000) return `${sign}¥${(abs / 10000).toFixed(1)}万`;
  return `${sign}¥${abs.toLocaleString()}`;
}

/** 0〜1 の割合を「12.3%」に。null は「—」。 */
export function formatPct(val: number | null | undefined, digits = 1): string {
  if (val == null) return "—";
  return `${(val * 100).toFixed(digits)}%`;
}

/** 相関係数を符号つきで（+0.68 / -0.30）。null は「—」。 */
export function formatCoef(val: number | null | undefined): string {
  if (val == null) return "—";
  return `${val >= 0 ? "+" : ""}${val.toFixed(2)}`;
}

/** 前日比・平均比などの増減を「▲ 2,480」「▼ 19」で。0 は「±0」。 */
export function formatDelta(val: number | null | undefined): string {
  if (val == null) return "—";
  if (val === 0) return "±0";
  return `${val > 0 ? "▲" : "▼"} ${Math.abs(val).toLocaleString()}`;
}
