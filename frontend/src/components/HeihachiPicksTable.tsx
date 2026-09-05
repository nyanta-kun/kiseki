"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { HeihachiPicks, fetchHeihachiPicksBrowser } from "@/lib/api";
import { HeihachiBacktestPanel } from "./HeihachiBacktestPanel";
import {
  HEIHACHI_RANGES,
  HeihachiThresholds,
  clearThresholds,
  matchesHeihachi,
} from "@/lib/heihachi";
import { useHeihachiThresholds } from "@/lib/useHeihachiThresholds";
import { cn } from "@/lib/utils";

/**
 * 推奨タブの中身＝平八バッジ該当馬の一覧 [[jra_heihachi_badge]]
 *
 * しきい値はスライダーで動かせる。動かした値は localStorage に入り、
 * **レース詳細の 🎯平八 バッジ表示にも同じ値が効く**（`lib/heihachi.ts` が単一真実源）。
 * サーバーは候補（指数順位5位以内の全馬）だけを返し、絞り込みと回収率集計は
 * ここで行う ── 一覧・回収率・バッジが必ず同じ判定を通るようにするため。
 *
 * 回収率は**その日の確定分のみ**を母数にした実績なので、朝は n=0 になる。
 * 長期の目安は reference（バックテスト実測）を併記する。
 */

/** 上部に出す年間バックテストの対象年。丸1年ぶん確定している直近の年。 */
const BACKTEST_YEAR = 2025;

