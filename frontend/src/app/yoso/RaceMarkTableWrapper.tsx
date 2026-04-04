"use client";

import { useState, useTransition } from "react";
import type { YosoRace, YosoPrediction } from "@/lib/api";
import { savePrediction } from "@/app/actions/yoso";

const MARKS = ["◎", "○", "▲", "△", "×"] as const;
type Mark = typeof MARKS[number];

const MARK_COLOR: Record<Mark, string> = {
  "◎": "text-red-600 font-bold",
  "○": "text-blue-600 font-bold",
  "▲": "text-orange-500 font-bold",
  "△": "text-green-600 font-bold",
  "×": "text-gray-500",
};

const FINISH_COLOR: Record<number, string> = {
  1: "text-yellow-700 font-bold",
  2: "text-gray-600 font-semibold",
  3: "text-orange-600 font-semibold",
};

type Props = {
  race: YosoRace;
  canInputIndex: boolean;
};

export function RaceMarkTableWrapper({ race, canInputIndex }: Props) {
  const [predictions, setPredictions] = useState<Map<number, YosoPrediction>>(
    new Map(race.horses.map((h) => [h.horse_id, h]))
  );
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [indexInput, setIndexInput] = useState<string>("");
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  // 占有率再計算
  const totalIndex = [...predictions.values()]
    .map((p) => p.user_index ?? 0)
    .reduce((a, b) => a + b, 0);

  const handleMarkChange = (horseId: number, mark: Mark | null) => {
    const pred = predictions.get(horseId)!;
    const newMark = pred.mark === mark ? null : mark;

    startTransition(async () => {
      const result = await savePrediction(race.race_id, horseId, newMark, pred.user_index);
      if (result.ok) {
        setPredictions((prev) => {
          const next = new Map(prev);
          next.set(horseId, { ...pred, mark: newMark });
          return next;
        });
      } else {
        setMessage(result.error ?? "保存失敗");
        setTimeout(() => setMessage(null), 3000);
      }
    });
  };

  const handleIndexSave = (horseId: number) => {
    const pred = predictions.get(horseId)!;
    const parsed = indexInput === "" ? null : parseFloat(indexInput);
    if (parsed !== null && isNaN(parsed)) {
      setMessage("数値を入力してください");
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    startTransition(async () => {
      const result = await savePrediction(race.race_id, horseId, pred.mark, parsed);
      if (result.ok) {
        setPredictions((prev) => {
          const next = new Map(prev);
          next.set(horseId, { ...pred, user_index: parsed });
          return next;
        });
        setEditingIndex(null);
      } else {
        setMessage(result.error ?? "保存失敗");
        setTimeout(() => setMessage(null), 3000);
      }
    });
  };

  const hasOtherUsers = race.other_users.length > 0;

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
      {/* レースヘッダー */}
      <div className="px-4 py-2.5 border-b border-gray-100" style={{ background: "#f8fafc" }}>
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-gray-500 w-6 text-center">
            {race.race_number}R
          </span>
          <span className="text-sm font-semibold text-gray-800 truncate">
            {race.race_name ?? `第${race.race_number}レース`}
          </span>
          <span className="text-xs text-gray-400 ml-auto">{race.course_name}</span>
        </div>
      </div>

      {message && (
        <div className="px-4 py-1.5 text-xs text-red-600 bg-red-50 border-b border-red-100">
          {message}
        </div>
      )}

      {/* テーブル */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 border-b border-gray-100">
              <th className="px-2 py-1.5 text-center w-8">枠</th>
              <th className="px-2 py-1.5 text-center w-8">馬番</th>
              <th className="px-2 py-1.5 text-left">馬名</th>
              <th className="px-2 py-1.5 text-center w-24">印</th>
              {canInputIndex && (
                <>
                  <th className="px-2 py-1.5 text-right w-16">自指数</th>
                  <th className="px-2 py-1.5 text-right w-14">占有率</th>
                </>
              )}
              <th className="px-2 py-1.5 text-right w-16">AI指数</th>
              {hasOtherUsers && race.other_users.map((u) => (
                <th key={u.user_id} className="px-2 py-1.5 text-center w-14 text-blue-400">
                  {u.yoso_name.slice(0, 4)}
                </th>
              ))}
              <th className="px-2 py-1.5 text-right w-14">単オッズ</th>
              <th className="px-2 py-1.5 text-right w-14">複オッズ</th>
              <th className="px-2 py-1.5 text-center w-8">着</th>
            </tr>
          </thead>
          <tbody>
            {race.horses.map((horse) => {
              const pred = predictions.get(horse.horse_id) ?? horse;
              const share = (pred.user_index != null && totalIndex > 0)
                ? pred.user_index / totalIndex
                : null;
              const finishColor = pred.finish_position ? FINISH_COLOR[pred.finish_position] : "text-gray-700";

              return (
                <tr
                  key={horse.horse_id}
                  className={`border-b border-gray-50 hover:bg-blue-50/30 transition-colors ${
                    pred.finish_position === 1 ? "bg-yellow-50/40" : ""
                  }`}
                >
                  {/* 枠番 */}
                  <td className="px-2 py-2 text-center text-gray-500">{horse.frame_number}</td>
                  {/* 馬番 */}
                  <td className="px-2 py-2 text-center font-mono font-semibold text-gray-700">
                    {horse.horse_number}
                  </td>
                  {/* 馬名 */}
                  <td className="px-2 py-2 text-gray-800 max-w-[120px] truncate">{horse.horse_name}</td>
                  {/* 印セレクター */}
                  <td className="px-1 py-1.5">
                    <div className="flex gap-0.5 justify-center">
                      {MARKS.map((m) => (
                        <button
                          key={m}
                          onClick={() => handleMarkChange(horse.horse_id, m)}
                          disabled={isPending}
                          className={`w-6 h-6 text-xs rounded transition-all ${
                            pred.mark === m
                              ? "bg-gray-800 text-white shadow-sm"
                              : "bg-gray-100 hover:bg-gray-200 text-gray-500"
                          } ${pred.mark === m ? MARK_COLOR[m].replace(/text-\w+-\d+/, "text-white") : ""}`}
                          aria-label={m}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                  </td>

                  {/* 自分の指数 */}
                  {canInputIndex && (
                    <>
                      <td className="px-2 py-1.5 text-right">
                        {editingIndex === horse.horse_id ? (
                          <div className="flex items-center gap-1 justify-end">
                            <input
                              type="number"
                              step="0.1"
                              value={indexInput}
                              onChange={(e) => setIndexInput(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleIndexSave(horse.horse_id);
                                if (e.key === "Escape") setEditingIndex(null);
                              }}
                              className="w-16 border border-blue-300 rounded px-1 py-0.5 text-xs text-right focus:outline-none focus:ring-1 focus:ring-blue-400"
                              autoFocus
                            />
                            <button
                              onClick={() => handleIndexSave(horse.horse_id)}
                              className="text-blue-500 hover:text-blue-700 text-xs"
                            >
                              ✓
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => {
                              setEditingIndex(horse.horse_id);
                              setIndexInput(pred.user_index?.toString() ?? "");
                            }}
                            className="w-full text-right text-gray-700 hover:text-blue-600 hover:underline"
                          >
                            {pred.user_index != null ? pred.user_index.toFixed(1) : <span className="text-gray-300">—</span>}
                          </button>
                        )}
                      </td>
                      {/* 占有率 */}
                      <td className="px-2 py-2 text-right text-gray-500">
                        {share != null ? `${(share * 100).toFixed(1)}%` : <span className="text-gray-200">—</span>}
                      </td>
                    </>
                  )}

                  {/* GallopLab AI指数 */}
                  <td className="px-2 py-2 text-right font-mono text-blue-700">
                    {pred.galloplab_index != null
                      ? pred.galloplab_index.toFixed(1)
                      : <span className="text-gray-200">—</span>}
                  </td>

                  {/* 他ユーザー印 */}
                  {hasOtherUsers && race.other_users.map((ou) => {
                    const op = ou.predictions.find((p) => p.horse_id === horse.horse_id);
                    return (
                      <td key={ou.user_id} className="px-2 py-2 text-center">
                        {op?.mark ? (
                          <span className={MARK_COLOR[op.mark as Mark] ?? "text-gray-700"}>
                            {op.mark}
                          </span>
                        ) : <span className="text-gray-200">—</span>}
                      </td>
                    );
                  })}

                  {/* オッズ */}
                  <td className="px-2 py-2 text-right font-mono text-gray-700">
                    {pred.win_odds != null ? pred.win_odds.toFixed(1) : <span className="text-gray-200">—</span>}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-gray-600">
                    {pred.place_odds != null ? pred.place_odds.toFixed(1) : <span className="text-gray-200">—</span>}
                  </td>

                  {/* 着順 */}
                  <td className={`px-2 py-2 text-center font-mono ${finishColor}`}>
                    {pred.finish_position ?? <span className="text-gray-200">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
