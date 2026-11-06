"use client";

/**
 * レース詳細: 組合せ確率（三連複・馬連・ワイド）の上位。中央・地方の両方で使う（`scope`）。
 *
 * 値は能力分布モデル（勝率と複勝率の両方に合う着順の同時分布）。
 * Harville より当たりやすさの予測が良いことを確かめてから出している
 * （中央: 前向き 2026-09・計画 §5.5.1 / 地方: 前向き記録の VAL・§5.6.1）。
 * 🔴 当たりやすさの確率であって、回収率（期待値）の印ではない。オッズとは比べない。
 */

import { useEffect, useState } from "react";
import {
  FINISH_DIST_KINDS,
  FinishDist,
  FinishDistKind,
  FinishDistScope,
  comboPct,
  fetchFinishDistBrowser,
  formatCombo,
  harvilleNote,
} from "@/lib/finishDist";
import { cn } from "@/lib/utils";

const SHOW = 10;

export function FinishDistPanel({ raceId, scope = "jra" }: { raceId: number; scope?: FinishDistScope }) {
  const [data, setData] = useState<FinishDist | null>(null);
  const [failed, setFailed] = useState(false);
  const [kind, setKind] = useState<FinishDistKind>("trio");

  useEffect(() => {
    let alive = true;
    fetchFinishDistBrowser(raceId, SHOW, scope)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [raceId, scope]);

  if (failed || data === null) return null;
  const combos = data[kind];
  if (combos.length === 0) return null;
  const max = combos[0].probability;

  return (
    <section className="mt-4 rounded-lg border border-gray-100 p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <h3 className="text-xs font-bold text-gray-700 whitespace-nowrap">組合せの確率（上位{SHOW}）</h3>
        <div className="flex gap-1">
          {FINISH_DIST_KINDS.map((k) => (
            <button
              key={k.key}
              onClick={() => setKind(k.key)}
              className={cn(
                "text-[10px] px-2 py-0.5 rounded-full border transition-colors whitespace-nowrap",
                kind === k.key
                  ? "text-white border-green-600 bg-green-700"
                  : "text-gray-500 border-gray-200 hover:border-green-400 bg-white",
              )}
            >
              {k.label}
            </button>
          ))}
        </div>
      </div>
      <ol className="space-y-1">
        {combos.map((c) => (
          <li key={c.horses.join("-")} className="flex items-center gap-2 text-xs">
            <span className="min-w-[4.5rem] font-mono text-gray-800 whitespace-nowrap">{formatCombo(c.horses)}</span>
            <span className="flex-1 h-1.5 rounded bg-gray-100 overflow-hidden">
              <span
                className="block h-full bg-green-500"
                style={{ width: `${max > 0 ? Math.max(2, (c.probability / max) * 100) : 0}%` }}
              />
            </span>
            <span className="min-w-[3rem] text-right text-gray-600 whitespace-nowrap">{comboPct(c.probability)}</span>
          </li>
        ))}
      </ol>
      <p className="mt-2 text-[10px] text-gray-400">
        勝率と複勝率の両方に合うように作った着順の分布から計算した、組合せが来る確率です。
        当たりやすさの目安で、回収率を示すものではありません。
        {harvilleNote(data) && ` ${harvilleNote(data)}`}
      </p>
    </section>
  );
}
