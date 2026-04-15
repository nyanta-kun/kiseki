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
import { computeChihouBuySignal } from "@/components/BuySignalBadge";

type Params = Promise<{ id: string }>;

// SiteHeader の高さ（py-3 × 2 = 24px + h-8 ロゴ = 32px → 56px = 3.5rem）
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

export default async function ChihouRacePage({ params }: { params: Params }) {
  const { id } = await params;
  const raceId = parseInt(id);

  // レース情報を最初に取得（date が後続フェッチに必要）
  let race = null;
  try {
    race = await fetchChihouRace(raceId);
  } catch {
    // ignore
  }
  const date = race?.date ?? "";

  // 残り4つを並列取得
  const [allRaces, initialResults, indicesResp, initialOdds] = await Promise.all([
    date ? fetchChihouRacesByDate(date).catch(() => [] as Race[]) : Promise.resolve([] as Race[]),
    fetchChihouResults(raceId).catch(() => [] as RaceResult[]),
    fetchChihouIndices(raceId).catch(() => null),
    fetchChihouOdds(raceId).catch(() => ({ win: {}, place: {} } as OddsData)),
  ]);

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

  if (!indicesResp) {
    return (
      <div style={wrapperStyle}>
        <RaceSubHeader
          raceId={raceId}
          race={race}
          date={date}
          allRaces={allRaces}
          basePath="/chihou/races"
        />
        <main style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto" }}>
          <div className="max-w-3xl mx-auto px-4 py-8 text-center text-gray-400">
            <p className="text-3xl mb-2"><span aria-hidden="true">📊</span></p>
            <p>このレースの指数データがありません</p>
            <p className="text-xs mt-1">算出が完了していない可能性があります</p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div style={wrapperStyle}>
      <RaceSubHeader
        raceId={raceId}
        race={race}
        date={date}
        allRaces={allRaces}
        basePath="/chihou/races"
      />

      <main id="main-content" style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto" }}>
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
          <ChihouRaceDetailClient
            horses={indicesResp.horses}
            initialResults={initialResults}
            initialOdds={initialOdds}
            ranks={indicesResp.ranks ?? null}
            buySignal={computeChihouBuySignal(race?.course_name ?? "")}
          />

          {/* 凡例 */}
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
      </main>
    </div>
  );
}
