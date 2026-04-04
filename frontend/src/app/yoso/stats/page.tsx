import { fetchYosoStats } from "@/app/actions/yoso";
import { StatsClient } from "./StatsClient";
import type { YosoStats } from "@/lib/api";

type SearchParams = Promise<{
  from_date?: string;
  to_date?: string;
  course?: string;
  surface?: string;
  dist_min?: string;
  dist_max?: string;
}>;

export default async function YosoStatsPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;

  const stats = (await fetchYosoStats({
    from_date: params.from_date,
    to_date: params.to_date,
    course: params.course,
    surface: params.surface,
    dist_min: params.dist_min ? Number(params.dist_min) : undefined,
    dist_max: params.dist_max ? Number(params.dist_max) : undefined,
  })) as YosoStats | null;

  return (
    <div className="space-y-6">
      <h1 className="text-sm font-semibold text-gray-700">成績集計</h1>
      <StatsClient initialStats={stats} initialParams={params} />
    </div>
  );
}
