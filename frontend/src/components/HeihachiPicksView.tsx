import { HeihachiPicks, fetchHeihachiPicks } from "@/lib/api";
import { HEIHACHI_FALLBACK_DEFAULTS } from "@/lib/heihachi";
import { HeihachiPicksTable } from "./HeihachiPicksTable";

type Props = { date: string };

/** SSR 失敗時の空データ。既定しきい値はフロント側フォールバックを使う。 */
function empty(date: string): HeihachiPicks {
  const d = HEIHACHI_FALLBACK_DEFAULTS;
  return {
    date,
    candidates: [],
    defaults: {
      max_index_rank: d.maxIndexRank,
      min_odds: d.minOdds,
      max_odds: d.maxOdds,
      min_place_prob: d.minPlaceProb,
      graded_only: d.gradedOnly,
      grades: [],
    },
    reference: {
      in_sample: { window: "2025-07〜2026-06", n: 26, place_rate: 0.538, base_place_rate: 0.215 },
      out_of_sample: {
        window: "2026-06-29〜2026-09-06", n: 9, place_rate: 0.111, base_place_rate: 0.228,
      },
    },
  };
}

/** 推奨タブの中身。SSR で初期データを入れ、以降はクライアント側が 30 秒ごとに更新する。 */
export async function HeihachiPicksView({ date }: Props) {
  let data: HeihachiPicks = empty(date);
  try {
    data = await fetchHeihachiPicks(date);
  } catch {
    // SSR 失敗時は空で渡す（クライアントのポーリングで回復を試みる）
  }
  return <HeihachiPicksTable initial={data} date={date} />;
}
