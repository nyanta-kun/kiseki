import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "地方競馬 | GallopLab",
  description: "地方競馬の指数・期待値一覧。速度指数・後3ハロン指数・騎手指数・ローテーション指数で合理的な馬券購入をサポート。",
};

export default function ChihouLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
