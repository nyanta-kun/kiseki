"use client";

/**
 * 地方 推奨タブの激走馬一覧（並び替えつき）。
 *
 * 並びは「確率順」（複勝圏に入る確率の高い順）と「発走順」を切り替える。
 * 選択は端末ごとに localStorage へ覚える（見た目の好みなので共有しない）。
 *
 * ⚠️ 確率は較正済みだが、高いほど回収率が高いわけではない
 *    （前向き記録でどの帯も 0.68〜0.91）。確率順は「当たりやすい順」であって
 *    「買うべき順」ではない。
 */
import Link from "next/link";
import { useSyncExternalStore } from "react";
import type { ChihouGekisouPick } from "@/lib/api";
import { sortGekisouPicks, type GekisouSortMode } from "@/lib/chihouGekisou";
import { cn } from "@/lib/utils";
import { ChihouGekisouBadge } from "./ChihouGekisouBadge";

type SortMode = GekisouSortMode;
const STORAGE_KEY = "chihou-gekisou-sort";

function formatPostTime(t: string | null): string {
  if (!t || t.length < 4) return "-";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function formatOdds(v: number | null): string {
  return v === null ? "-" : `${v.toFixed(1)}倍`;
}

function formatProb(p: number | null): string {
  return p === null ? "-" : `${Math.round(p * 100)}%`;
}

function positionCell(pos: number | null): { text: string; cls: string } {
  if (pos === null) return { text: "-", cls: "text-gray-300" };
  if (pos === 1) return { text: "1着", cls: "text-amber-600 font-bold" };
  if (pos <= 3) return { text: `${pos}着`, cls: "text-blue-600 font-bold" };
  return { text: `${pos}着`, cls: "text-gray-400" };
}

function probClass(p: number | null): string {
  if (p === null) return "text-gray-300";
  if (p >= 0.3) return "text-rose-700 font-bold";
  if (p >= 0.2) return "text-rose-600";
  return "text-gray-500";
}

// 並びの選択。localStorage を外部ストアとして読む（SSR と初回描画は既定の確率順）。
// 保存できない環境（プライベートモード等）でも切り替えが効くよう、メモリにも持つ。
const DEFAULT_MODE: SortMode = "prob";
let memoryMode: SortMode | null = null;
const listeners = new Set<() => void>();

function readMode(): SortMode {
  if (memoryMode) return memoryMode;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "prob" || saved === "time") return saved;
  } catch {
    // 読めなくても既定で動く
  }
  return DEFAULT_MODE;
}

function writeMode(m: SortMode): void {
  memoryMode = m;
  try {
    window.localStorage.setItem(STORAGE_KEY, m);
  } catch {
    // 保存できなくても表示は切り替わる
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function ChihouGekisouTable({ picks }: { picks: ChihouGekisouPick[] }) {
  const mode = useSyncExternalStore(subscribe, readMode, () => DEFAULT_MODE);
  const choose = writeMode;

  const rows = sortGekisouPicks(picks, mode);

  return (
    <div>
      <div className="flex items-center gap-1.5 mb-2" role="group" aria-label="並び替え">
        <span className="text-[11px] text-gray-400 mr-0.5">並び</span>
        {(
          [
            ["prob", "確率順"],
            ["time", "発走順"],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            type="button"
            onClick={() => choose(m)}
            aria-pressed={mode === m}
            className={cn(
              "text-[11px] px-2.5 py-1 rounded-full border transition-colors whitespace-nowrap",
              mode === m
                ? "text-white border-transparent"
                : "text-gray-600 border-gray-200 bg-white hover:border-gray-300",
            )}
            style={mode === m ? { background: "var(--chihou-primary)" } : undefined}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-sm border-collapse min-w-[520px]">
          <thead>
            <tr className="text-xs text-gray-500 border-b border-gray-100">
              <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">発走</th>
              <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">競馬場</th>
              <th className="text-center py-1.5 px-1 font-medium">R</th>
              <th className="text-left py-1.5 px-1 font-medium">馬名</th>
              <th
                className="text-right py-1.5 px-1 font-medium whitespace-nowrap"
                title="複勝圏（3着以内）に入る確率。オッズから較正した値で、発走前はオッズとともに動く"
              >
                確率
              </th>
              <th className="text-center py-1.5 px-1 font-medium whitespace-nowrap">人気</th>
              <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">単オッズ</th>
              <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">複オッズ</th>
              <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">着順</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => {
              const pos = positionCell(p.finish_position);
              return (
                <tr
                  key={`${p.race_id}-${p.horse_number}`}
                  className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors"
                >
                  <td className="py-2 px-1 text-gray-500 whitespace-nowrap tabular-nums">
                    {formatPostTime(p.post_time)}
                  </td>
                  <td className="py-2 px-1 font-medium text-gray-700 whitespace-nowrap">
                    {p.course_name}
                  </td>
                  <td className="py-2 px-1 text-center text-gray-500">
                    <Link
                      href={`/chihou/races/${p.race_id}`}
                      className="hover:underline"
                      style={{ color: "var(--chihou-primary)" }}
                    >
                      {p.race_number}R
                    </Link>
                  </td>
                  <td className="py-2 px-1 font-semibold text-gray-800 whitespace-nowrap">
                    <span className="text-xs text-gray-400 mr-1">{p.horse_number}番</span>
                    {p.horse_name ?? "-"}
                    <span className="ml-1">
                      <ChihouGekisouBadge status="gekisou" provisional={p.source === "live"} />
                    </span>
                  </td>
                  <td className={cn("py-2 px-1 text-right tabular-nums", probClass(p.place_prob))}>
                    {formatProb(p.place_prob)}
                  </td>
                  <td className="py-2 px-1 text-center tabular-nums">
                    <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
                      {p.popularity ?? "-"}番人気
                    </span>
                  </td>
                  <td className="py-2 px-1 text-right text-gray-600 tabular-nums">
                    {formatOdds(p.win_odds)}
                  </td>
                  <td className="py-2 px-1 text-right text-gray-600 tabular-nums">
                    {formatOdds(p.place_odds)}
                  </td>
                  <td className={`py-2 px-1 text-right tabular-nums ${pos.cls}`}>{pos.text}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
