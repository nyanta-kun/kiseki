"use client";

/**
 * 競輪の画面を開いたまま結果が入ったときに、自動で追従させるためのフック
 * （2026-09-03・ユーザー要望「ページ表示中に結果が更新されたら自動で更新」）。
 *
 * 採点は VPS の cron が **15分ごと**（`5,20,35,50 8-23,0`・前日+当日）に走る。
 * それまで一覧も入稿確認も**開いた瞬間の値で固まっていた**ので、結果が入っても
 * 手で更新ボタンを押すまで気づけなかった。
 *
 * 🔴 **`frontend/src/lib/` へ置かない。** そこは `pillars.sh` が `jra` 柱と判定するため、
 *    競輪の変更が柱をまたぐ PR になる（1ブランチ=1柱の原則）。競輪しか使わない
 *    うちは競輪の下に置く。他の柱でも要るようになったら shared として切り出すこと。
 */
import { useEffect, useRef } from "react";

/** 既定の間隔。採点 cron が15分ごとなので、これより細かくしても得るものは無い。 */
export const AUTO_REFRESH_MS = 60_000;

/**
 * いま取りに行ってよいか。**純関数**（テストできるようにフックから切り出してある）。
 *
 * - `visible`  … タブが見えているか。隠れている間に投げても誰も見ないので止める
 * - `live`     … その画面の内容がまだ動くか（過去日は動かないので止める）
 * - `busy`     … 手動更新や承認処理が進行中か。重ねると結果が入れ替わって見える
 */
export function shouldPoll(o: { visible: boolean; live: boolean; busy: boolean }): boolean {
  return o.visible && o.live && !o.busy;
}

/**
 * `run` を定期的に呼ぶ。タブが**見えるようになった瞬間にも1回呼ぶ**
 * （戻ってきたときに古い値が残っていると、自動更新の意味が無い）。
 *
 * ⚠️ `run` は ref 経由で呼ぶ。依存に入れると、呼び出し側が毎描画で新しい関数を
 *    渡したときに**間隔が毎回リセットされて永久に発火しない**。
 */
export function useAutoRefresh(
  run: () => void,
  opts: { live: boolean; busy?: boolean; intervalMs?: number },
): void {
  const { live, busy = false, intervalMs = AUTO_REFRESH_MS } = opts;
  const runRef = useRef(run);
  // 判定材料も ref で見る（間隔を張り直さずに最新の値を使うため）
  const stateRef = useRef({ live, busy });
  // 🔴 **描画中に ref を書き換えないこと**（`react-hooks/refs`）。並行レンダリングでは
  //    描画が捨てられたり巻き戻ったりするので、副作用は effect の中で行う。
  //    依存配列を付けない＝毎描画のあとに最新へ入れ替える。
  useEffect(() => {
    runRef.current = run;
    stateRef.current = { live, busy };
  });

  useEffect(() => {
    if (typeof document === "undefined") return;
    const visible = () => document.visibilityState === "visible";
    const maybeRun = () => {
      if (shouldPoll({ visible: visible(), ...stateRef.current })) runRef.current();
    };
    const id = setInterval(maybeRun, intervalMs);
    const onVisible = () => { if (visible()) maybeRun(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs]);
}