/** 着順バッジの色。1着=金、複勝圏=青、それ以外は無彩色。 */
function posColor(p: number): string {
  if (p === 1) return "bg-amber-100 text-amber-700";
  if (p <= 3) return "bg-blue-100 text-blue-700";
  return "bg-gray-100 text-gray-500";
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

type Props = { initial: HeihachiPicks; date: string };

export function HeihachiPicksTable({ initial, date }: Props) {
  const [data, setData] = useState<HeihachiPicks>(initial);
  const [stale, setStale] = useState(false);

  const defaults: HeihachiThresholds = useMemo(
    () => ({
      maxIndexRank: data.defaults.max_index_rank,
      minOdds: data.defaults.min_odds,
      maxOdds: data.defaults.max_odds,
      minPlaceProb: data.defaults.min_place_prob,
      gradedOnly: data.defaults.graded_only,
    }),
    [data.defaults],
  );
  const { thresholds, setThresholds } = useHeihachiThresholds(defaults);

  // 単勝オッズが判定条件に入っているので、オッズが動くと対象馬自体が入れ替わる。
  useEffect(() => {
    let fails = 0;
    const timer = setInterval(async () => {
      try {
        setData(await fetchHeihachiPicksBrowser(date));
        fails = 0;
        setStale(false);
      } catch {
        fails += 1;
        if (fails >= 2) setStale(true);
      }
    }, 30_000);
    return () => clearInterval(timer);
  }, [date]);

  const picks = useMemo(
    () =>
      data.candidates.filter((c) =>
        matchesHeihachi(
          {
            grade: c.grade,
            indexRank: c.index_rank,
            winOdds: c.win_odds,
            placeProbability: c.place_probability,
          },
          thresholds,
        ),
      ),
    [data.candidates, thresholds],
  );

  // 回収率は確定済みのみを母数にする。複勝の的中判定は着順ではなく払戻の有無で行う
  // （7頭以下のレースは2着までしか払わないため）。
  const summary = useMemo(() => {
    const settled = picks.filter((p) => p.finish_position !== null);
    const n = settled.length;
    const winHits = settled.filter((p) => p.finish_position === 1);
    const placeHits = settled.filter((p) => p.result_place_odds !== null);
    const winRet = winHits.reduce((a, p) => a + (p.result_win_odds ?? 0), 0);
    const placeRet = placeHits.reduce((a, p) => a + (p.result_place_odds ?? 0), 0);
    return {
      total: picks.length,
      settled: n,
      winHits: winHits.length,
      placeHits: placeHits.length,
      winRoi: n ? winRet / n : null,
      placeRoi: n ? placeRet / n : null,
    };
  }, [picks]);

  const ref = data.reference;
  const set = (patch: Partial<HeihachiThresholds>) => setThresholds({ ...thresholds, ...patch });

  return (
    <div className="space-y-3">
      {/* --- 同じしきい値の年間バックテスト（当日サマリーの上） --- */}
      <HeihachiBacktestPanel year={BACKTEST_YEAR} thresholds={thresholds} />

      {/* --- 回収率サマリ --- */}
      <div className="rounded-xl border border-amber-300 bg-amber-50 p-3">
        <div className="flex items-baseline justify-between gap-2 mb-2">
          <p className="font-bold text-amber-900 text-sm">
            🎯 平八{" "}
            <span className="font-normal text-amber-800 text-[11px]">
              対象馬の回収率（本日の確定分）
            </span>
          </p>
          {stale && <span className="text-[10px] text-red-600 font-bold">更新できません</span>}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <Stat label="単勝回収率" value={pct(summary.winRoi)} sub={`的中 ${summary.winHits}/${summary.settled}`} />
          <Stat label="複勝回収率" value={pct(summary.placeRoi)} sub={`的中 ${summary.placeHits}/${summary.settled}`} />
          <Stat label="対象馬" value={`${summary.total}頭`} sub={`確定 ${summary.settled}頭`} />
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-amber-900/80">
          当日の確定分のみを母数にした実績です（未確定は除外）。既定値での長期の目安は
          n={ref.n} で 3着内率 {(ref.place_rate * 100).toFixed(1)}%・単勝{" "}
          {(ref.win_roi * 100).toFixed(0)}%・複勝 {(ref.place_roi * 100).toFixed(0)}%
          （2023〜2026の4年とも複勝100%超）。
          <strong>既定値から動かした場合、この長期実測は当てはまりません。</strong>
        </p>
      </div>

      {/* --- しきい値スライダー --- */}
      <details className="rounded-xl border border-gray-200 bg-white p-3" open>
        <summary className="cursor-pointer text-xs font-bold text-gray-700 select-none">
          ⚙️ しきい値を調整
          <span className="ml-2 font-normal text-gray-500">
            （レース詳細の 🎯平八 バッジにも反映されます）
          </span>
        </summary>
        <div className="mt-3 space-y-3">
          <Slider
            label="指数順位"
            value={thresholds.maxIndexRank}
            range={HEIHACHI_RANGES.maxIndexRank}
            format={(v) => `${v}位以内`}
            onChange={(v) => set({ maxIndexRank: v })}
          />
          <Slider
            label="単勝オッズ下限"
            value={thresholds.minOdds}
            range={HEIHACHI_RANGES.minOdds}
            format={(v) => `${v.toFixed(1)}倍以上`}
            onChange={(v) => set({ minOdds: Math.min(v, thresholds.maxOdds - 0.5) })}
          />
          <Slider
            label="単勝オッズ上限"
            value={thresholds.maxOdds}
            range={HEIHACHI_RANGES.maxOdds}
            format={(v) => `${v.toFixed(0)}倍未満`}
            onChange={(v) => set({ maxOdds: Math.max(v, thresholds.minOdds + 0.5) })}
          />
          <Slider
            label="複勝確率下限"
            value={thresholds.minPlaceProb}
            range={HEIHACHI_RANGES.minPlaceProb}
            format={(v) => `${(v * 100).toFixed(0)}%以上`}
            onChange={(v) => set({ minPlaceProb: v })}
          />
          <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-xs text-gray-700">
              <input
                type="checkbox"
                checked={thresholds.gradedOnly}
                onChange={(e) => set({ gradedOnly: e.target.checked })}
                className="h-4 w-4"
              />
              OP特別以上のみ
              <span className="text-[10px] text-gray-500">
                （外すと平場も対象。実測では平場は複勝回収94%で機能しません）
              </span>
            </label>
            <button
              type="button"
              onClick={() => clearThresholds()}
              className="text-[10px] px-2 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
            >
              既定値に戻す
            </button>
          </div>
        </div>
      </details>

      {/* --- 一覧 --- */}
      {picks.length === 0 ? (
        <p className="text-sm text-gray-500 py-8 text-center">
          この条件に該当する馬はいません。
          <br />
          <span className="text-[11px]">
            （候補 {data.candidates.length} 頭 — しきい値を緩めると増えます。オッズ未取得のうちは0件です）
          </span>
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-300 text-gray-600">
                <th className="text-left py-1.5 px-1">レース</th>
                <th className="text-left py-1.5 px-1">発走</th>
                <th className="text-left py-1.5 px-1">馬</th>
                <th className="text-right py-1.5 px-1">指数</th>
                <th className="text-right py-1.5 px-1">複勝率</th>
                <th className="text-right py-1.5 px-1">単勝</th>
                <th className="text-center py-1.5 px-1">着順</th>
              </tr>
            </thead>
            <tbody>
              {picks.map((p) => (
                <tr key={`${p.race_id}-${p.horse_number}`} className="border-b border-gray-100">
                  <td className="py-1.5 px-1 whitespace-nowrap">
                    <Link href={`/races/${p.race_id}`} className="text-blue-600 hover:underline">
                      {p.course_name}
                      {p.race_number}R
                    </Link>
                    {p.grade && (
                      <span className="ml-1 text-[9px] px-1 rounded bg-gray-100 text-gray-600">
                        {p.grade}
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 px-1 text-gray-500 whitespace-nowrap">{p.post_time ?? "—"}</td>
                  <td className="py-1.5 px-1">
                    <span className="font-bold">{p.horse_number}</span> {p.horse_name}
                  </td>
                  <td className="py-1.5 px-1 text-right whitespace-nowrap">{p.index_rank}位</td>
                  <td className="py-1.5 px-1 text-right">{pct(p.place_probability)}</td>
                  <td className="py-1.5 px-1 text-right">
                    {p.win_odds === null ? "—" : p.win_odds.toFixed(1)}
                  </td>
                  <td className="py-1.5 px-1 text-center">
                    {p.finish_position ? (
                      <span
                        className={cn(
                          "inline-block px-1.5 rounded font-bold",
                          posColor(p.finish_position),
                        )}
                      >
                        {p.finish_position}
                      </span>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg bg-white border border-amber-200 py-2">
      <p className="text-[10px] text-gray-500">{label}</p>
      <p className="text-lg font-bold text-amber-900 leading-tight">{value}</p>
      <p className="text-[10px] text-gray-500">{sub}</p>
    </div>
  );
}

function Slider({
  label,
  value,
  range,
  format,
  onChange,
}: {
  label: string;
  value: number;
  range: { min: number; max: number; step: number };
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <label className="text-gray-700">{label}</label>
        <span className="font-bold text-amber-900 tabular-nums">{format(value)}</span>
      </div>
      <input
        type="range"
        min={range.min}
        max={range.max}
        step={range.step}
        value={value}
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-amber-600"
      />
    </div>
  );
}
