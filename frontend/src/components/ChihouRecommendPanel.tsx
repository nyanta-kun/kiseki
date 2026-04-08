import { ChihouRecommendation, fetchChihouRecommendations } from "@/lib/api";
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

export async function ChihouRecommendPanel({ date }: { date: string }) {
  let recs: ChihouRecommendation[] = [];
  try {
    recs = await fetchChihouRecommendations(date);
  } catch {
    return (
      <div className="text-center py-8 text-gray-400 text-sm">
        推奨データを取得できませんでした
      </div>
    );
  }

  if (recs.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        <p className="text-2xl mb-2">🏇</p>
        <p className="text-sm">この日の推奨はまだ生成されていません</p>
        <p className="text-xs mt-1 text-gray-300">毎日10:00に自動生成されます</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400 text-right">
        ※ 毎日10:00に指数から自動生成。発走10分前にオッズ判断を更新。
      </p>
      {recs.map((rec) => (
        <RecommendCard key={rec.id} rec={rec} />
      ))}
    </div>
  );
}
