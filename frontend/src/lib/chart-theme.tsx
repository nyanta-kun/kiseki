/**
 * グラフの配色を1か所にまとめる（2026-09-03 新設）。
 *
 * ## なぜ要るか
 *
 * 🔴🔴 **Recharts の既定ツールチップは「白い面 ＋ 系列色そのままの文字」**。
 *    暗いテーマでは面ごと浮き、明るいテーマでも淡い系列（`#c7d2fe` / `#d1d5db`）が
 *    白地に載って**読めない**。2026-09-03 に競輪の売上グラフで
 *    「販売無償pt」「内訳不明」「日付」が実際に消えていた。
 *
 * 🔴 **系列色を文字色にしない。** 系列は**色見本（swatch）**で表し、文字は
 *    常に `--chart-fg`（面に対して 4.5:1 以上）で描く。こうすると
 *    「淡い系列を足したら読めなくなった」という壊れ方が起きない。
 *
 * ⚠️ `contentStyle` に色を書くだけでは足りない（項目の文字色は系列色のまま）。
 *    面・枠・文字を全部こちらで描くために `content` を差し替える。
 */
import type { ReactNode } from "react";

/** グリッド線。`#f0f0f0` の直書きは暗いテーマで消える。 */
export const CHART_GRID = "var(--chart-grid)";

/** 軸の目盛り。 */
export const chartAxisTick = (fontSize = 10) => ({ fontSize, fill: "var(--chart-axis)" });

/** 凡例。**色を直書きしない**（`color: "#111827"` は暗いテーマで消える）。 */
export const chartLegendStyle = (fontSize = 11, paddingTop = 8) => ({
  fontSize,
  paddingTop,
  color: "var(--chart-fg)",
});

type Entry = {
  name?: string | number;
  value?: string | number;
  color?: string;
  dataKey?: string | number;
};

export type ChartTooltipProps = {
  active?: boolean;
  payload?: Entry[];
  label?: string | number;
  /** Recharts の `formatter` と同じ形（`[表示値, 表示名]` を返す）。 */
  formatter?: (value: string | number, name: string | number) => [ReactNode, ReactNode];
  labelFormatter?: (label: string | number) => ReactNode;
  /** 値が数値のとき既定で3桁区切りにする。`formatter` があればそちらが優先。 */
  localize?: boolean;
};

/**
 * 共通のツールチップ。`<Tooltip content={<ChartTooltip … />} />` で使う。
 *
 * Recharts は `content` に渡した要素を `active` / `payload` / `label` 付きで
 * クローンするので、こちらで受け取るのは整形の指定だけでよい。
 */
export function ChartTooltip({
  active, payload, label, formatter, labelFormatter, localize = true,
}: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div
      className="rounded-lg border px-2.5 py-2 text-xs shadow-lg"
      style={{
        background: "var(--chart-surface)",
        borderColor: "var(--chart-border)",
        color: "var(--chart-fg)",
      }}
    >
      {label !== undefined && label !== "" && (
        <p className="font-semibold mb-1" style={{ color: "var(--chart-fg)" }}>
          {labelFormatter ? labelFormatter(label) : label}
        </p>
      )}
      <div className="space-y-0.5">
        {payload.map((e, i) => {
          const name = e.name ?? e.dataKey ?? "";
          const raw = e.value ?? "";
          const [v, n] = formatter
            ? formatter(raw, name)
            : [localize && typeof raw === "number" ? raw.toLocaleString() : raw, name];
          return (
            <div key={`${String(name)}-${i}`} className="flex items-center gap-1.5">
              {/* 🔴 系列は**色見本**で表す。文字色にすると淡い系列が読めなくなる。 */}
              <span
                aria-hidden
                className="inline-block w-2 h-2 rounded-[2px] flex-shrink-0"
                style={{ background: e.color ?? "var(--chart-muted)" }}
              />
              <span style={{ color: "var(--chart-muted)" }}>{n}</span>
              <span className="tabular-nums ml-auto pl-3" style={{ color: "var(--chart-fg)" }}>
                {v}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
