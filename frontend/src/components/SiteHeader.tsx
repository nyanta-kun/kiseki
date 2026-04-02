"use client";

import Image from "next/image";
import { usePathname } from "next/navigation";
import { AppNav } from "@/components/AppNav";

const HIDE_HEADER_PATHS = new Set(["/", "/login"]);

type Props = {
  isAdmin: boolean;
};

export function SiteHeader({ isAdmin }: Props) {
  const pathname = usePathname();
  if (HIDE_HEADER_PATHS.has(pathname)) return null;

  return (
    <header style={{ background: "var(--primary)" }} className="sticky top-0 z-10 shadow-md">
      <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-3">
        <Image
          src="/images/logo.png"
          alt="GallopLab"
          width={160}
          height={98}
          className="select-none opacity-90 flex-shrink-0 h-8 w-auto"
          priority
        />
        <div className="flex-1 min-w-0" />
        <AppNav isAdmin={isAdmin} />
      </div>
    </header>
  );
}
