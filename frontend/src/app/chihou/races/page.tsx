import { Suspense } from "react";
import type { Metadata } from "next";
import {
  fetchChihouRacesByDate,
  fetchChihouNearestDate,
  fetchChihouFeaturedPlace,
} from "@/lib/api";
import { todayYYYYMMDD } from "@/lib/utils";
import { CourseTabView } from "@/components/CourseTabView";
import { DateNav } from "@/components/DateNav";
import { ChihouFeaturedPlacePanel } from "@/components/ChihouFeaturedPlacePanel";

function ChihouRecommendSkeleton() {
  return (
    <div className="bg-white rounded-xl border border-gray-100 p-4 mb-4 animate-pulse motion-reduce:animate-none" aria-busy="true" aria-label="注目馬読み込み中">
      <div className="h-4 bg-gray-200 rounded w-1/2 mb-3" />
      <div className="h-3 bg-gray-100 rounded w-full mb-1" />
      <div className="h-3 bg-gray-100 rounded w-3/4 mb-4" />
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-8 bg-gray-50 rounded mb-1.5" />
      ))}
    </div>
  );
}

export const metadata: Metadata = {
  title: "地方競馬 開催レース一覧 | GallopLab",
  description: "本日の地方競馬開催レース指数一覧",
};

type SearchParams = Promise<{ date?: string }>;

/** 前後開催日を非同期フェッチして DateNav をレンダリング（ページシェルのブロッキングを排除） */
async function DateNavAsync({ date, basePath }: { date: string; basePath: string }) {
  const [prevDate, nextDate] = await Promise.all([
    fetchChihouNearestDate(date, "prev").then((r) => r.date).catch(() => null),
    fetchChihouNearestDate(date, "next").then((r) => r.date).catch(() => null),
  ]);
  return <DateNav currentDate={date} prevDate={prevDate} nextDate={nextDate} basePath={basePath} />;
}

export default async function ChihouRacesPage({ searchParams }: { searchParams: SearchParams }) {
  const { date } = await searchParams;
  const targetDate = date ?? todayYYYYMMDD();

  return (
    <div className="min-h-screen" style={{ background: "var(--page-bg-chihou)" }}>
      {/* DateNav: nearest-date フェッチを非同期化 → ページシェルを即ストリーム */}
      <div style={{ background: "var(--chihou-primary-mid)" }} className="shadow-sm">
        <Suspense
          fallback={
            <DateNav currentDate={targetDate} prevDate={null} nextDate={null} basePath="/chihou/races" />
          }
        >
          <DateNavAsync date={targetDate} basePath="/chihou/races" />
        </Suspense>
      </div>

      <main id="main-content" className="max-w-3xl mx-auto px-4 py-4">
        <h1 className="sr-only">地方競馬 開催レース一覧</h1>
        {/* key={targetDate}: 日付切り替え時に古いコンテンツを即クリアしスケルトンを表示する */}
        <Suspense key={targetDate} fallback={<RaceListSkeleton />}>
          <ChihouRaceList date={targetDate} />
        </Suspense>
      </main>
    </div>
  );
}


async function ChihouRaceList({ date }: { date: string }) {
  let races;
  try {
    // races と注目馬を並列フェッチ: 各 fetcher が Next.js fetch キャッシュに結果を蓄積し
    // ChihouFeaturedPlacePanel での同一フェッチはキャッシュから即解決する
    [races] = await Promise.all([
      fetchChihouRacesByDate(date),
      fetchChihouFeaturedPlace(date).catch(() => []),
    ]);
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
      recommendPanel={
        // 推奨タブは注目馬のみ（2026-08-05）。
        // 旧 ChihouRecommendPanel（高オッズ穴/穴軸/低オッズ本命など5カテゴリ）は
        // 検証で ROI・的中率とも根拠が弱く、表示コストだけ高かったため外した。
        <Suspense fallback={<ChihouRecommendSkeleton />}>
          <ChihouFeaturedPlacePanel date={date} />
        </Suspense>
      }
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
