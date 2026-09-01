import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

/**
 * フロントのテスト設定。
 *
 * ⚠️ 拡張子は **.mts**。Vite が将来 native config loader を既定にすると
 *    .ts の ESM 構文が CJS として読まれて壊れる（導入時に実測で警告が出た）。
 *    package.json に "type": "module" を足す手もあるが、Next.js の他の
 *    設定ファイルに波及するのでこちらを選んだ。
 *
 * 🔴 **node 環境のみ。jsdom もテスティングライブラリも入れていない。**
 *    対象は「DOM を使わない純ロジック」だけに絞る方針（2026-09-02 決定）。
 *    コンポーネントの描画は backend/tests 側の静的検査で守る
 *    （`test_frontend_display_flags_reachable.py` = 表示フラグに到達可能な
 *    描画先があるか）。実際にその型のバグ（is_sweet_spot の赤字表示が
 *    死んだファイルにしか無かった）を捕まえた実績がある。
 *
 *    コンポーネントの描画までテストしたくなったら jsdom +
 *    @testing-library/react を足すことになるが、依存とメンテのコストが
 *    大きいので、必要になってから判断する。
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
