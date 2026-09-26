"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PlacePickMonth, PlacePickRow, fetchPlacePicksBrowser } from "@/lib/api";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

/**
 * 複勝ピックの当月一覧（クライアント側）。
 *
 * 当日の候補はオッズ次第で出たり消えたりし、発走約10分前に確定へ切り替わるので、
 * 60秒ごとに取り直す。判定はサーバー側（services/jra_place_pick.py）だけが行う。
 */
export function PlacePicksTable({ initial }: { initial: PlacePickMonth }) {
  const [data, setData] = useState(initial);
  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        setData(await fetchPlacePicksBrowser(initial.month));
      } catch {
        // 次回のポーリングで回復を試みる
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [initial.month]);

  const month = data.month;
  const label = `${Number(month.slice(0, 4))}年${Number(month.slice(4, 6))}月`;
  const s = data.summary;
  const candidates = data.picks.filter((p) => p.stage === "candidate");
  const confirmed = data.picks.filter((p) => p.stage === "confirmed");

  return (
    <div className="space-y-3">
      <section className="bg-white rounded-xl border border-gray-100 p-3 shadow-sm">
        <h2 className="text-sm font-bold text-gray-800">{label}の複勝ピック</h2>
        <p className="text-[11px] text-gray-500 mt-0.5 leading-relaxed">
          1レース最大1頭・該当なしは見送り。条件: 8頭以上 ∧ 複勝3.0〜4.0倍未満 ∧
          複勝確率5位以内 ∧ 単勝÷複勝≤3.5。当日は候補から表示し、発走約10分前のオッズで確定する
        </p>
        <dl className="grid grid-cols-4 gap-2 mt-2 text-center">
          <Stat label="確定" value={`${s.n_picks}点`} sub={s.n_candidates > 0 ? `候補${s.n_candidates}` : undefined} />
          <Stat
            label="的中"
            value={`${s.n_hits}/${s.n_settled}`}
            sub={s.hit_rate != null ? `${(s.hit_rate * 100).toFixed(1)}%` : "—"}
          />
          <Stat
            label="払戻/投資"
            value={`${s.payout.toLocaleString()}円`}
            sub={`${s.invest.toLocaleString()}円`}
          />
          <Stat
            label="回収率"
            value={s.roi != null ? `${(s.roi * 100).toFixed(0)}%` : "—"}
            emphasize={s.roi != null && s.roi >= 1}
          />
        </dl>
        <p className="text-[10px] text-gray-400 mt-2 leading-relaxed">
          集計は確定分のみ。探索時（2026-03〜09・8頭以上）は118点・的中43.2%・回収率133%。
          7月以降も条件選びに使ったため、採否は10月以降の成績で判断する（回収率の保証ではない）。
        </p>
      </section>

      {candidates.length > 0 && (
        <section>
          <h3 className="text-xs font-bold text-emerald-800 px-1 mb-1">
            本日の候補（最新オッズで判定・発走約10分前に確定）
          </h3>
          <ul className="bg-white rounded-xl border border-dashed border-emerald-400 shadow-sm divide-y divide-gray-100">
            {candidates.map((p) => (
              <PickItem key={`c-${p.race_id}`} p={p} />
            ))}
          </ul>
        </section>
      )}

      {confirmed.length === 0 ? (
        <div className="text-center py-8 text-gray-400 text-sm">
          今月の確定ピックはまだありません
        </div>
      ) : (
        <section>
          {candidates.length > 0 && (
            <h3 className="text-xs font-bold text-gray-700 px-1 mb-1">確定</h3>
          )}
          <ul className="bg-white rounded-xl border border-gray-100 shadow-sm divide-y divide-gray-100">
            {confirmed.map((p) => (
              <PickItem key={p.race_id} p={p} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value, sub, emphasize = false }: {
  label: string; value: string; sub?: string; emphasize?: boolean;
}) {
  return (
    <div className="rounded-lg bg-gray-50 py-1.5 min-w-0">
      <dt className="text-[10px] text-gray-500">{label}</dt>
      <dd className={cn("text-sm font-bold tabular-nums", emphasize ? "text-emerald-700" : "text-gray-800")}>
        {value}
      </dd>
      {sub && <dd className="text-[10px] text-gray-500 tabular-nums">{sub}</dd>}
    </div>
  );
}

function StatusBadge({ p }: { p: PlacePickRow }) {
  const base = "text-[11px] px-1.5 py-0.5 rounded font-bold whitespace-nowrap tabular-nums";
  switch (p.status) {
    case "hit":
      return (
        <span className={cn(base, "bg-emerald-600 text-white")}>
          {p.finish_position}着 {p.place_payout?.toLocaleString()}円
        </span>
      );
    case "miss":
      return (
        <span className={cn(base, "bg-gray-100 text-gray-500")}>
          {p.finish_position ? `${p.finish_position}着` : "着外"}
        </span>
      );
    case "void":
      return <span className={cn(base, "bg-amber-50 text-amber-700")}>返還</span>;
    case "candidate":
      return (
        <span className={cn(base, "bg-emerald-50 text-emerald-800 border border-dashed border-emerald-500")}>
          候補
        </span>
      );
    default:
      return <span className={cn(base, "bg-emerald-600 text-white")}>確定・発走前</span>;
  }
}

function PickItem({ p }: { p: PlacePickRow }) {
  const md = `${Number(p.date.slice(4, 6))}/${Number(p.date.slice(6, 8))}`;
  const post = p.post_time ? `${p.post_time.slice(0, 2)}:${p.post_time.slice(2, 4)}` : "";
  return (
    <li className="px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <Link
          href={`/races/${p.race_id}`}
          className="text-xs text-gray-600 hover:underline truncate min-w-0"
        >
          {md} {p.course_name}{p.race_number}R {post}
          <span className="text-gray-400">
            {" "}· {p.surface}{p.distance} · {p.field_size}頭
          </span>
        </Link>
        <StatusBadge p={p} />
      </div>
      <div className="flex items-center gap-2 mt-0.5 min-w-0">
        <span className="shrink-0 inline-flex items-center justify-center w-5 h-5 rounded bg-gray-800 text-white text-[11px] font-bold">
          {p.horse_number}
        </span>
        <span className="font-medium text-sm text-gray-800 truncate min-w-0">
          {p.horse_name ?? "—"}
        </span>
        <span className="ml-auto shrink-0 text-[11px] text-gray-500 tabular-nums whitespace-nowrap">
          単{p.pre_win_odds?.toFixed(1) ?? "—"} 複{p.pre_place_odds?.toFixed(1) ?? "—"}
          {p.pop_rank != null && ` · ${p.pop_rank}人気`}
        </span>
      </div>
    </li>
  );
}
