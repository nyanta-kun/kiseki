import { PlacePickMonth, fetchPlacePicks } from "@/lib/api";
import { PlacePicksTable } from "./PlacePicksTable";

type Props = { date: string };

/**
 * 推奨タブの中身 = 当月の複勝ピック一覧（結果つき）。
 *
 * 2026-09-26 に平八バッジの一覧から置き換えた。確定分の入力は前向き記録（発走約10分前の
 * スナップショット）、当日の候補は最新オッズで、判定はどちらも backend の
 * `services/jra_place_pick.py`。レース詳細の「複勝」バッジと同じ関数を通るので、
 * 一覧とバッジはずれない。SSR で初期データを入れ、以降はクライアントが60秒ごとに更新する。
 */
export async function PlacePicksView({ date }: Props) {
  const month = date.slice(0, 6);
  let data: PlacePickMonth | null = null;
  try {
    data = await fetchPlacePicks(month);
  } catch {
    // 取得失敗時は下の案内を出す
  }
  if (!data) {
    return (
      <div className="text-center py-12 text-gray-400 text-sm">
        複勝ピックを取得できませんでした
      </div>
    );
  }
  return <PlacePicksTable initial={data} />;
}
