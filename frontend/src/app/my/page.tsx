import { redirect } from "next/navigation";
import Image from "next/image";
import { auth } from "@/auth";
import { LogoutButton } from "@/components/LogoutButton";
import { RedeemCodeForm } from "./RedeemCodeForm";

export const metadata = {
  title: "マイページ | GallopLab",
};

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

type AccessStatus = {
  user_id: number;
  is_premium: boolean;
  access_expires_at: string | null;
};

async function fetchAccessStatus(dbId: number): Promise<AccessStatus | null> {
  try {
    const res = await fetch(`${BACKEND_URL}/users/${dbId}/access`, {
      headers: { "X-API-Key": API_KEY },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json() as Promise<AccessStatus>;
  } catch {
    return null;
  }
}

function formatExpiry(iso: string | null): string {
  if (!iso) return "無期限";
  return new Date(iso).toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }) + " まで";
}

export default async function MyPage() {
  const session = await auth();

  if (!session?.user) {
    redirect("/login");
  }

  const dbId = session.user.db_id;
  const user = session.user;

  // 最新のアクセス状態を DB から直接取得（JWT より新鮮）
  const access = dbId ? await fetchAccessStatus(dbId) : null;
  const isPremium = access?.is_premium ?? session.user.is_premium ?? false;
  const accessExpiresAt = access?.access_expires_at ?? session.user.access_expires_at ?? null;

  const paidMode = process.env.NEXT_PUBLIC_PAID_MODE === "true";

  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      <main id="main-content" className="max-w-3xl mx-auto px-4 py-6 space-y-4">
        <h1 className="text-lg font-bold text-gray-800">マイページ</h1>

        {/* 1. 会員ステータスカード（有料モード時のみ表示） */}
        {paidMode && (
          <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
            <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
              <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
              会員ステータス
            </h2>
            {isPremium ? (
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-green-100 text-green-800 border border-green-300">
                    アクセス有効
                  </span>
                  <p className="text-sm text-gray-700">
                    {accessExpiresAt ? formatExpiry(accessExpiresAt) : "無期限アクセス"}
                  </p>
                </div>
                <p className="text-xs text-gray-500">全レースの指数・予想を閲覧できます</p>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-gray-100 text-gray-600 border border-gray-300">
                    無料プラン
                  </span>
                  <p className="text-sm text-gray-700">各競馬場1R目のみ閲覧可能</p>
                </div>
              </div>
            )}
          </section>
        )}

        {/* 2. 招待コード入力カード（有料モード時のみ表示） */}
        {paidMode && (
          <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
            <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
              <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
              招待コード入力
            </h2>
            <p className="text-xs text-gray-500 mb-3 leading-relaxed">
              note 購入者の方は招待コードを入力してアクセスを有効化してください。
              コードは大文字・小文字を区別しません。
            </p>
            <RedeemCodeForm />
          </section>
        )}

        {/* 3. アカウント情報カード */}
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

        {/* 4. ログアウト */}
        <section className="bg-white rounded-xl border border-gray-100 p-5 shadow-sm">
          <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
            <span className="w-1 h-4 rounded inline-block" style={{ background: "var(--primary)" }} />
            ログアウト
          </h2>
          <LogoutButton />
        </section>
      </main>
    </div>
  );
}
