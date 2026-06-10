"use client";

import { useEffect, useState } from "react";
import {
  ChihouCategorySummary,
  ChihouRecommendation,
  ChihouRecommendCategory,
  ChihouSweetSpotResponse,
  RaceConcentration,
  fetchChihouRecommendationsBrowser,
  fetchChihouSweetSpotRecommendationsBrowser,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import Link from "next/link";

// ---------------------------------------------------------------------------
// ヘルパー関数
// ---------------------------------------------------------------------------

function formatPostTime(t: string | null): string {
  if (!t || t.length < 4) return "-";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

const CATEGORY_LABEL: Record<ChihouRecommendCategory, string> = {
  sweet_spot: "★ 穴",
  place_bet: "◆ 複穴",
  upset_place: "🎯 穴軸",
  low_odds_trusted: "🟢 信頼",
  low_odds_untrusted: "🟡 不信頼",
};

const CATEGORY_ACCENT: Record<ChihouRecommendCategory, string> = {
  sweet_spot: "text-red-700 bg-red-50 border-red-100",
  place_bet: "text-blue-700 bg-blue-50 border-blue-100",
  upset_place: "text-rose-700 bg-rose-50 border-rose-100",
  low_odds_trusted: "text-emerald-700 bg-emerald-50 border-emerald-100",
  low_odds_untrusted: "text-amber-700 bg-amber-50 border-amber-100",
};

function ResultCell({
  correct,
  payout,
  finishPos,
  betType,
}: {
  correct: boolean | null;
  payout: number | null;
  finishPos?: number | null;
  betType?: string;
}) {
  if (correct === null) {
    return <span className="text-xs text-gray-300">—</span>;
  }
  if (correct) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-bold text-amber-700">
        ◎ {payout ? `${payout}円` : ""}
        {finishPos != null && (
          <span className="text-[10px] text-gray-500 font-normal">({finishPos}着)</span>
        )}
      </span>
    );
  }
  const isPlaceZone =
    betType === "win" && finishPos != null && finishPos >= 2 && finishPos <= 3;
  if (isPlaceZone) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-bold text-sky-700 bg-sky-50 border border-sky-200 px-1.5 py-[1px] rounded">
        △ 複圏
        <span className="text-[10px] font-normal text-sky-600">{finishPos}着</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-gray-400">
      ×
      {finishPos != null && (
        <span className="text-[10px] text-gray-400 font-normal">({finishPos}着)</span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 競馬場 × カテゴリ サマリ
// ---------------------------------------------------------------------------

type VenueCellStat = {
  n_total: number;
  n_settled: number;
  n_hits: number;
  payout_sum: number;
  payout_count: number;
  bet_type: "win" | "place" | null;
};

function buildVenueStats(
  items: ChihouRecommendation[],
): Map<string, Record<ChihouRecommendCategory, VenueCellStat>> {
  const result = new Map<string, Record<ChihouRecommendCategory, VenueCellStat>>();
  const empty = (): VenueCellStat => ({
    n_total: 0, n_settled: 0, n_hits: 0, payout_sum: 0, payout_count: 0, bet_type: null,
  });
  const emptyRow = (): Record<ChihouRecommendCategory, VenueCellStat> => ({
    sweet_spot: empty(), place_bet: empty(), upset_place: empty(),
    low_odds_trusted: empty(), low_odds_untrusted: empty(),
  });
  for (const rec of items) {
    const cat = (rec.category ?? "sweet_spot") as ChihouRecommendCategory;
    const venue = rec.race.course_name;
    if (!result.has(venue)) result.set(venue, emptyRow());
    const cell = result.get(venue)![cat];
    cell.n_total += 1;
    if (rec.result_updated_at != null) {
      cell.n_settled += 1;
      if (rec.result_correct === true) cell.n_hits += 1;
      if (rec.result_payout != null) {
        cell.payout_sum += rec.result_payout;
        cell.payout_count += 1;
      }
    }
    cell.bet_type =
      rec.bet_type === "win" || rec.bet_type === "place" ? rec.bet_type : null;
  }
  return result;
}

function VenueCell({ stat }: { stat: VenueCellStat }) {
  if (stat.n_total === 0) return <span className="text-xs text-gray-300">—</span>;
  const hitText =
    stat.n_settled > 0 ? `${stat.n_hits}/${stat.n_settled}` : `0/${stat.n_total}*`;
  const roi =
    stat.payout_count > 0 ? stat.payout_sum / (stat.payout_count * 100) : null;
  return (
    <div className="text-[11px] leading-tight">
      <div className="font-semibold text-gray-700">{hitText}</div>
      {roi != null ? (
        <div className={cn("text-[10px]", roi >= 1 ? "text-emerald-600 font-bold" : "text-gray-400")}>
          {stat.bet_type === "place" ? "複" : "単"}{roi.toFixed(2)}
        </div>
      ) : (
        <div className="text-[10px] text-gray-300">未確定</div>
      )}
    </div>
  );
}

function VenueSummaryTable({ items }: { items: ChihouRecommendation[] }) {
  const stats = buildVenueStats(items);
  if (stats.size === 0) return null;
  const venues = Array.from(stats.keys()).sort();
  const cats: ChihouRecommendCategory[] = [
    "sweet_spot", "place_bet", "upset_place", "low_odds_trusted", "low_odds_untrusted",
  ];
  return (
    <div className="overflow-x-auto -mx-2 px-2">
      <table className="min-w-max text-xs border-collapse whitespace-nowrap">
        <thead>
          <tr className="border-b border-gray-200">
            <th className="text-left py-1.5 px-2 font-medium text-gray-500 sticky left-0 bg-white">競馬場</th>
            {cats.map((c) => (
              <th key={c} className={cn("text-center py-1.5 px-2 font-medium border-l border-gray-100 min-w-[70px]", CATEGORY_ACCENT[c].split(" ")[0])}>
                {CATEGORY_LABEL[c]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {venues.map((venue) => {
            const row = stats.get(venue)!;
            return (
              <tr key={venue} className="border-b border-gray-50">
                <td className="py-1.5 px-2 font-semibold text-gray-700 sticky left-0 bg-white">{venue}</td>
                {cats.map((c) => (
                  <td key={c} className="text-center py-1.5 px-2 border-l border-gray-100">
                    <VenueCell stat={row[c]} />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-[10px] text-gray-400 mt-1">
        各セル: 上段 = 的中/確定数（* は発走前）、下段 = ROI（単=単勝・複=複勝、ROI&ge;1.0は緑）。
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// カテゴリ別サマリ
// ---------------------------------------------------------------------------

function CategorySummaryStrip({ summary }: { summary: ChihouCategorySummary | undefined }) {
  if (!summary || summary.n_total === 0) return null;
  const isPending = summary.n_settled === 0;
  const hitText = summary.hit_rate != null ? `${(summary.hit_rate * 100).toFixed(1)}%` : "—";
  const roiText = summary.win_roi != null ? summary.win_roi.toFixed(2) : "—";
  const roiLabel = summary.bet_type === "place" ? "複勝ROI" : "単勝ROI";
  return (
    <span className="text-[11px] text-gray-600">
      {isPending ? (
        <span>確定 0/{summary.n_total} 件（発走後集計）</span>
      ) : (
        <>
          的中 {summary.n_hits}/{summary.n_settled} ({hitText})
          <span className="mx-1.5 text-gray-300">|</span>
          {roiLabel} {roiText}
          {summary.n_settled < summary.n_total && (
            <span className="ml-1.5 text-gray-400">/ 残{summary.n_total - summary.n_settled}件</span>
          )}
        </>
      )}
    </span>
  );
}

function FinishBadge({ pos }: { pos: number }) {
  if (pos === 1) {
    return (
      <span className="ml-1.5 text-[10px] font-bold px-1 py-[0.5px] rounded bg-amber-100 text-amber-700 border border-amber-200">
        🥇 1着
      </span>
    );
  }
  if (pos === 2 || pos === 3) {
    return (
      <span className="ml-1.5 text-[10px] font-bold px-1 py-[0.5px] rounded bg-sky-50 text-sky-700 border border-sky-200">
        {pos === 2 ? "🥈" : "🥉"} {pos}着
      </span>
    );
  }
  return <span className="ml-1.5 text-[10px] text-gray-400">{pos}着</span>;
}

function HorseInline({ rec }: { rec: ChihouRecommendation }) {
  const category = (rec.category ?? "sweet_spot") as ChihouRecommendCategory;
  return (
    <div className="space-y-0.5">
      {rec.target_horses.map((h) => (
        <div key={h.horse_number} className="text-xs leading-tight">
          <span className="text-gray-400 mr-1">{h.horse_number}</span>
          <span className="font-medium text-gray-800">{h.horse_name ?? "-"}</span>
          {h.win_probability != null &&
            (category === "low_odds_trusted" || category === "low_odds_untrusted") && (
              <span className="ml-1.5 text-[10px] text-gray-400">
                v10勝率{(h.win_probability * 100).toFixed(0)}%
              </span>
            )}
          {h.finish_position != null && <FinishBadge pos={h.finish_position} />}
        </div>
      ))}
    </div>
  );
}

function OddsInline({ rec, field }: { rec: ChihouRecommendation; field: "win_odds" | "place_odds" }) {
  return (
    <div className="space-y-0.5 text-right">
      {rec.target_horses.map((h) => {
        const v = h[field];
        return (
          <div key={h.horse_number} className="text-xs text-gray-700">
            {v != null ? v.toFixed(1) : <span className="text-gray-300">—</span>}
          </div>
        );
      })}
    </div>
  );
}

function ConcentrationBadge({ conc }: { conc: RaceConcentration | null }) {
  if (!conc || !conc.confidence_level) return null;
  const cfg = {
    high:   { label: "集中◎", cls: "text-emerald-700 bg-emerald-50 border-emerald-200", title: `top2シェア ${((conc.top2_share ?? 0) * 100).toFixed(0)}% — 1位複勝ヒット率 約76%` },
    medium: { label: "集中△", cls: "text-gray-500 bg-gray-50 border-gray-200",         title: `top2シェア ${((conc.top2_share ?? 0) * 100).toFixed(0)}% — 1位複勝ヒット率 約65-70%` },
    low:    { label: "分散▼", cls: "text-amber-700 bg-amber-50 border-amber-200",       title: `top2シェア ${((conc.top2_share ?? 0) * 100).toFixed(0)}% — 1位複勝ヒット率 約57%` },
  } as const;
  const { label, cls, title } = cfg[conc.confidence_level];
  return (
    <span
      className={cn("inline-block text-[10px] font-semibold px-1 py-[1px] rounded border leading-tight", cls)}
      title={title}
    >
      {label}
    </span>
  );
}

function EvInline({ rec }: { rec: ChihouRecommendation }) {
  return (
    <div className="space-y-0.5 text-right">
      {rec.target_horses.map((h) => (
        <div key={h.horse_number} className={cn("text-xs font-bold", (h.ev ?? 0) >= 1.5 ? "text-red-600" : "text-gray-600")}>
          {h.ev != null ? h.ev.toFixed(2) : <span className="text-gray-300">—</span>}
        </div>
      ))}
    </div>
  );
}

function computePlaceRoi(items: ChihouRecommendation[]): number | null {
  const settled = items.filter((r) => r.result_updated_at != null);
  if (settled.length === 0) return null;
  let payoutSum = 0;
  for (const rec of settled) {
    const placed = rec.target_horses
      .filter((h) => h.finish_position != null && h.finish_position <= 3 && h.place_odds != null)
      .sort((a, b) => (a.finish_position ?? 99) - (b.finish_position ?? 99));
    if (placed.length > 0) payoutSum += Math.round((placed[0].place_odds ?? 0) * 100);
  }
  return payoutSum / (settled.length * 100);
}

function CategoryTable({
  category, items, summary, title, note, titleClass,
}: {
  category: ChihouRecommendCategory;
  items: ChihouRecommendation[];
  summary: ChihouCategorySummary | undefined;
  title: string;
  note: string;
  titleClass: string;
}) {
  if (items.length === 0) return null;
  const sorted = [...items].sort((a, b) => (a.race.post_time ?? "").localeCompare(b.race.post_time ?? ""));
  const showPlace = category === "place_bet" || category === "sweet_spot" || category === "upset_place";
  const placeRoi = category === "sweet_spot" ? computePlaceRoi(sorted) : null;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <h3 className={cn("text-xs font-bold", titleClass)}>
          {title}
          <span className="ml-1.5 text-gray-400 font-normal">({items.length}件)</span>
        </h3>
        <div className="flex items-center gap-1.5 flex-wrap justify-end">
          <CategorySummaryStrip summary={summary} />
          {placeRoi != null && (
            <span className="text-[11px] text-gray-600">
              複ROI <span className={cn("font-bold", placeRoi >= 1 ? "text-emerald-600" : "text-gray-400")}>
                {placeRoi.toFixed(2)}
              </span>
            </span>
          )}
        </div>
      </div>
      <p className="text-[10px] text-gray-400 mb-1.5">{note}</p>
      <div className={cn("overflow-x-auto -mx-2 px-2 border-t", CATEGORY_ACCENT[category].split(" ")[2])}>
        <table className="min-w-max text-xs border-collapse whitespace-nowrap">
          <thead>
            <tr className="border-b border-gray-100 text-gray-500">
              <th className="text-left py-1 px-1.5 font-normal">レース</th>
              <th className="text-left py-1 px-1.5 font-normal">推奨馬</th>
              <th className="text-right py-1 px-1.5 font-normal w-12">単</th>
              {showPlace && <th className="text-right py-1 px-1.5 font-normal w-12">複</th>}
              {(category === "sweet_spot" || category === "place_bet") && (
                <th className="text-right py-1 px-1.5 font-normal w-12">EV</th>
              )}
              <th className="text-center py-1 px-1.5 font-normal w-16" title="複勝確率の集中度: ◎=高信頼(ヒット率76%) △=中 ▼=低(ヒット率57%)">信頼度</th>
              <th className="text-left py-1 px-1.5 font-normal w-20">結果</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((rec) => {
              const surface = rec.race.surface === "grass" ? "芝" : "ダ";
              const finishes = rec.target_horses.map((h) => h.finish_position).filter((p): p is number => p != null);
              const bestFinish = finishes.length > 0 ? Math.min(...finishes) : null;
              return (
                <tr key={rec.id} className="border-b border-gray-50 hover:bg-gray-50/50">
                  <td className="py-1.5 px-1.5 align-top">
                    <div className="text-[10px] text-gray-400 leading-tight">{formatPostTime(rec.race.post_time)}</div>
                    <Link href={`/chihou/races/${rec.race.race_id}`} className="text-blue-700 hover:underline whitespace-nowrap font-medium">
                      {rec.race.course_name}{rec.race.race_number}R
                    </Link>
                    <div className="text-[10px] text-gray-400 leading-tight">{rec.race.distance}m{surface}</div>
                  </td>
                  <td className="py-1.5 px-1.5 align-top"><HorseInline rec={rec} /></td>
                  <td className="py-1.5 px-1.5 align-top"><OddsInline rec={rec} field="win_odds" /></td>
                  {showPlace && <td className="py-1.5 px-1.5 align-top"><OddsInline rec={rec} field="place_odds" /></td>}
                  {(category === "sweet_spot" || category === "place_bet") && (
                    <td className="py-1.5 px-1.5 align-top"><EvInline rec={rec} /></td>
                  )}
                  <td className="py-1.5 px-1.5 align-middle text-center">
                    <ConcentrationBadge conc={rec.race_concentration} />
                  </td>
                  <td className="py-1.5 px-1.5 align-top whitespace-nowrap">
                    <ResultCell correct={rec.result_correct} payout={rec.result_payout} finishPos={bestFinish} betType={rec.bet_type} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function LegacyRecRow({ rec }: { rec: ChihouRecommendation }) {
  const horse = rec.target_horses[0];
  return (
    <tr className="border-b border-gray-50 hover:bg-gray-50/50">
      <td className="py-1.5 px-1.5 align-top">
        <div className="text-[10px] text-gray-400 leading-tight">{formatPostTime(rec.race.post_time)}</div>
        <Link href={`/chihou/races/${rec.race.race_id}`} className="text-blue-700 hover:underline whitespace-nowrap font-medium">
          {rec.race.course_name}{rec.race.race_number}R
        </Link>
      </td>
      <td className="py-1.5 px-1.5 align-top text-[11px]">
        <span className={cn("text-[10px] font-bold px-1 py-0.5 rounded mr-1", rec.bet_type === "win" ? "bg-red-100 text-red-700" : "bg-blue-100 text-blue-700")}>
          {rec.bet_type === "win" ? "単" : "複"}
        </span>
        {horse && (
          <>
            <span className="text-gray-400 mr-0.5">{horse.horse_number}</span>
            <span className="font-medium text-gray-800">{horse.horse_name ?? "-"}</span>
          </>
        )}
      </td>
      <td className="py-1.5 px-1.5 align-top text-[10px] whitespace-nowrap">
        {rec.odds_decision === "buy" && <span className="text-emerald-600 font-bold">◎買</span>}
        {rec.odds_decision === "pass" && <span className="text-gray-400">✕見送</span>}
      </td>
      <td className="py-1.5 px-1.5 align-top whitespace-nowrap">
        <ResultCell correct={rec.result_correct} payout={rec.result_payout} finishPos={horse?.finish_position} betType={rec.bet_type} />
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// セクション設定
// ---------------------------------------------------------------------------

const SECTIONS: Array<{
  key: ChihouRecommendCategory;
  title: string;
  titleClass: string;
  note: string;
}> = [
  {
    key: "sweet_spot",
    title: "★ 高オッズ穴狙い（スイートスポット）",
    titleClass: "text-red-600",
    note: "v10 LGB ∧ 単勝≥10 ∧ EV 1.0-2.0 ∧ ROI陽性9場 ∧ k≤2",
  },
  {
    key: "place_bet",
    title: "◆ 複穴（断然人気R × 高オッズ複勝買い）",
    titleClass: "text-blue-700",
    note: "1番人気<2.0 ∧ 単勝≥10 ∧ EV 1.2-2.0。30日実勢 hit≈22% / 複勝ROI≈0.78（控除率分マイナス・予想の参考）",
  },
  {
    key: "upset_place",
    title: "🎯 穴軸複勝（人気薄リランカー）",
    titleClass: "text-rose-700",
    note: "単勝10-15倍 × 非オッズスコア上位1/3 × 外部バッジ。検証: 確定オッズ的中37% / 発走前30-32%（複勝ROI≈0.83・的中精度特化）",
  },
  {
    key: "low_odds_trusted",
    title: "🟢 信頼できる本命（単勝<1.5）",
    titleClass: "text-emerald-700",
    note: "バックテスト的中率 約70% / 単勝ROI≈0.85（控除率分のマイナス）",
  },
  {
    key: "low_odds_untrusted",
    title: "🟡 信頼できない本命（1.5≤単勝<2.0）",
    titleClass: "text-amber-700",
    note: "バックテスト的中率 約48% / 単勝ROI≈0.81。半分は外れる帯",
  },
];

// ---------------------------------------------------------------------------
// メインクライアントコンポーネント
// ---------------------------------------------------------------------------

type Props = {
  date: string;
  initialRecList: ChihouRecommendation[];
  initialSweetList: ChihouRecommendation[];
  initialSummaries: Partial<Record<ChihouRecommendCategory, ChihouCategorySummary>>;
};

export function ChihouRecommendPanelClient({
  date,
  initialRecList,
  initialSweetList,
  initialSummaries,
}: Props) {
  const [recList, setRecList] = useState<ChihouRecommendation[]>(initialRecList);
  const [sweetList, setSweetList] = useState<ChihouRecommendation[]>(initialSweetList);
  const [summaries, setSummaries] = useState<Partial<Record<ChihouRecommendCategory, ChihouCategorySummary>>>(initialSummaries);

  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const [newRecs, newSweet] = await Promise.all([
          fetchChihouRecommendationsBrowser(date),
          fetchChihouSweetSpotRecommendationsBrowser(date),
        ]);
        setRecList(newRecs);
        setSweetList(newSweet.items);
        setSummaries(newSweet.summaries);
      } catch {
        // ネットワーク障害時は無視（次回ポーリングで回復）
      }
    }, 20_000);
    return () => clearInterval(timer);
  }, [date]);

  const byCategory: Record<ChihouRecommendCategory, ChihouRecommendation[]> = {
    sweet_spot: [],
    place_bet: [],
    upset_place: [],
    low_odds_trusted: [],
    low_odds_untrusted: [],
  };
  for (const rec of sweetList) {
    const c = (rec.category ?? "sweet_spot") as ChihouRecommendCategory;
    if (byCategory[c]) byCategory[c].push(rec);
  }

  if (recList.length === 0 && sweetList.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        <p className="text-2xl mb-2">🏇</p>
        <p className="text-sm">この日の推奨はまだ生成されていません</p>
        <p className="text-xs mt-1 text-gray-300">毎日10:00に自動生成されます</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {sweetList.length > 0 && (
        <>
          <section>
            <h3 className="text-xs font-bold text-gray-700 mb-1.5">競馬場別 当日サマリ</h3>
            <VenueSummaryTable items={sweetList} />
          </section>

          <p className="text-[10px] text-gray-400 leading-snug">
            ※ 単勝&lt;2.0 の本命は構造的に単勝ROIが1.0未満（控除率分の損失帯）。
            「儲かる買い目」ではなく「予想の参考」としてご利用ください。
          </p>

          {SECTIONS.map((s) => (
            <CategoryTable
              key={s.key}
              category={s.key}
              items={byCategory[s.key]}
              summary={summaries[s.key]}
              title={s.title}
              note={s.note}
              titleClass={s.titleClass}
            />
          ))}
        </>
      )}

      {recList.length > 0 && (
        <section>
          <h3 className="text-xs font-bold text-gray-700 mb-1">
            Claude AI 推奨
            <span className="ml-1.5 text-gray-400 font-normal">({recList.length}件)</span>
          </h3>
          <p className="text-[10px] text-gray-400 mb-1.5">
            毎日10:00に指数から自動生成。発走10分前にオッズ判断を更新。
          </p>
          <div className="overflow-x-auto -mx-2 px-2 border-t border-gray-100">
            <table className="min-w-max text-xs border-collapse whitespace-nowrap">
              <thead>
                <tr className="border-b border-gray-100 text-gray-500">
                  <th className="text-left py-1 px-1.5 font-normal">レース</th>
                  <th className="text-left py-1 px-1.5 font-normal">推奨</th>
                  <th className="text-left py-1 px-1.5 font-normal w-16">判断</th>
                  <th className="text-left py-1 px-1.5 font-normal w-20">結果</th>
                </tr>
              </thead>
              <tbody>
                {[...recList]
                  .sort((a, b) => (a.race.post_time ?? "").localeCompare(b.race.post_time ?? ""))
                  .map((rec) => <LegacyRecRow key={rec.id} rec={rec} />)}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
