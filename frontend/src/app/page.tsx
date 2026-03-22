import { Suspense } from "react";
import { fetchRacesByDate } from "@/lib/api";
import { formatDate, todayYYYYMMDD } from "@/lib/utils";
import { RaceCard } from "@/components/RaceCard";
import { DateNav } from "@/components/DateNav";

type SearchParams = Promise<{ date?: string }>;

export default async function HomePage({ searchParams }: { searchParams: SearchParams }) {
  const { date } = await searchParams;
  const targetDate = date ?? todayYYYYMMDD();

  return (
    <div className="min-h-screen" style={{ background: "#f8faf9" }}>
      {/* ヘッダー */}
      <header style={{ background: "var(--green-deep)" }} className="sticky top-0 z-10 shadow-md">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between">
          <div>
            <h1 className="text-white font-bold text-lg leading-tight">kiseki</h1>
            <p className="text-green-200 text-[10px]">競馬予測指数システム</p>
          </div>
          <div className="text-right">
            <p className="text-white text-sm font-medium">{formatDate(targetDate)}</p>
          </div>
        </div>
        <DateNav currentDate={targetDate} />
      </header>

      {/* コンテンツ */}
      <main className="max-w-3xl mx-auto px-4 py-4">
        <Suspense fallback={<RaceListSkeleton />}>
          <RaceList date={targetDate} />
        </Suspense>
      </main>
    </div>
  );
}

async function RaceList({ date }: { date: string }) {
  let races;
  try {
    races = await fetchRacesByDate(date);
  } catch {
    return (
      <div className="text-center py-12 text-gray-400">
        <p className="text-4xl mb-2">🏇</p>
        <p>APIに接続できませんでした</p>
        <p className="text-xs mt-1">バックエンドが起動しているか確認してください</p>
      </div>
    );
  }

  if (races.length === 0) {
    return (
      <div className="text-center py-12 text-gray-400">
        <p className="text-4xl mb-2">🏟️</p>
        <p>この日の開催データがありません</p>
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
    <div className="space-y-5">
      {Object.entries(courseGroups).map(([course, courseRaces]) => (
        <section key={course}>
          <h2 className="text-sm font-bold text-gray-600 mb-2 flex items-center gap-2">
            <span
              className="w-1 h-4 rounded inline-block"
              style={{ background: "var(--green-deep)" }}
            />
            {course}
            <span className="text-xs text-gray-400 font-normal">{courseRaces.length}R</span>
          </h2>
          <div className="space-y-1.5">
            {courseRaces.map((race) => (
              <RaceCard key={race.id} race={race} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function RaceListSkeleton() {
  return (
    <div className="space-y-2 animate-pulse">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-16 bg-gray-100 rounded-lg" />
      ))}
    </div>
  );
}
