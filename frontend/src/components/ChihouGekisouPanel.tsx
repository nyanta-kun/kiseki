/**
 * 地方 推奨タブ: 当日の「激走」馬一覧（1レース最大1頭）。
 *
 * 2026-09-28 に注目馬（★・`ChihouFeaturedPlacePanel`）から置き換えた。
 * 判定の正本は backend `indices/chihou_gekisou.py`、一覧は `GET /api/chihou/races/gekisou`。
 */
import Link from "next/link";
import { fetchChihouGekisou, type ChihouGekisouDay } from "@/lib/api";
import { ChihouGekisouBadge, GEKISOU_DESC } from "./ChihouGekisouBadge";

function formatPostTime(t: string | null): string {
  if (!t || t.length < 4) return "-";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function formatOdds(v: number | null): string {
  return v === null ? "-" : `${v.toFixed(1)}倍`;
}

function positionCell(pos: number | null): { text: string; cls: string } {
  if (pos === null) return { text: "-", cls: "text-gray-300" };
  if (pos === 1) return { text: "1着", cls: "text-amber-600 font-bold" };
  if (pos <= 3) return { text: `${pos}着`, cls: "text-blue-600 font-bold" };
  return { text: `${pos}着`, cls: "text-gray-400" };
}

export async function ChihouGekisouPanel({ date }: { date: string }) {
  let day: ChihouGekisouDay;
  try {
    day = await fetchChihouGekisou(date);
  } catch {
    return null;
  }
  const { picks } = day;

  // 激走は8頭立て以上のみ（複勝が3着まで）なので 3着以内が的中
  const settled = picks.filter((p) => p.finish_position !== null);
  const hits = settled.filter((p) => (p.finish_position ?? 99) <= 3);
  const returned = settled.reduce((s, p) => s + (p.place_payout ?? 0), 0);

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 mb-4">
      <h2 className="text-sm font-bold text-gray-700 mb-1 flex items-center gap-1.5">
        <ChihouGekisouBadge status="gekisou" size="sm" />
        本日の激走馬（複勝）
      </h2>
      <p className="text-[11px] text-gray-500 mb-3 leading-relaxed">
        {GEKISOU_DESC}。人気馬の好走見込み（指数とオッズの平均）から複勝圏の「空き枠」を出し、
        6番人気以下の最有力馬が人気1〜3番の誰かを指数で上回るときだけ印を付けます。
        <span className="text-gray-400">
          {" "}
          前向き確認（2026-08-14〜09-27・541点）で複勝的中 <strong>23.8%</strong>
          （人気薄全体 12.0% の約2倍）。
          <strong>当たりやすさの印で、収支は保証しません</strong>（複勝回収率 0.79）。
        </span>
      </p>

      <div className="flex items-center gap-3 text-xs mb-3 px-2.5 py-1.5 rounded-lg bg-gray-50 border border-gray-100 flex-wrap">
        <span className="text-gray-500 tabular-nums">
          判定 {day.n_judged}R ／ 激走 {day.n_gekisou}R ／ 見送り {day.n_miokuri}R
        </span>
        {settled.length > 0 && (
          <>
            <span className="font-semibold text-gray-700 tabular-nums">
              複勝圏 {hits.length}/{settled.length}
            </span>
            <span className="text-gray-400 tabular-nums">
              回収率 {Math.round((returned / settled.length) * 100)}%
            </span>
          </>
        )}
      </div>

      {picks.length === 0 ? (
        <p className="text-sm text-gray-400 py-3 px-1">
          本日は激走の条件に一致する馬がいません（毎レース出るものではありません）
        </p>
      ) : (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-sm border-collapse min-w-[480px]">
            <thead>
              <tr className="text-xs text-gray-500 border-b border-gray-100">
                <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">発走</th>
                <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">競馬場</th>
                <th className="text-center py-1.5 px-1 font-medium">R</th>
                <th className="text-left py-1.5 px-1 font-medium">馬名</th>
                <th className="text-center py-1.5 px-1 font-medium whitespace-nowrap">人気</th>
                <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">単オッズ</th>
                <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">複オッズ</th>
                <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">着順</th>
              </tr>
            </thead>
            <tbody>
              {picks.map((p) => {
                const pos = positionCell(p.finish_position);
                return (
                  <tr
                    key={`${p.race_id}-${p.horse_number}`}
                    className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors"
                  >
                    <td className="py-2 px-1 text-gray-500 whitespace-nowrap tabular-nums">
                      {formatPostTime(p.post_time)}
                    </td>
                    <td className="py-2 px-1 font-medium text-gray-700 whitespace-nowrap">
                      {p.course_name}
                    </td>
                    <td className="py-2 px-1 text-center text-gray-500">
                      <Link
                        href={`/chihou/races/${p.race_id}`}
                        className="hover:underline"
                        style={{ color: "var(--chihou-primary)" }}
                      >
                        {p.race_number}R
                      </Link>
                    </td>
                    <td className="py-2 px-1 font-semibold text-gray-800 whitespace-nowrap">
                      <span className="text-xs text-gray-400 mr-1">{p.horse_number}番</span>
                      {p.horse_name ?? "-"}
                      <span className="ml-1">
                        <ChihouGekisouBadge status="gekisou" provisional={p.source === "live"} />
                      </span>
                    </td>
                    <td className="py-2 px-1 text-center tabular-nums">
                      <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                        {p.popularity ?? "-"}番人気
                      </span>
                    </td>
                    <td className="py-2 px-1 text-right text-gray-600 tabular-nums">
                      {formatOdds(p.win_odds)}
                    </td>
                    <td className="py-2 px-1 text-right text-gray-600 tabular-nums">
                      {formatOdds(p.place_odds)}
                    </td>
                    <td className={`py-2 px-1 text-right tabular-nums ${pos.cls}`}>{pos.text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-[10px] text-gray-400 mt-2">
            点線の「候補」は発走前の最新オッズでの暫定。発走約6分前の記録で確定し、以後は変わりません
          </p>
        </div>
      )}
    </div>
  );
}
