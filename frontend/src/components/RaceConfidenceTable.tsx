"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { RaceConfidenceRow, fetchRaceConfidenceBrowser } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Tier バッジの配色。C は見送りなので無彩色にする。 */
const TIER_STYLE: Record<string, string> = {
  S: "bg-red-500 text-white",
  A: "bg-orange-500 text-white",
  B: "bg-sky-500 text-white",
  "C+": "bg-slate-400 text-white",
  C: "bg-gray-200 text-gray-600",
};

/** 着順バッジの色。1着=金、複勝圏=青、それ以外は無彩色。 */
function posColor(p: number): string {
  if (p === 1) return "bg-amber-100 text-amber-700";
  if (p <= 3) return "bg-blue-100 text-blue-700";
  return "bg-gray-100 text-gray-500";
}

/** tier の堅さ順（並び替え用）。大きいほど堅い。 */
const TIER_RANK: Record<string, number> = { S: 5, A: 4, B: 3, "C+": 2, C: 1 };

type SortKey =
  | "race"
  | "post_time"
  | "tier"
  | "confidence_score"
  | "horse_number"
  | "horse_name"
  | "win_odds"
  | "win_probability"
  | "ev"
  | "finish_position";

type Dir = "asc" | "desc";

type Column = {
  key: SortKey;
  label: string;
  /** 数値列は右寄せ */
  numeric: boolean;
  /** その列を最初にクリックしたときの向き（「良い順」が先頭に来るように） */
  firstDir: Dir;
};

const COLUMNS: Column[] = [
  { key: "race", label: "レース", numeric: false, firstDir: "asc" },
  { key: "post_time", label: "発走", numeric: false, firstDir: "asc" },
  { key: "tier", label: "tier", numeric: false, firstDir: "desc" },
  { key: "confidence_score", label: "信頼度", numeric: true, firstDir: "desc" },
  { key: "horse_number", label: "馬番", numeric: true, firstDir: "asc" },
  { key: "horse_name", label: "馬名", numeric: false, firstDir: "asc" },
  { key: "win_odds", label: "単勝", numeric: true, firstDir: "asc" },
  { key: "win_probability", label: "単勝率", numeric: true, firstDir: "desc" },
  { key: "ev", label: "単勝EV", numeric: true, firstDir: "desc" },
  // 未確定（発走前）は null。並び替えでは向きに関わらず末尾へ送られる
  { key: "finish_position", label: "着順", numeric: true, firstDir: "asc" },
];

/** "1025" → "10:25" */
function fmtTime(t: string | null): string {
  if (!t || t.length !== 4) return t ?? "-";
  return `${t.slice(0, 2)}:${t.slice(2)}`;
}

/** 並び替えに使う値。null は向きに関係なく末尾へ送る。 */
function sortValue(r: RaceConfidenceRow, key: SortKey): number | string | null {
  switch (key) {
    // 場内はレース番号順に並ぶよう、場名 + ゼロ埋めR で1つの文字列にする
    case "race":
      return `${r.course_name}${String(r.race_number).padStart(2, "0")}`;
    case "post_time":
      return r.post_time;
    case "tier":
      return r.tier ? (TIER_RANK[r.tier] ?? 0) : null;
    case "confidence_score":
      return r.confidence_score;
    case "horse_number":
      return r.horse_number;
    case "horse_name":
      return r.horse_name;
    case "win_odds":
      return r.win_odds;
    case "win_probability":
      return r.win_probability;
    case "ev":
      return r.ev;
    case "finish_position":
      return r.finish_position;
  }
}

/** 同値のときの安定した副次順（発走時刻 → 場 → R） */
function tieBreak(a: RaceConfidenceRow, b: RaceConfidenceRow): number {
  const ta = a.post_time ?? "9999";
  const tb = b.post_time ?? "9999";
  if (ta !== tb) return ta < tb ? -1 : 1;
  if (a.course_name !== b.course_name) return a.course_name.localeCompare(b.course_name, "ja");
  return a.race_number - b.race_number;
}

type Props = {
  initialRows: RaceConfidenceRow[];
  date: string;
};

