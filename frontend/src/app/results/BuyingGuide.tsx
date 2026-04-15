import { fetchJraBuyingGuide } from "@/lib/api";
import type { BuyingGuideRow } from "@/lib/api";

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

function roiClass(roi: number): string {
  if (roi >= 110) return "text-green-600 font-bold";
  if (roi >= 100) return "text-green-600";
  if (roi >= 90)  return "text-yellow-600";
  return "text-red-500";
}

function roiBg(roi: number): string {
  if (roi >= 110) return "bg-green-50";
  if (roi >= 100) return "bg-green-50/60";
  if (roi >= 90)  return "bg-yellow-50/40";
  return "";
}

// ---------------------------------------------------------------------------
// サブコンポーネント
// ---------------------------------------------------------------------------

function GuideTable({
  rows,
  caption,
  highlightTop = false,
}: {
  rows: BuyingGuideRow[];
  caption: string;
  highlightTop?: boolean;
}) {
  if (rows.length === 0) return null;
  const maxRoi = Math.max(...rows.map((r) => r.win_roi));

  return (
    <div>
      <h3 className="text-xs font-semibold text-gray-600 mb-2">{caption}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-gray-50 text-gray-500 font-medium">
              <th className="py-1.5 px-3 text-left">区分</th>
              <th className="py-1.5 px-2 text-right">レース数</th>
              <th className="py-1.5 px-2 text-right">単勝的中</th>
              <th className="py-1.5 px-2 text-right">複勝的中</th>
              <th className="py-1.5 px-2 text-right">単勝ROI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const isTop = highlightTop && row.win_roi === maxRoi;
              return (
                <tr
                  key={row.label}
                  className={`border-t border-gray-50 ${roiBg(row.win_roi)} ${isTop ? "ring-1 ring-inset ring-green-300" : ""}`}
                >
                  <td className="py-1.5 px-3 text-gray-700 whitespace-nowrap">{row.label}</td>
                  <td className="py-1.5 px-2 text-right text-gray-600 tabular-nums">{row.races.toLocaleString()}</td>
                  <td className="py-1.5 px-2 text-right text-blue-700 tabular-nums">{row.win_pct.toFixed(1)}%</td>
                  <td className="py-1.5 px-2 text-right text-purple-700 tabular-nums">{row.place_pct.toFixed(1)}%</td>
                  <td className={`py-1.5 px-2 text-right tabular-nums ${roiClass(row.win_roi)}`}>
                    {row.win_roi.toFixed(1)}%
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

// ---------------------------------------------------------------------------
// メインコンポーネント（Server Component）
// ---------------------------------------------------------------------------

export async function JraBuyingGuide() {
  let data;
  try {
    data = await fetchJraBuyingGuide("20250101");
  } catch {
    return null;
  }

  return (
    <section
      className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-5"
      aria-label="JRA購入指針統計"
    >
      <div>
        <h2 className="text-sm font-bold text-gray-700">購入指針（JRA 過去実績）</h2>
        <p className="text-[11px] text-gray-400 mt-0.5">
          {data.since.slice(0, 4)}/{data.since.slice(4, 6)}/{data.since.slice(6)} 以降・composite指数1位に毎回単勝購入した場合のシミュレーション
        </p>
      </div>

      {/* 購入指針サマリー */}
      <div className="rounded-lg bg-blue-50 border border-blue-100 px-4 py-3 text-xs text-blue-800 space-y-1">
        <p className="font-semibold">◆ 主要な購入基準（2025年実績）</p>
        <ul className="space-y-0.5 list-disc list-inside">
          <li><strong>短距離(〜1400m)</strong>はオッズ問わず見送り推奨（ROI 83〜85%）</li>
          <li><strong>マイル以上 + 4倍以上</strong>で単勝ROI 100%超（購入推奨）</li>
          <li><strong>中距離・長距離 + 6倍以上</strong>でROI 128〜175%（強く推奨）</li>
          <li>得意会場: <strong>札幌・中山</strong>（ROI 106〜123%）</li>
          <li>苦手会場: 函館・東京・阪神（ROI 82〜83%）</li>
        </ul>
      </div>

      <GuideTable rows={data.odds_cutoff} caption="① オッズ別（全距離）" />
      <GuideTable rows={data.by_distance} caption="② 距離帯別（全オッズ）" />
      <GuideTable rows={data.by_course}   caption="③ 競馬場別（全オッズ・ROI順）" highlightTop />

      <details className="text-[11px] text-gray-400 border-t border-gray-50 pt-2">
        <summary className="cursor-pointer select-none">指標の定義</summary>
        <div className="pt-2 space-y-1">
          <p>・ 対象: 8頭以上・JRA10場・指数バージョンv17</p>
          <p>・ 単勝ROI = 毎レース指数1位馬に同額単勝購入した場合の仮想回収率</p>
          <p>・ 100%=収支トントン。黄色=90〜100%、緑=100%超</p>
        </div>
      </details>
    </section>
  );
}
