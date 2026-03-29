"use client";

import { useActionState } from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { verifyPasswordAndRedirect } from "./actions";

function LoginForm() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/kiseki";

  const [errorMessage, formAction, isPending] = useActionState(
    async (_prev: string | null, formData: FormData) => {
      return await verifyPasswordAndRedirect(callbackUrl, formData);
    },
    null
  );

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#1a5c38]">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm mx-4 overflow-hidden">
        {/* ヘッダー */}
        <div className="bg-[#1a5c38] px-8 py-6 text-center">
          <h1 className="text-2xl font-bold text-[#c9a84c] tracking-widest">
            kiseki
          </h1>
          <p className="text-green-200 text-sm mt-1">競馬予測指数システム</p>
        </div>

        <div className="px-8 py-8 space-y-4">
          <p className="text-sm text-gray-500 text-center">
            合言葉を入力後、Googleアカウントで認証してください
          </p>

          <form action={formAction} className="space-y-3">
            <input
              name="password"
              type="password"
              placeholder="合言葉を入力"
              required
              className="w-full px-4 py-3 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1a5c38] focus:border-transparent placeholder-gray-400"
            />
            {errorMessage && (
              <p className="text-red-500 text-sm">{errorMessage}</p>
            )}
            <button
              type="submit"
              disabled={isPending}
              className="w-full flex items-center justify-center gap-3 bg-[#1a5c38] hover:bg-[#2d7a50] disabled:opacity-60 text-white font-semibold py-3 rounded-lg transition-colors text-sm"
            >
              {isPending ? (
                "確認中..."
              ) : (
                <>
                  <GoogleIcon />
                  合言葉を確認してGoogleでログイン
                </>
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z"
        fill="#4285F4"
      />
      <path
        d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"
        fill="#34A853"
      />
      <path
        d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"
        fill="#FBBC05"
      />
      <path
        d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 6.29C4.672 4.163 6.656 3.58 9 3.58z"
        fill="#EA4335"
      />
    </svg>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center bg-[#1a5c38]">
        <div className="text-white">読み込み中...</div>
      </div>
    }>
      <LoginForm />
    </Suspense>
  );
}
