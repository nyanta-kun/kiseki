"use client";

import styles from "./RangeSlider.module.css";

/**
 * つまみ2つの範囲スライダー。
 *
 * 単勝オッズのように「下限と上限が同じ単位の1つの量」を、2本のスライダーでなく
 * 1本の範囲指定として扱うために使う。依存を増やさないよう、ネイティブの
 * input[type=range] を2本重ねて実装している（つまみだけ pointer-events を通す）。
 *
 * 下限と上限は `minGap` ぶん必ず離す。潰れると「常に0件」になって
 * 何も表示されない状態から抜け出せなくなるため。
 */

type Props = {
  label: string;
  lower: number;
  upper: number;
  range: { min: number; max: number; step: number };
  /** 下限と上限の間に必ず空ける幅 */
  minGap: number;
  /** ラベル右側に出す現在値の文字列 */
  display: string;
  onChange: (lower: number, upper: number) => void;
};

export function RangeSlider({ label, lower, upper, range, minGap, display, onChange }: Props) {
  const span = range.max - range.min;
  const pos = (v: number) => ((v - range.min) / span) * 100;

  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-gray-700">{label}</span>
        <span className="font-bold text-amber-900 tabular-nums">{display}</span>
      </div>
      <div className={styles.wrap}>
        <div className={styles.track} />
        <div
          className={styles.fill}
          style={{ left: `${pos(lower)}%`, right: `${100 - pos(upper)}%` }}
        />
        <input
          type="range"
          className={`${styles.input} ${styles.lower}`}
          min={range.min}
          max={range.max}
          step={range.step}
          value={lower}
          aria-label={`${label} 下限`}
          onChange={(e) => onChange(Math.min(Number(e.target.value), upper - minGap), upper)}
        />
        <input
          type="range"
          className={`${styles.input} ${styles.upper}`}
          min={range.min}
          max={range.max}
          step={range.step}
          value={upper}
          aria-label={`${label} 上限`}
          onChange={(e) => onChange(lower, Math.max(Number(e.target.value), lower + minGap))}
        />
      </div>
    </div>
  );
}
