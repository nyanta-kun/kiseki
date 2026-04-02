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
 * - アンマウント時にクリーンアップ（タイムアウト・WebSocket を確実に破棄）
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

  const clearReconnect = useCallback(() => {
    if (reconnectRef.current !== null) {
      clearTimeout(reconnectRef.current);
      reconnectRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!url || !mountedRef.current) return;

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

  return { isConnected };
}
