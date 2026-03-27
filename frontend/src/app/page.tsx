import { Suspense } from "react";
import { fetchNearestDate, fetchRacesByDate } from "@/lib/api";
import { formatDate, todayYYYYMMDD } from "@/lib/utils";
import { CourseTabView } from "@/components/CourseTabView";
import { DateNav } from "@/components/DateNav";

type SearchParams = Promise<{ date?: string }>;

export default async function HomePage({ searchParams }: { searchParams: SearchParams }) {
  const { date } = await searchParams;
  const targetDate = date ?? todayYYYYMMDD();

  // 前後の開催日をサーバーサイドで取得（平日スキップ）
  const [prevDate, nextDate] = await Promise.all([
    fetchNearestDate(targetDate, "prev").then((r) => r.date).catch(() => null),
    fetchNearestDate(targetDate, "next").then((r) => r.date).catch(() => null),
  ]);

  return (
    <div className="min-h-screen" style={{ background: "#f8faf9" }}>
      {/* ヘッダー */}
      <header style={{ background: "var(--green-deep)" }} className="sticky top-0 z-10 shadow-md">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center">
          <h1 className="text-white font-bold text-lg leading-tight">kiseki</h1>
          <p className="text-green-200 text-[10px] ml-2">競馬予測指数システム</p>
        </div>
        <DateNav currentDate={targetDate} prevDate={prevDate} nextDate={nextDate} />
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

  // 表示順: 関東（東京→中山）→ 関西（阪神→京都）→ その他（札幌→函館→福島→新潟→中京→小倉）
  const COURSE_ORDER = ["東京", "中山", "阪神", "京都", "札幌", "函館", "福島", "新潟", "中京", "小倉"];
  const sortedGroups: Record<string, typeof races> = {};
  for (const name of COURSE_ORDER) {
    if (courseGroups[name]) sortedGroups[name] = courseGroups[name];
  }
  // 上記リストにない競馬場は末尾に追加
  for (const name of Object.keys(courseGroups)) {
    if (!sortedGroups[name]) sortedGroups[name] = courseGroups[name];
  }

  return <CourseTabView courseGroups={sortedGroups} />;
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
