import { Suspense } from "react";
import type { Metadata } from "next";
import { fetchChihouRacesByDate, fetchChihouNearestDate } from "@/lib/api";
import { todayYYYYMMDD } from "@/lib/utils";
import { CourseTabView } from "@/components/CourseTabView";
import { DateNav } from "@/components/DateNav";
import { ChihouRecommendPanel } from "@/components/ChihouRecommendPanel";

export const metadata: Metadata = {
  title: "地方競馬 開催レース一覧 | GallopLab",
  description: "本日の地方競馬開催レース指数一覧",
};

type SearchParams = Promise<{ date?: string }>;

export default async function ChihouRacesPage({ searchParams }: { searchParams: SearchParams }) {
  const { date } = await searchParams;
  const targetDate = date ?? todayYYYYMMDD();

  const [prevDate, nextDate] = await Promise.all([
    fetchChihouNearestDate(targetDate, "prev").then((r) => r.date).catch(() => null),
    fetchChihouNearestDate(targetDate, "next").then((r) => r.date).catch(() => null),
  ]);

  return (
    <div className="min-h-screen" style={{ background: "#f0faf4" }}>
      {/* 日付ナビゲーション */}
      <div style={{ background: "var(--chihou-primary-mid)" }} className="shadow-sm">
        <DateNav
          currentDate={targetDate}
          prevDate={prevDate}
          nextDate={nextDate}
          basePath="/chihou/races"
        />
      </div>

      <main id="main-content" className="max-w-3xl mx-auto px-4 py-4">
        <h1 className="sr-only">地方競馬 開催レース一覧</h1>
        <Suspense fallback={<RaceListSkeleton />}>
          <ChihouRaceList date={targetDate} />
        </Suspense>
      </main>
    </div>
  );
}


async function ChihouRaceList({ date }: { date: string }) {
  let races;
  try {
    races = await fetchChihouRacesByDate(date);
  } catch {
    return (
      <div className="text-center py-12 text-gray-400">
        <p className="text-4xl mb-2"><span aria-hidden="true">🏇</span></p>
        <p>APIに接続できませんでした</p>
        <p className="text-xs mt-1">バックエンドが起動しているか確認してください</p>
        <a
          href="."
          className="mt-4 inline-block px-4 py-2 text-white text-sm rounded-lg font-medium transition-colors"
          style={{ background: "var(--chihou-primary)" }}
        >
          再読み込み
        </a>
      </div>
    );
  }

  if (races.length === 0) {
    return (
      <div className="text-center py-12 text-gray-400">
        <p className="text-4xl mb-2"><span aria-hidden="true">🏟️</span></p>
        <p>この日の地方競馬データがありません</p>
      </div>
    );
  }

  // 競馬場ごとにグループ化
  const courseGroups: Record<string, typeof races> = {};
  for (const race of races) {
    if (!courseGroups[race.course_name]) courseGroups[race.course_name] = [];
    courseGroups[race.course_name].push(race);
  }

  return (
    <CourseTabView
      courseGroups={courseGroups}
      recommendPanel={<ChihouRecommendPanel date={date} />}
      basePath="/chihou/races"
      hideRecommend={false}
    />
  );
}

function RaceListSkeleton() {
  return (
    <div className="space-y-2 animate-pulse motion-reduce:animate-none" aria-busy="true" aria-label="読み込み中">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-16 bg-gray-100 rounded-lg" />
      ))}
    </div>
  );
}
