"use client";

import { logout } from "@/app/actions/auth";

export function LogoutButton() {
  return (
    <form action={logout}>
      <button
        type="submit"
        className="flex-shrink-0 text-blue-500 hover:text-blue-700 text-xs px-2.5 py-1 rounded border border-blue-300 hover:border-blue-500 hover:bg-blue-50 transition-colors"
        aria-label="ログアウト"
      >
        ログアウト
      </button>
    </form>
  );
}
