"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { MenuFlags } from "@/lib/menuAccess";
import { HamburgerMenu } from "./HamburgerMenu";

export type NavProps = {
  isAdmin: boolean;
  /** 実際に見えるメニュー（`lib/menu.ts` が毎リクエスト引いた値）。 */
  access: MenuFlags;
  pogYear?: number | null;
};

export type NavItem = {
  key: string;
  icon: string;
  label: string;
  shortLabel: string;
  href: string;
  matchPath: string;
};

/**
 * ナビの項目を組み立てる。
 *
 * 🔴 **AppNav / HamburgerMenu / BottomNav の唯一の情報源。** 3 か所で別々に
 * 組んでいた頃は、ハンバーガーにだけ POG が無い（2026-09-10 実測）といった
 * 取りこぼしが静かに起きていた。表示先が増えてもここだけ直せばよいようにする。
 *
 * @param access 見えるメニュー。ここで弾いた項目はどの表示先にも出ない。
 */
export function buildNavItems(access: MenuFlags, pogYear: number | null, isChihou: boolean): NavItem[] {
  const items: NavItem[] = [];
  if (access.jra) {
    items.push({ key: "jra", icon: "🏇", label: "中央競馬", shortLabel: "中央", href: "/races", matchPath: "/races" });
  }
  if (access.chihou) {
    items.push({ key: "chihou", icon: "🏘", label: "地方競馬", shortLabel: "地方", href: "/chihou/races", matchPath: "/chihou" });
  }
  if (access.keirin) {
    items.push({ key: "keirin", icon: "🚴", label: "競輪", shortLabel: "競輪", href: "/keirin", matchPath: "/keirin" });
  }
  // 実績は「今いる柱」の実績へ送る。中央も地方も見えないなら出さない。
  if (access.jra || access.chihou) {
    const useChihou = isChihou ? access.chihou : !access.jra;
    const href = useChihou ? "/chihou/results" : "/results";
    items.push({ key: "results", icon: "📊", label: "実績", shortLabel: "実績", href, matchPath: href });
  }
  // 予想は中央のレースに紐づく（`menuOfPath` と同じ束ね方）。
  if (access.jra) {
    items.push({ key: "yoso", icon: "🎯", label: "予想", shortLabel: "予想", href: "/yoso", matchPath: "/yoso" });
  }
  if (access.pog) {
    items.push({
      key: "pog",
      icon: "🐴",
      label: "POG",
      shortLabel: "POG",
      // 参加実績が無ければ `/pog` へ。あちらが最新年度へ転送する。
      href: pogYear ? `/pog/${pogYear}` : "/pog",
      matchPath: "/pog",
    });
  }
  items.push({ key: "my", icon: "👤", label: "マイページ", shortLabel: "マイページ", href: "/my", matchPath: "/my" });
  return items;
}

export function AppNav({ isAdmin, access, pogYear = null }: NavProps) {
  const pathname = usePathname();
  const isChihou = pathname.startsWith("/chihou");
  const navItems = buildNavItems(access, pogYear, isChihou);

  const inactiveCls = isChihou
    ? "text-green-200 hover:text-white border-green-400/40 hover:border-white/40 hover:bg-white/10"
    : "text-blue-200 hover:text-white border-blue-400/40 hover:border-white/40 hover:bg-white/10";

  return (
    <>
      {/* PC用ナビゲーション */}
      <nav className="hidden md:flex items-center gap-2" aria-label="グローバルナビゲーション">
        {navItems.map(({ key, shortLabel, href, matchPath }) => {
          const isActive = pathname.startsWith(matchPath);
          return (
            <Link
              key={key}
              href={href}
              className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                isActive
                  ? "text-white border-white/40 bg-white/10"
                  : inactiveCls
              }`}
            >
              {shortLabel}
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
      <HamburgerMenu isAdmin={isAdmin} access={access} pogYear={pogYear} />
    </>
  );
}
