import { redirect } from "next/navigation";
import { auth } from "@/auth";
import type { Metadata } from "next";
import { YosoTabNav } from "./YosoTabNav";

export const metadata: Metadata = {
  title: "予想管理 | GallopLab",
};

export default async function YosoLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  if (!session?.user) redirect("/login");

  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
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
