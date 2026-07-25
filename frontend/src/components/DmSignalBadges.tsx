"use client";

import { cn } from "@/lib/utils";

/**
 * DM シグナルタグ → 短縮ラベル/色/ツールチップのマッピング（共通定義・単一真実源）
 *
 * タグ文字列はバックエンド backend/src/indices/dm_signals.py の定数と一致させること。
 *
 * ⚠️ 2026-07-25 全面簡素化([[jra_upset_badge_redesign]]): 軸信頼度は
 * `recommend_rank`（購入指針パネル）に一本化済みのため、per-horseの軸タグ
 * (三冠一致/高得点鉄板)・警戒タグ(人気下振れ)はユーザー指示により廃止。
 * 穴タグも複数指数一致穴/指数一致穴の2段階を単一の「穴」マークに統合。
 *
 * 「穴」: レース内で最も有力な穴候補1頭のみ(単勝≥10倍 ∧ 穴ぐさ/netkeiba/
 * kichiuma/DM-battleのうち1つ以上が上位評価、複勝的中約20%・両窓で安定)。
 * 複勝ROIは<1(控除率の壁)で「的中率の分離」用途、回収率の保証はない。
 */
export const DM_SIGNAL_META: Record<string, { label: string; cls: string; title: string }> = {
  "穴": { label: "🔍穴", cls: "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-300", title: "レース内で最も有力な穴候補1頭(単勝≥10倍 ∧ 穴ぐさ/netkeiba/kichiuma/DM-battleのいずれかが上位評価、複勝的中約20%・回収率の保証はなし)" },
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
