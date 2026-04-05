import { fetchRecommendations } from "@/lib/api";
import { RecommendCard } from "./RecommendCard";

type Props = {
  date: string;
};

export async function RecommendView({ date }: Props) {
  let recs;
  try {
    recs = await fetchRecommendations(date);
  } catch {
    return (
      <div className="text-center py-10 text-gray-400">
        <p className="text-3xl mb-2">⚠️</p>
        <p className="text-sm">推奨データを取得できませんでした</p>
      </div>
    );
  }

  if (recs.length === 0) {
    return (
      <div className="text-center py-10 text-gray-400">
        <p className="text-3xl mb-2">🏇</p>
        <p className="text-sm">この日の推奨はまだ生成されていません</p>
        <p className="text-xs mt-1 text-gray-400">指数・オッズデータが揃い次第、自動生成されます</p>
      </div>
    );
  }

  // スナップショット時刻（全件共通の想定）
  const snapshotAt = recs[0]?.snapshot_at
    ? new Date(recs[0].snapshot_at).toLocaleString("ja-JP", {
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  return (
    <div className="space-y-3">
      {/* 説明ヘッダー */}
      <div className="flex items-center justify-between text-xs text-gray-500 px-1">
        <span>
          期待値・指数から算出した推奨{recs.length}レース
        </span>
        {snapshotAt && (
          <span className="text-gray-400">{snapshotAt}時点</span>
        )}
      </div>

      {/* 推奨カード */}
      {recs.map((rec) => (
        <RecommendCard key={rec.id} rec={rec} />
      ))}
    </div>
  );
}
