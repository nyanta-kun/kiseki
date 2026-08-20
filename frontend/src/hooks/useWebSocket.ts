"use client";

import { useEffect, useRef, useCallback, useState } from "react";

type Options = {
  /** 再接続間隔の初期値（ミリ秒）。デフォルト 5000ms。Exponential backoff で最大 60 秒まで伸長する。 */
  reconnectInterval?: number;
};

type UseWebSocketResult = {
  isConnected: boolean;
};

/**
 * WebSocket 接続を管理するカスタムフック。
 *
 * - Exponential backoff で再接続（初期 reconnectInterval → 最大 60 秒）
 * - **タブが隠れている間は接続しない**（下記）
 * - アンマウント時にクリーンアップ（タイムアウト・WebSocket を確実に破棄）
 *
 * ## バックグラウンド時に切断する理由（2026-08-20）
 *
 * iOS Safari で他アプリへ切り替えると OS がソケットを黙って切る。すると `onclose` →
 * バックオフ再接続に入るが、**`attemptRef` は `onopen` 成功時にしかリセットされない**
 * ため、切替を繰り返すと遅延が 5→10→20→40→最大60秒 と積み上がる。
 * 結果、復帰しても**最大1分間データが更新されない**。
 *
 * 加えて、開いたままの WebSocket は WebKit の bfcache（page cache）を阻害する。
 * 復帰が「即時復元」ではなくフルリロードになり、体感の遅さにつながる。
 *
 * → `hidden` で明示的に閉じ、`visible` で**バックオフをリセットして即再接続**する。
 *
 * @param url 接続先 WebSocket URL。`null` を渡すと接続しない。
 * @param onMessage メッセージ受信コールバック（`data` は JSON.parse 済みの値）
 * @param options オプション設定
 */
export function useWebSocket(
  url: string | null,
  onMessage: (data: unknown) => void,
  options?: Options,
): UseWebSocketResult {
  const reconnectInterval = options?.reconnectInterval ?? 5000;
  const MAX_INTERVAL = 60_000;

  const [isConnected, setIsConnected] = useState(false);

  // 最新の onMessage を ref で保持して、useEffect の再実行を防ぐ
  const onMessageRef = useRef(onMessage);
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  const mountedRef = useRef(true);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectRef = useRef<() => void>(() => {});
  const attemptRef = useRef(0);

  // 「隠れている間は接続しない」ためのフラグ。connect() から参照する
  const hiddenRef = useRef(false);

  const clearReconnect = useCallback(() => {
    if (reconnectRef.current !== null) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!url || !mountedRef.current) return;
    // 隠れている間は張らない（復帰時に visibilitychange から張り直す）
    if (hiddenRef.current) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        attemptRef.current = 0;
        setIsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data: unknown = JSON.parse(event.data as string);
          onMessageRef.current(data);
        } catch {
          // JSON パース失敗は無視
        }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setIsConnected(false);
        // 隠れている間は再接続を積まない。積むとバックオフだけが伸びて、
        // 復帰後に最大60秒つながらない状態になる
        if (hiddenRef.current) return;
        // Exponential backoff: reconnectInterval * 2^attempt（最大 MAX_INTERVAL）
        const delay = Math.min(
          reconnectInterval * Math.pow(2, attemptRef.current),
          MAX_INTERVAL,
        );
        attemptRef.current += 1;
        reconnectRef.current = setTimeout(() => connectRef.current(), delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // WebSocket 非対応環境など、接続例外は無視
    }
  }, [url, reconnectInterval]);

  // connectRef を最新の connect に同期
  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    if (!url) return;

    mountedRef.current = true;
    attemptRef.current = 0;
    connect();

    return () => {
      mountedRef.current = false;
      clearReconnect();
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        if (ws.readyState === WebSocket.CONNECTING) {
          ws.onopen = () => ws.close();
          ws.onclose = null;
          ws.onerror = null;
        } else {
          ws.close();
        }
      }
    };
    // connect が変わったとき（= url / reconnectInterval が変わったとき）だけ再実行
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connect]);

  // タブの表示状態に追従する
  useEffect(() => {
    if (!url || typeof document === "undefined") return;

    const onVisibility = () => {
      if (document.hidden) {
        hiddenRef.current = true;
        clearReconnect();
        const ws = wsRef.current;
        wsRef.current = null;
        if (ws) {
          // onclose 経由で再接続を積まないよう先に外す
          ws.onclose = null;
          ws.onerror = null;
          ws.close();
        }
        setIsConnected(false);
        return;
      }
      hiddenRef.current = false;
      // 復帰時はバックオフを捨てて即座に張り直す
      attemptRef.current = 0;
      clearReconnect();
      if (wsRef.current === null) connectRef.current();
    };

    document.addEventListener("visibilitychange", onVisibility);
    // マウント時点で既に隠れている場合に備えて初期状態も反映する
    hiddenRef.current = document.hidden;
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [url, clearReconnect]);

  return { isConnected };
}
