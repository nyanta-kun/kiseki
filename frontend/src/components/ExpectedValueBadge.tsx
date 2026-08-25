/**
 * 期待値バッジ（2026-08-25 新設）。軸信頼と同じ**左からの棒グラフ**にする。
 *
 * 期待値＝`Σ(その目の確率 × 賭け金 × オッズ) ÷ 投資` で **1.00 が収支トントン**。
 * 棒の満尺は **2.00**（＝1.00 がちょうど真ん中）にしてあるので、
 * 「半分より上か下か」だけで損益分岐との位置が読める。
 * 実測（2026-08-24・n=35）で 中央 0.86 / 95%点 1.50 / 最大 2.95 なので、
 * 2.00 を超えるのは稀（1/35）。超えた分は満尺で頭打ちにする。
 *
 * 色は **1.00 未満＝赤 / 1.00 以上＝青**（2026-08-25 ユーザー指定）。
 *
 * 🔴 **この値は購入判断の根拠に使えない。**
 *    確率は各車の3着内率の積をレース内で正規化したもので、**ライン内の連動を
 *    織り込んでいない**（実測で同ライン +4〜5pt / 別ライン −4.5〜−7pt の依存が
 *    ある）。別ライン中心の買い目を過大評価し、同ライン中心を過小評価する。
 *    競輪の市場は効率的で、モデル由来の期待値による選別は繰り返し否定されている
 *    （`backend/src/api/keirin_router.py::_expected_value` の docstring）。
 *    **シグナルとして眺めるためのもの**で、色を強くしても煽らないこと。
 */

/** 棒の満尺。1.00 が真ん中に来るように 2.00 にしてある。 */
export const EV_FULL_SCALE = 2.0;

export default function ExpectedValueBadge({
  ev,
  compact = false,
}: {
  ev?: number | null;
  compact?: boolean;
}) {
  if (ev == null) return null;
  const pos = Math.max(0, Math.min(100, (ev / EV_FULL_SCALE) * 100));
  const ok = ev >= 1.0;
  return (
    <span
      className="relative inline-flex shrink-0 items-center overflow-hidden rounded border border-gray-300 bg-gray-100 px-1.5 py-0.5 dark:border-gray-600 dark:bg-gray-700"
      title={
        "期待値（見込み回収率）。1.00 で収支トントン。棒の満尺は 2.00 なので" +
        "真ん中が損益分岐。Σ(その目の確率 × 賭け金 × オッズ) ÷ 投資。" +
        "確率は各車の3着内率の積をレース内で正規化したもので、ライン内の連動を" +
        "織り込んでいない。🔴 購入判断の根拠には使わないこと。"
      }
    >
      <span
        aria-hidden
        className={`absolute inset-y-0 left-0 ${
          ok ? "bg-blue-500/70 dark:bg-blue-400/60" : "bg-rose-500/60 dark:bg-rose-400/50"
        }`}
        style={{ width: `${pos}%` }}
      />
      {/* 損益分岐（1.00）の目盛り。棒の色だけで伝えず、位置でも分かるようにする。 */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-1/2 w-px bg-gray-500/50 dark:bg-gray-300/40"
      />
      <span className="relative text-xs font-bold tabular-nums text-gray-800 dark:text-gray-100">
        {compact ? null : (
          <span className="mr-0.5 font-normal opacity-70">期待値</span>
        )}
        {ev.toFixed(2)}
      </span>
    </span>
  );
}
