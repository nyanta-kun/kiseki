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
 *
 * 「特穴」(2026-07-26追加、[[jra_anagusa_elite_signal]]): 穴ぐさ(A/B/C) ∧
 * 指数(composite)順位3位以内 ∧ 単勝オッズ10倍以上。「穴」と異なりROIを狙う
 * タグ。実質2.5年(sekito.anagusaは2024-01以降のみ)のtrain+val/test 2窓で
 * 単勝ROI 1.40前後が一貫(FULL n=535, 単ROI1.417, drop1=1.329)。95%信頼区間は
 * わずかに1を跨ぐため回収率を保証するものではないが、「穴」より一段強い
 * シグナルとして扱う。
 *
 * 「平八」(2026-09-06追加、[[jra_heihachi_badge]]): 平地OP特別以上 ∧ 指数順位3位以内
 * ∧ 単勝10〜40倍 ∧ 複勝確率≥0.30。note の予想家「平八」の馬印19,429件を逆解析して
 * 得た「穴サイドで3着内率が構造的に高いゾーン」を、印を使わず自前の指数だけで
 * 再現したタグ。n=1,017 (2.83件/開催日) で 3着内率34.6%(同オッズ帯ベース17.8%)、
 * 複勝ROI 1.147・単勝ROI 1.171、年別複勝ROIは2023-2026の4年とも1超。
 * レース選定(OP特別以上)がROIを担っており、平場・条件戦では複回収94%で機能しない。
 */
export const DM_SIGNAL_META: Record<string, { label: string; cls: string; title: string }> = {
  "穴": { label: "🔍穴", cls: "bg-fuchsia-100 text-fuchsia-800 border-fuchsia-300", title: "レース内で最も有力な穴候補1頭(単勝≥10倍 ∧ 穴ぐさ/netkeiba/kichiuma/DM-battleのいずれかが上位評価、複勝的中約20%・回収率の保証はなし)" },
  "特穴": { label: "💥特穴", cls: "bg-red-100 text-red-800 border-red-400 font-extrabold", title: "穴ぐさ(A/B/C) ∧ 指数順位3位以内 ∧ 単勝10倍以上(単勝ROI約1.4・2窓で一貫。ただし信頼区間はわずかに1を跨ぎ回収率を保証するものではない)" },
  "平八": { label: "🎯平八", cls: "bg-amber-100 text-amber-900 border-amber-400 font-extrabold", title: "OP特別以上 ∧ 指数順位3位以内 ∧ 単勝10〜40倍 ∧ 複勝確率30%以上。3着内率34.6%(同オッズ帯の全馬は17.8%)・複勝ROI約1.15で2023〜2026の4年とも1超。回収率を保証するものではない" },
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
