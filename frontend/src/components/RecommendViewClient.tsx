"use client";

import { useEffect, useState } from "react";
import { Recommendation, fetchRecommendationsBrowser } from "@/lib/api";
import { RecommendCard } from "./RecommendCard";

type Props = {
  initialRecs: Recommendation[];
  date: string;
};

export function RecommendViewClient({ initialRecs, date }: Props) {
  const [recs, setRecs] = useState<Recommendation[]>(initialRecs);

  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const data = await fetchRecommendationsBrowser(date);
        setRecs(data);
      } catch {
        // ネットワーク障害時は無視（次回ポーリングで回復）
      }
    }, 30_000);
    return () => clearInterval(timer);
  }, [date]);

  if (recs.length === 0) {
    return (
      <div className="text-center py-10 text-gray-400">
        <p className="text-3xl mb-2">🏇</p>
        <p className="text-sm">この日の推奨はまだ生成されていません</p>
        <p className="text-xs mt-1 text-gray-400">指数・オッズデータが揃い次第、自動生成されます</p>
      </div>
    );
  }

  const snapshotAt = recs[0]?.snapshot_at
    ? new Date(recs[0].snapshot_at).toLocaleString("ja-JP", {
        timeZone: "Asia/Tokyo",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-gray-500 px-1">
        <span>期待値・指数から算出した推奨{recs.length}レース</span>
        {snapshotAt && (
          <span className="text-gray-400">{snapshotAt}時点</span>
        )}
      </div>
      {recs.map((rec) => (
        <RecommendCard key={rec.id} rec={rec} />
      ))}
    </div>
  );
}
