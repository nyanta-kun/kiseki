"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { HamburgerMenu } from "./HamburgerMenu";

type Props = { isAdmin: boolean };

export function AppNav({ isAdmin }: Props) {
  const pathname = usePathname();
  const isChihou = pathname.startsWith("/chihou");

  const NAV_ITEMS = [
    { label: "レース", href: isChihou ? "/chihou/races" : "/races", matchPath: isChihou ? "/chihou/races" : "/races" },
    { label: "実績", href: isChihou ? "/chihou/results" : "/results", matchPath: isChihou ? "/chihou/results" : "/results" },
    { label: "予想", href: "/yoso", matchPath: "/yoso" },
    { label: "マイページ", href: "/my", matchPath: "/my" },
  ];

  const inactiveCls = isChihou
    ? "text-green-200 hover:text-white border-green-400/40 hover:border-white/40 hover:bg-white/10"
    : "text-blue-200 hover:text-white border-blue-400/40 hover:border-white/40 hover:bg-white/10";

  return (
    <>
      {/* PC用ナビゲーション */}
      <nav className="hidden md:flex items-center gap-2" aria-label="グローバルナビゲーション">
        {NAV_ITEMS.map(({ label, href, matchPath }) => {
          const isActive = pathname.startsWith(matchPath);
          return (
            <Link
              key={label}
              href={href}
              className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                isActive
                  ? "text-white border-white/40 bg-white/10"
                  : inactiveCls
              }`}
            >
              {label}
            </Link>
          );
        })}
        {isAdmin && (
          <Link
            href="/admin"
            className={`text-xs px-2.5 py-1 rounded border transition-colors ${
              pathname.startsWith("/admin")
                ? "text-white border-white/40 bg-white/10"
                : "text-blue-200 hover:text-white border-blue-400/40 hover:border-white/40 hover:bg-white/10"
            }`}
          >
            管理
          </Link>
        )}
      </nav>
      {/* スマホ用ハンバーガーメニュー */}
      <HamburgerMenu isAdmin={isAdmin} />
    </>
  );
}
