import type { MekauchidaUpsetLevel } from "@/lib/api";

/**
 * メカウチダの「荒れやすさ」（波乱度）をレース詳細の見出しと一覧カードに出すときの見た目。
 *
 * 外部サイトの値。画面のラベルは「波乱度」だけにし（2026-10-25 ユーザー判断）、
 * 出典はツールチップに残す。値の作り方はサイトの説明による:
 * 1・2番人気の複勝圏の確率をモデルとオッズで比べ、モデルの方が人気馬を
 * 低く見ていれば「高」（人気馬が危ない）、高く見ていれば「低」（堅い）。
 *
 * 値は発走前はサイトの最新の札、発走後はサイトが判断済みとして出した札
 * （2026-10-25 から。それより前のレースは履歴ページの札）。
 */
export const MEKAUCHIDA_UPSET_NOTE =
  "外部サイト「メカウチダ」の荒れやすさ。1・2番人気の複勝圏の確率を、モデルとオッズで比べたもの。高いほど人気馬が危ない。オッズで変わるので、発走前は途中の値。kiseki の指数とは別";

/** header = 詳細の濃い色の見出し / card = 一覧の白いカード */
export type MekauchidaUpsetVariant = "header" | "card";

const CLASSES: Record<MekauchidaUpsetVariant, Record<MekauchidaUpsetLevel, string>> = {
  header: {
    高: "bg-rose-500 text-white",
    中: "bg-amber-300 text-gray-900",
    低: "bg-white/20 text-white",
  },
  card: {
    高: "bg-rose-50 text-rose-700 border border-rose-200",
    中: "bg-amber-50 text-amber-800 border border-amber-200",
    低: "bg-sky-50 text-sky-700 border border-sky-200",
  },
};

/** 表示する値なら見た目のクラスを返す。未取得・想定外の値は null（チップを出さない）。 */
export function mekauchidaUpsetClass(
  level: string | null | undefined,
  variant: MekauchidaUpsetVariant = "header",
): string | null {
  if (level === "高" || level === "中" || level === "低") return CLASSES[variant][level];
  return null;
}
