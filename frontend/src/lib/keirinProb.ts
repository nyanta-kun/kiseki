/**
 * 競輪の予測確率（1着率・2着内率・3着内率）のレース内正規化。
 *
 * `wt_entries.pred_win_pct` / `pred_top2_pct` / `pred_top3_pct` は**選手ごと独立
 * モデルの生確率**で、レース内合計が揃っていない（実例: 単勝合計9.7%・複勝合計
 * 43.9%）。そのまま並べると「1着率の合計が10%」という読めない表になる。
 *
 * 単純な比例配分（線形スケール）だと必要な補正倍率が大きく（複勝で約6.8倍）
 * 個々の値が100%を超えて頭打ちが頻発するため、**ロジット空間で一律シフト**して
 * からシグモイドで戻す。シグモイドの値域により100%は超えない。
 *
 * 🔴 **この実装は netkeirin 入稿コメントの出走表と揃っていること**
 *    （keirin 側 `scripts/netkeirin_submit_wt.py::_build_entry_table`）。
 *    片方だけ変えると、**顧客に見せている表と管理画面の表が食い違う**。
 *
 * ⚠️ 単調変換なのでレース内の**順位は一切変わらない**。変わるのは見た目の値だけ。
 */

export function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-x));
}

export function logit(p: number): number {
  const eps = 1e-6;
  const c = Math.min(Math.max(p, eps), 1 - eps);
  return Math.log(c / (1 - c));
}

/**
 * probs（0〜1の生確率配列）に対し、Σ sigmoid(logit(p_i)+shift) = target となる
 * shift を二分探索で求める。target は 0 < target < probs.length である必要がある。
 */
export function solveLogitShift(probs: number[], target: number): number {
  let lo = -50;
  let hi = 50;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    const sum = probs.reduce((s, p) => s + sigmoid(logit(p) + mid), 0);
    if (sum < target) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

/**
 * レース内の生確率（%スケール・null 可）を合計 `target` に正規化する関数を返す。
 *
 * 全車が 0 / null のときは null を返す関数になる（＝表示は「—」）。
 * `target` は 1着率なら 1、2着内率なら min(出走数, 2)、3着内率なら min(出走数, 3)。
 */
export function makeRaceNormalizer(
  pcts: (number | null | undefined)[],
  target: number,
): (v: number | null | undefined) => number | null {
  const probs = pcts.map((v) => (v ?? 0) / 100);
  const shift = probs.some((p) => p > 0) ? solveLogitShift(probs, target) : null;
  return (v) =>
    v != null && shift != null ? 100 * sigmoid(logit(v / 100) + shift) : null;
}
