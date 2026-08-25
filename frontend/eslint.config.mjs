import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// eslint-config-next は eslint-plugin-jsx-a11y を内包しているため
// jsxA11y.flatConfigs.recommended を別途追加すると plugin 二重定義エラーになる

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // 🔴 `"use server"` のファイルは **async 関数しか export できない**（Next.js の規約）。
    //    定数や型以外の値を置くと、そのモジュールを読み込むページがランタイムで
    //    `A "use server" file can only export async functions, found object.` を投げて
    //    **まるごと 500 になる**。2026-08-26 に /keirin/review（入稿の確認・公開）が
    //    これで落ち、入稿も公開もできなくなった（原因は `CANCEL_REASONS` の export）。
    //    ⚠️ `next build` は通ってしまう。CI で止められるのはこのルールだけ。
    //    定数は同階層の別ファイル（例: `app/keirin/cancelReasons.ts`）へ置くこと。
    files: ["src/app/**/actions.ts", "src/app/actions/*.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "ExportNamedDeclaration > VariableDeclaration",
          message:
            '"use server" のファイルで値を export しない（async 関数のみ）。' +
            "定数は別ファイルへ移すこと。",
        },
        {
          selector: "ExportNamedDeclaration > ClassDeclaration",
          message: '"use server" のファイルで class を export しない（async 関数のみ）。',
        },
      ],
    },
  },
]);

export default eslintConfig;
