"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { AppNav } from "@/components/AppNav";
import { LiveStreamButton } from "@/components/LiveStreamButton";

const HIDE_HEADER_PATHS = new Set(["/", "/login"]);

type Props = {
  isAdmin: boolean;
};

export function SiteHeader({ isAdmin }: Props) {
  const pathname = usePathname();
  if (HIDE_HEADER_PATHS.has(pathname)) return null;

  const isChihou = pathname.startsWith("/chihou");
  const isKeirin = pathname.startsWith("/keirin");
  const headerBg = isChihou ? "var(--chihou-primary)" : "var(--primary)";

  const logoHref = isChihou ? "/chihou/races" : "/races";

  return (
    <header style={{ background: headerBg }} className="sticky top-0 z-10 shadow-md">
      <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-3">
        <Link
          href={logoHref}
          aria-label="GallopLab トップへ"
          className="flex-shrink-0 opacity-90 hover:opacity-100 transition-opacity"
        >
          <Image
            src="/images/logo.png"
            alt="GallopLab"
            width={160}
            height={98}
            className="select-none h-8 w-auto"
            priority
          />
        </Link>
        {isChihou && (
          <span className="text-green-100 text-xs font-medium px-2 py-0.5 rounded-full border border-green-400/60 bg-green-800/40 flex-shrink-0">
            地方
          </span>
        )}
        {isKeirin && (
          <span className="text-cyan-100 text-xs font-medium px-2 py-0.5 rounded-full border border-cyan-400/60 bg-cyan-800/40 flex-shrink-0">
            競輪
          </span>
        )}
        <div className="flex-1 min-w-0" />
        <LiveStreamButton />
        <AppNav isAdmin={isAdmin} />
      </div>
    </header>
  );
}
