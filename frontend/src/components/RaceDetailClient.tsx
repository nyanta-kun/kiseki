"use client";

import { useEffect, useRef, useState } from "react";
import { HorseIndex, OddsData, RaceResult, buildResultsWsUrl } from "@/lib/api";
import { ProbabilityChart } from "@/components/ProbabilityChart";
import { IndicesTable } from "@/components/IndicesTable";

type Props = {
  raceId: number;
  indices: HorseIndex[];
  initialOdds: OddsData;
  initialResults: RaceResult[];
};

function toResultsMap(results: RaceResult[]): Map<number, number | null> {
  return new Map(
    results
      .filter((r) => r.horse_number !== null)
      .map((r) => [r.horse_number as number, r.finish_position])
  );
}

export function RaceDetailClient({ raceId, indices, initialOdds, initialResults }: Props) {
  const [resultsMap, setResultsMap] = useState<Map<number, number | null> | undefined>(
    initialResults.length > 0 ? toResultsMap(initialResults) : undefined
  );
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const url = buildResultsWsUrl(raceId);
    if (!url) return;

    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const data: RaceResult[] = JSON.parse(event.data);
          if (Array.isArray(data) && data.length > 0) {
            setResultsMap(toResultsMap(data));
          }
        } catch {
          // 無視
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // WebSocket非対応環境は無視
    }

    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [raceId]);

  return (
    <>
      {/* 確率チャート */}
      <ProbabilityChart indices={indices} initialOdds={initialOdds} results={resultsMap} />

      {/* 指数テーブル */}
      <section className="bg-white rounded-xl border border-gray-100 p-4 shadow-sm">
        <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
          <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--green-deep)" }} />
          出馬表 指数一覧
          <span className="text-xs text-gray-400 font-normal ml-1">{indices.length}頭</span>
        </h2>
        <IndicesTable indices={indices} results={resultsMap} initialOdds={initialOdds} raceId={raceId} />
      </section>
    </>
  );
}
