"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ConfidenceBoardHorse,
  ConfidenceBoardRace,
  fetchJraConfidenceBoardBrowser,
} from "@/lib/api";
import { cn, surfaceIcon } from "@/lib/utils";

/**
 * 単勝信頼度ボード。
 *
 * 各レースの**全出走馬**を単勝信頼度の降順で並べる。
 *
 * ⚠️ 見出しの「信頼度 A 71pt」は**レース単位**の指標（指数差・頭数・分散・勝率集中）で、
 * 表の「単勝信頼度」は**馬ごと**の勝率予測。名前は似ているが別物。
 *
 * 「ｵｯｽﾞ×信頼度」は単勝期待値と同義で **1.0 が損益分岐**。丸めはサーバ側で済ませてある
 * （画面側で丸めると API の値と表示が食い違う経路ができるため）。
 */

type Props = {
  initialRaces: ConfidenceBoardRace[];
  date: string;
};

const RANK_STYLE: Record<string, string> = {
  S: "bg-red-500 text-white",
  A: "bg-orange-500 text-white",
  B: "bg-sky-500 text-white",
  C: "bg-slate-400 text-white",
};

/** "1025" → "10:25" */
function fmtTime(t: string | null): string {
  if (!t || t.length !== 4) return "";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function fmtOdds(v: number | null): string {
  return v != null ? `${v.toFixed(1)}倍` : "—";
}

/** 単勝信頼度。小数第1位まで出す（1%未満の馬が全部 0% に潰れるのを避ける） */
function fmtConfidence(v: number | null): string {
  return v != null ? `${(v * 100).toFixed(1)}%` : "—";
}

function fmtProduct(v: number | null): string {
  return v != null ? v.toFixed(1) : "—";
}

/** ｵｯｽﾞ×信頼度 の色。1.0 が損益分岐なので、そこを境に変える */
function productClass(v: number | null): string {
  if (v == null) return "text-gray-300";
  if (v >= 1.5) return "text-emerald-600 font-bold";
  if (v >= 1.0) return "text-emerald-600";
  if (v >= 0.8) return "text-gray-600";
  return "text-gray-400";
}

function finishClass(p: number | null): string {
  if (p == null) return "text-gray-300";
  if (p === 1) return "text-amber-600 font-bold";
  if (p <= 3) return "text-blue-600 font-semibold";
  return "text-gray-400";
}

function HorseRows({
  horses,
  hasFinish,
}: {
  horses: ConfidenceBoardHorse[];
  hasFinish: boolean;
}) {
  return (
    <>
      {horses.map((h, i) => (
        <tr key={h.horse_number ?? `x${i}`} className="border-b border-gray-50 last:border-0">
          <td className="py-1.5 pr-2 text-right text-[10px] text-gray-400 tabular-nums">
            {h.confidence_rank_in_race ?? "—"}
          </td>
          <td className="py-1.5 pr-2 text-right font-bold text-gray-700 tabular-nums">
            {h.horse_number ?? "—"}
          </td>
          <td className="py-1.5 pr-3 text-gray-800 font-medium whitespace-nowrap">
            {h.horse_name ?? "—"}
          </td>
          <td className="py-1.5 pr-3 text-right text-gray-600 tabular-nums">
            {fmtOdds(h.win_odds)}
          </td>
          <td className="py-1.5 pr-3 text-right text-gray-700 tabular-nums">
            {fmtConfidence(h.win_probability)}
          </td>
          <td className={cn("py-1.5 pr-2 text-right tabular-nums", productClass(h.odds_x_confidence))}>
            {fmtProduct(h.odds_x_confidence)}
          </td>
          {hasFinish && (
            <td className={cn("py-1.5 text-right tabular-nums", finishClass(h.finish_position))}>
              {h.finish_position != null ? `${h.finish_position}着` : "—"}
            </td>
          )}
        </tr>
      ))}
    </>
  );
}

function RaceBoard({ race }: { race: ConfidenceBoardRace }) {
  const hasFinish = race.horses.some((h) => h.finish_position != null);
  const rankStyle = race.confidence_rank ? RANK_STYLE[race.confidence_rank] : null;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      {/* レース見出し */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100 bg-gray-50/70 flex-wrap">
        <span className="text-xs text-gray-500 tabular-nums w-10 shrink-0">
          {fmtTime(race.post_time)}
        </span>
        <Link
          href={`/races/${race.race_id}`}
          className="flex items-center gap-1.5 text-gray-800 hover:text-emerald-700 transition-colors min-w-0"
        >
          <span className="font-bold text-sm whitespace-nowrap">
            {race.course_name} {race.race_number}R
          </span>
          {race.race_name && (
            <span className="text-xs text-gray-500 truncate">{race.race_name}</span>
          )}
          {race.grade && (
            <span className="text-[10px] bg-gray-200 text-gray-700 px-1.5 py-0.5 rounded-full font-bold shrink-0">
              {race.grade}
            </span>
          )}
        </Link>
        <span className="text-[10px] text-gray-400 whitespace-nowrap">
          {race.surface && surfaceIcon(race.surface)}
          {race.distance ? `${race.distance}m` : ""}
          {race.horses.length > 0 ? ` ${race.horses.length}頭` : ""}
        </span>
        <span className="ml-auto flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] text-gray-400">信頼度</span>
          {rankStyle ? (
            <>
              <span className={cn("px-1.5 py-0.5 rounded text-[11px] font-bold", rankStyle)}>
                {race.confidence_rank}
              </span>
              <span className="text-xs text-gray-600 tabular-nums">{race.confidence_score}pt</span>
            </>
          ) : (
            <span className="text-[11px] text-gray-400">—</span>
          )}
        </span>
      </div>

      {race.horses.length === 0 ? (
        <p className="px-3 py-3 text-xs text-gray-400">出走馬データがありません</p>
      ) : (
        <div className="px-3 py-2 overflow-x-auto">
          <table className="w-full min-w-[400px] text-xs whitespace-nowrap">
            <thead>
              <tr className="text-gray-400 border-b border-gray-100">
                <th className="text-right py-1 pr-2 font-medium">位</th>
                <th className="text-right py-1 pr-2 font-medium">馬番</th>
                <th className="text-left py-1 pr-3 font-medium w-full">馬名</th>
                <th className="text-right py-1 pr-3 font-medium">オッズ</th>
                <th className="text-right py-1 pr-3 font-medium">単勝信頼度</th>
                <th className="text-right py-1 pr-2 font-medium">ｵｯｽﾞ×信頼度</th>
                {hasFinish && <th className="text-right py-1 font-medium">着順</th>}
              </tr>
            </thead>
            <tbody>
              <HorseRows horses={race.horses} hasFinish={hasFinish} />
            </tbody>
          </table>
          {race.n_rated < race.horses.length && (
            <p className="mt-1.5 text-[10px] text-amber-600">
              {race.horses.length - race.n_rated}頭は指数未算出（末尾）
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function ConfidenceBoardClient({ initialRaces, date }: Props) {
  const [races, setRaces] = useState<ConfidenceBoardRace[]>(initialRaces);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const data = await fetchJraConfidenceBoardBrowser(date);
        if (!cancelled) setRaces(data);
      } catch {
        // ネットワーク障害時は無視（次回ポーリングで回復）
      }
    };
    // 隠れている間は回さず、visible へ戻った瞬間に取り直す
    const timer = setInterval(() => {
      if (document.hidden) return;
      void refresh();
    }, 30_000);
    const onVisibility = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [date]);

  if (races.length === 0) {
    return (
      <div className="text-center py-10 text-gray-400">
        <p className="text-3xl mb-2"><span aria-hidden="true">🏇</span></p>
        <p className="text-sm">この日の開催データがありません</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-gray-500 px-1 gap-2 flex-wrap">
        <span>単勝信頼度順（{races.length}レース）</span>
        <span className="text-gray-400">ｵｯｽﾞ×信頼度 1.0 が損益分岐</span>
      </div>
      {races.map((race) => (
        <RaceBoard key={race.race_id} race={race} />
      ))}
    </div>
  );
}
