"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * POG のページ間タブ。
 *
 * 🔴 **移設元は各ページの末尾に「◯◯へ」のリンクを 2〜3 本並べていた**ため、
 * どこから何へ行けるかがページごとに違い、兄弟馬とドラフトは順位表からしか
 * 辿れなかった。行き先を 1 か所にまとめ、全ページで同じ並びを出す。
 *
 * スマホは横スクロール、PC は 1 行に収まる。
 */

type Section = {
  key: string;
  label: string;
  /** `:year` は現在の年度へ差し替える。 */
  href: string;
  /** 年度に依らないページ（兄弟馬）。 */
  yearless?: boolean;
};

const SECTIONS: Section[] = [
  { key: "standings", label: "順位表", href: "/pog/:year" },
  { key: "horses", label: "指名馬", href: "/pog/:year/horses" },
  { key: "records", label: "記録室", href: "/pog/:year/records" },
  { key: "score", label: "スコア", href: "/pog/:year/score" },
  { key: "rankings", label: "ランキング", href: "/pog/:year/rankings" },
  { key: "siblings", label: "兄弟馬", href: "/pog/siblings", yearless: true },
  { key: "draft", label: "ドラフト", href: "/pog/:year/draft" },
];

export function PogSectionNav({ year }: { year: number }) {
  const pathname = usePathname();

  return (
    <nav
      aria-label="POG のページ"
      // 🔴 負マージンは `PogShell` の水平パディングと**必ず同じ値**にする
      //    （スマホ `px-3` / PC `md:px-0`）。`-mx-4` のままだと左右 4px ずつ
      //    シェルの外へ出て、ページ全体に横スクロールが出る。
      className="-mx-3 overflow-x-auto px-3 md:mx-0 md:px-0"
    >
      <ul className="flex w-max gap-1 md:w-full">
        {SECTIONS.map((s) => {
          const href = s.href.replace(":year", String(year));
          // 順位表（`/pog/2026`）は完全一致でないと全ページで現在地になる。
          const isActive =
            s.key === "standings" ? pathname === href : pathname.startsWith(href);
          return (
            <li key={s.key}>
              <Link
                href={href}
                aria-current={isActive ? "page" : undefined}
                // スマホは 1 行ぶんの高さが惜しいので上下の余白を詰める。
                // ⚠️ 文字は 13px 未満にしない（読みやすさの下限）。
                className="block whitespace-nowrap rounded-lg px-2.5 py-1 text-[13px] font-medium transition-colors md:px-3 md:py-1.5 md:text-sm"
                style={
                  isActive
                    ? { background: "var(--pog-accent)", color: "#fff" }
                    : {
                        background: "var(--pog-card)",
                        color: "var(--surface-muted)",
                        border: "1px solid var(--pog-card-border)",
                      }
                }
              >
                {s.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
