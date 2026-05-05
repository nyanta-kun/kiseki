import { fetchRecommendations, Recommendation } from "@/lib/api";
import { RecommendViewClient } from "./RecommendViewClient";

type Props = {
  date: string;
};

export async function RecommendView({ date }: Props) {
  let recs: Recommendation[] = [];
  try {
    recs = await fetchRecommendations(date);
  } catch {
    // SSR失敗時は空配列でクライアントに渡す（ポーリングで回復を試みる）
  }
  return <RecommendViewClient initialRecs={recs} date={date} />;
}
