import type { Metadata } from "next";
import { OddsData, RaceEntry, RaceResult, fetchEntries, fetchIndices, fetchOdds, fetchRace, fetchRacesByDate, fetchResults, Race } from "@/lib/api";
import { ConfidencePanel } from "@/components/ConfidencePanel";
import { computeJraBuySignal } from "@/lib/buySignal";
import { RaceDetailClient } from "@/components/RaceDetailClient";
import { RaceSubHeader } from "@/components/RaceSubHeader";
import { EntriesTable } from "@/components/EntriesTable";
import { auth } from "@/auth";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

async function fetchPaidMode(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/admin/settings`, {
      headers: { "X-API-Key": API_KEY },
      next: { revalidate: 60 },
    });
    if (!res.ok) return false;
    const data = (await res.json()) as { settings: { key: string; value: string }[] };
    const setting = data.settings.find((s) => s.key === "PAID_MODE");
    return setting?.value === "true";
  } catch {
    return false;
  }
}

type Params = Promise<{ id: string }>;

// SiteHeader の高さ（py-3 × 2 = 24px + h-8 ロゴ = 32px → 56px = 3.5rem）
const SITE_HEADER_H = "3.5rem";

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { id } = await params;
  try {
    const race = await fetchRace(parseInt(id));
    const title = `${race.course_name} ${race.race_number}R ${race.race_name ?? race.race_class_label ?? ""} | GallopLab`;
    return {
      title,
      description: `${race.surface} ${race.distance}m ${race.condition ?? ""}`.trim(),
      alternates: { canonical: `https://galloplab.com/races/${id}` },
    };
  } catch {
    return {
      title: `レース詳細 | GallopLab`,
      alternates: { canonical: `https://galloplab.com/races/${id}` },
    };
  }
}

export default async function RacePage({ params }: { params: Params }) {
  const { id } = await params;
  const raceId = parseInt(id);

  // auth・PAID_MODE・レース基本情報は互いに依存しないので並列取得
  const [session, paidMode, race] = await Promise.all([
    auth(),
    fetchPaidMode(),
    fetchRace(raceId).catch(() => null),
  ]);
  const isPremium = session?.user?.is_premium ?? false;
  const date = race?.date ?? "";
  const raceNumber = race?.race_number ?? 1;

  // 残り5つを並列取得
  const [allRaces, initialResults, initialOdds, indicesResp, entries] = await Promise.all([
    date ? fetchRacesByDate(date).catch(() => [] as Race[]) : Promise.resolve([] as Race[]),
    fetchResults(raceId).catch(() => [] as RaceResult[]),
    fetchOdds(raceId).catch(() => ({ win: {}, place: {} } as OddsData)),
    fetchIndices(raceId).catch(() => null),
    fetchEntries(raceId).catch(() => [] as RaceEntry[]),
  ]);

  // SiteHeader 直下〜画面下端を fixed で占有。DOM の flex 高さ計算に依存しない。
  // z-0 にして SiteHeader(z-10) が必ず前面に来るようにする。
  const wrapperStyle = {
    position: "fixed" as const,
    top: SITE_HEADER_H,
    left: 0,
    right: 0,
    bottom: 0,
    display: "flex",
    flexDirection: "column" as const,
    background: "#f0f5fb",
    zIndex: 0,
  };

  if (!indicesResp) {
    return (
      <div style={wrapperStyle}>
        <RaceSubHeader raceId={raceId} race={race} date={date} allRaces={allRaces} />
        <main style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto" }}>
          <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
            {entries.length > 0 ? (
              <EntriesTable entries={entries} odds={initialOdds} />
            ) : (
              <div className="py-8 text-center text-gray-400">
                <p className="text-3xl mb-2"><span aria-hidden="true">📊</span></p>
                <p>このレースの指数データがありません</p>
                <p className="text-xs mt-1">算出が完了していない可能性があります</p>
              </div>
            )}
          </div>
        </main>
      </div>
    );
  }

  const indices = indicesResp.horses;
  const confidence = indicesResp.confidence;

  return (
    <div style={wrapperStyle}>
      <RaceSubHeader raceId={raceId} race={race} date={date} allRaces={allRaces} />

      <main id="main-content" style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto" }}>
        <div className="max-w-3xl mx-auto px-4 py-4 space-y-4">
          {/* 信頼度パネル */}
          <ConfidencePanel
            confidence={confidence}
            buySignal={computeJraBuySignal(race?.distance ?? 0, confidence.top_win_odds)}
          />

          {/* 確率チャート・指数テーブル（成績WebSocketで自動更新） */}
          <RaceDetailClient
            raceId={raceId}
            indices={indices}
            initialOdds={initialOdds}
            initialResults={initialResults}
            isPremium={isPremium}
            raceNumber={raceNumber}
            paywallEnabled={paidMode}
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
        </div>
      </main>
    </div>
  );
}
