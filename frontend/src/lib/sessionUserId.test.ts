import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * セッションから DB のユーザー ID を取る場所を固定する。
 *
 * 🔴 **`session.user.id` は Auth.js 自身が振る ID**（Google の sub 由来）で、
 * `keiba.users.id` とは無関係。DB の ID は `session.user.db_id`。
 *
 * 取り違えても**型は通る**（`user.id` は string で存在する）。壊れ方は
 * 「ログインしているのにログイン画面へ飛ばされる」だけで理由が出ない。
 * 2026-09-10 に POG ドラフトで実際に踏み、ダミーのグループを画面から
 * 開いて初めて分かった。
 */
const FILES = [
  "src/app/pog/draftActions.ts",
  "src/app/pog/[year]/draft/page.tsx",
];

describe("セッションのユーザーID", () => {
  for (const rel of FILES) {
    it(`${rel} は db_id を使う`, () => {
      const src = readFileSync(join(process.cwd(), rel), "utf-8");
      expect(src).toContain("db_id");
      // `user.id` を DB の ID として使っていないこと。
      expect(src).not.toMatch(/session\??\.user\??\.id\b/);
    });
  }
});
