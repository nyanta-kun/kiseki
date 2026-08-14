"use client";

/**
 * 成績ページの「実売」タブ（2026-08-15）。
 *
 * ## 「成績」タブとの違い
 *
 * 「成績」は `picks_history`＝**ペーパー成績**（各ランクが条件を満たした全レース）。
 * netkeirin で実際に売れるのは **1レース1商品**なので、両者は母集団が違う:
 *
 *   - 売っていないのに picks_history にはある（他ランクに商品を譲ったレース）
 *   - **売ったのに picks_history には無い**（看板の穴埋め）
 *
 * 実測（2026-08-15）: 入稿472件のうち **250件＝53% に picks_history 行が無い**
 * （うち233件が穴埋め）。したがって picks_history をいくら足しても
 * 「いくら売って、いくら返ってきたか」は出ない。ここは情報源を
 * `netkeirin_submissions` + `bet_detail` だけに固定した別系統。
 *
 * ⚠️ **「的中」は2種類ある。** 素の的中（買い目が当たった）と実質的中
 *    （払戻>=賭け金）で、**netkeirin の表示的中率は後者**。前者だけを見ると
 *    点数を増やしたときに「改善した」と誤読する。両方を並べる。
 */

import type { KeirinSoldPerformanceResponse, KeirinSoldSummary } from "@/lib/api";
import { formatPct, formatYen } from "./format";

export type SoldGroupBy = "rank" | "date" | "origin";

const GROUPS: [SoldGroupBy, string][] = [
  ["rank", "ランク別"],
  ["date", "日別"],
  ["origin", "経路別"],
];

/** `origin` は DB の生値なので日本語へ。未知の値はそのまま出す（黙って隠さない）。 */
const ORIGIN_LABEL: Record<string, string> = {
  rank: "通常（ゲート通過）",
  marquee_fill: "看板の穴埋め",
};

function roiClass(roi: number | null): string {
  if (roi == null) return "text-gray-400";
  return roi >= 1 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500";
}

function Row({ label, s, bold }: { label: string; s: KeirinSoldSummary; bold?: boolean }) {
  const w = bold ? "font-bold" : "font-medium";
  return (
    <tr className={bold ? "bg-gray-50 dark:bg-gray-800/60" : ""}>
      <td className={`px-2 py-1.5 whitespace-nowrap ${w} text-gray-700 dark:text-gray-200`}>
        {label}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-gray-600 dark:text-gray-300">
        {s.n_races}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-gray-500">
        {formatPct(s.hit_rate)}
      </td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${w} text-gray-800 dark:text-gray-100`}>
        {formatPct(s.net_hit_rate)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-gray-500">
        {formatPct(s.gami_rate)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-gray-600 dark:text-gray-300">
        {formatYen(s.total_bet)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-gray-600 dark:text-gray-300">
        {formatYen(s.total_payout)}
      </td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${w} ${roiClass(s.roi)}`}>
        {formatPct(s.roi)}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-gray-500">
        {s.median_payout == null ? "—" : formatYen(s.median_payout)}
      </td>
    </tr>
  );
}

export default function SoldTab({ data, loading, group, onGroup }: {
  data: KeirinSoldPerformanceResponse | null;
  loading: boolean;
  group: SoldGroupBy;
  onGroup: (g: SoldGroupBy) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 dark:bg-blue-950/30 border border-blue-100 dark:border-blue-900 rounded-lg p-3 text-xs text-blue-900 dark:text-blue-200">
        <p className="font-semibold mb-1">実際に売った商品だけの成績です</p>
        <p className="leading-relaxed">
          「成績」タブは各ランクが条件を満たした全レース（ペーパー成績）ですが、
          netkeirin で売れるのは1レース1商品です。ここは入稿の原本
          （買い目・賭け金）と確定結果だけから計算しているので、売上・収益と
          そのまま突き合わせられます。
          <br />
          <span className="font-semibold">実質的中</span> は払戻が賭け金を上回った割合で、
          netkeirin が表示する的中率はこちらです（ガミは不的中として数えられます）。
        </p>
      </div>

      <div className="flex gap-1">
        {GROUPS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => onGroup(key)}
            className={`px-3 py-1 text-xs rounded-full border transition ${
              group === key
                ? "bg-gray-800 text-white border-gray-800 dark:bg-gray-100 dark:text-gray-900"
                : "bg-white text-gray-600 border-gray-200 dark:bg-gray-900 dark:text-gray-300 dark:border-gray-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {loading && <p className="text-sm text-gray-500 py-8 text-center">読み込み中…</p>}

      {!loading && data && data.total.n_races === 0 && (
        <p className="text-sm text-gray-500 py-8 text-center">
          この期間に採点できる入稿がありません。
        </p>
      )}

      {!loading && data && data.total.n_races > 0 && (
        <>
          {/* 横スクロールで収める。列を削るとガミ率や中央値が消えて誤読の元になる。 */}
          <div className="overflow-x-auto bg-white dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 dark:border-gray-700 text-gray-400">
                  <th className="px-2 py-2 text-left font-medium">区分</th>
                  <th className="px-2 py-2 text-right font-medium whitespace-nowrap">売った数</th>
                  <th className="px-2 py-2 text-right font-medium whitespace-nowrap">素の的中</th>
                  <th className="px-2 py-2 text-right font-medium whitespace-nowrap">実質的中</th>
                  <th className="px-2 py-2 text-right font-medium whitespace-nowrap">ガミ率</th>
                  <th className="px-2 py-2 text-right font-medium">投資</th>
                  <th className="px-2 py-2 text-right font-medium">回収</th>
                  <th className="px-2 py-2 text-right font-medium">ROI</th>
                  <th className="px-2 py-2 text-right font-medium whitespace-nowrap">的中時中央</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50 dark:divide-gray-800">
                <Row label="合計" s={data.total} bold />
                {data.items.map(i => (
                  <Row
                    key={i.key}
                    label={group === "origin" ? (ORIGIN_LABEL[i.key] ?? i.key) : i.key}
                    s={i}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* 🔴 集計から外した件数は必ず出す。黙って落とすと「売った全部を集計した」
              ように見える。bet_detail の保存は 2026-08-07 開始。 */}
          {data.missing_bet_detail > 0 && (
            <p className="text-xs text-gray-500 leading-relaxed">
              ⚠️ 買い目が記録されていない入稿 {data.missing_bet_detail} 件を集計から
              外しています（買い目・賭け金の保存は 2026-08-07 開始のため、それ以前は
              「入稿した事実」しか残っていません）。
            </p>
          )}
        </>
      )}
    </div>
  );
}