export function RaceConfidenceTable({ initialRows, date }: Props) {
  const [rows, setRows] = useState<RaceConfidenceRow[]>(initialRows);
  const [sortKey, setSortKey] = useState<SortKey>("confidence_score");
  const [dir, setDir] = useState<Dir>("desc");
  const [stale, setStale] = useState(false);

  // オッズは刻々と動くので 30 秒ごとに取り直す。
  // 失敗を握り潰すと「開いたまま古い値を見続ける」事故になるので、
  // 連続失敗したら見出しに「更新できません」を出す。
  useEffect(() => {
    let fails = 0;
    const timer = setInterval(async () => {
      try {
        setRows(await fetchRaceConfidenceBrowser(date));
        fails = 0;
        setStale(false);
      } catch {
        fails += 1;
        if (fails >= 2) setStale(true);
      }
    }, 30_000);
    return () => clearInterval(timer);
  }, [date]);

  const sorted = useMemo(() => {
    const arr = [...rows];
    arr.sort((a, b) => {
      const va = sortValue(a, sortKey);
      const vb = sortValue(b, sortKey);
      if (va === null && vb === null) return tieBreak(a, b);
      if (va === null) return 1;
      if (vb === null) return -1;
      const c =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb), "ja");
      if (c === 0) return tieBreak(a, b);
      return dir === "asc" ? c : -c;
    });
    return arr;
  }, [rows, sortKey, dir]);

  function toggle(col: Column) {
    if (col.key === sortKey) {
      setDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(col.key);
      setDir(col.firstDir);
    }
  }

  if (rows.length === 0) {
    return (
      <div className="text-center py-10 text-gray-400">
        <p className="text-3xl mb-2" aria-hidden="true">🏇</p>
        <p className="text-sm">この日のレースデータがありません</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs px-1">
        <span className="text-gray-500">全{rows.length}レースを信頼度順に表示</span>
        {stale && <span className="text-amber-600">最新のオッズを取得できていません</span>}
      </div>

      {/* 列が多いので、横幅が足りない端末では表だけを横スクロールさせる */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm whitespace-nowrap">
          <caption className="sr-only">
            レース信頼度一覧。各列の見出しを押すと並び替わります。
          </caption>
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              {COLUMNS.map((col) => {
                const active = col.key === sortKey;
                return (
                  <th
                    key={col.key}
                    scope="col"
                    aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
                    className={cn("p-0", col.numeric ? "text-right" : "text-left")}
                  >
                    <button
                      type="button"
                      onClick={() => toggle(col)}
                      className={cn(
                        "w-full px-2 py-2 text-xs font-medium transition-colors hover:bg-gray-100",
                        col.numeric ? "text-right" : "text-left",
                        active ? "text-gray-900" : "text-gray-500"
                      )}
                    >
                      {col.label}
                      <span aria-hidden="true" className="ml-0.5 text-[10px]">
                        {active ? (dir === "asc" ? "▲" : "▼") : "　"}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.race_id} className="border-b border-gray-100 last:border-0 hover:bg-blue-50/40">
                <td className="px-2 py-2">
                  <Link
                    href={`/races/${r.race_id}`}
                    className="font-medium text-gray-900 hover:underline"
                  >
                    {r.course_name}
                    {r.race_number}R
                  </Link>
                </td>
                <td className="px-2 py-2 text-gray-500 tabular-nums">{fmtTime(r.post_time)}</td>
                <td className="px-2 py-2">
                  {r.tier ? (
                    <span
                      className={cn(
                        "inline-block px-1.5 py-0.5 rounded-full text-[11px] font-bold",
                        TIER_STYLE[r.tier] ?? "bg-gray-200 text-gray-600"
                      )}
                    >
                      {r.tier}
                    </span>
                  ) : (
                    <span className="text-gray-300">-</span>
                  )}
                </td>
                <td className="px-2 py-2 text-right tabular-nums font-medium">
                  {r.confidence_score ?? <span className="text-gray-300">-</span>}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {r.horse_number ?? <span className="text-gray-300">-</span>}
                </td>
                <td className="px-2 py-2 max-w-[10rem] truncate" title={r.horse_name ?? undefined}>
                  {r.horse_name ?? <span className="text-gray-300">-</span>}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {r.win_odds !== null ? `${r.win_odds.toFixed(1)}倍` : <span className="text-gray-300">-</span>}
                </td>
                <td className="px-2 py-2 text-right tabular-nums">
                  {r.win_probability !== null ? (
                    `${(r.win_probability * 100).toFixed(1)}%`
                  ) : (
                    <span className="text-gray-300">-</span>
                  )}
                </td>
                <td
                  className={cn(
                    "px-2 py-2 text-right tabular-nums font-medium",
                    r.ev !== null && r.ev >= 1.0 ? "text-orange-600" : "text-gray-700"
                  )}
                >
                  {r.ev !== null ? r.ev.toFixed(1) : <span className="text-gray-300">-</span>}
                </td>
                <td className="px-2 py-2 text-right">
                  {r.finish_position !== null ? (
                    <span
                      className={cn(
                        "inline-block px-1.5 py-0.5 rounded text-[11px] font-bold tabular-nums",
                        posColor(r.finish_position)
                      )}
                    >
                      {r.finish_position}着
                    </span>
                  ) : (
                    <span className="text-gray-300">-</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-gray-400 px-1 leading-relaxed">
        信頼度・tier はレース単位の指標（指数1位馬が単勝1番人気と一致するか等から算出）。
        馬番以降は<strong>そのレースの単勝1番人気馬</strong>。単勝EV = 単勝オッズ × 単勝率。
        着順は確定後に入り、未確定のレースは「-」。
      </p>
    </div>
  );
}
