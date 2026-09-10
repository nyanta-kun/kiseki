import type { Metadata } from "next";

import { requireMenu } from "@/lib/menu";

export const metadata: Metadata = {
  title: "POG | GallopLab",
  description: "ペーパーオーナーゲームの順位表・指名馬・記録室・スコア集計。",
};

/**
 * POG のセクションガード。
 *
 * POG が ON なら中央・地方も必ず ON になっている（`menuAccess` の規則 1）ので、
 * ここから張っている `/races/{id}` `/chihou/races/{id}` のリンクは必ず開ける。
 */
export default async function PogLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireMenu("pog");
  return <>{children}</>;
}
