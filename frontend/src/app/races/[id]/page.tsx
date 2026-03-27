import Link from "next/link";
import { fetchIndices, fetchRace, fetchRacesByDate, fetchResults, Race } from "@/lib/api";
import { surfaceIcon, gradeClass, formatDate } from "@/lib/utils";
import { IndicesTable } from "@/components/IndicesTable";
import { EVSummary } from "@/components/EVSummary";
import { ProbabilityChart } from "@/components/ProbabilityChart";
import { RaceNav } from "@/components/RaceNav";
import { ConfidencePanel } from "@/components/ConfidencePanel";

type Params = Promise<{ id: string }>;

export default async function RacePage({ params }: { params: Params }) {
  const { id } = await params;
  const raceId = parseInt(id);

  // レース情報を取得
  let race = null;
  try {
    race = await fetchRace(raceId);
  } catch {
    // ignore
  }
  const date = race?.date ?? "";

  // 同日全レースを取得（ナビゲーション用）
  let allRaces: Race[] = [];
  if (date) {
    try {
      allRaces = await fetchRacesByDate(date);
    } catch {
      // ignore
    }
  }

  // 成績を取得（レース前は空配列）
  let resultsMap: Map<number, number | null> | undefined;
  try {
    const results = await fetchResults(raceId);
    if (results.length > 0) {
      resultsMap = new Map(
        results
          .filter((r) => r.horse_number !== null)
          .map((r) => [r.horse_number as number, r.finish_position])
      );
    }
  } catch {
    // 成績なし（レース前）は無視
  }

  let indicesResp = null;
  try {
    indicesResp = await fetchIndices(raceId);
  } catch {
    return (
      <div className="min-h-screen" style={{ background: "#f8faf9" }}>
        <Header raceId={raceId} race={race} date={date} allRaces={allRaces} />
        <main className="max-w-3xl mx-auto px-4 py-8 text-center text-gray-400">
          <p className="text-3xl mb-2">📊</p>
          <p>この レースの指数データがありません</p>
          <p className="text-xs mt-1">算出が完了していない可能性があります</p>
        </main>
      </div>
    );
  }

  const indices = indicesResp.horses;
  const confidence = indicesResp.confidence;

  return (
    <div className="min-h-screen" style={{ background: "#f8faf9" }}>
      <Header raceId={raceId} race={race} date={date} allRaces={allRaces} />

      <main className="max-w-3xl mx-auto px-4 py-4 space-y-4">
        {/* 期待値サマリー */}
        <EVSummary indices={indices} />

        {/* 信頼度パネル */}
        <ConfidencePanel confidence={confidence} />

        {/* 確率チャート */}
        <ProbabilityChart indices={indices} />

        {/* 指数テーブル */}
        <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
          <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--green-deep)" }} />
            出馬表 指数一覧
            <span className="text-xs text-gray-400 font-normal ml-1">{indices.length}頭</span>
          </h2>
          <IndicesTable indices={indices} results={resultsMap} />
        </section>

        {/* 凡例 */}
        <div className="text-xs text-gray-400 bg-white rounded-lg border border-gray-100 p-3">
          <p className="font-medium text-gray-500 mb-1">指数について</p>
          <ul className="space-y-0.5">
            <li>・ 総合指数: 各指数を重み付け合計（0-100）</li>
            <li>・ ◎ 本命: 総合指数1位の馬（ソート順に関係なく固定）</li>
            <li>・ ☆ 穴ぐさ: 穴ぐさ指数58以上の本命以外の馬</li>
            <li>・ 勝率/複勝率: Softmax + Harville式で算出</li>
            <li>・ <span className="text-green-700 font-medium">緑</span>=高評価 / <span className="text-red-600">赤</span>=低評価</li>
          </ul>
        </div>
      </main>
    </div>
  );
}

function Header({
  raceId,
  race,
  date,
  allRaces,
}: {
  raceId: number;
  race: Awaited<ReturnType<typeof fetchRace>> | null;
  date: string;
  allRaces: Race[];
}) {
  return (
    <header style={{ background: "var(--green-deep)" }} className="sticky top-0 z-10 shadow-md">
      <div className="max-w-3xl mx-auto px-4 py-3">
        <div className="flex items-center gap-3">
          <Link
            href={`/?date=${date}`}
            className="text-green-200 hover:text-white text-lg leading-none"
          >
            ←
          </Link>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-white font-bold text-base leading-tight truncate">
                {race
                  ? `${race.course_name} ${race.race_number}R ${race.race_name ?? ""}`
                  : `Race #${raceId}`}
              </h1>
              {race?.grade && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${gradeClass(race.grade)}`}>
                  {race.grade}
                </span>
              )}
            </div>
            {race && (
              <p className="text-green-200 text-[11px] mt-0.5">
                {formatDate(date)} {surfaceIcon(race.surface)} {race.surface} {race.distance}m
                {race.condition ? ` · ${race.condition}` : ""}
              </p>
            )}
          </div>
        </div>
      </div>
      {allRaces.length > 0 && (
        <RaceNav currentRaceId={raceId} races={allRaces} />
      )}
    </header>
  );
}
