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
  | "tier_score"
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
  /** その列を最初に選んだときの向き（「良い順」が先頭に来るように） */
  firstDir: Dir;
};

const COLUMNS: Column[] = [
  { key: "race", label: "レース", numeric: false, firstDir: "asc" },
  { key: "post_time", label: "発走", numeric: false, firstDir: "asc" },
  { key: "tier", label: "tier", numeric: false, firstDir: "desc" },
  // tier を数値化したもの。tier と食い違わない唯一の並び替え軸なのでこれを既定にする。
  { key: "tier_score", label: "評価", numeric: true, firstDir: "desc" },
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
    case "tier_score":
      return r.tier_score;
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

const DASH = <span className="text-gray-300">-</span>;

function fmtOdds(v: number | null) {
  return v !== null ? `${v.toFixed(1)}倍` : DASH;
}
function fmtPct(v: number | null) {
  return v !== null ? `${(v * 100).toFixed(1)}%` : DASH;
}
/** 単勝EV。表示は小数第1位だが、並び替えは素の値で行うので同表示でも順序は潰れない。 */
function fmtEv(v: number | null) {
  return v !== null ? v.toFixed(1) : DASH;
}
function evClass(v: number | null): string {
  return v !== null && v >= 1.0 ? "text-orange-600" : "text-gray-900";
}

function TierBadge({ tier }: { tier: string | null }) {
  if (!tier) return DASH;
  return (
    <span
      className={cn(
        "inline-block rounded-full text-[11px] font-bold leading-none px-1.5 py-1",
        TIER_STYLE[tier] ?? "bg-gray-200 text-gray-600"
      )}
    >
      {tier}
    </span>
  );
}

function FinishBadge({ pos }: { pos: number | null }) {
  if (pos === null) return DASH;
  return (
    <span
      className={cn(
        "inline-block px-1.5 py-0.5 rounded text-[11px] font-bold tabular-nums",
        posColor(pos)
      )}
    >
      {pos}着
    </span>
  );
}

type Props = {
  initialRows: RaceConfidenceRow[];
  date: string;
};

export function RaceConfidenceTable({ initialRows, date }: Props) {
  const [rows, setRows] = useState<RaceConfidenceRow[]>(initialRows);
  const [sortKey, setSortKey] = useState<SortKey>("tier_score");
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

  const arrow = dir === "asc" ? "▲" : "▼";
  const sortLabel = COLUMNS.find((c) => c.key === sortKey)?.label ?? "";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs px-1">
        <span className="text-surface-muted">
          全{rows.length}レースを{sortLabel}順に表示
        </span>
        {stale && <span className="text-amber-600">最新のオッズを取得できていません</span>}
      </div>

      {/*
        スマホ（< md）は10列の表が画面幅に収まらず、横スクロールしないと
        単勝・単勝率・EV・着順が見えない。カード表示に切り替え、
        並び替えは列見出しの代わりにチップで行う（全列とも並び替え可能）。
      */}
      <div className="md:hidden space-y-2">
        <div
          className="flex gap-1 overflow-x-auto pb-1 scrollbar-none"
          role="group"
          aria-label="並び替え"
        >
          {COLUMNS.map((col) => {
            const active = col.key === sortKey;
            return (
              <button
                key={col.key}
                type="button"
                onClick={() => toggle(col)}
                aria-pressed={active}
                className={cn(
                  "flex-shrink-0 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors",
                  active
                    ? "bg-gray-900 text-white border-gray-900"
                    : "bg-white text-gray-600 border-gray-200"
                )}
              >
                {col.label}
                {active && <span aria-hidden="true"> {arrow}</span>}
              </button>
            );
          })}
        </div>

        <ul className="space-y-1.5">
          {sorted.map((r) => (
            <li key={r.race_id} className="bg-white rounded-xl border border-gray-200 px-3 py-2.5">
              {/* 1段目: レース / 発走 / tier … 信頼度 */}
              <div className="flex items-center gap-2">
                <Link
                  href={`/races/${r.race_id}`}
                  className="font-bold text-gray-900 hover:underline"
                >
                  {r.course_name}
                  {r.race_number}R
                </Link>
                <span className="text-xs text-gray-500 tabular-nums">{fmtTime(r.post_time)}</span>
                <TierBadge tier={r.tier} />
                {/* tier の数値表現。tier と並び順が一致する唯一の指標なので主役に置く */}
                <span className="text-base font-bold text-gray-900 tabular-nums leading-none">
                  {r.tier_score != null ? r.tier_score.toFixed(1) : "-"}
                </span>
                <span className="ml-auto flex items-baseline gap-1">
                  <span className="text-[10px] text-gray-400">信頼度</span>
                  <span className="text-sm font-semibold text-gray-600 tabular-nums leading-none">
                    {r.confidence_score ?? "-"}
                  </span>
                </span>
              </div>

              {/* 2段目: 単勝1番人気馬 … 着順 */}
              <div className="mt-2 flex items-center gap-2">
                <span className="w-6 h-6 flex-shrink-0 rounded bg-gray-100 text-gray-700 text-xs font-bold flex items-center justify-center tabular-nums">
                  {r.horse_number ?? "-"}
                </span>
                <span className="font-medium text-gray-900 truncate">{r.horse_name ?? "-"}</span>
                <span className="ml-auto flex-shrink-0">
                  <FinishBadge pos={r.finish_position} />
                </span>
              </div>

              {/* 3段目: オッズ系 */}
              <dl className="mt-1.5 flex items-center gap-x-3 text-xs">
                <div>
                  <dt className="inline text-gray-400">単勝 </dt>
                  <dd className="inline font-medium text-gray-900 tabular-nums">
                    {fmtOdds(r.win_odds)}
                  </dd>
                </div>
                <div>
                  <dt className="inline text-gray-400">単勝率 </dt>
                  <dd className="inline font-medium text-gray-900 tabular-nums">
                    {fmtPct(r.win_probability)}
                  </dd>
                </div>
                <div>
                  <dt className="inline text-gray-400">EV </dt>
                  <dd className={cn("inline font-bold tabular-nums", evClass(r.ev))}>
                    {fmtEv(r.ev)}
                  </dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      </div>

      {/* PC（md 以上）は表。列見出しクリックで並び替え。 */}
      <div className="hidden md:block bg-white rounded-xl border border-gray-200 overflow-x-auto">
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
                        {active ? arrow : "　"}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr
                key={r.race_id}
                className="border-b border-gray-100 last:border-0 hover:bg-blue-50/40"
              >
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
                  <TierBadge tier={r.tier} />
                </td>
                <td
                  className="px-2 py-2 text-right tabular-nums font-bold text-gray-900"
                  title={
                    r.tier_score == null
                      ? undefined
                      : `市場一致 ${r.market_agree === null ? "不明" : r.market_agree ? "○" : "×"}` +
                        ` / 混戦度 ${r.entropy_norm ?? DASH}` +
                        ` / tier内順位スコア ${r.priority_score ?? DASH}`
                  }
                >
                  {r.tier_score != null ? r.tier_score.toFixed(1) : DASH}
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-gray-600">
                  {r.confidence_score ?? DASH}
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-gray-900">
                  {r.horse_number ?? DASH}
                </td>
                <td
                  className="px-2 py-2 max-w-[10rem] truncate text-gray-900"
                  title={r.horse_name ?? undefined}
                >
                  {r.horse_name ?? DASH}
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-gray-900">
                  {fmtOdds(r.win_odds)}
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-gray-900">
                  {fmtPct(r.win_probability)}
                </td>
                <td className={cn("px-2 py-2 text-right tabular-nums font-bold", evClass(r.ev))}>
                  {fmtEv(r.ev)}
                </td>
                <td className="px-2 py-2 text-right">
                  <FinishBadge pos={r.finish_position} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-gray-400 px-1 leading-relaxed">
        「評価」は tier を 0-100 の連続値にしたもの。降順に並べると tier 順が再現され、同じ tier の中でも順位がつく。⚠️「信頼度」(confidence_score) は指数差ベースの別指標で **tier とは対応しない**（tier の第一分岐は市場一致、信頼度は第二分岐でしか効かないため、信頼度が高くても tier が低いことがある）。
        馬番以降は<strong>そのレースの単勝1番人気馬</strong>。単勝EV = 単勝オッズ × 単勝率。
        着順は確定後に入り、未確定のレースは「-」。
      </p>
    </div>
  );
}
