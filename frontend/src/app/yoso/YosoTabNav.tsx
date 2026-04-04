"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { label: "予想一覧", href: "/yoso", exact: true },
  { label: "データ投入", href: "/yoso/import", exact: false },
  { label: "成績集計", href: "/yoso/stats", exact: false },
  { label: "表示設定", href: "/yoso/settings", exact: false },
] as const;

export function YosoTabNav() {
  const pathname = usePathname();

  return (
    <nav className="max-w-3xl mx-auto px-4 flex gap-1 overflow-x-auto" aria-label="予想メニュー">
      {TABS.map((tab) => {
        const isActive = tab.exact ? pathname === tab.href : pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={`text-xs px-3 py-2.5 whitespace-nowrap border-b-2 transition-colors ${
              isActive
                ? "text-white border-white"
                : "text-blue-200 hover:text-white border-transparent hover:border-white/50"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
