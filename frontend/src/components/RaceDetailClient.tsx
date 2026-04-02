"use client";

import dynamic from "next/dynamic";
import { useCallback, useState, useSyncExternalStore } from "react";
import { HorseIndex, OddsData, RaceResult, buildResultsWsUrl } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { WsStatusBadge } from "@/components/WsStatusBadge";
import { IndicesTable } from "@/components/IndicesTable";
import { PaywallGate } from "@/components/PaywallGate";

// ResponsiveContainer はDOMサイズ計測が必要なため SSR を無効化
const ProbabilityChart = dynamic(
  () => import("@/components/ProbabilityChart").then((m) => m.ProbabilityChart),
  { ssr: false }
);

type Props = {
  raceId: number;
  indices: HorseIndex[];
  initialOdds: OddsData;
  initialResults: RaceResult[];
  isPremium?: boolean;
  raceNumber?: number;
};

function toResultsMap(results: RaceResult[]): Map<number, number | null> {
  return new Map(
    results
      .filter((r) => r.horse_number !== null)
      .map((r) => [r.horse_number as number, r.finish_position])
  );
}

// SSR=false、クライアント=true を返すhook（useEffect/setState不要）
function useIsMounted() {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false
  );
}

export function RaceDetailClient({ raceId, indices, initialOdds, initialResults, isPremium = false, raceNumber = 1 }: Props) {
  const mounted = useIsMounted();
  const [resultsMap, setResultsMap] = useState<Map<number, number | null> | undefined>(
    initialResults.length > 0 ? toResultsMap(initialResults) : undefined
  );

  const wsUrl = mounted ? buildResultsWsUrl(raceId) : null;

  const handleMessage = useCallback((data: unknown) => {
    if (Array.isArray(data) && data.length > 0) {
      setResultsMap(toResultsMap(data as RaceResult[]));
    }
  }, []);

  const { isConnected: wsConnected } = useWebSocket(wsUrl, handleMessage);

  return (
    <PaywallGate isPremium={isPremium} raceNumber={raceNumber}>
      <>
        {/* 確率チャート */}
        <ProbabilityChart indices={indices} initialOdds={initialOdds} results={resultsMap} />

        {/* 指数テーブル */}
        <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
          <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--green-deep)" }} />
            出馬表 指数一覧
            <span className="text-xs text-gray-400 font-normal ml-1">{indices.length}頭</span>
            {mounted && wsUrl && (
              <span className="ml-auto">
                <WsStatusBadge connected={wsConnected} label="成績更新: 再接続中…" />
              </span>
            )}
          </h2>
          <IndicesTable indices={indices} results={resultsMap} initialOdds={initialOdds} raceId={raceId} />
        </section>
      </>
    </PaywallGate>
  );
}
