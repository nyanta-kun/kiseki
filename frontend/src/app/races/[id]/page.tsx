import Link from "next/link";
import type { Metadata } from "next";
import { OddsData, RaceResult, fetchIndices, fetchOdds, fetchRace, fetchRacesByDate, fetchResults, Race } from "@/lib/api";
import { surfaceIcon, gradeClass, raceClassBadgeClass, raceClassShort, formatDate } from "@/lib/utils";
import { EVSummary } from "@/components/EVSummary";
import { RaceNav } from "@/components/RaceNav";
import { ConfidencePanel } from "@/components/ConfidencePanel";
import { RaceDetailClient } from "@/components/RaceDetailClient";
import { LogoutButton } from "@/components/LogoutButton";

type Params = Promise<{ id: string }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { id } = await params;
  try {
    const race = await fetchRace(parseInt(id));
    const title = `${race.course_name} ${race.race_number}R ${race.race_name ?? race.race_class_label ?? ""} | GallopLab`;
    return { title, description: `${race.surface} ${race.distance}m ${race.condition ?? ""}`.trim() };
  } catch {
    return { title: `レース詳細 | GallopLab` };
  }
}

export default async function RacePage({ params }: { params: Params }) {
  const { id } = await params;
  const raceId = parseInt(id);

  // レース情報を最初に取得（date が後続フェッチに必要なため）
  let race = null;
  try {
    race = await fetchRace(raceId);
  } catch {
    // ignore
  }
  const date = race?.date ?? "";

  // 残り4つを並列取得
  const [allRaces, initialResults, initialOdds, indicesResp] = await Promise.all([
    date ? fetchRacesByDate(date).catch(() => [] as Race[]) : Promise.resolve([] as Race[]),
    fetchResults(raceId).catch(() => [] as RaceResult[]),
    fetchOdds(raceId).catch(() => ({ win: {}, place: {} } as OddsData)),
    fetchIndices(raceId).catch(() => null),
  ]);

  if (!indicesResp) {
    return (
      <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
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
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      <Header raceId={raceId} race={race} date={date} allRaces={allRaces} />

      <main id="main-content" className="max-w-3xl mx-auto px-4 py-4 space-y-4">
        {/* 期待値サマリー */}
        <EVSummary indices={indices} />

        {/* 信頼度パネル */}
        <ConfidencePanel confidence={confidence} />

        {/* 確率チャート・指数テーブル（成績WebSocketで自動更新） */}
        <RaceDetailClient
          raceId={raceId}
          indices={indices}
          initialOdds={initialOdds}
          initialResults={initialResults}
        />

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

function formatPostTime(postTime: string | null): string {
  if (!postTime || postTime.length < 4) return "";
  return `${postTime.slice(0, 2)}:${postTime.slice(2, 4)}`;
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
  // 発走時刻順でソートして前後レースを取得
  const sortedRaces = [...allRaces].sort((a, b) => {
    const pa = a.post_time ?? "9999";
    const pb = b.post_time ?? "9999";
    return pa.localeCompare(pb) || a.id - b.id;
  });
  const currentIdx = sortedRaces.findIndex((r) => r.id === raceId);
  const prevRace = currentIdx > 0 ? sortedRaces[currentIdx - 1] : null;
  const nextRace = currentIdx < sortedRaces.length - 1 ? sortedRaces[currentIdx + 1] : null;

  return (
    <header style={{ background: "var(--primary)" }} className="sticky top-0 z-10 shadow-md">
      <div className="max-w-3xl mx-auto px-4 py-3">
        <div className="flex items-center gap-3">
          <Link
            href={`/races?date=${date}`}
            className="text-blue-200 hover:text-white text-lg leading-none"
            aria-label="レース一覧に戻る"
          >
            ←
          </Link>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-white font-bold text-base leading-tight">
                {race
                  ? `${race.course_name} ${race.race_number}R ${race.race_name ?? race.race_class_label ?? ""}`
                  : `Race #${raceId}`}
              </h1>
              {race?.grade && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 ${gradeClass(race.grade)}`}>
                  {race.grade}
                </span>
              )}
              {race && !race.grade && raceClassShort(race.race_class_label) && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 ${raceClassBadgeClass(race.race_class_label)}`}>
                  {raceClassShort(race.race_class_label)}
                </span>
              )}
            </div>
            {race && (
              <p className="text-blue-200 text-[11px] mt-0.5">
                {formatDate(date)}
                {race.post_time && (
                  <span className="ml-1.5 font-medium text-white/90">{formatPostTime(race.post_time)} 発走</span>
                )}
                {" · "}{surfaceIcon(race.surface)} {race.surface} {race.distance}m
                {race.condition ? ` · ${race.condition}` : ""}
              </p>
            )}
          </div>
          <LogoutButton />
        </div>
      </div>

      {/* 前後レースナビゲーション */}
      {(prevRace || nextRace) && (
        <div className="max-w-3xl mx-auto px-4 pb-1.5 flex items-center justify-between">
          {prevRace ? (
            <Link
              href={`/races/${prevRace.id}`}
              className="flex items-center gap-1 text-blue-200 hover:text-white text-[11px] transition-colors"
              aria-label={`前のレース: ${prevRace.course_name} ${prevRace.race_number}R`}
            >
              <span className="text-sm leading-none" aria-hidden="true">‹</span>
              <span>
                {prevRace.course_name}{prevRace.race_number}R
                {prevRace.post_time && ` ${formatPostTime(prevRace.post_time)}`}
              </span>
            </Link>
          ) : <span />}
          {nextRace ? (
            <Link
              href={`/races/${nextRace.id}`}
              className="flex items-center gap-1 text-blue-200 hover:text-white text-[11px] transition-colors"
              aria-label={`次のレース: ${nextRace.course_name} ${nextRace.race_number}R`}
            >
              <span>
                {nextRace.course_name}{nextRace.race_number}R
                {nextRace.post_time && ` ${formatPostTime(nextRace.post_time)}`}
              </span>
              <span className="text-sm leading-none" aria-hidden="true">›</span>
            </Link>
          ) : <span />}
        </div>
      )}

      {allRaces.length > 0 && (
        <RaceNav currentRaceId={raceId} races={allRaces} />
      )}
    </header>
  );
}
