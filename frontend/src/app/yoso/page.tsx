import { Suspense } from "react";
import { auth } from "@/auth";
import { todayYYYYMMDD } from "@/lib/utils";
import { DateNav } from "@/components/DateNav";
import { fetchNearestDate } from "@/lib/api";
import { fetchYosoRaces } from "@/app/actions/yoso";
import { RaceMarkTableWrapper } from "./RaceMarkTableWrapper";
import type { YosoRace } from "@/lib/api";

type SearchParams = Promise<{ date?: string }>;

export default async function YosoPage({ searchParams }: { searchParams: SearchParams }) {
  const { date } = await searchParams;
  const targetDate = date ?? todayYYYYMMDD();
  const session = await auth();
  const canInputIndex = session?.user?.can_input_index ?? false;

  const [prevDate, nextDate] = await Promise.all([
    fetchNearestDate(targetDate, "prev").then((r) => r.date).catch(() => null),
    fetchNearestDate(targetDate, "next").then((r) => r.date).catch(() => null),
  ]);

  return (
    <div>
      <div style={{ background: "var(--primary-mid)" }} className="shadow-sm -mx-4 px-4 mb-4">
        <DateNav currentDate={targetDate} prevDate={prevDate} nextDate={nextDate} />
      </div>

      <h1 className="text-sm font-semibold text-gray-700 mb-3">予想一覧</h1>

      <Suspense fallback={<div className="text-center py-12 text-gray-400 text-sm">読み込み中...</div>}>
        <YosoRaceList date={targetDate} canInputIndex={canInputIndex} />
      </Suspense>
    </div>
  );
}

async function YosoRaceList({ date, canInputIndex }: { date: string; canInputIndex: boolean }) {
  const races = (await fetchYosoRaces(date)) as YosoRace[];

  if (races.length === 0) {
    return (
      <div className="text-center py-12 text-gray-400">
        <p className="text-3xl mb-2">🏇</p>
        <p className="text-sm">この日の開催データがありません</p>
        <p className="text-xs mt-1 text-gray-300">日付を変更してください</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {races.map((race) => (
        <RaceMarkTableWrapper
          key={race.race_id}
          race={race}
          canInputIndex={canInputIndex}
        />
      ))}
    </div>
  );
}
