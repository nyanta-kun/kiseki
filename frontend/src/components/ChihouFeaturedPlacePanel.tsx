import { fetchChihouFeaturedPlace, ChihouFeaturedPlaceHorse } from "@/lib/api";
import Link from "next/link";

function formatPostTime(t: string | null): string {
  if (!t || t.length < 4) return "-";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function formatOdds(v: number | null, suffix = "倍"): string {
  if (v === null) return "-";
  return `${v.toFixed(1)}${suffix}`;
}

/** 複勝は7頭以下だと2着まで。注目馬は8頭以上限定なので常に3着以内が的中。 */
function placeHit(pos: number | null): boolean {
  return pos !== null && pos <= 3;
}

function positionCell(pos: number | null): { text: string; cls: string } {
  if (pos === null) return { text: "-", cls: "text-gray-300" };
  if (pos === 1) return { text: "1着", cls: "text-amber-600 font-bold" };
  if (pos === 2) return { text: "2着", cls: "text-blue-600 font-bold" };
  if (pos === 3) return { text: "3着", cls: "text-blue-600 font-bold" };
  return { text: `${pos}着`, cls: "text-gray-400" };
}

export async function ChihouFeaturedPlacePanel({ date }: { date: string }) {
  let horses: ChihouFeaturedPlaceHorse[] = [];
  try {
    horses = await fetchChihouFeaturedPlace(date);
  } catch {
    return null;
  }

  const settled = horses.filter((h) => h.finish_position !== null);
  const hits = settled.filter((h) => placeHit(h.finish_position));
  const returned = hits.reduce((s, h) => s + (h.place_odds ?? 0), 0);
  const roi = settled.length > 0 ? returned / settled.length : null;
  const hasPlaceOdds = hits.every((h) => h.place_odds !== null);

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 mb-4">
      <h2 className="text-sm font-bold text-gray-700 mb-1 flex items-center gap-1.5">
        <span aria-hidden="true">★</span>
        本日の注目馬（複勝・開いたレースの穴）
      </h2>
      <p className="text-[11px] text-gray-500 mb-3 leading-relaxed">
        上位人気が抜けていないレース（市場上位3頭シェア&lt;0.63）の 単勝30〜50倍・8頭立て以上。
        <span className="text-gray-400">
          {" "}
          walk-forward 検証 複勝ROI 1.22（n=676・7ヶ月連続で1.0超）。前向き確認中のため参考情報として扱ってください。
        </span>
      </p>

      {settled.length > 0 && (
        <div className="flex items-center gap-3 text-xs mb-3 px-2.5 py-1.5 rounded-lg bg-gray-50 border border-gray-100">
          <span className="text-gray-500">本日の結果</span>
          <span className="font-semibold text-gray-700 tabular-nums">
            {hits.length}/{settled.length} 的中
          </span>
          {roi !== null && hasPlaceOdds && (
            <span
              className={`font-bold tabular-nums ${roi >= 1 ? "text-emerald-600" : "text-gray-500"}`}
            >
              複勝ROI {roi.toFixed(2)}
            </span>
          )}
        </div>
      )}

      {horses.length === 0 ? (
        <p className="text-sm text-gray-400 py-3 px-1">
          本日は条件に一致する馬がいません（毎日出るものではありません）
        </p>
      ) : (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-sm border-collapse min-w-[420px]">
            <thead>
              <tr className="text-xs text-gray-500 border-b border-gray-100">
                <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">発走</th>
                <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">競馬場</th>
                <th className="text-center py-1.5 px-1 font-medium">R</th>
                <th className="text-left py-1.5 px-1 font-medium">馬名</th>
                <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">単オッズ</th>
                <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">複オッズ</th>
                <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">着順</th>
              </tr>
            </thead>
            <tbody>
              {horses.map((h) => {
                const pos = positionCell(h.finish_position);
                return (
                  <tr
                    key={`${h.race_id}-${h.horse_number}`}
                    className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors"
                  >
                    <td className="py-2 px-1 text-gray-500 whitespace-nowrap tabular-nums">
                      {formatPostTime(h.post_time)}
                    </td>
                    <td className="py-2 px-1 font-medium text-gray-700 whitespace-nowrap">
                      {h.course_name}
                    </td>
                    <td className="py-2 px-1 text-center text-gray-500">
                      <Link
                        href={`/chihou/races/${h.race_id}`}
                        className="hover:underline"
                        style={{ color: "var(--chihou-primary)" }}
                      >
                        {h.race_number}R
                      </Link>
                    </td>
                    <td className="py-2 px-1 font-semibold text-gray-800 whitespace-nowrap">
                      <span className="text-xs text-gray-400 mr-1">{h.horse_number}番</span>
                      {h.horse_name ?? "-"}
                      <span className="ml-1 text-amber-500" aria-label="注目馬">
                        ★
                      </span>
                    </td>
                    <td className="py-2 px-1 text-right text-gray-600 tabular-nums">
                      {formatOdds(h.win_odds)}
                    </td>
                    <td className="py-2 px-1 text-right text-gray-600 tabular-nums">
                      {formatOdds(h.place_odds)}
                    </td>
                    <td className={`py-2 px-1 text-right tabular-nums ${pos.cls}`}>{pos.text}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
