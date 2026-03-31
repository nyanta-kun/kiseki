"use client";

import { indexBarColor } from "@/lib/utils";

type Props = {
  value: number | null;
  max?: number;
  showValue?: boolean;
};

export function IndexBar({ value, max = 100, showValue = false }: Props) {
  const pct = value !== null ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;

  return (
    <div className="flex items-center gap-1.5 min-w-[60px]">
      {showValue && (
        <span className="text-xs w-7 text-right tabular-nums text-gray-700">
          {value !== null ? value.toFixed(0) : "-"}
        </span>
      )}
      <div
        className="index-bar flex-1"
        role="progressbar"
        aria-valuenow={value ?? 0}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-label="指数バー"
      >
        <div
          className={`index-bar-fill ${indexBarColor(value)}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
