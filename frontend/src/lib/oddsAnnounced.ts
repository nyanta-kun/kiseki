/**
 * オッズの**発表時刻**を「発表 14:32」の形にする（2026-09-29）。
 *
 * 鮮度バッジの経過時間は「API が受け取ってから」の時間で、UmaConn が古い
 * スナップショットを返していても新しく見える。発表時刻はデータそのものの時刻なので、
 * 楽天競馬など公式のオッズと見比べるときの基準になる。
 *
 * タイムゾーンは必ず Asia/Tokyo を明示する。端末の TZ に任せると海外端末でずれ、
 * SSR（コンテナは UTC）とブラウザで文字列が変わってハイドレーションも崩れる。
 */
export function formatAnnouncedJst(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const parts = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  const hh = parts.find((p) => p.type === "hour")?.value;
  const mm = parts.find((p) => p.type === "minute")?.value;
  if (!hh || !mm) return null;
  return `発表 ${hh}:${mm}`;
}
