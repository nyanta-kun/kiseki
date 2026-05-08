import { Suspense } from "react";
import type { Metadata } from "next";
import {
  fetchChihouRace,
  fetchChihouRacesByDate,
  fetchChihouIndices,
  fetchChihouResults,
  fetchChihouOdds,
  Race,
  RaceResult,
  OddsData,
} from "@/lib/api";
import { RaceSubHeader } from "@/components/RaceSubHeader";
import { ChihouRaceDetailClient } from "@/components/ChihouRaceDetailClient";
import { computeChihouBuySignal } from "@/lib/buySignal";

type Params = Promise<{ id: string }>;

const SITE_HEADER_H = "3.5rem";

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { id } = await params;
  try {
    const race = await fetchChihouRace(parseInt(id));
    const title = `[地方] ${race.course_name} ${race.race_number}R ${race.race_name ?? ""} | GallopLab`;
    return {
      title,
      description: `${race.surface} ${race.distance}m ${race.condition ?? ""}`.trim(),
    };
  } catch {
    return { title: "地方競馬 レース詳細 | GallopLab" };
  }
}

const wrapperStyle = {
  position: "fixed" as const,
  top: SITE_HEADER_H,
  left: 0,
  right: 0,
  bottom: 0,
  display: "flex",
  flexDirection: "column" as const,
  background: "#f0faf4",
  zIndex: 0,
};

/** サブヘッダー（レースナビ）を非同期でフェッチ */
async function ChihouRaceSubHeaderAsync({ raceId }: { raceId: number }) {
  const race = await fetchChihouRace(raceId).catch(() => null);
  const date = race?.date ?? "";
  const allRaces = date
    ? await fetchChihouRacesByDate(date).catch(() => [] as Race[])
    : ([] as Race[]);
  return (
    <RaceSubHeader
      raceId={raceId}
      race={race}
      date={date}
      allRaces={allRaces}
      basePath="/chihou/races"
    />
  );
}

/** メインコンテンツ（指数テーブル）を非同期でフェッチ */
async function ChihouRaceBodyAsync({ raceId }: { raceId: number }) {
  // race は generateMetadata / SubHeader でも呼ばれるが Next.js fetch が重複排除
  const race = await fetchChihouRace(raceId).catch(() => null);

  const [initialResults, indicesResp, initialOdds] = await Promise.all([
    fetchChihouResults(raceId).catch(() => [] as RaceResult[]),
    fetchChihouIndices(raceId).catch(() => null),
    fetchChihouOdds(raceId).catch(() => ({ win: {}, place: {} } as OddsData)),
  ]);

  if (!indicesResp) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8 text-center text-gray-400">
        <p className="text-3xl mb-2"><span aria-hidden="true">📊</span></p>
        <p>このレースの指数データがありません</p>
        <p className="text-xs mt-1">算出が完了していない可能性があります</p>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
      <ChihouRaceDetailClient
        raceId={raceId}
        horses={indicesResp.horses}
        initialResults={initialResults}
        initialOdds={initialOdds}
        ranks={indicesResp.ranks ?? null}
        buySignal={computeChihouBuySignal(race?.course_name ?? "", indicesResp.ranks?.recommend_rank)}
      />

      <div className="text-xs text-gray-400 bg-white rounded-lg border border-gray-100 p-3">
        <p className="font-medium text-gray-500 mb-1">指数について（地方競馬版）</p>
        <ul className="space-y-0.5">
          <li>・ 速度指数: フィールド内z-score（斤量補正済み）</li>
          <li>・ 後3F指数: 後3ハロンタイムのフィールド内z-score</li>
          <li>・ 騎手指数: 過去180日の勝率・複勝率</li>
          <li>・ ローテ指数: 前走間隔スコア + 着順ボーナス</li>
          <li>・ 総合指数: 速度40%・後3F 25%・騎手20%・ローテ15%</li>
        </ul>
      </div>
    </div>
  );
}

function SubHeaderSkeleton() {
  return <div className="h-10 bg-white/20 animate-pulse" />;
}

function BodySkeleton() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-4 space-y-3 animate-pulse">
      <div className="h-12 bg-white rounded-xl border border-gray-100" />
      <div className="h-64 bg-white rounded-xl border border-gray-100" />
    </div>
  );
}

export default async function ChihouRacePage({ params }: { params: Params }) {
  const { id } = await params;
  const raceId = parseInt(id);

  return (
    <div style={wrapperStyle}>
      {/* サブヘッダー: 独立した Suspense でページシェルをブロックしない */}
      <Suspense fallback={<SubHeaderSkeleton />}>
        <ChihouRaceSubHeaderAsync raceId={raceId} />
      </Suspense>

      <main id="main-content" style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto" }}>
        {/* 指数テーブル: サブヘッダーと並列フェッチ */}
        <Suspense fallback={<BodySkeleton />}>
          <ChihouRaceBodyAsync raceId={raceId} />
        </Suspense>
      </main>
    </div>
  );
}
