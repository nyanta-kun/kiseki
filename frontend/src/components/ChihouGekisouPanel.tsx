/**
 * 地方 推奨タブ: 当日の「激走」馬一覧（1レース最大1頭）。
 *
 * 2026-09-28 に注目馬（★・`ChihouFeaturedPlacePanel`）から置き換えた。
 * 判定の正本は backend `indices/chihou_gekisou.py`、一覧は `GET /api/chihou/races/gekisou`。
 */
import { fetchChihouGekisou, type ChihouGekisouDay } from "@/lib/api";
import { ChihouGekisouBadge, GEKISOU_DESC } from "./ChihouGekisouBadge";
import { ChihouGekisouTable } from "./ChihouGekisouTable";

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
  // 確率から見込まれる的中数。実際の的中数と並べて、確率が当たっているかを毎日見られるようにする
  const expected = settled.reduce((s, p) => s + (p.place_prob ?? 0), 0);

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
            <span className="text-gray-400 tabular-nums">見込み {expected.toFixed(1)}頭</span>
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
        <>
          <ChihouGekisouTable picks={picks} />
          <p className="text-[10px] text-gray-400 mt-2 leading-relaxed">
            確率は複勝圏（3着以内）に入る見込み。オッズから較正した値で、前向き記録では
            予測 25% に対し実際 24%。<strong>確率が高いほど回収率が高いわけではありません</strong>。
            点線の「候補」は発走前の最新オッズでの暫定で、発走約6分前の記録で確定し以後は変わりません
          </p>
        </>
      )}
    </div>
  );
}
