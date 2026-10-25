import {
  MEKAUCHIDA_UPSET_NOTE,
  type MekauchidaUpsetVariant,
  mekauchidaUpsetClass,
} from "@/lib/mekauchidaUpset";

/** 波乱度（メカウチダの荒れやすさ）。詳細の見出しと一覧カードで使う。未取得なら何も出さない。 */
export function MekauchidaUpsetChip({
  level,
  variant = "header",
}: {
  level: string | null | undefined;
  variant?: MekauchidaUpsetVariant;
}) {
  const cls = mekauchidaUpsetClass(level, variant);
  if (!cls) return null;
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 whitespace-nowrap ${cls}`}
      title={MEKAUCHIDA_UPSET_NOTE}
    >
      波乱度 <b>{level}</b>
    </span>
  );
}
