"use client";

import { useCallback, useMemo, useSyncExternalStore } from "react";
import {
  HEIHACHI_CHANGE_EVENT,
  HEIHACHI_FALLBACK_DEFAULTS,
  HEIHACHI_STORAGE_KEY,
  HeihachiThresholds,
  parseThresholds,
  saveThresholds,
} from "./heihachi";

/** localStorage の生文字列を購読する（同一タブは CustomEvent、別タブは storage）。 */
function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === HEIHACHI_STORAGE_KEY) onChange();
  };
  window.addEventListener(HEIHACHI_CHANGE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(HEIHACHI_CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** 生文字列（参照が安定するのでスナップショットに使える）。読めなければ null。 */
function getSnapshot(): string | null {
  try {
    return window.localStorage.getItem(HEIHACHI_STORAGE_KEY);
  } catch {
    // プライベートブラウズ等で localStorage が触れない場合は既定値で動かす
    return null;
  }
}

/** SSR とハイドレーション初回は必ず既定値（保存値を見ない）。 */
function getServerSnapshot(): string | null {
  return null;
}

/**
 * 平八しきい値の共有フック [[jra_heihachi_badge]]
 *
 * localStorage を真実源にしているので、推奨ページのスライダーを動かすと
 * 同じタブのレース詳細バッジにも別タブにも即座に反映される。
 *
 * @param defaults サーバー由来の既定値（`/races/heihachi` の defaults）。
 *                 保存値がないときはこれがそのまま使われる。
 */
export function useHeihachiThresholds(
  defaults: HeihachiThresholds = HEIHACHI_FALLBACK_DEFAULTS,
): {
  thresholds: HeihachiThresholds;
  setThresholds: (t: HeihachiThresholds) => void;
} {
  const raw = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const thresholds = useMemo(() => parseThresholds(raw, defaults), [raw, defaults]);
  const setThresholds = useCallback((t: HeihachiThresholds) => saveThresholds(t), []);
  return { thresholds, setThresholds };
}
