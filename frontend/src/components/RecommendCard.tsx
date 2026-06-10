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
  trifecta: "3連複",
};

/** Tier バッジのスタイル（的中重視: S 鉄板 / A 信頼 / B 複勝圏） */
const TIER_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  S: { bg: "bg-red-500", text: "text-white", label: "S 鉄板" },
  A: { bg: "bg-orange-500", text: "text-white", label: "A 信頼" },
  B: { bg: "bg-sky-500", text: "text-white", label: "B 複勝圏" },
  穴: { bg: "bg-rose-500", text: "text-white", label: "穴軸" },
  // 以下は旧 sweet_spot/3連複（現在は降格・通常は出力されない）
  SS: { bg: "bg-red-600", text: "text-white", label: "SS" },
  "3F-2軸": { bg: "bg-purple-500", text: "text-white", label: "3F-2軸" },
  "3F-BOX": { bg: "bg-purple-100", text: "text-purple-700", label: "3F-BOX" },
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
  const { race, target_horses, bet_type, tier, ticket_combos, points, roi_basis, is_verified, value_candidates, reason, confidence, snapshot_at } = rec;
  const betLabel = BET_LABEL[bet_type] ?? bet_type;
  const postTime = fmtTime(race.post_time);
  const tierStyle = tier ? TIER_STYLE[tier] : null;
  const isTrifecta = bet_type === "trifecta";
  const snapshotTime = snapshot_at
    ? new Date(snapshot_at).toLocaleTimeString("ja-JP", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit" })
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
          <div className="flex items-center gap-2 mb-1.5 flex-wrap">
            <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wider">推奨馬券</span>
            {/* Tier バッジ */}
            {tierStyle && (
              <span className={cn("px-2 py-0.5 rounded-full text-xs font-bold", tierStyle.bg, tierStyle.text)}>
                {tierStyle.label}
              </span>
            )}
            <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-blue-100 text-blue-700">
              {betLabel}
            </span>
            {/* 点数・ROI */}
            {points != null && (
              <span className="text-[11px] text-gray-500">{points}点</span>
            )}
            {roi_basis != null && (
              <span className="text-[11px] font-bold text-emerald-600">ROI実証{roi_basis.toFixed(2)}</span>
            )}
            {is_verified === false && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-bold">仮説</span>
            )}
            {snapshotTime && (
              <span className="text-[10px] text-gray-400 ml-auto">{snapshotTime}時点</span>
            )}
          </div>

          {/* 3連複: 組み合わせ一覧 */}
          {isTrifecta && ticket_combos && ticket_combos.length > 0 && (
            <div className="mb-2">
              <div className="flex flex-wrap gap-1">
                {ticket_combos.map((combo, i) => {
                  const nums = [...combo].sort((a, b) => a - b);
                  const comboStr = nums.join("-");
                  return (
                    <span
                      key={i}
                      className="px-2 py-0.5 rounded-full text-xs font-bold bg-purple-100 text-purple-700 border border-purple-200"
                    >
                      {comboStr}
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          {/* 対象馬テーブル */}
          <div className="overflow-x-auto">
            <table className="min-w-max text-xs whitespace-nowrap">
              <thead>
                <tr className="text-gray-400 border-b border-gray-100">
                  <th className="text-left py-1 pr-2 font-medium w-6">番</th>
                  <th className="text-left py-1 pr-3 font-medium">馬名</th>
                  <th className="text-right py-1 pr-2 font-medium">指数</th>
                  <th className="text-right py-1 pr-2 font-medium">勝率</th>
                  <th className="text-right py-1 pr-2 font-medium">複率</th>
                  <th className="text-right py-1 pr-2 font-medium">単勝</th>
                  {!isTrifecta && <th className="text-right py-1 pr-2 font-medium">複勝</th>}
                  <th className="text-right py-1 pr-2 font-medium">単EV</th>
                  {!isTrifecta && <th className="text-right py-1 pr-2 font-medium">複EV</th>}
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
                    {!isTrifecta && <td className="py-1.5 pr-2 text-right text-gray-600">{fmtOdds(h.place_odds)}</td>}
                    <td className={cn("py-1.5 pr-2 text-right", evColor(h.ev_win))}>{fmtEV(h.ev_win)}</td>
                    {!isTrifecta && <td className={cn("py-1.5 pr-2 text-right", evColor(h.ev_place))}>{fmtEV(h.ev_place)}</td>}
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

        {/* 妙味候補（穴・収支保証なし） */}
        {value_candidates && value_candidates.length > 0 && (
          <div className="bg-amber-50 rounded-lg px-3 py-2 border border-amber-100">
            <p className="text-[11px] font-bold text-amber-600 mb-1">
              妙味候補<span className="font-normal text-amber-500">（穴・収支保証なし）</span>
            </p>
            <div className="space-y-1">
              {value_candidates.map((v) => (
                <div key={v.horse_number} className="flex items-center gap-1.5 flex-wrap text-xs">
                  <span className="font-bold text-gray-700">{v.horse_number}</span>
                  <span className="text-gray-800">{v.horse_name ?? "—"}</span>
                  <span className="text-gray-500">{fmtOdds(v.win_odds)}</span>
                  {v.index_rank != null && (
                    <span className="text-[10px] text-gray-400">指数{v.index_rank}位</span>
                  )}
                  {v.badges.map((b, i) => (
                    <span
                      key={i}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium"
                    >
                      {b}
                    </span>
                  ))}
                  {v.is_place_axis && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-100 text-rose-700 font-bold">
                      🎯複勝＋ワイド軸{v.upset_tier === "strong" && "★強"}
                      {v.wide_partner_horse_number != null && `(相手${v.wide_partner_horse_number}番=本命)`}
                    </span>
                  )}
                  {v.is_place_axis && v.finish_position != null && (
                    <span
                      className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded font-medium",
                        v.finish_position <= 3
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-gray-100 text-gray-500"
                      )}
                    >
                      {v.finish_position <= 3 ? `複勝圏${v.finish_position}着` : `${v.finish_position}着`}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

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
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className={cn(
                  "text-xs font-bold px-2 py-0.5 rounded-full shrink-0",
                  resultCorrect ? "bg-emerald-500 text-white" : "bg-gray-400 text-white"
                )}
              >
                {resultCorrect ? "的中" : "不的中"}
              </span>
              <span className="text-[11px] font-bold text-gray-400 shrink-0">レース結果</span>
              {target_horses.map((h) => (
                <span key={h.horse_number} className={cn("text-xs px-1.5 py-0.5 rounded font-bold", posColor(h.finish_position))}>
                  {fmtPos(h.finish_position)}
                </span>
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
