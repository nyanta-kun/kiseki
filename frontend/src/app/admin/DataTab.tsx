"use client";

import { useEffect, useState, useTransition } from "react";
import { triggerFetchData } from "./actions";

type MonthCoverage = { year_month: string; race_count: number };
type YearCoverage = { year: string; months: MonthCoverage[]; total: number };
type CoverageData = { coverage: YearCoverage[]; total_races: number };

export function DataTab() {
  const [data, setData] = useState<CoverageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchingMonth, setFetchingMonth] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  async function loadCoverage() {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/data-coverage");
      if (res.ok) setData(await res.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCoverage();
  }, []);

  function handleFetch(yearMonth: string) {
    const fromYear = yearMonth.slice(0, 4);
    if (!confirm(`${fromYear}年以降のデータを取得します（Windows Agent の recent モード）。よろしいですか？`)) return;
    setFetchingMonth(yearMonth);
    startTransition(async () => {
      const result = await triggerFetchData(yearMonth);
      if (result.error) {
        alert(`取得指示に失敗しました: ${result.error}`);
      }
      setFetchingMonth(null);
    });
  }

  if (loading) {
    return <div className="py-8 text-center text-gray-400 text-sm">読み込み中...</div>;
  }
  if (!data) {
    return <div className="py-8 text-center text-red-400 text-sm">取得状況の読み込みに失敗しました</div>;
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">
        総レース数: <span className="font-medium text-[#0d1f35]">{data.total_races.toLocaleString()}</span> 件
      </p>
      <p className="text-xs text-gray-400">
        ※「取得」ボタンをクリックすると、その年以降のデータを Windows Agent の recent モードで再取得します。
      </p>
      {data.coverage.map((year) => (
        <div key={year.year}>
          <h3 className="font-medium text-sm text-[#0d1f35] mb-2">
            {year.year}年 （計 {year.total.toLocaleString()} 件）
          </h3>
          <div className="grid grid-cols-6 gap-1">
            {year.months.map((m) => {
              const month = m.year_month.slice(4);
              const isEmpty = m.race_count === 0;
              const isFetching = fetchingMonth === m.year_month;
              return (
                <div
                  key={m.year_month}
                  className={`rounded border p-2 text-xs text-center ${
                    isEmpty ? "border-red-200 bg-red-50" : "border-gray-200 bg-white"
                  }`}
                >
                  <div className="font-medium">{month}月</div>
                  <div className={isEmpty ? "text-red-500" : "text-gray-600"}>
                    {isEmpty ? "0" : m.race_count.toLocaleString()}件
                  </div>
                  {isEmpty && (
                    <button
                      onClick={() => handleFetch(m.year_month)}
                      disabled={isPending || fetchingMonth !== null}
                      className="mt-1 w-full text-xs bg-[#0d1f35] text-white rounded py-0.5 disabled:opacity-40 hover:bg-[#1a3560] transition-colors"
                    >
                      {isFetching ? "指示中..." : "取得"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
