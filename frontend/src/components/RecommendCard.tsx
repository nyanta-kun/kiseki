import Link from "next/link";
import { Recommendation } from "@/lib/api";
import { cn, surfaceIcon } from "@/lib/utils";

type Props = {
  rec: Recommendation;
};

const BET_LABEL: Record<string, string> = {
  win: "単勝",
  place: "複勝",
  quinella: "馬連",
};

/** "1025" → "10:25" */
function fmtTime(t: string | null): string {
  if (!t || t.length !== 4) return "";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function fmtOdds(v: number | null | undefined): string {
  return v != null ? `${v.toFixed(1)}倍` : "—";
}

function fmtEV(v: number | null | undefined): string {
  return v != null ? v.toFixed(2) : "—";
}

function fmtPct(v: number | null | undefined): string {
  return v != null ? `${Math.round(v * 100)}%` : "—";
}

function fmtPos(p: number | null | undefined): string {
  if (p == null) return "—";
  return `${p}着`;
}

/** EV値に応じた色クラス */
function evColor(ev: number | null | undefined): string {
  if (ev == null) return "text-gray-400";
  if (ev >= 1.2) return "text-emerald-600 font-bold";
  if (ev >= 1.0) return "text-amber-600";
  return "text-gray-500";
}

/** 着順バッジの色 */
function posColor(p: number | null | undefined): string {
  if (p == null) return "bg-gray-100 text-gray-400";
  if (p === 1) return "bg-amber-100 text-amber-700 font-bold";
  if (p <= 3) return "bg-blue-100 text-blue-700 font-bold";
  return "bg-gray-100 text-gray-500";
}

export function RecommendCard({ rec }: Props) {
  const { race, target_horses, bet_type, reason, confidence, snapshot_at } = rec;
  const betLabel = BET_LABEL[bet_type] ?? bet_type;
  const postTime = fmtTime(race.post_time);
  const snapshotTime = snapshot_at
    ? new Date(snapshot_at).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })
    : null;

  const hasResult = rec.result_correct !== null && rec.result_correct !== undefined;
  const resultCorrect = rec.result_correct;
  const resultPayout = rec.result_payout;
  // 着順が入っているか（結果更新済みか）
  const hasFinish = target_horses.some(
    (h) => h.finish_position !== null && h.finish_position !== undefined
  );

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      {/* ヘッダー */}
      <div
        className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-100"
        style={{ background: "var(--primary)" }}
      >
        <span className="text-white/80 text-xs font-medium">推奨{rec.rank}</span>
        <Link
          href={`/races/${race.race_id}`}
          className="flex-1 flex items-center gap-1.5 text-white hover:text-white/80 transition-colors"
        >
          <span className="font-bold text-sm">{race.course_name} {race.race_number}R</span>
          {race.race_name && (
            <span className="text-white/80 text-xs truncate">{race.race_name}</span>
          )}
          {race.grade && (
            <span className="ml-auto text-xs bg-white/20 text-white px-1.5 py-0.5 rounded-full font-bold shrink-0">
              {race.grade}
            </span>
          )}
        </Link>
        <div className="flex items-center gap-1.5 shrink-0">
          {postTime && <span className="text-white/70 text-xs">{postTime}発走</span>}
          {race.surface && <span className="text-white/70 text-xs">{surfaceIcon(race.surface)}</span>}
          {race.distance && <span className="text-white/70 text-xs">{race.distance}m</span>}
        </div>
      </div>

      <div className="px-4 py-3 space-y-3">
        {/* 推奨馬テーブル */}
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wider">推奨馬券</span>
            <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-blue-100 text-blue-700">
              {betLabel}
            </span>
            {snapshotTime && (
              <span className="text-[10px] text-gray-400 ml-auto">{snapshotTime}時点オッズ</span>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-100">
                  <th className="text-left py-1 pr-2 font-medium w-6">番</th>
                  <th className="text-left py-1 pr-3 font-medium">馬名</th>
                  <th className="text-right py-1 pr-2 font-medium">指数</th>
                  <th className="text-right py-1 pr-2 font-medium">勝率</th>
                  <th className="text-right py-1 pr-2 font-medium">複率</th>
                  <th className="text-right py-1 pr-2 font-medium">単勝</th>
                  <th className="text-right py-1 pr-2 font-medium">複勝</th>
                  <th className="text-right py-1 pr-2 font-medium">単EV</th>
                  <th className="text-right py-1 pr-2 font-medium">複EV</th>
                  {hasFinish && <th className="text-right py-1 font-medium">着順</th>}
                </tr>
              </thead>
              <tbody>
                {target_horses.map((h) => (
                  <tr key={h.horse_number} className="border-b border-gray-50 last:border-0">
                    <td className="py-1.5 pr-2 font-bold text-gray-700">{h.horse_number}</td>
                    <td className="py-1.5 pr-3 text-gray-800 font-medium whitespace-nowrap">
                      {h.horse_name ?? "—"}
                    </td>
                    <td className="py-1.5 pr-2 text-right text-gray-700">
                      {h.composite_index?.toFixed(1) ?? "—"}
                    </td>
                    <td className="py-1.5 pr-2 text-right text-gray-600">{fmtPct(h.win_probability)}</td>
                    <td className="py-1.5 pr-2 text-right text-gray-600">{fmtPct(h.place_probability)}</td>
                    <td className="py-1.5 pr-2 text-right text-gray-600">{fmtOdds(h.win_odds)}</td>
                    <td className="py-1.5 pr-2 text-right text-gray-600">{fmtOdds(h.place_odds)}</td>
                    <td className={cn("py-1.5 pr-2 text-right", evColor(h.ev_win))}>{fmtEV(h.ev_win)}</td>
                    <td className={cn("py-1.5 pr-2 text-right", evColor(h.ev_place))}>{fmtEV(h.ev_place)}</td>
                    {hasFinish && (
                      <td className="py-1.5 text-right">
                        <span className={cn("px-1.5 py-0.5 rounded text-[11px]", posColor(h.finish_position))}>
                          {fmtPos(h.finish_position)}
                        </span>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* 信頼スコア */}
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-400 font-medium">信頼スコア</span>
          <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full",
                confidence >= 0.7 ? "bg-emerald-500" : confidence >= 0.5 ? "bg-amber-400" : "bg-gray-400"
              )}
              style={{ width: `${Math.round(confidence * 100)}%` }}
            />
          </div>
          <span
            className={cn(
              "text-xs font-bold",
              confidence >= 0.7 ? "text-emerald-600" : confidence >= 0.5 ? "text-amber-600" : "text-gray-500"
            )}
          >
            {Math.round(confidence * 100)}%
          </span>
        </div>

        {/* 推奨理由 */}
        <div className="bg-blue-50 rounded-lg px-3 py-2">
          <p className="text-[11px] font-bold text-blue-600 mb-0.5">推奨理由</p>
          <p className="text-xs text-gray-700 leading-relaxed">{reason}</p>
        </div>

        {/* 結果カード（レース後） */}
        {hasResult && (
          <div
            className={cn(
              "rounded-lg px-3 py-2.5",
              resultCorrect
                ? "bg-emerald-50 border border-emerald-200"
                : "bg-gray-50 border border-gray-200"
            )}
          >
            <div className="flex items-center gap-2 mb-1">
              <span
                className={cn(
                  "text-xs font-bold px-2 py-0.5 rounded-full",
                  resultCorrect ? "bg-emerald-500 text-white" : "bg-gray-400 text-white"
                )}
              >
                {resultCorrect ? "的中" : "不的中"}
              </span>
              <span className="text-[11px] font-bold text-gray-400">レース結果</span>
            </div>

            {/* 推奨馬の着順一覧 */}
            <div className="flex flex-wrap gap-2 mt-1.5">
              {target_horses.map((h) => (
                <div key={h.horse_number} className="flex items-center gap-1">
                  <span className="text-xs text-gray-500">{h.horse_number}番 {h.horse_name}</span>
                  <span className={cn("text-xs px-1.5 py-0.5 rounded font-bold", posColor(h.finish_position))}>
                    {fmtPos(h.finish_position)}
                  </span>
                </div>
              ))}
            </div>

            {/* 払戻 */}
            {resultCorrect && resultPayout != null && (
              <div className="mt-1.5 flex items-baseline gap-2">
                <span className="text-[11px] text-gray-500">払戻</span>
                <span className="text-sm font-bold text-emerald-700">{resultPayout}円</span>
                <span className="text-[11px] text-gray-400">（100円購入あたり）</span>
                <span
                  className={cn(
                    "text-xs font-bold",
                    resultPayout >= 100 ? "text-emerald-600" : "text-red-500"
                  )}
                >
                  {resultPayout >= 100 ? `+${resultPayout - 100}円` : `${resultPayout - 100}円`}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
