"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState, useCallback } from "react";
import { HorseIndex, OddsData, RaceResult, buildResultsWsUrl } from "@/lib/api";
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

export function RaceDetailClient({ raceId, indices, initialOdds, initialResults, isPremium = false, raceNumber = 1 }: Props) {
  const [resultsMap, setResultsMap] = useState<Map<number, number | null> | undefined>(
    initialResults.length > 0 ? toResultsMap(initialResults) : undefined
  );
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const connectRef = useRef<() => void>(() => {});

  const connect = useCallback(() => {
    const url = buildResultsWsUrl(raceId);
    if (!url || !mountedRef.current) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => { if (mountedRef.current) setWsConnected(true); };

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

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setWsConnected(false);
        reconnectRef.current = setTimeout(() => connectRef.current(), 5000);
      };

      ws.onerror = () => { ws.close(); };
    } catch {
      // WebSocket非対応環境は無視
    }
  }, [raceId]);
  connectRef.current = connect;

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

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
            {buildResultsWsUrl(raceId) && !wsConnected && (
              <span
                className="ml-auto text-[10px] text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5"
                role="status"
                aria-live="polite"
              >
                成績更新: 再接続中…
              </span>
            )}
          </h2>
          <IndicesTable indices={indices} results={resultsMap} initialOdds={initialOdds} raceId={raceId} />
        </section>
      </>
    </PaywallGate>
  );
}
