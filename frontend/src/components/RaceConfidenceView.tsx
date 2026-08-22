import { fetchRaceConfidence, RaceConfidenceRow } from "@/lib/api";
import { RaceConfidenceTable } from "./RaceConfidenceTable";

type Props = {
  date: string;
};

/** 推奨タブの中身。SSR で初期データを入れ、以降はクライアント側が 30 秒ごとに更新する。 */
export async function RaceConfidenceView({ date }: Props) {
  let rows: RaceConfidenceRow[] = [];
  try {
    rows = await fetchRaceConfidence(date);
  } catch {
    // SSR 失敗時は空で渡す（クライアントのポーリングで回復を試みる）
  }
  return <RaceConfidenceTable initialRows={rows} date={date} />;
}
