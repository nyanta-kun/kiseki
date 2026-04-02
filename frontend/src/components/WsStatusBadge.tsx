/**
 * WebSocket 接続状態バッジ。
 * 切断中のときのみ表示し、接続中は何も描画しない。
 */
type Props = {
  connected: boolean;
  /** バッジに表示するラベル。省略時は「再接続中…」 */
  label?: string;
};

export function WsStatusBadge({ connected, label = "再接続中…" }: Props) {
  if (connected) return null;

  return (
    <span
      role="status"
      aria-live="polite"
      className="text-[10px] text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5"
    >
      {label}
    </span>
  );
}
