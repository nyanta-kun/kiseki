import { MEKAUCHIDA_UPSET_NOTE, mekauchidaUpsetClass } from "@/lib/mekauchidaUpset";

/** レース詳細の見出しに出す波乱度（メカウチダの荒れやすさ）。未取得なら何も出さない。 */
export function MekauchidaUpsetChip({ level }: { level: string | null | undefined }) {
  const cls = mekauchidaUpsetClass(level);
  if (!cls) return null;
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 whitespace-nowrap ${cls}`}
      title={MEKAUCHIDA_UPSET_NOTE}
    >
      波乱度 <b>{level}</b>
      <span className="ml-1 opacity-80">メカウチダ</span>
    </span>
  );
}
