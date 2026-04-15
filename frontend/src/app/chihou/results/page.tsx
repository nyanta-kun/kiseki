import type { Metadata } from "next";
import { fetchChihouPerformanceSummary } from "@/lib/api";
import type { ChihouPerformanceFilters } from "@/lib/api";
import { ChihouFilterForm } from "./ChihouFilterForm";
import { ChihouBuyingGuide } from "./BuyingGuide";

export const metadata: Metadata = {
  title: "地方競馬 実績 | GallopLab",
  description: "地方競馬 AI指数の予測精度・的中率・回収率シミュレーション実績",
};

// ---------------------------------------------------------------------------
// サブコンポーネント
// ---------------------------------------------------------------------------

function MetricCard({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: string;
  sub?: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-xl p-4 border shadow-sm ${
        highlight ? "border-green-200 bg-green-50" : "border-gray-100 bg-white"
      }`}
    >
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-2xl font-bold ${highlight ? "text-green-700" : "text-gray-800"}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ページ本体
// ---------------------------------------------------------------------------

type RawSearchParams = Record<string, string | string[] | undefined>;
type SearchParams = Promise<RawSearchParams>;

function toArray(v: string | string[] | undefined): string[] | undefined {
  if (!v) return undefined;
  const str = Array.isArray(v) ? v.join(",") : v;
  const arr = str.split(",").map((s) => s.trim()).filter(Boolean);
  return arr.length > 0 ? arr : undefined;
}

export default async function ChihouResultsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const sp = await searchParams;

  const _today = new Date();
  const _defaultFrom = `${_today.getFullYear()}${String(_today.getMonth() + 1).padStart(2, "0")}01`;
  const _defaultTo   = `${_today.getFullYear()}${String(_today.getMonth() + 1).padStart(2, "0")}${String(_today.getDate()).padStart(2, "0")}`;

  const filters: ChihouPerformanceFilters = {
    from_date:   typeof sp.from_date === "string" ? sp.from_date : _defaultFrom,
    to_date:     typeof sp.to_date === "string" ? sp.to_date : _defaultTo,
    course_name: toArray(sp.course_name),
    surface:     toArray(sp.surface),
  };

  let data;
  try {
    data = await fetchChihouPerformanceSummary(filters);
  } catch {
    return (
      <div className="min-h-screen" style={{ background: "#f0f8f3" }}>
        <main className="max-w-3xl mx-auto px-4 py-8">
          <ChihouFilterForm current={filters} />
          <div className="mt-6 text-center py-12 bg-white rounded-xl border border-gray-100 shadow-sm text-gray-400">
            <p className="text-4xl mb-2">📊</p>
            <p>実績データを取得できませんでした</p>
            <p className="text-xs mt-1">バックエンドが起動しているか確認してください</p>
          </div>
        </main>
      </div>
    );
  }

  const roiDiff = ((data.simulated_roi_win - 1.0) * 100).toFixed(1);
  const roiSign = data.simulated_roi_win >= 1.0 ? "+" : "";
  const placeRoiDiff = ((data.simulated_roi_place - 1.0) * 100).toFixed(1);
  const placeRoiSign = data.simulated_roi_place >= 1.0 ? "+" : "";

  return (
    <div className="min-h-screen" style={{ background: "#f0f8f3" }}>
      <main className="max-w-3xl mx-auto px-4 py-6 space-y-4">

        {/* タイトル */}
        <div>
          <h1 className="text-lg font-bold text-gray-800">地方競馬 AI指数 実績</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            予測1位（composite_index最高）vs 実際着順
          </p>
        </div>

        {/* フィルタ */}
        <ChihouFilterForm current={filters} key={JSON.stringify(filters)} />

        {data.total_races === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl border border-gray-100 shadow-sm text-gray-400">
            <p className="text-3xl mb-2">📊</p>
            <p className="text-sm">該当レースがありません</p>
            <p className="text-xs mt-1">フィルタ条件を変更するか、指数算出・成績取得が完了しているか確認してください</p>
          </div>
        ) : (
          <>
            {/* サマリーカード */}
            <section aria-label="全体成績サマリー">
              <p className="text-xs text-gray-400 mb-2">
                全体 — {data.total_races.toLocaleString()} レース
              </p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <MetricCard
                  label="単勝的中率"
                  value={`${(data.win_hit_rate * 100).toFixed(1)}%`}
                  sub="予測1位が1着"
                  highlight
                />
                <MetricCard
                  label="複勝的中率"
                  value={`${(data.place_hit_rate * 100).toFixed(1)}%`}
                  sub="予測1位が3着以内"
                />
                <MetricCard
                  label="top3カバー率"
                  value={`${(data.top3_coverage_rate * 100).toFixed(1)}%`}
                  sub="3着以内が予測top3に含まれる割合"
                />
                <MetricCard
                  label="単勝ROI"
                  value={`${(data.simulated_roi_win * 100).toFixed(1)}%`}
                  sub={`損益 ${roiSign}${roiDiff}% | 毎回同額賭け`}
                />
                <MetricCard
                  label="複勝ROI"
                  value={`${(data.simulated_roi_place * 100).toFixed(1)}%`}
                  sub={`損益 ${placeRoiSign}${placeRoiDiff}% | ${data.place_roi_races.toLocaleString()}レース対象`}
                />
              </div>
            </section>

            {/* 競馬場別 */}
            {data.by_course.length > 0 && (
              <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden" aria-label="競馬場別成績">
                <div className="px-4 py-3 border-b border-gray-50">
                  <h2 className="text-sm font-bold text-gray-700">競馬場別</h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="py-2 px-3 text-left text-xs text-gray-500 font-medium">競馬場</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">レース</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">単勝的中</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">複勝的中</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">単勝ROI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.by_course.map((row) => {
                        const roiCls = row.simulated_roi_win >= 1.0
                          ? "text-green-600 font-bold"
                          : row.simulated_roi_win >= 0.85 ? "text-yellow-600" : "text-red-500";
                        return (
                          <tr key={row.label} className="border-t border-gray-50 hover:bg-gray-50/50">
                            <td className="py-2 px-3 text-xs font-medium text-gray-700">{row.label}</td>
                            <td className="py-2 px-3 text-right text-xs text-gray-600">{row.total_races}</td>
                            <td className="py-2 px-3 text-right text-xs text-green-700">
                              {(row.win_hit_rate * 100).toFixed(1)}%
                            </td>
                            <td className="py-2 px-3 text-right text-xs text-blue-700">
                              {(row.place_hit_rate * 100).toFixed(1)}%
                            </td>
                            <td className={`py-2 px-3 text-right text-xs ${roiCls}`}>
                              {(row.simulated_roi_win * 100).toFixed(1)}%
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* 購入指針統計 */}
            <ChihouBuyingGuide />

            {/* 月次テーブル */}
            {data.monthly_stats.length > 0 && (
              <section
                className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden"
                aria-label="月次詳細テーブル"
              >
                <div className="px-4 py-3 border-b border-gray-50">
                  <h2 className="text-sm font-bold text-gray-700">月次詳細</h2>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="py-2 px-3 text-left  text-xs text-gray-500 font-medium">月</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">レース</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">単勝的中</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">複勝的中</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">top3カバー</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">単勝ROI</th>
                        <th className="py-2 px-3 text-right text-xs text-gray-500 font-medium">複勝ROI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...data.monthly_stats].reverse().map((m) => {
                        const roiW = m.simulated_roi_win;
                        const roiP = m.simulated_roi_place;
                        const clsW = roiW >= 1.0 ? "text-green-600 font-bold" : roiW >= 0.85 ? "text-yellow-600" : "text-red-500";
                        const clsP = roiP >= 1.0 ? "text-green-600 font-bold" : roiP >= 0.85 ? "text-yellow-600" : "text-red-500";
                        return (
                          <tr key={m.year_month} className="border-t border-gray-50 hover:bg-gray-50/50">
                            <td className="py-2 px-3 text-xs font-medium text-gray-600">{m.year_month}</td>
                            <td className="py-2 px-3 text-right text-xs text-gray-600">{m.total_races}</td>
                            <td className="py-2 px-3 text-right text-xs text-blue-700">
                              {(m.win_hit_rate * 100).toFixed(1)}%
                            </td>
                            <td className="py-2 px-3 text-right text-xs text-green-700">
                              {(m.place_hit_rate * 100).toFixed(1)}%
                            </td>
                            <td className="py-2 px-3 text-right text-xs text-purple-700">
                              {(m.top3_coverage_rate * 100).toFixed(1)}%
                            </td>
                            <td className={`py-2 px-3 text-right text-xs ${clsW}`}>
                              {(roiW * 100).toFixed(1)}%
                            </td>
                            <td className={`py-2 px-3 text-right text-xs ${clsP}`}>
                              {m.place_roi_races > 0 ? `${(roiP * 100).toFixed(1)}%` : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* 指標定義 */}
            <details className="bg-white rounded-xl border border-gray-100 shadow-sm">
              <summary className="px-4 py-3 text-xs text-gray-500 cursor-pointer select-none">
                指標の定義を見る
              </summary>
              <div className="px-4 pb-4 pt-1 text-xs text-gray-500 space-y-1.5 border-t border-gray-50">
                <p>
                  <strong className="text-gray-700">単勝的中率</strong>{" "}
                  — 総合指数が最も高い馬（予測1位）が実際に1着になった割合
                </p>
                <p>
                  <strong className="text-gray-700">複勝的中率</strong>{" "}
                  — 予測1位馬が3着以内に入った割合
                </p>
                <p>
                  <strong className="text-gray-700">top3カバー率</strong>{" "}
                  — 実際の1〜3着馬のうち、予測top3に含まれていた馬の割合
                </p>
                <p>
                  <strong className="text-gray-700">単勝ROI</strong>{" "}
                  — 毎レース予測1位に同額の単勝を購入した場合の仮想回収率。100%=収支トントン
                </p>
                <p>
                  <strong className="text-gray-700">複勝ROI</strong>{" "}
                  — 毎レース予測1位に同額の複勝を購入した場合の仮想回収率。複勝オッズの記録があるレースのみ集計
                </p>
              </div>
            </details>
          </>
        )}
      </main>
    </div>
  );
}
