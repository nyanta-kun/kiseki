/**
 * 軸信頼バッジ（2026-08-25 新設）。
 *
 * 100% ＝ 上位2車の3着内率の合計が 2.00（軸2車がどちらも確実に3着以内）。
 * 値の算出はサーバ側で完結しており、判定の正本は
 * `backend/src/services/keirin_p3_calibration.confidence_pct`
 * （＝**入稿ランクのゲートが見ているのと同じ量**）。
 *
 * 🔴 **ここで再計算しない。** 画面で作り直すと、出る／出ないを決めている値と
 *    表示がずれる。フロントは受け取った整数を描くだけにする。
 * 🔴 **カードを畳んでいるときも見える位置に置くこと**（ユーザー指定）。
 *    詳細を開かないと分からない指標は、一覧を眺める用途では存在しないのと同じ。
 *
 * 【棒グラフ】背景を左からの帯にして、数字を読まなくても高低が分かるようにする
 * （2026-08-25 ユーザー要望）。帯は `width: {pct}%` の絶対配置で、文字はその上へ
 * 重ねる。**帯の色だけで意味を伝えない**——数字も必ず併記する。
 *
 * 【○△×】信頼度は「軸2車がそろって3着以内に入る」確からしさなので、確定後は
 * **その2車が何車そろったか**を横に出す（ユーザー要望）:
 *
 *   2車とも3着内 → ○ ／ **1車だけ → △** ／ 0車 → ×
 *
 * 🔴 **○×の二値に潰さない**（2026-08-25 ユーザー指定）。1軸だけ来たのか
 *    両方飛んだのかは、信頼度の当たり外れを目で追うときに意味が違う。
 * 🔴 **買い目の的中とは別物。** 相手が外れても二軸はそろっていることがあるので、
 *    ○ が出ているのに不的中、は普通に起きる。tooltip で明示する。
 */

/** 帯の色。段は 9C(65%) / 7C(72%) のゲート閾値に合わせてある。 */
function fillTone(pct: number): string {
  if (pct >= 72) return "bg-emerald-500/70 dark:bg-emerald-400/60";
  if (pct >= 65) return "bg-sky-500/70 dark:bg-sky-400/60";
  if (pct >= 55) return "bg-amber-400/70 dark:bg-amber-300/60";
  return "bg-gray-400/50 dark:bg-gray-400/40";
}

export default function RaceConfidenceBadge({
  pct,
  hitCount,
  compact = false,
}: {
  pct?: number | null;
  /** 信頼度が見ている2車のうち3着以内に入った数（0/1/2）。未確定なら null。 */
  hitCount?: number | null;
  compact?: boolean;
}) {
  if (pct == null) return null;
  const v = Math.max(0, Math.min(100, Math.round(pct)));
  return (
    <span className="inline-flex shrink-0 items-center gap-1">
      <span
        className="relative inline-flex shrink-0 items-center overflow-hidden rounded border border-gray-300 bg-gray-100 px-1.5 py-0.5 dark:border-gray-600 dark:bg-gray-700"
        title={
          "軸信頼。上位2車の3着内率の合計を 2.00 = 100% として表したもの" +
          "（＝軸2車がそろって3着以内に入る確からしさ）。" +
          "入稿ランクの採否ゲートと同じ量（9C は 65%・7C は 72% が下限）。"
        }
      >
        <span
          aria-hidden
          className={`absolute inset-y-0 left-0 ${fillTone(v)}`}
          style={{ width: `${v}%` }}
        />
        <span className="relative text-xs font-bold tabular-nums text-gray-800 dark:text-gray-100">
          {compact ? null : (
            <span className="mr-0.5 font-normal opacity-70">軸信頼</span>
          )}
          {v}%
        </span>
      </span>
      {hitCount != null && (
        <span
          className={`text-sm font-bold leading-none ${
            hitCount >= 2
              ? "text-emerald-600 dark:text-emerald-400"
              : hitCount === 1
                ? "text-amber-600 dark:text-amber-400"
                : "text-rose-600 dark:text-rose-400"
          }`}
          title={
            `軸信頼が見ている2車のうち ${hitCount}車が3着以内に入りました` +
            "（買い目の的中とは別）"
          }
        >
          {hitCount >= 2 ? "○" : hitCount === 1 ? "△" : "×"}
        </span>
      )}
    </span>
  );
}
