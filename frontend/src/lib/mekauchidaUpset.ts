import type { MekauchidaUpsetLevel } from "@/lib/api";

/**
 * メカウチダの「荒れやすさ」をレース詳細の見出しに出すときの見た目。
 *
 * 外部サイトの値なので、出典（メカウチダ）を必ず添える。kiseki の指数と
 * 取り違えないため。値の作り方はサイトの説明による:
 * 1・2番人気の複勝圏の確率をモデルとオッズで比べ、モデルの方が人気馬を
 * 低く見ていれば「高」（人気馬が危ない）、高く見ていれば「低」（堅い）。
 */
export const MEKAUCHIDA_UPSET_NOTE =
  "外部サイト「メカウチダ」の荒れやすさ（発走前の値）。1・2番人気の複勝圏の確率を、モデルとオッズで比べたもの。高いほど人気馬が危ない。kiseki の指数とは別";

const CLASSES: Record<MekauchidaUpsetLevel, string> = {
  高: "bg-rose-500 text-white",
  中: "bg-amber-300 text-gray-900",
  低: "bg-white/20 text-white",
};

/** 表示する値なら見た目のクラスを返す。未取得・想定外の値は null（チップを出さない）。 */
export function mekauchidaUpsetClass(level: string | null | undefined): string | null {
  if (level === "高" || level === "中" || level === "低") return CLASSES[level];
  return null;
}
