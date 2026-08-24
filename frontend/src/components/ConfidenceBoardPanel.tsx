import { fetchJraConfidenceBoard, ConfidenceBoardRace } from "@/lib/api";
import { ConfidenceBoardClient } from "./ConfidenceBoardClient";

type Props = {
  date: string;
};

/** 中央「推奨」タブ本体。SSR で初期データを取り、以降はクライアントがポーリングする。 */
export async function ConfidenceBoardPanel({ date }: Props) {
  let races: ConfidenceBoardRace[] = [];
  try {
    races = await fetchJraConfidenceBoard(date);
  } catch {
    // SSR 失敗時は空で渡す（クライアントのポーリングで回復を試みる）
  }
  return <ConfidenceBoardClient initialRaces={races} date={date} />;
}
