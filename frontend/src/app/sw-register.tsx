"use client";

/**
 * 残っている Service Worker を解除し、旧 SW のキャッシュを消す（2026-10-15）。
 *
 * 🔴 **このサイトは SW を使わない。** 2026-10-15 に撤去した。
 *
 * iPhone で「しばらく放置してから再表示すると描画まで時間がかかる」という報告。
 * #507（2026-09-08）で `_next/static` を SW から外したあとも再発していた。
 * nginx のアクセスログで「HTML が届いてから最初の API まで」を、同じ端末の
 * 10/12〜10/15 の全ナビゲーションについて測った:
 *
 *   SW の起動・更新確認（/sw.js）から始まる回  42件  中央値 11秒（最大 29秒）
 *   SW が起きたままの回                        28件  中央値  1秒
 *   9秒以上かかった 24件 → 全件で /sw.js が先行していた
 *
 * SW は撤去前もページ・RSC・API のすべてのリクエストを仲介していた。iOS は
 * 放置された SW を終了させるので、戻るたびに起動から待つことになる。
 * 加えて next-pwa の既定 `reloadOnOnline: true` が復帰時の `online` イベントで
 * `location.reload()` を呼び、約4割の回で HTML を**2回**読み込んでいた。
 *
 * SW が残す価値は無かった: 認証と API が必須なのでオフラインでは元々動かない。
 * `_next/static` は `immutable` の HTTP キャッシュで完結している。HTML/RSC を
 * 3秒で諦めて古いキャッシュを返す挙動は、陳腐化の原因にもなっていた。
 * ホーム画面への追加は manifest だけで成り立つ（SW は不要）。
 *
 * 旧 SW を持つ端末は 2 経路で掃除する:
 *   - `public/sw.js`（自分を消すだけの SW）へ更新されて自滅する
 *   - ここでも登録解除とキャッシュ削除を行う（更新確認を待たずに済ませる）
 *
 * ⚠️ **SW を戻すときは実機で測ること。** 印象ではなく nginx ログの
 *    「HTML → 初 API」で、間を空けた回だけを見る。
 */

import { useEffect } from "react";

export default function ServiceWorkerCleanup() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    void (async () => {
      try {
        const registrations = await navigator.serviceWorker.getRegistrations();
        if (registrations.length === 0) return;
        await Promise.all(registrations.map((r) => r.unregister()));
        if ("caches" in window) {
          const keys = await caches.keys();
          await Promise.all(keys.map((key) => caches.delete(key)));
        }
      } catch {
        // 掃除に失敗しても表示には影響しない。次の読み込みでやり直す。
      }
    })();
  }, []);

  return null;
}
