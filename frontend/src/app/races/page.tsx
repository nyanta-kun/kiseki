import { Suspense } from "react";
import type { Metadata } from "next";
import { fetchNearestDate, fetchRacesByDate, fetchJraTopProbability, fetchRecommendations, fetchAnagusaRules } from "@/lib/api";
import { todayYYYYMMDD, formatDate } from "@/lib/utils";
import { CourseTabView } from "@/components/CourseTabView";
import { DateNav } from "@/components/DateNav";
import { RecommendView } from "@/components/RecommendView";
import { JraTopProbabilityPanel } from "@/components/TopProbabilityPanel";
import { AnagusaRuleView } from "@/components/AnagusaRuleView";

export const metadata: Metadata = {
  title: "開催レース一覧 | GallopLab",
  description: "本日のJRA開催レース指数・期待値一覧",
  alternates: {
    canonical: "https://galloplab.com/races",
  },
};

type SearchParams = Promise<{ date?: string }>;

export default async function RacesPage({ searchParams }: { searchParams: SearchParams }) {
  const { date } = await searchParams;
  const targetDate = date ?? todayYYYYMMDD();

  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      {/* 日付ナビゲーション: 前後開催日の取得を Suspense 内で非同期化しスケルトンを即時表示 */}
      <div style={{ background: "var(--primary-mid)" }} className="shadow-sm">
        <Suspense fallback={<DateNavSkeleton currentDate={targetDate} />}>
          <DateNavLoader currentDate={targetDate} />
        </Suspense>
      </div>

      <main id="main-content" className="max-w-3xl mx-auto px-4 py-4">
        <h1 className="sr-only">開催レース一覧</h1>
        {/* key={targetDate}: 日付切り替え時に古いコンテンツを即クリアしスケルトンを表示する */}
        <Suspense key={targetDate} fallback={<RaceListSkeleton />}>
          <RaceList date={targetDate} />
        </Suspense>
      </main>
    </div>
  );
}

/** 前後開催日を取得してから DateNav を描画する非同期 RSC */
async function DateNavLoader({ currentDate }: { currentDate: string }) {
  const [prevDate, nextDate] = await Promise.all([
    fetchNearestDate(currentDate, "prev").then((r) => r.date).catch(() => null),
    fetchNearestDate(currentDate, "next").then((r) => r.date).catch(() => null),
  ]);
  return <DateNav currentDate={currentDate} prevDate={prevDate} nextDate={nextDate} />;
}

/** 前後開催日取得中に表示するスケルトン（現在日付は即時表示） */
function DateNavSkeleton({ currentDate }: { currentDate: string }) {
  return (
    <div className="max-w-3xl mx-auto flex items-center justify-between px-4 pb-2 gap-2">
      <span className="text-blue-200 text-sm px-2 opacity-40 flex-shrink-0">← 前開催</span>
      <span className="text-white text-sm font-medium whitespace-nowrap">{formatDate(currentDate)}</span>
      <span className="text-blue-200 text-sm px-2 opacity-40 flex-shrink-0">翌開催 →</span>
    </div>
  );
}


async function RaceList({ date }: { date: string }) {
  let races;
  try {
    // 推奨系を並列プリフェッチ: 各パネルでの同一フェッチはキャッシュから即解決する
    [races] = await Promise.all([
      fetchRacesByDate(date),
      fetchJraTopProbability(date).catch(() => []),
      fetchRecommendations(date).catch(() => []),
      fetchAnagusaRules(date).catch(() => []),
    ]);
  } catch {
    return (
      <div className="text-center py-12 text-gray-400">
        <p className="text-4xl mb-2"><span aria-hidden="true">🏇</span></p>
        <p>APIに接続できませんでした</p>
        <p className="text-xs mt-1">バックエンドが起動しているか確認してください</p>
        <a
          href="."
          className="mt-4 inline-block px-4 py-2 bg-green-700 text-white text-sm rounded-lg font-medium hover:bg-green-800 transition-colors"
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

  const recommendPanel = (
    <>
      {/* 穴ぐさ条件ルール推奨（rank_A × 場/面/距離ルール） */}
      <Suspense fallback={<AnagusaSkeleton />}>
        <AnagusaRuleView date={date} />
      </Suspense>
      {/* TopProbabilityPanel を独立した Suspense で囲み RecommendView と並列ストリーミング */}
      <Suspense>
        <JraTopProbabilityPanel date={date} />
      </Suspense>
      <Suspense fallback={<RecommendSkeleton />}>
        <RecommendView date={date} />
      </Suspense>
    </>
  );

  return <CourseTabView courseGroups={sortedGroups} recommendPanel={recommendPanel} />;
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

function RecommendSkeleton() {
  return (
    <div className="space-y-3 animate-pulse motion-reduce:animate-none" aria-busy="true">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-52 bg-gray-100 rounded-xl" />
      ))}
    </div>
  );
}

function AnagusaSkeleton() {
  return (
    <div className="h-24 bg-gray-100 rounded-xl animate-pulse motion-reduce:animate-none" aria-busy="true" />
  );
}
