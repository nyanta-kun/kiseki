import type { Metadata } from "next";

import { requireMenu } from "@/lib/menu";

export const metadata: Metadata = {
  title: "地方競馬 | GallopLab",
  description: "地方競馬の指数・期待値一覧。速度指数・後3ハロン指数・騎手指数・ローテーション指数で合理的な馬券購入をサポート。",
};

/**
 * 地方競馬のセクションガード。
 *
 * 🔴 **ナビからリンクを消すだけでは URL 直打ちを止められない。** 実効ガードは
 * ここ（と各セクションの同じレイアウト）にある。`/chihou/results` もこの下に
 * 入るので、地方の実績は地方の可視性に従う。
 */
export default async function ChihouLayout({ children }: { children: React.ReactNode }) {
  await requireMenu("chihou");
  return <>{children}</>;
}
