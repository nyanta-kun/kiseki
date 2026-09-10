"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { DEFAULT_MENU_FLAGS, type MenuFlags } from "@/lib/menuAccess";
import { buildNavItems } from "./AppNav";

/**
 * スマホのボトムナビ。
 *
 * 🔴 項目は `buildNavItems`（AppNav）に一本化してある。ここで並べ直さないこと。
 *
 * ⚠️ 管理（/admin）はここに出さない。ヘッダとハンバーガーにあり、
 *    ボトムナビは横幅を等分するので枠を 1 つ増やすと全部が細くなる。
 *
 * ⚠️ ボトムナビは**横幅を等分**するので、項目が増えるほど 1 つが細くなる。
 *    実績・予想はハンバーガー側に任せ、ここには柱（中央・地方・競輪・POG）と
 *    マイページだけを出す。
 */
const BOTTOM_KEYS = new Set(["jra", "chihou", "keirin", "pog", "my"]);

export function BottomNav({
  access = DEFAULT_MENU_FLAGS,
  pogYear = null,
}: {
  access?: MenuFlags;
  pogYear?: number | null;
}) {
  const pathname = usePathname();
  const NAV_ITEMS = buildNavItems(
    access,
    pogYear,
    pathname.startsWith("/chihou"),
  ).filter((item) => BOTTOM_KEYS.has(item.key));

  // 見えるものがマイページだけなら、ボトムナビを出す意味が無い（画面下を
  // 14px ぶん占めるだけになる）。
  if (NAV_ITEMS.length <= 1) return null;

  return (
    <nav
      aria-label="ボトムナビゲーション"
      className="fixed bottom-0 left-0 right-0 z-20 flex md:hidden"
      style={{
        background: "#0d1f35",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      {NAV_ITEMS.map((item) => {
        const isActive = pathname.startsWith(item.matchPath);
        return (
          <Link
            key={item.key}
            href={item.href}
            aria-current={isActive ? "page" : undefined}
            className="flex-1 flex flex-col items-center justify-center gap-0.5 h-14 transition-colors"
          >
            <span className="text-xl leading-none" aria-hidden="true">
              {item.icon}
            </span>
            <span
              className="text-xs leading-none"
              style={{ color: isActive ? "#ffffff" : "rgba(255,255,255,0.5)" }}
            >
              {item.shortLabel}
            </span>
            {isActive && (
              <span
                className="w-1 h-1 rounded-full bg-white mt-0.5"
                aria-hidden="true"
              />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
