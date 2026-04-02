import { redirect } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import { auth } from "@/auth";
import { LogoutButton } from "@/components/LogoutButton";
import { BottomNav } from "@/components/BottomNav";

export const metadata = {
  title: "マイページ | GallopLab",
};

export default async function MyPage() {
  const session = await auth();

  if (!session?.user) {
    redirect("/login");
  }

  const isPremium = session.user.is_active ?? false;
  const user = session.user;

  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      {/* ヘッダー */}
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
          <Link
            href="/races"
            className="text-blue-200 hover:text-white text-xs px-2.5 py-1 rounded border border-blue-400/40 hover:border-white/40 hover:bg-white/10 transition-colors"
          >
            ← レース一覧
          </Link>
          <LogoutButton />
        </div>
      </header>

      {/* コンテンツ */}
      <main id="main-content" className="max-w-3xl mx-auto px-4 py-6 pb-20 space-y-4">
        <h1 className="text-lg font-bold text-gray-800">マイページ</h1>

        {/* 1. 会員ステータスカード */}
        <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
          <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
            会員ステータス
          </h2>
          {isPremium ? (
            <div className="flex items-center gap-3">
              <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-green-100 text-green-800 border border-green-300">
                有料会員
              </span>
              <p className="text-sm text-gray-700">プレミアム会員として登録中です</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-gray-100 text-gray-600 border border-gray-300">
                  無料プラン
                </span>
                <p className="text-sm text-gray-700">現在無料プランをご利用中です</p>
              </div>
              <p className="text-xs text-gray-500 leading-relaxed">
                有料プランにアップグレードすると全レースの指数を閲覧できます
              </p>
              <a
                href="/pricing"
                className="inline-block px-4 py-2 bg-[#1a5c38] text-white rounded-lg text-sm font-medium hover:bg-[#14472c] transition-colors"
              >
                今すぐアップグレード
              </a>
            </div>
          )}
        </section>

        {/* 2. アカウント情報カード */}
        <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
          <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
            アカウント情報
          </h2>
          <div className="flex items-center gap-4">
            {user.image ? (
              <Image
                src={user.image}
                alt={user.name ?? "プロフィール画像"}
                width={56}
                height={56}
                className="rounded-full border border-gray-200 flex-shrink-0"
              />
            ) : (
              <div className="w-14 h-14 rounded-full bg-gray-200 flex items-center justify-center text-gray-400 text-xl flex-shrink-0">
                👤
              </div>
            )}
            <div className="min-w-0 space-y-1">
              {user.name && (
                <p className="text-sm font-semibold text-gray-900 truncate">{user.name}</p>
              )}
              {user.email && (
                <p className="text-xs text-gray-500 truncate">{user.email}</p>
              )}
            </div>
          </div>
        </section>

        {/* 3. プラン・決済管理カード（暫定） */}
        <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
          <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
            プラン・決済管理
          </h2>
          <div className="flex items-center gap-3 p-4 bg-gray-50 rounded-lg border border-dashed border-gray-300">
            <span className="text-2xl" aria-hidden="true">🚧</span>
            <div>
              <p className="text-sm text-gray-600 font-medium">準備中</p>
              <p className="text-xs text-gray-400 mt-0.5">
                サブスクリプション管理は現在準備中です。近日公開予定です。
              </p>
            </div>
          </div>
        </section>

        {/* 4. ログアウト */}
        <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
          <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
            ログアウト
          </h2>
          <LogoutButton />
        </section>
      </main>

      <BottomNav />
    </div>
  );
}
