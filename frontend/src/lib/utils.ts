/**
 * ユーティリティ関数
 */

/** CSS クラス結合 */
export function cn(...classes: (string | undefined | null | false)[]): string {
  return classes.filter(Boolean).join(" ");
}

/** YYYYMMDD → "3月22日(土)" 形式 */
export function formatDate(yyyymmdd: string): string {
  const y = parseInt(yyyymmdd.slice(0, 4));
  const m = parseInt(yyyymmdd.slice(4, 6));
  const d = parseInt(yyyymmdd.slice(6, 8));
  const date = new Date(y, m - 1, d);
  const days = ["日", "月", "火", "水", "木", "金", "土"];
  return `${m}月${d}日(${days[date.getDay()]})`;
}

/** 今日の日付を YYYYMMDD 形式で返す（JST = UTC+9 固定） */
export function todayYYYYMMDD(): string {
  // サーバー(Docker/UTC)・クライアント問わず JST で統一
  const jst = new Date(Date.now() + 9 * 60 * 60 * 1000);
  return (
    jst.getUTCFullYear().toString() +
    String(jst.getUTCMonth() + 1).padStart(2, "0") +
    String(jst.getUTCDate()).padStart(2, "0")
  );
}

/** 指数(0-100)を色クラスに変換 */
export function indexColor(value: number | null): string {
  if (value === null) return "text-gray-400";
  if (value >= 65) return "text-green-700 font-bold";
  if (value >= 55) return "text-green-600";
  if (value >= 45) return "text-gray-700";
  if (value >= 35) return "text-orange-600";
  return "text-red-600";
}

/** 指数バーの色 */
export function indexBarColor(value: number | null): string {
  if (value === null) return "bg-gray-200";
  if (value >= 65) return "bg-green-500";
  if (value >= 55) return "bg-green-400";
  if (value >= 45) return "bg-yellow-400";
  if (value >= 35) return "bg-orange-400";
  return "bg-red-400";
}

/**
 * 期待値を算出する。
 * EV = 単勝オッズ × 勝率予測（オッズがない場合は null）
 */
export function calcEV(
  winProbability: number | null,
  winOdds: number | null
): number | null {
  if (winProbability === null || winOdds === null) return null;
  return winOdds * winProbability;
}

/** 期待値の評価クラス */
export function evClass(ev: number | null): string {
  if (ev === null) return "";
  if (ev >= 1.2) return "ev-badge-high";
  if (ev >= 0.9) return "ev-badge-mid";
  return "ev-badge-low";
}

/** 期待値の評価ラベル */
export function evLabel(ev: number | null): string {
  if (ev === null) return "-";
  if (ev >= 1.2) return `▲ ${ev.toFixed(2)}`;
  if (ev >= 0.9) return ev.toFixed(2);
  return ev.toFixed(2);
}

/** 馬場面を絵文字で表示 */
export function surfaceIcon(surface: string): string {
  if (surface.startsWith("芝")) return "🌿";
  if (surface.startsWith("ダ")) return "🟤";
  return "⛰";
}

/** グレードバッジの色 */
export function gradeClass(grade: string | null): string {
  if (!grade) return "bg-gray-100 text-gray-600";
  if (grade === "G1") return "bg-red-100 text-red-700 font-bold";
  if (grade === "G2") return "bg-blue-100 text-blue-700";
  if (grade === "G3") return "bg-purple-100 text-purple-700";
  if (grade.includes("OP") || grade.includes("L")) return "bg-yellow-100 text-yellow-700";
  return "bg-gray-100 text-gray-600";
}

/** 条件戦クラスバッジの色 */
export function raceClassBadgeClass(label: string | null): string {
  if (!label) return "bg-gray-100 text-gray-600";
  if (label.includes("3勝")) return "bg-indigo-100 text-indigo-700";
  if (label.includes("2勝")) return "bg-blue-100 text-blue-700";
  if (label.includes("1勝")) return "bg-sky-50 text-sky-700 border border-sky-200";
  if (label.includes("未勝利")) return "bg-gray-100 text-gray-600";
  return "bg-gray-100 text-gray-600";
}

/** 条件戦クラスの短縮表記（バッジ用）"4歳以上2勝クラス" → "2勝" */
export function raceClassShort(label: string | null): string | null {
  if (!label) return null;
  if (label.includes("3勝")) return "3勝";
  if (label.includes("2勝")) return "2勝";
  if (label.includes("1勝")) return "1勝";
  if (label.includes("未勝利")) return "未勝利";
  return null;
}
