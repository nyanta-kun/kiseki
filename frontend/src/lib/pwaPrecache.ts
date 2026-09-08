/**
 * Service Worker の precache に**載せない**アセットの判定（2026-09-08 新設）。
 *
 * `next.config.ts` の `workboxOptions.exclude` から使う。webpack が吐いた
 * アセット名（`static/chunks/xxx.js` のように **`/_next/` が付かない**形）で判定する。
 *
 * 🔴 **`exclude` を自前で渡すと next-pwa の既定除外は効かなくなる**（2026-09-08 実測）。
 *    ドキュメント上は自前の配列の後ろに既定が足される読み方だが、実際にビルドすると
 *    `server/` 配下と各種 manifest が **precache に載ってしまう**（93件→26件に減らした
 *    ついでに、載るはずのない6件が増えた）。
 *
 *    これは黙って壊れる方向の事故になる: `/_next/server/...` は**公開されていない
 *    URL** なので、SW の install が取りに行って失敗し、**precache ごと install が
 *    失敗する**＝ SW が一切動かなくなる。既定の除外はここで明示的に持つ。
 */

/** `_next/static` 配下（内容ハッシュ付き・`immutable` で配信）。 */
const IMMUTABLE_STATIC = "static/";

/** サーバ専用アセット。公開 URL が無いので precache すると install が落ちる。 */
const SERVER_ONLY = "server/";

/** ビルド時のマニフェスト類（同上）。 */
const BUILD_MANIFESTS = /^(?:(?:app-)?build-manifest\.json|react-loadable-manifest\.json)$/;

export function isExcludedFromPrecache(assetName: string): boolean {
  return (
    assetName.startsWith(IMMUTABLE_STATIC) ||
    assetName.startsWith(SERVER_ONLY) ||
    BUILD_MANIFESTS.test(assetName)
  );
}
