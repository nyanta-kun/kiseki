import { requireMenu } from "@/lib/menu";

/**
 * 中央競馬のセクションガード。
 *
 * 🔴 **ナビからリンクを消すだけでは URL 直打ちを止められない。** 実効ガードは
 * ここ（と各セクションの同じレイアウト）にある。判定は `lib/menu.ts` が
 * 毎リクエスト引くので、管理者の切り替えが次の遷移で効く。
 */
export default async function RacesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireMenu("jra");
  return <>{children}</>;
}
