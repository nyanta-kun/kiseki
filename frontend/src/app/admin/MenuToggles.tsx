"use client";

import { useState, useTransition } from "react";

import type { User } from "./AdminTabs";
import { updateUser } from "./actions";
import { normalizeMenuFlags, type MenuFlags, type MenuKey } from "@/lib/menuAccess";

/**
 * ユーザー 1 人ぶんの表示メニュー切り替え。
 *
 * 🔴 **規則の実装は `lib/menuAccess.ts` にある。** ここはその写しを描くだけ。
 * POG を入れると中央・地方が一緒に点き、その 2 つは POG が入っている間
 * 外せなくなる（外せてしまうと POG の順位表から張ったレース詳細が全部
 * ガードに弾かれる）。
 *
 * ⚠️ 楽観更新している。押した瞬間に見た目を変え、サーバが失敗したら戻す。
 * 9 人ぶんのトグルを 1 つ押すたびにテーブル全体が再取得されるのを避けるため。
 */

const ITEMS: { key: MenuKey; label: string; hint: string }[] = [
  { key: "pog", label: "POG", hint: "POG（中央・地方も自動で ON になる）" },
  { key: "jra", label: "中央", hint: "中央競馬（実績・予想を含む）" },
  { key: "chihou", label: "地方", hint: "地方競馬（地方の実績を含む）" },
  { key: "keirin", label: "競輪", hint: "競輪（現在は admin のみ有効）" },
];

function flagsOf(user: User): MenuFlags {
  return {
    pog: user.menu_pog,
    jra: user.menu_jra,
    chihou: user.menu_chihou,
    keirin: user.menu_keirin,
  };
}

export function MenuToggles({ user }: { user: User }) {
  const [flags, setFlags] = useState<MenuFlags>(() => flagsOf(user));
  const [error, setError] = useState<string | null>(null);
  const [, startTransition] = useTransition();
  const isAdmin = user.role === "admin";

  function toggle(key: MenuKey) {
    const next = normalizeMenuFlags({ ...flags, [key]: !flags[key] });
    const previous = flags;
    setFlags(next);
    setError(null);
    startTransition(async () => {
      const result = await updateUser(user.id, {
        menu_pog: next.pog,
        menu_jra: next.jra,
        menu_chihou: next.chihou,
        menu_keirin: next.keirin,
      });
      if (result.error) {
        setFlags(previous);
        setError(result.error);
      }
    });
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1">
        {ITEMS.map(({ key, label, hint }) => {
          const on = flags[key];
          // POG が ON の間は中央・地方を外せない（規則そのもの）。
          const locked = flags.pog && (key === "jra" || key === "chihou");
          return (
            <button
              key={key}
              type="button"
              onClick={() => toggle(key)}
              disabled={locked}
              aria-pressed={on}
              title={locked ? "POG が ON の間は外せません" : hint}
              className={`px-2 py-0.5 rounded-full text-xs font-medium border transition-colors ${
                on
                  ? "bg-blue-600 text-white border-blue-600 hover:bg-blue-700"
                  : "bg-white text-gray-400 border-gray-200 hover:border-gray-300 hover:text-gray-600"
              } ${locked ? "opacity-70 cursor-not-allowed" : ""}`}
            >
              {label}
            </button>
          );
        })}
      </div>
      {isAdmin && (
        // 保存値と実際の見え方が食い違う唯一のケース。黙っていると
        // 「OFF にしたのに見えている」と誤解される。
        <span className="text-[10px] text-purple-600">
          admin は設定に関わらず全表示
        </span>
      )}
      {flags.keirin && !isAdmin && (
        <span className="text-[10px] text-amber-600">
          競輪は現在 admin 限定のため未反映
        </span>
      )}
      {error && <span className="text-[10px] text-red-600">{error}</span>}
    </div>
  );
}
