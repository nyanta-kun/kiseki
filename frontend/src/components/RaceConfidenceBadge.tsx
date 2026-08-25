/**
 * レース信頼度バッジ（2026-08-25 新設）。
 *
 * 100% ＝ 上位2車の3着内率の合計が 2.00（軸2車がどちらも確実に3着以内）。
 * 値の算出はサーバ側で完結しており、判定の正本は keirin 側
 * `src/p3_calibration.confidence_pct`（＝**ランクのゲートが見ているのと同じ量**）。
 *
 * 🔴 **ここで再計算しない。** 画面で作り直すと、出る／出ないを決めている値と
 *    表示がずれる。フロントは受け取った整数を出すだけにする。
 * 🔴 **カードを畳んでいるときも見える位置に置くこと**（ユーザー指定）。
 *    詳細を開かないと分からない指標は、一覧を眺める用途では存在しないのと同じ。
 */

/** 色の段は 9C(65%) / 7C(72%) のゲート閾値に合わせてある。 */
function tone(pct: number): string {
  if (pct >= 72) return "bg-emerald-600 text-white dark:bg-emerald-500";
  if (pct >= 65) return "bg-sky-600 text-white dark:bg-sky-500";
  if (pct >= 55) return "bg-amber-500 text-white";
  return "bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300";
}

export default function RaceConfidenceBadge({
  pct,
  compact = false,
}: {
  pct?: number | null;
  compact?: boolean;
}) {
  if (pct == null) return null;
  const v = Math.max(0, Math.min(100, Math.round(pct)));
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-0.5 rounded px-1.5 py-0.5 text-xs font-bold ${tone(v)}`}
      title={
        "レース信頼度。上位2車の3着内率の合計を 2.00 = 100% として表したもの。" +
        "ランクの採否ゲートと同じ量（9C は 65%・7C は 72% が下限）。"
      }
    >
      {compact ? null : <span className="font-normal opacity-80">信頼度</span>}
      {v}%
    </span>
  );
}
