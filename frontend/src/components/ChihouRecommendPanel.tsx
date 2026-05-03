import {
  ChihouCategorySummary,
  ChihouRecommendation,
  ChihouRecommendCategory,
  fetchChihouRecommendations,
  fetchChihouSweetSpotRecommendations,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import Link from "next/link";

function formatPostTime(t: string | null): string {
  if (!t || t.length < 4) return "-";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function BetTypeBadge({ betType }: { betType: string }) {
  return (
    <span
      className={cn(
        "text-[10px] font-bold px-1.5 py-0.5 rounded",
        betType === "win" ? "bg-red-100 text-red-700" : "bg-blue-100 text-blue-700"
      )}
    >
      {betType === "win" ? "単勝" : "複勝"}
    </span>
  );
}

function OddsDecisionBadge({ decision }: { decision: "buy" | "pass" | null }) {
  if (decision === null) return null;
  return (
    <span
      className={cn(
        "text-[11px] font-bold px-2 py-0.5 rounded-full",
        decision === "buy"
          ? "bg-emerald-500 text-white"
          : "bg-gray-300 text-gray-600"
      )}
    >
      {decision === "buy" ? "◎ 買い" : "✕ 見送り"}
    </span>
  );
}

function ResultBadge({ correct, payout }: { correct: boolean | null; payout: number | null }) {
  if (correct === null) return null;
  return (
    <span
      className={cn(
        "text-[11px] font-bold px-2 py-0.5 rounded-full",
        correct ? "bg-amber-400 text-white" : "bg-gray-200 text-gray-500"
      )}
    >
      {correct ? `的中 ${payout ? `${payout}円` : ""}` : "外れ"}
    </span>
  );
}

function RecommendCard({ rec }: { rec: ChihouRecommendation }) {
  const horse = rec.target_horses[0];
  const surface = rec.race.surface === "grass" ? "芝" : "ダ";

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-2">
      {/* ヘッダー */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className="text-xs font-bold text-white px-2 py-0.5 rounded-full"
          style={{ background: "var(--chihou-primary)" }}
        >
          推奨{rec.rank}
        </span>
        <BetTypeBadge betType={rec.bet_type} />
        <OddsDecisionBadge decision={rec.odds_decision} />
        <ResultBadge correct={rec.result_correct} payout={rec.result_payout} />
        <span className="ml-auto text-xs text-gray-400">
          信頼 {Math.round(rec.confidence * 100)}%
        </span>
      </div>

      {/* レース情報 */}
      <Link
        href={`/chihou/races/${rec.race.race_id}`}
        className="flex items-baseline gap-2 hover:underline"
      >
        <span className="text-sm font-semibold text-gray-800">
          {rec.race.course_name} {rec.race.race_number}R
        </span>
        <span className="text-xs text-gray-500">
          {formatPostTime(rec.race.post_time)} / {rec.race.distance}m{surface}
        </span>
        {rec.race.race_name && (
          <span className="text-xs text-gray-400 truncate max-w-[100px]">{rec.race.race_name}</span>
        )}
      </Link>

      {/* 推奨馬 */}
      {horse && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">⑤{horse.horse_number}</span>
          <span className="text-sm font-bold text-gray-900">{horse.horse_name ?? "-"}</span>
          {horse.win_probability != null && (
            <span className="text-xs text-gray-400">
              勝率 {(horse.win_probability * 100).toFixed(1)}%
            </span>
          )}
          {rec.result_correct !== null && horse.finish_position != null && (
            <span className="text-xs text-gray-500">{horse.finish_position}着</span>
          )}
        </div>
      )}

      {/* オッズスナップショット */}
      {rec.snapshot_win_odds && horse && (
        <div className="text-xs text-gray-500">
          {rec.bet_type === "win" && rec.snapshot_win_odds[String(horse.horse_number)] && (
            <span>単勝 {rec.snapshot_win_odds[String(horse.horse_number)].toFixed(1)}倍</span>
          )}
          {rec.bet_type === "place" && rec.snapshot_place_odds?.[String(horse.horse_number)] && (
            <span>複勝 {rec.snapshot_place_odds[String(horse.horse_number)].toFixed(1)}倍</span>
          )}
          {rec.odds_decision_reason && (
            <span className="ml-2 text-gray-400">{rec.odds_decision_reason}</span>
          )}
        </div>
      )}

      {/* 推奨理由 */}
      <p className="text-xs text-gray-600 leading-relaxed border-t border-gray-50 pt-2">
        {rec.reason}
      </p>
    </div>
  );
}

type SweetSpotTheme = {
  border: string;
  badgeBg: string;
  badgeLabel: string;
  oddsBg: string;
  evBg: string;
};

const SWEET_SPOT_THEME: Record<ChihouRecommendCategory, SweetSpotTheme> = {
  sweet_spot: {
    border: "border-red-100",
    badgeBg: "bg-red-600",
    badgeLabel: "★ SS",
    oddsBg: "bg-orange-50 text-orange-700 border-orange-200",
    evBg: "bg-red-50 text-red-600 border-red-200",
  },
  low_odds_trusted: {
    border: "border-emerald-100",
    badgeBg: "bg-emerald-600",
    badgeLabel: "🟢 信頼",
    oddsBg: "bg-emerald-50 text-emerald-700 border-emerald-200",
    evBg: "bg-emerald-50 text-emerald-700 border-emerald-200",
  },
  low_odds_untrusted: {
    border: "border-amber-100",
    badgeBg: "bg-amber-500",
    badgeLabel: "🟡 不信頼",
    oddsBg: "bg-amber-50 text-amber-700 border-amber-200",
    evBg: "bg-amber-50 text-amber-700 border-amber-200",
  },
};

function SweetSpotCard({ rec }: { rec: ChihouRecommendation }) {
  const surface = rec.race.surface === "grass" ? "芝" : "ダ";
  const category: ChihouRecommendCategory = rec.category ?? "sweet_spot";
  const theme = SWEET_SPOT_THEME[category];
  return (
    <div className={cn("bg-white rounded-xl border shadow-sm p-4 space-y-2", theme.border)}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={cn("text-xs font-bold text-white px-2 py-0.5 rounded-full", theme.badgeBg)}>
          {theme.badgeLabel}#{rec.rank}
        </span>
        <BetTypeBadge betType={rec.bet_type} />
        <ResultBadge correct={rec.result_correct} payout={rec.result_payout} />
        <span className="ml-auto text-xs text-gray-400">
          {category === "sweet_spot"
            ? `EV最大 ≈ ${Math.round(rec.confidence * 100)}%信頼`
            : `想定的中率 ${Math.round(rec.confidence * 100)}%`}
        </span>
      </div>
      <Link href={`/chihou/races/${rec.race.race_id}`} className="flex items-baseline gap-2 hover:underline">
        <span className="text-sm font-semibold text-gray-800">
          {rec.race.course_name} {rec.race.race_number}R
        </span>
        <span className="text-xs text-gray-500">
          {formatPostTime(rec.race.post_time)} / {rec.race.distance}m{surface}
        </span>
      </Link>
      <div className="space-y-1">
        {rec.target_horses.map((h) => (
          <div key={h.horse_number} className="flex items-center gap-2 text-sm">
            <span className="text-xs text-gray-500">⑤{h.horse_number}</span>
            <span className="font-bold text-gray-900">{h.horse_name ?? "-"}</span>
            {h.win_odds != null && (
              <span className={cn("text-xs border px-1.5 py-0.5 rounded", theme.oddsBg)}>
                単勝 {h.win_odds.toFixed(1)}倍
              </span>
            )}
            {category === "sweet_spot" && h.ev != null && (
              <span className={cn("text-xs border px-1.5 py-0.5 rounded font-bold", theme.evBg)}>
                EV {h.ev.toFixed(2)}
              </span>
            )}
            {category !== "sweet_spot" && h.win_probability != null && (
              <span className={cn("text-xs border px-1.5 py-0.5 rounded", theme.evBg)}>
                v10勝率 {(h.win_probability * 100).toFixed(0)}%
              </span>
            )}
            {h.finish_position != null && (
              <span className="text-xs text-gray-400">{h.finish_position}着</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function CategorySummaryStrip({
  summary,
  category,
}: {
  summary: ChihouCategorySummary | undefined;
  category: ChihouRecommendCategory;
}) {
  if (!summary || summary.n_total === 0) return null;
  const isPending = summary.n_settled === 0;
  const hitText =
    summary.hit_rate != null ? `${(summary.hit_rate * 100).toFixed(1)}%` : "—";
  const roiText = summary.win_roi != null ? summary.win_roi.toFixed(2) : "—";
  const accent =
    category === "sweet_spot"
      ? "text-red-700 bg-red-50 border-red-100"
      : category === "low_odds_trusted"
        ? "text-emerald-700 bg-emerald-50 border-emerald-100"
        : "text-amber-700 bg-amber-50 border-amber-100";
  return (
    <div className={cn("text-[11px] border rounded-md px-2 py-1 flex items-center gap-2", accent)}>
      <span className="font-bold">本日合計</span>
      {isPending ? (
        <span>確定 0/{summary.n_total} 件（発走後に集計）</span>
      ) : (
        <>
          <span>
            的中 {summary.n_hits}/{summary.n_settled} ({hitText})
          </span>
          <span className="text-gray-400">|</span>
          <span>単勝ROI {roiText}</span>
          {summary.n_settled < summary.n_total && (
            <span className="text-gray-400">/ 残{summary.n_total - summary.n_settled}件</span>
          )}
        </>
      )}
    </div>
  );
}

export async function ChihouRecommendPanel({ date }: { date: string }) {
  const [recs, sweetSpots] = await Promise.allSettled([
    fetchChihouRecommendations(date),
    fetchChihouSweetSpotRecommendations(date),
  ]);

  const recList: ChihouRecommendation[] = recs.status === "fulfilled" ? recs.value : [];
  const sweetResp =
    sweetSpots.status === "fulfilled" ? sweetSpots.value : { items: [], summaries: {} };
  const sweetList = sweetResp.items;
  const summaries = sweetResp.summaries;

  const byCategory: Record<ChihouRecommendCategory, ChihouRecommendation[]> = {
    sweet_spot: [],
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

  const categorySections: Array<{
    key: ChihouRecommendCategory;
    title: string;
    titleClass: string;
    note: string;
    items: ChihouRecommendation[];
  }> = [
    {
      key: "sweet_spot",
      title: "★ 高オッズ穴狙い（スイートスポット）",
      titleClass: "text-red-600",
      note: "v10 LGB ∧ 単勝≥10 ∧ EV 1.0-2.0 ∧ ROI陽性9場 ∧ k≤2",
      items: byCategory.sweet_spot,
    },
    {
      key: "low_odds_trusted",
      title: "🟢 信頼できる本命（単勝<1.5）",
      titleClass: "text-emerald-700",
      note: "バックテスト的中率 約70% / 単勝ROI≈0.85（控除率分のマイナス）",
      items: byCategory.low_odds_trusted,
    },
    {
      key: "low_odds_untrusted",
      title: "🟡 信頼できない本命（1.5≤単勝<2.0）",
      titleClass: "text-amber-700",
      note: "バックテスト的中率 約48% / 単勝ROI≈0.81。半分は外れる帯",
      items: byCategory.low_odds_untrusted,
    },
  ];

  return (
    <div className="space-y-4">
      {sweetList.length > 0 && (
        <section>
          <p className="text-[10px] text-gray-400 leading-snug mb-2">
            ※ 単勝&lt;2.0 の本命は構造的に単勝ROIが1.0未満（控除率分の損失帯）。
            「儲かる買い目」ではなく「予想の参考」としてご利用ください。
          </p>
          <div className="space-y-4">
            {categorySections.map((section) =>
              section.items.length === 0 ? null : (
                <div key={section.key}>
                  <h3
                    className={cn(
                      "text-xs font-bold mb-1 flex items-center gap-1",
                      section.titleClass,
                    )}
                  >
                    {section.title}
                    <span className="font-normal text-gray-400">（{section.note}）</span>
                  </h3>
                  <div className="mb-2">
                    <CategorySummaryStrip
                      summary={summaries[section.key]}
                      category={section.key}
                    />
                  </div>
                  <div className="space-y-2">
                    {section.items.map((rec) => (
                      <SweetSpotCard key={`ss-${rec.id}`} rec={rec} />
                    ))}
                  </div>
                </div>
              ),
            )}
          </div>
        </section>
      )}

      {recList.length > 0 && (
        <section>
          <h3 className="text-xs font-bold text-gray-500 mb-2">Claude AI 推奨</h3>
          <p className="text-xs text-gray-400 text-right mb-2">
            ※ 毎日10:00に指数から自動生成。発走10分前にオッズ判断を更新。
          </p>
          <div className="space-y-3">
            {recList.map((rec) => (
              <RecommendCard key={rec.id} rec={rec} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
