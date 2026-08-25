/**
 * 取り消した理由（2026-08-25）。一覧の「取消」バッジに出る。
 *
 * 🔴 **自由入力にしない。** 画面のボタンと1対1の固定文言にすることで、
 *    あとから「どの操作で消えたのか」を集計できる。
 * ⚠️ DB は varchar(255)。長い文言を足さないこと。
 *
 * 🔴 **`app/keirin/actions.ts`（"use server"）へ戻さないこと**（2026-08-26）。
 *    Server Action のファイルは **async 関数しか export できない**。定数を置くと
 *    `A "use server" file can only export async functions, found object.` が
 *    ランタイムで投げられ、**それを読み込むページ（/keirin/review）がまるごと
 *    500 になる**。実際 2026-08-26 07:43 に入稿の確認・公開ができなくなった。
 *    ⚠️ `next build` は通ってしまう。止められるのは eslint.config.mjs の
 *       `no-restricted-syntax`（"use server" ファイルの値 export 禁止）だけ。
 */
export const CANCEL_REASONS = {
  manual: "手動取消",
  forced: "強制取消",
  // ⚠️ `cheap: "平均払戻が安い"` は 2026-08-26 に廃止（入稿時の自動ゲートへ移行）。
  //    **過去の取消行にはこの文言が残っている**ので、集計するときは忘れないこと。
  venue: "場単位で取消",
  all: "全件取消",
} as const;

export type CancelReason = (typeof CANCEL_REASONS)[keyof typeof CANCEL_REASONS];
