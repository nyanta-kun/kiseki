"use client";

import { useEffect, useState } from "react";
import type { OddsFreshness } from "@/lib/api";

/**
 * オッズ更新の鮮度バッジ（常時表示）。
 *
 * 🔴 **止まっていることは値を見ても分からない。**
 * 取得が止まっても DB には最後のスナップショットが残るので、API は 200 と
 * 「それらしい倍率」を返し続ける。2026-08-20 に地方のオッズが 14:55 から
 * 19:48 まで4時間51分止まったとき、発走直前でも画面には朝の値が出ていたのに
 * 異常を示すものが何も無く、公式オッズと見比べるまで誰も気づかなかった。
 *
 * ⚠️ **サーバの status をそのまま出してはいけない。**
 * ポーリングが失敗している・端末が圏外・API が落ちている場合、最後に受け取った
 * 「最新です」という応答が画面に残り続ける。**受信からの経過を足して判定し直す**
 * ことで、更新が届かなくなればバッジ自身が黄→赤へ進む。
 * 端末の時計が狂っていても影響しない（絶対時刻ではなく経過時間しか使わないため）。
 */

// しきい値は backend の `services/chihou_odds_freshness.py` と同値。
// 判定の正本はあちら。ここは「応答が届かない間も進む」ためだけの再判定。
const LIVE_MAX_SECONDS = 300;
const STALE_MIN_SECONDS = 900;

type Props = {
  freshness: OddsFreshness | null | undefined;
  /**
   * `freshness` をブラウザで受け取った時刻 (`Date.now()`)。
   * SSR 時は null を渡すこと（サーバとクライアントで値が変わりハイドレーションが
   * 不一致になるため、初回描画はサーバの age だけで行う）。
   */
  receivedAtMs: number | null;
};

type Tone = {
  label: string;
  dot: string;
  box: string;
  /** 経過時間を併記するか */
  showAge: boolean;
};

const TONES: Record<string, Tone> = {
  live:    { label: "オッズ最新",   dot: "bg-green-500",  box: "bg-green-50 text-green-700 border-green-200",   showAge: true  },
  delayed: { label: "更新遅延",     dot: "bg-amber-500",  box: "bg-amber-50 text-amber-700 border-amber-200",   showAge: true  },
  stale:   { label: "更新停止",     dot: "bg-red-500",    box: "bg-red-50 text-red-700 border-red-300 font-bold", showAge: true },
  missing: { label: "オッズ未取得", dot: "bg-gray-300",   box: "bg-gray-50 text-gray-500 border-gray-200",      showAge: false },
  closed:  { label: "発走済み",     dot: "bg-gray-400",   box: "bg-gray-50 text-gray-500 border-gray-200",      showAge: false },
};

/** 経過秒を「12秒前 / 7分前 / 4時間51分前」にする。 */
export function formatAge(seconds: number): string {
  if (seconds < 60) return `${seconds}秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分前`;
  const hours = Math.floor(minutes / 60);
  return `${hours}時間${minutes % 60}分前`;
}

/**
 * 受信からの経過を足した鮮度を返す。
 *
 * closed / missing は経過で変わらない（発走済みの停止は正常、未取得は未取得）。
 */
export function ageFreshness(
  status: OddsFreshness["status"],
  ageSeconds: number | null,
  extraSeconds: number,
): { status: OddsFreshness["status"]; ageSeconds: number | null } {
  if (status === "closed" || status === "missing" || ageSeconds === null) {
    return { status, ageSeconds };
  }
  const aged = ageSeconds + extraSeconds;
  const next =
    aged <= LIVE_MAX_SECONDS ? "live" : aged < STALE_MIN_SECONDS ? "delayed" : "stale";
  return { status: next, ageSeconds: aged };
}

export function OddsFreshnessBadge({ freshness, receivedAtMs }: Props) {
  // SSR と初回描画では 0。マウント後に受信からの経過へ差し替える
  // （Date.now() を初期値にするとハイドレーションが一致しない）。
  const [extraSeconds, setExtraSeconds] = useState(0);

  useEffect(() => {
    if (receivedAtMs === null) return;
    const tick = () =>
      setExtraSeconds(Math.max(0, Math.round((Date.now() - receivedAtMs) / 1000)));
    tick();
    // 10秒ごとに進める。隠れている間の抑制は気にしなくてよい
    // （復帰時に visibilitychange で即座に取り直すため）。
    const timer = setInterval(tick, 10_000);
    const onVisibility = () => {
      if (!document.hidden) tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [receivedAtMs]);

  if (!freshness) return null;

  const { status, ageSeconds } = ageFreshness(
    freshness.status,
    freshness.age_seconds,
    extraSeconds,
  );
  const tone = TONES[status] ?? TONES.missing;
  const ageText = tone.showAge && ageSeconds !== null ? formatAge(ageSeconds) : null;

  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] leading-none border rounded px-1.5 py-0.5 whitespace-nowrap ${tone.box}`}
      title={
        status === "stale"
          ? "オッズの更新が届いていません。表示中の倍率は古い可能性があります。"
          : status === "closed"
            ? "発走済みのため更新は終了しています"
            : undefined
      }
    >
      <span
        aria-hidden="true"
        className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${tone.dot} ${status === "stale" ? "animate-pulse" : ""}`}
      />
      {/* 状態だけを読み上げ対象にする。経過時間は10秒ごとに変わるので読み上げると煩い */}
      <span role="status" aria-live="polite">
        {tone.label}
      </span>
      {ageText && <span aria-hidden="true" className="opacity-70">{ageText}</span>}
    </span>
  );
}
