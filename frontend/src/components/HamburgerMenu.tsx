"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout } from "@/app/actions/auth";

type Props = {
  isAdmin?: boolean;
};

export function HamburgerMenu({ isAdmin = false }: Props) {
  // pathnameが変わると自動的に閉じる派生state（useEffect不要）
  const [openedOnPath, setOpenedOnPath] = useState<string | null>(null);
  const pathname = usePathname();
  const isChihou = pathname.startsWith("/chihou");

  const NAV_ITEMS = [
    { icon: "🏇", label: "レース", href: isChihou ? "/chihou/races" : "/races", matchPath: isChihou ? "/chihou/races" : "/races" },
    { icon: "📊", label: "実績", href: isChihou ? "/chihou/results" : "/results", matchPath: isChihou ? "/chihou/results" : "/results" },
    { icon: "🎯", label: "予想", href: "/yoso", matchPath: "/yoso" },
    { icon: "👤", label: "マイページ", href: "/my", matchPath: "/my" },
  ];
  const isOpen = openedOnPath === pathname;
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const closeMenu = useCallback(() => {
    setOpenedOnPath(null);
    // メニューを閉じたときにトリガーボタンへフォーカスを戻す
    setTimeout(() => triggerRef.current?.focus(), 0);
  }, []);

  // 外側クリックで閉じる
  useEffect(() => {
    if (!isOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpenedOnPath(null);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [isOpen]);

  // Escapeキーでメニューを閉じる
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeMenu();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, closeMenu]);

  return (
    <div ref={containerRef} className="relative md:hidden">
      {/* ハンバーガーボタン */}
      <button
        ref={triggerRef}
        onClick={() => setOpenedOnPath((v) => v === pathname ? null : pathname)}
        aria-expanded={isOpen}
        aria-haspopup="true"
        aria-label={isOpen ? "メニューを閉じる" : "メニューを開く"}
        className="flex flex-col justify-center items-center w-9 h-9 gap-1.5 rounded-md hover:bg-white/10 transition-colors"
      >
        <span
          className={`block w-5 h-0.5 bg-white origin-center transition-all duration-200 ${
            isOpen ? "rotate-45 translate-y-2" : ""
          }`}
        />
        <span
          className={`block w-5 h-0.5 bg-white transition-all duration-200 ${
            isOpen ? "opacity-0 scale-x-0" : ""
          }`}
        />
        <span
          className={`block w-5 h-0.5 bg-white origin-center transition-all duration-200 ${
            isOpen ? "-rotate-45 -translate-y-2" : ""
          }`}
        />
      </button>

      {/* ドロップダウンメニュー */}
      <div
        className={`absolute right-0 top-full mt-2 w-52 rounded-xl shadow-2xl overflow-hidden z-50 transition-all duration-200 origin-top-right ${
          isOpen
            ? "opacity-100 scale-100 translate-y-0"
            : "opacity-0 scale-95 -translate-y-1 pointer-events-none"
        }`}
        style={{
          background: "#0d1f35",
          border: "1px solid rgba(255,255,255,0.12)",
        }}
        role="menu"
      >
        {NAV_ITEMS.map((item) => {
          const isActive = pathname.startsWith(item.matchPath);
          return (
            <Link
              key={item.label}
              href={item.href}
              role="menuitem"
              onClick={() => setOpenedOnPath(null)}
              className="flex items-center gap-3 px-4 py-3.5 text-sm transition-colors hover:bg-white/10"
              style={{ color: isActive ? "#ffffff" : "rgba(255,255,255,0.65)" }}
            >
              <span aria-hidden="true">{item.icon}</span>
              <span className={isActive ? "font-semibold" : ""}>{item.label}</span>
              {isActive && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-white flex-shrink-0" aria-hidden="true" />
              )}
            </Link>
          );
        })}

        {isAdmin && (
          <>
            <div style={{ borderTop: "1px solid rgba(255,255,255,0.1)" }} />
            <Link
              href="/admin"
              role="menuitem"
              onClick={() => setOpenedOnPath(null)}
              className="flex items-center gap-3 px-4 py-3.5 text-sm text-blue-200 hover:text-white hover:bg-white/10 transition-colors"
            >
              <span aria-hidden="true">⚙️</span>
              <span>管理</span>
            </Link>
          </>
        )}

        <div style={{ borderTop: "1px solid rgba(255,255,255,0.1)" }} />
        <form action={logout}>
          <button
            type="submit"
            role="menuitem"
            className="w-full flex items-center gap-3 px-4 py-3.5 text-sm text-blue-200 hover:text-white hover:bg-white/10 transition-colors"
          >
            <span aria-hidden="true">🚪</span>
            <span>ログアウト</span>
          </button>
        </form>
      </div>
    </div>
  );
}
