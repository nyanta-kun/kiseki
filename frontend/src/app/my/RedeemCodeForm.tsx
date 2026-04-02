"use client";

import { useActionState } from "react";
import { redeemCode } from "./actions";

export function RedeemCodeForm() {
  const [message, formAction, isPending] = useActionState(redeemCode, null);
  const isSuccess = message === "SUCCESS";
  const isError = message !== null && message !== "SUCCESS";

  return (
    <form action={formAction} className="space-y-3">
      <div className="flex gap-2">
        <input
          name="code"
          type="text"
          placeholder="招待コードを入力（例: ABC3XK9M2PQR）"
          maxLength={20}
          required
          disabled={isPending}
          className="flex-1 px-3 py-2 rounded-lg text-sm border border-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:opacity-50 font-mono tracking-wider uppercase"
          style={{ background: "#f9fafb" }}
        />
        <button
          type="submit"
          disabled={isPending}
          className="px-4 py-2 rounded-lg text-sm font-semibold bg-[#1a5c38] text-white hover:bg-[#14472c] transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
        >
          {isPending ? "確認中..." : "適用"}
        </button>
      </div>

      {isError && (
        <p className="text-xs px-3 py-2 rounded-lg border bg-red-50 border-red-200 text-red-700">
          {message}
        </p>
      )}

      {isSuccess && (
        <p className="text-xs px-3 py-2 rounded-lg border bg-green-50 border-green-200 text-green-700">
          コードが適用されました。ログアウト後に再ログインするとアクセスが有効になります。
        </p>
      )}
    </form>
  );
}
