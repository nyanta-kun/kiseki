import { redirect } from "next/navigation";
import { auth } from "@/auth";
import type { Metadata } from "next";
import { requireMenu } from "@/lib/menu";
import { YosoTabNav } from "./YosoTabNav";

export const metadata: Metadata = {
  title: "予想管理 | GallopLab",
};

export default async function YosoLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  if (!session?.user) redirect("/login");
  // 予想は中央のレースに紐づくので、中央の可視性に従う（`menuOfPath` と同じ）。
  await requireMenu("jra");

  return (
    <div className="min-h-screen" style={{ background: "var(--page-bg)" }}>
      {/* タブナビゲーション */}
      <div style={{ background: "var(--primary-mid)" }} className="shadow-sm">
        <YosoTabNav />
      </div>
      <main id="main-content" className="max-w-3xl mx-auto px-4 py-4">
        {children}
      </main>
    </div>
  );
}
