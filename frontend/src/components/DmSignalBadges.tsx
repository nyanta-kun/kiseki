"use client";

import { cn } from "@/lib/utils";

/**
 * DM シグナルタグ → 短縮ラベル/色/ツールチップのマッピング（共通定義・単一真実源）
 *
 * タグ文字列はバックエンド backend/src/indices/dm_signals.py の定数と一致させること。
 * バックテスト実証値: 99.0%カバレッジ・8,618レース・3年実績 (2023-2026)
 *
 * 注: 実装には表の条件に加えて「高得点鉄板の composite 順位≤2 キャップ」と
 * コース/セグメント別 deny フィルタがある（詳細は dm_signals.py 参照）。
 */
export const DM_SIGNAL_META: Record<string, { label: string; cls: string; title: string }> = {
  "三冠一致":      { label: "🔥三冠", cls: "bg-rose-100 text-rose-800 border-rose-300",          title: "総合・DMtime・DMbattle 全1位 (勝率39%/複勝72%)" },
  "高得点鉄板":    { label: "⭐鉄板", cls: "bg-amber-100 text-amber-800 border-amber-300",        title: "総合≥60 ∧ DM-battle≥65 ∧ 総合2位以内 (ROI 101%, 勝率47%)" },
  "穴ぐさDM":      { label: "🏆穴DM", cls: "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-300", title: "穴ぐさA/B ∧ DM-battle1位 ∧ 人気≥5 (ROI 189% / 最強)" },
  "DM大穴":        { label: "⚡大穴", cls: "bg-purple-100 text-purple-800 border-purple-300",    title: "DM-battle1位 ∧ 人気≥7 ∧ battle≥65 (ROI 154%)" },
  "DM高オッズ":    { label: "⚡高オ", cls: "bg-violet-100 text-violet-800 border-violet-300",    title: "DM-battle1位 ∧ 単勝≥10倍 ∧ DM-time≤2位 (ROI 130%)" },
  "穴ぐさ+DMtime": { label: "💎穴T",  cls: "bg-cyan-100 text-cyan-800 border-cyan-300",          title: "穴ぐさA ∧ DM-time1位 (ROI 103%)" },
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
