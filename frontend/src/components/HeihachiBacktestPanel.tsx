"use client";

import { useEffect, useState } from "react";
import { HeihachiBacktest, fetchHeihachiBacktest } from "@/lib/api";
import { HeihachiThresholds } from "@/lib/heihachi";

/**
 * 「同じしきい値を過去1年に当てるとどうなるか」欄 [[jra_heihachi_badge]]
 *
 * 当日のサマリーは n が一桁で日ごとのブレが大きいため、スライダーを動かした
 * 結果を年間の母数で見られるようにする。しきい値の判定はサーバー側
 * `aggregate_backtest()` が行うが、条件は `lib/heihachi.ts` matchesHeihachi() と
 * 同じに揃えてある（オッズ下限は含み、上限は含まない）。
 *
 * スライダーは連続的に動くので、debounce してから投げ、
 * 前のリクエストは AbortController で捨てる。
 */

const DEBOUNCE_MS = 400;

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

/** 回収率の色。100%未満は赤、100%以上は緑。 */
function roiColor(v: number | null): string {
  if (v === null) return "text-gray-500";
  return v >= 1 ? "text-emerald-700" : "text-red-600";
}

type Props = { year: number; thresholds: HeihachiThresholds };

export function HeihachiBacktestPanel({ year, thresholds }: Props) {
  const [data, setData] = useState<HeihachiBacktest | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const { maxIndexRank, minOdds, maxOdds, minPlaceProb, gradedOnly } = thresholds;

  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const r = await fetchHeihachiBacktest(
          year,
          { maxIndexRank, minOdds, maxOdds, minPlaceProb, gradedOnly },
          ac.signal,
        );
        setData(r);
        setFailed(false);
      } catch (e) {
        if ((e as Error)?.name === "AbortError") return; // 後続のリクエストに引き継ぐ
        setFailed(true);
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ac.abort();
    };
  }, [year, maxIndexRank, minOdds, maxOdds, minPlaceProb, gradedOnly]);

  return (
    <div className="rounded-xl border border-slate-300 bg-slate-50 p-3">
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <p className="font-bold text-slate-800 text-sm">
          📊 {year}年に同じしきい値を当てると{" "}
          <span className="font-normal text-slate-600 text-[11px]">
            {data ? `${data.races.toLocaleString()}レース / ${data.days}開催日` : "集計中"}
          </span>
        </p>
        {loading && <span className="text-[10px] text-slate-500">計算中…</span>}
        {failed && !loading && (
          <span className="text-[10px] text-red-600 font-bold">取得できません</span>
        )}
      </div>

      {data === null ? (
        <div className="h-16 rounded-lg bg-slate-100 animate-pulse motion-reduce:animate-none" />
      ) : data.n === 0 ? (
        <p className="text-xs text-slate-500 py-3 text-center">
          この条件に該当する馬は {year} 年に1頭もありません。しきい値を緩めてください。
        </p>
      ) : (
        <div
          className={loading ? "opacity-50 transition-opacity" : "transition-opacity"}
          aria-busy={loading}
        >
          <div className="grid grid-cols-4 gap-2 text-center">
            <Stat label="対象馬" value={`${data.n.toLocaleString()}頭`}
              sub={data.picks_per_day === null ? "" : `${data.picks_per_day.toFixed(2)}件/日`} />
            <Stat label="3着内率" value={pct(data.place_rate)} sub={`${data.place_hits}的中`} />
            <Stat label="単勝回収率" value={pct(data.win_roi)} sub={`${data.win_hits}勝`}
              color={roiColor(data.win_roi)} />
            <Stat label="複勝回収率" value={pct(data.place_roi)} sub="100円均等"
              color={roiColor(data.place_roi)} />
          </div>
          <p className="mt-2 text-[10px] leading-relaxed text-slate-600">
            確定オッズでの100円均等買い。複勝の的中は払戻の有無で判定（7頭以下は2着まで）。
            {year <= 2025 && (
              <>
                {" "}
                ⚠️ 指数 v28 は2023〜2025にバックフィルされており学習期間と重なるため、
                この年の数字は楽観側に出ている可能性があります。
              </>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  color = "text-slate-800",
}: {
  label: string;
  value: string;
  sub: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg bg-white border border-slate-200 py-2">
      <p className="text-[10px] text-gray-500">{label}</p>
      <p className={`text-base font-bold leading-tight ${color}`}>{value}</p>
      <p className="text-[10px] text-gray-500">{sub}</p>
    </div>
  );
}
