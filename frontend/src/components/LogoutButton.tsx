"use client";

import { logout } from "@/app/actions/auth";

export function LogoutButton() {
  return (
    <form action={logout}>
      <button
        type="submit"
        className="flex-shrink-0 text-blue-200 hover:text-white text-xs px-2.5 py-1 rounded border border-blue-400/40 hover:border-white/40 hover:bg-white/10 transition-colors"
        aria-label="ログアウト"
      >
        ログアウト
      </button>
    </form>
  );
}
