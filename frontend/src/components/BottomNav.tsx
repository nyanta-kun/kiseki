"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  icon: string;
  label: string;
  href: string;
  matchPath: string;
};

// 競輪は admin 限定（2026-08-03・AppNav/HamburgerMenu/proxy.ts と同基準）。
// netkeirin入稿トリガー等を含むため一般メンバーには導線を出さない。
function navItems(isAdmin: boolean): NavItem[] {
  return [
    { icon: "🏇", label: "中央", href: "/races", matchPath: "/races" },
    { icon: "🏘", label: "地方", href: "/chihou/races", matchPath: "/chihou" },
    ...(isAdmin ? [{ icon: "🚴", label: "競輪", href: "/keirin", matchPath: "/keirin" }] : []),
    { icon: "👤", label: "マイページ", href: "/my", matchPath: "/my" },
  ];
}

export function BottomNav({ isAdmin = false }: { isAdmin?: boolean }) {
  const pathname = usePathname();
  const NAV_ITEMS = navItems(isAdmin);

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
            key={item.label}
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
              {item.label}
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
