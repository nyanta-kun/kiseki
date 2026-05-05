import {
  ChihouRecommendation,
  ChihouSweetSpotResponse,
  fetchChihouRecommendations,
  fetchChihouSweetSpotRecommendations,
} from "@/lib/api";
import { ChihouRecommendPanelClient } from "./ChihouRecommendPanelClient";

export async function ChihouRecommendPanel({ date }: { date: string }) {
  const [recs, sweetSpots] = await Promise.allSettled([
    fetchChihouRecommendations(date),
    fetchChihouSweetSpotRecommendations(date),
  ]);

  const recList: ChihouRecommendation[] =
    recs.status === "fulfilled" ? recs.value : [];
  const sweetResp: ChihouSweetSpotResponse =
    sweetSpots.status === "fulfilled"
      ? sweetSpots.value
      : { items: [], summaries: {} };

  return (
    <ChihouRecommendPanelClient
      date={date}
      initialRecList={recList}
      initialSweetList={sweetResp.items}
      initialSummaries={sweetResp.summaries}
    />
  );
}
