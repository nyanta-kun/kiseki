"use client";

import { cn } from "@/lib/utils";

/**
 * DM シグナルタグ → 短縮ラベル/色/ツールチップのマッピング（共通定義・単一真実源）
 *
 * タグ文字列はバックエンド backend/src/indices/dm_signals.py の定数と一致させること。
 * 軸タグ(三冠一致/高得点鉄板)はバックテスト実証値: 99.0%カバレッジ・8,618レース・3年実績。
 * 穴タグ(複数指数一致穴/指数一致穴)は2026-07-25再設計([[jra_upset_badge_redesign]])。
 * 旧4タグ(穴ぐさDM/DM大穴/DM高オッズ/穴ぐさ+DMtime)は小標本でOOS不安定だったため廃止し、
 * 4情報源(穴ぐさ/netkeiba/kichiuma/DM-battle)の一致数(badge_cnt)に一本化。
 * 単勝オッズ≥10の人気薄馬のみ対象。複勝ROIは<1(控除率の壁)で「的中率の分離」用途。
 * 1レースにつきbadge_cnt最大の1頭のみに付与(複数頭に付くと判断が曖昧になるため、
 * 複勝圏頭数キャップ案は合算的中率が基準未達で不採用・K=1に集約)。
 *
 * 注: 実装には表の条件に加えて「高得点鉄板の composite 順位≤2 キャップ」と
 * コース/セグメント別 deny フィルタがある（詳細は dm_signals.py 参照）。
 */
export const DM_SIGNAL_META: Record<string, { label: string; cls: string; title: string }> = {
  "三冠一致":      { label: "🔥三冠", cls: "bg-rose-100 text-rose-800 border-rose-300",          title: "総合・DMtime・DMbattle 全1位 (勝率39%/複勝72%)" },
  "高得点鉄板":    { label: "⭐鉄板", cls: "bg-amber-100 text-amber-800 border-amber-300",        title: "総合≥60 ∧ DM-battle≥65 ∧ 総合2位以内 (勝率26-27%/複勝率59-60%、両窓で安定。単勝回収率は保証しない軸候補シグナル)" },
  "複数指数一致穴": { label: "🔍複数穴", cls: "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-300", title: "レース内で最も有力な穴候補1頭(単勝≥10倍 ∧ 穴ぐさ/netkeiba/kichiuma/DM-battleのうち2つ以上が上位評価、複勝的中約20%)" },
  "指数一致穴":    { label: "指数穴",  cls: "bg-violet-100 text-violet-800 border-violet-300",    title: "レース内で最も有力な穴候補1頭(単勝≥10倍 ∧ 上記情報源のうち1つが上位評価、複勝的中約20%)" },
  "人気下振れ":    { label: "❌警戒", cls: "bg-slate-200 text-slate-700 border-slate-400",       title: "人気≤3位だが総合・DM-battle両方が4位以下 (ROI 74%、軸候補から除外推奨)" },
};

type Props = {
  signals: string[] | null | undefined;
  /** true: 表形式向けの小さめ表示 (text-[9px] + nowrap) */
  compact?: boolean;
};

export function DmSignalBadges({ signals, compact = false }: Props) {
  if (!signals || signals.length === 0) return null;
  return (
    <>
      {signals.map((sig) => {
        const meta = DM_SIGNAL_META[sig];
        if (!meta) return null;
        return (
          <span
            key={sig}
            title={meta.title}
            className={cn(
              compact
                ? "text-[9px] px-1 py-0.5 rounded border font-bold whitespace-nowrap"
                : "text-[10px] px-1 py-0.5 rounded border font-bold",
              meta.cls,
            )}
          >
            {meta.label}
          </span>
        );
      })}
    </>
  );
}
