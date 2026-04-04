"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import type { YosoStats, MarkStats, IndexRangeStats, ShareRangeStats } from "@/lib/api";

type Params = {
  from_date?: string;
  to_date?: string;
  course?: string;
  surface?: string;
  dist_min?: string;
  dist_max?: string;
};

type Props = {
  initialStats: YosoStats | null;
  initialParams: Params;
};

export function StatsClient({ initialStats, initialParams }: Props) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [params, setParams] = useState<Params>(initialParams);

  const handleFilter = () => {
    const qs = new URLSearchParams();
    if (params.from_date) qs.set("from_date", params.from_date);
    if (params.to_date) qs.set("to_date", params.to_date);
    if (params.course) qs.set("course", params.course);
    if (params.surface) qs.set("surface", params.surface);
    if (params.dist_min) qs.set("dist_min", params.dist_min);
    if (params.dist_max) qs.set("dist_max", params.dist_max);
    startTransition(() => {
      router.push(`/yoso/stats?${qs.toString()}`);
    });
  };

  const handleReset = () => {
    setParams({});
    startTransition(() => router.push("/yoso/stats"));
  };

  return (
    <div className="space-y-5">
      {/* フィルターパネル */}
      <div className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
        <h2 className="text-xs font-semibold text-gray-500 mb-3">集計条件</h2>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-500 block mb-1">期間（開始）</label>
            <input
              type="date"
              value={params.from_date?.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3") ?? ""}
              onChange={(e) => setParams((p) => ({ ...p, from_date: e.target.value.replace(/-/g, "") }))}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">期間（終了）</label>
            <input
              type="date"
              value={params.to_date?.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3") ?? ""}
              onChange={(e) => setParams((p) => ({ ...p, to_date: e.target.value.replace(/-/g, "") }))}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">競馬場（部分一致）</label>
            <input
              type="text"
              placeholder="例: 東京、阪神"
              value={params.course ?? ""}
              onChange={(e) => setParams((p) => ({ ...p, course: e.target.value }))}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">コース</label>
            <select
              value={params.surface ?? ""}
              onChange={(e) => setParams((p) => ({ ...p, surface: e.target.value || undefined }))}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            >
              <option value="">全て</option>
              <option value="芝">芝</option>
              <option value="ダート">ダート</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">距離（最小）</label>
            <input
              type="number"
              placeholder="例: 1200"
              value={params.dist_min ?? ""}
              onChange={(e) => setParams((p) => ({ ...p, dist_min: e.target.value || undefined }))}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">距離（最大）</label>
            <input
              type="number"
              placeholder="例: 2400"
              value={params.dist_max ?? ""}
              onChange={(e) => setParams((p) => ({ ...p, dist_max: e.target.value || undefined }))}
              className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
          </div>
        </div>
        <div className="flex gap-2 mt-3">
          <button
            onClick={handleFilter}
            disabled={isPending}
            className="flex-1 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-xs font-medium rounded-lg transition-colors"
          >
            {isPending ? "集計中..." : "集計する"}
          </button>
          <button
            onClick={handleReset}
            disabled={isPending}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-600 text-xs rounded-lg transition-colors"
          >
            リセット
          </button>
        </div>
      </div>

      {!initialStats ? (
        <div className="text-center py-12 text-gray-400 text-sm">データがありません</div>
      ) : (
        <>
          <MarkStatsTable data={initialStats.by_mark} />
          <IndexRangeTable data={initialStats.by_index_range} />
          <ShareRangeTable data={initialStats.by_share_range} />
        </>
      )}
    </div>
  );
}

function pct(v: number) { return `${(v * 100).toFixed(1)}%`; }
function roi(v: number) { return `${(v * 100).toFixed(0)}%`; }

function StatsTableWrapper({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50">
        <h2 className="text-xs font-semibold text-gray-600">{title}</h2>
      </div>
      <div className="overflow-x-auto">{children}</div>
    </div>
  );
}

function MarkStatsTable({ data }: { data: MarkStats[] }) {
  if (data.length === 0) return (
    <StatsTableWrapper title="印別成績">
      <p className="text-xs text-gray-400 text-center py-6">印データなし</p>
    </StatsTableWrapper>
  );
  return (
    <StatsTableWrapper title="印別成績">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-400 border-b border-gray-100">
            <th className="px-3 py-2 text-center w-8">印</th>
            <th className="px-3 py-2 text-right">件数</th>
            <th className="px-3 py-2 text-right">単的中率</th>
            <th className="px-3 py-2 text-right">複的中率</th>
            <th className="px-3 py-2 text-right">単回収率</th>
            <th className="px-3 py-2 text-right">複回収率</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.mark} className="border-b border-gray-50 last:border-0">
              <td className="px-3 py-2 text-center font-bold text-lg leading-none">{row.mark}</td>
              <td className="px-3 py-2 text-right text-gray-600">{row.count}</td>
              <td className="px-3 py-2 text-right font-mono">{pct(row.win_rate)}</td>
              <td className="px-3 py-2 text-right font-mono">{pct(row.place_rate)}</td>
              <td className={`px-3 py-2 text-right font-mono font-semibold ${row.win_roi >= 1 ? "text-green-600" : "text-red-500"}`}>
                {roi(row.win_roi)}
              </td>
              <td className={`px-3 py-2 text-right font-mono font-semibold ${row.place_roi >= 1 ? "text-green-600" : "text-red-500"}`}>
                {roi(row.place_roi)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </StatsTableWrapper>
  );
}

function IndexRangeTable({ data }: { data: IndexRangeStats[] }) {
  const filtered = data.filter((r) => r.count > 0);
  if (filtered.length === 0) return (
    <StatsTableWrapper title="指数帯別成績">
      <p className="text-xs text-gray-400 text-center py-6">指数データなし</p>
    </StatsTableWrapper>
  );
  return (
    <StatsTableWrapper title="指数帯別成績">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-400 border-b border-gray-100">
            <th className="px-3 py-2 text-left">指数帯</th>
            <th className="px-3 py-2 text-right">件数</th>
            <th className="px-3 py-2 text-right">単的中率</th>
            <th className="px-3 py-2 text-right">複的中率</th>
            <th className="px-3 py-2 text-right">単回収率</th>
            <th className="px-3 py-2 text-right">複回収率</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((row) => (
            <tr key={row.label} className="border-b border-gray-50 last:border-0">
              <td className="px-3 py-2 text-gray-700 font-medium">{row.label}</td>
              <td className="px-3 py-2 text-right text-gray-600">{row.count}</td>
              <td className="px-3 py-2 text-right font-mono">{pct(row.win_rate)}</td>
              <td className="px-3 py-2 text-right font-mono">{pct(row.place_rate)}</td>
              <td className={`px-3 py-2 text-right font-mono font-semibold ${row.win_roi >= 1 ? "text-green-600" : "text-red-500"}`}>
                {roi(row.win_roi)}
              </td>
              <td className={`px-3 py-2 text-right font-mono font-semibold ${row.place_roi >= 1 ? "text-green-600" : "text-red-500"}`}>
                {roi(row.place_roi)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </StatsTableWrapper>
  );
}

function ShareRangeTable({ data }: { data: ShareRangeStats[] }) {
  const filtered = data.filter((r) => r.count > 0);
  if (filtered.length === 0) return (
    <StatsTableWrapper title="指数占有率別成績">
      <p className="text-xs text-gray-400 text-center py-6">占有率データなし（指数未入力）</p>
    </StatsTableWrapper>
  );
  return (
    <StatsTableWrapper title="指数占有率別成績">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-400 border-b border-gray-100">
            <th className="px-3 py-2 text-left">占有率帯</th>
            <th className="px-3 py-2 text-right">件数</th>
            <th className="px-3 py-2 text-right">単的中率</th>
            <th className="px-3 py-2 text-right">複的中率</th>
            <th className="px-3 py-2 text-right">単回収率</th>
            <th className="px-3 py-2 text-right">複回収率</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((row) => (
            <tr key={row.label} className="border-b border-gray-50 last:border-0">
              <td className="px-3 py-2 text-gray-700 font-medium">{row.label}</td>
              <td className="px-3 py-2 text-right text-gray-600">{row.count}</td>
              <td className="px-3 py-2 text-right font-mono">{pct(row.win_rate)}</td>
              <td className="px-3 py-2 text-right font-mono">{pct(row.place_rate)}</td>
              <td className={`px-3 py-2 text-right font-mono font-semibold ${row.win_roi >= 1 ? "text-green-600" : "text-red-500"}`}>
                {roi(row.win_roi)}
              </td>
              <td className={`px-3 py-2 text-right font-mono font-semibold ${row.place_roi >= 1 ? "text-green-600" : "text-red-500"}`}>
                {roi(row.place_roi)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </StatsTableWrapper>
  );
}
