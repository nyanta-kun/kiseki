"use client";

import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { AppNav } from "@/components/AppNav";
import { todayYYYYMMDD } from "@/lib/utils";

const HIDE_HEADER_PATHS = new Set(["/", "/login"]);

type Props = {
  isAdmin: boolean;
};

export function SiteHeader({ isAdmin }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  if (HIDE_HEADER_PATHS.has(pathname)) return null;

  const isChihou = pathname.startsWith("/chihou");
  const headerBg = isChihou ? "var(--chihou-primary)" : "var(--primary)";

  const handleLogoClick = (e: React.MouseEvent) => {
    e.preventDefault();
    const params = new URLSearchParams(typeof window !== "undefined" ? window.location.search : "");
    const date = params.get("date") ?? todayYYYYMMDD();
    const target = isChihou ? `/races?date=${date}` : `/chihou/races?date=${date}`;
    router.push(target);
  };

  const toggleLabel = isChihou ? "中央競馬へ切り替え" : "地方競馬へ切り替え";

  return (
    <header style={{ background: headerBg }} className="sticky top-0 z-10 shadow-md">
      <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-3">
        <button
          onClick={handleLogoClick}
          aria-label={toggleLabel}
          className="flex-shrink-0 opacity-90 hover:opacity-100 transition-opacity cursor-pointer"
        >
          <Image
            src="/images/logo.png"
            alt="GallopLab"
            width={160}
            height={98}
            className="select-none h-8 w-auto"
            priority
          />
        </button>
        {isChihou && (
          <span className="text-green-100 text-xs font-medium px-2 py-0.5 rounded-full border border-green-400/60 bg-green-800/40 flex-shrink-0">
            地方
          </span>
        )}
        <div className="flex-1 min-w-0" />
        <AppNav isAdmin={isAdmin} />
      </div>
    </header>
  );
}
