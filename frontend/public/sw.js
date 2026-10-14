/*
 * Service Worker の撤去用（2026-10-15）。**fetch を一切扱わず、自分を消すだけ**。
 *
 * 経緯は `src/app/sw-register.tsx` の冒頭コメント。要点:
 * iPhone で間を空けて戻ると、SW の起動・更新確認から始まる回だけ
 * 「HTML → 最初の API」が 11〜29 秒かかっていた（続けて操作中は 1 秒）。
 *
 * 旧 SW（next-pwa / Workbox）を登録済みの端末は、ナビゲーションのたびに
 * この URL を更新確認として取りに来る。中身が変わっていれば新しい SW として
 * install → activate されるので、ここで:
 *   1. 旧 SW が溜めた Cache Storage を全部消す
 *   2. 自分の登録を解除する
 * 以後その端末では SW が動かない。
 *
 * 🔴 **fetch リスナーを足さないこと。** 足すとすべてのリクエストが再び SW を
 *    経由し、iOS の SW 起動待ちが戻る（`swRemoval.test.ts` が固定している）。
 * 🔴 **クライアントをリロード（`client.navigate`）させないこと。** 復帰時の
 *    強制リロードこそ消したかった症状の一つ。現在のページは fetch ハンドラの
 *    無い SW の下でそのまま動き、次の読み込みから SW 無しになる。
 */
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      try {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      } finally {
        await self.registration.unregister();
      }
    })(),
  );
});
