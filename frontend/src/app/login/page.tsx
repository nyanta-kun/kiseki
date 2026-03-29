"use client";

import { useActionState } from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { verifyPasswordAndRedirect } from "./actions";

// next/image は basePath を自動付与しないため、CSS background-image では
// /kiseki プレフィックスを手動で含める。
// <Image unoptimized> は Next.js が basePath を付加するため src に不要。
const BASEPATH = "/kiseki";

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
    <div className="min-h-screen w-full flex items-center justify-center md:justify-end relative overflow-hidden">
      {/* ---- 背景画像: CSS background-image で SSR/CSR ミスマッチを回避 ---- */}
      {/* デスクトップ (md以上) */}
      <div
        className="absolute inset-0 z-0 hidden md:block"
        style={{
          backgroundImage: `url('${BASEPATH}/images/login-bg-desktop.png')`,
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      />
      {/* モバイル (md未満) */}
      <div
        className="absolute inset-0 z-0 block md:hidden"
        style={{
          backgroundImage: `url('${BASEPATH}/images/login-bg-mobile.png')`,
          backgroundSize: "cover",
          backgroundPosition: "top center",
        }}
      />

      {/* ---- フォームカード ---- */}
      <div className="relative z-10 w-full max-w-sm mx-6 md:mx-0 md:mr-16 lg:mr-24">
        <div
          className="rounded-2xl overflow-hidden border shadow-[0_8px_48px_rgba(0,0,0,0.5)]"
          style={{
            background: "rgba(5, 18, 45, 0.82)",
            backdropFilter: "blur(28px)",
            WebkitBackdropFilter: "blur(28px)",
            borderColor: "rgba(80, 150, 220, 0.35)",
          }}
        >
          {/* カードヘッダー: ロゴ + ブランディング統合 */}
          <div
            className="px-8 py-6 text-center border-b"
            style={{
              background: "rgba(0, 10, 30, 0.50)",
              borderColor: "rgba(80, 150, 220, 0.25)",
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`${BASEPATH}/images/logo.png`}
              alt="PEGASUS AI"
              width={160}
              height={97}
              className="drop-shadow-[0_0_20px_rgba(100,180,255,0.8)] select-none mx-auto"
            />
            <p className="text-blue-200/80 text-xs mt-3">競馬予測指数システム</p>
          </div>

          {/* カードボディ */}
          <div className="px-8 py-8 space-y-5">
            <p className="text-white/90 text-sm text-center leading-relaxed">
              合言葉を入力後、Googleアカウントで認証してください
            </p>

            <form action={formAction} className="space-y-4">
              <input
                name="password"
                type="password"
                placeholder="合言葉を入力"
                required
                className="
                  w-full px-4 py-3 rounded-xl text-sm
                  text-white placeholder-white/60
                  focus:outline-none focus:ring-2 transition-all
                "
                style={{
                  background: "rgba(255,255,255,0.12)",
                  border: "1px solid rgba(100,160,220,0.45)",
                  // focus ring は Tailwind の ring-blue-400/60
                }}
              />

              {errorMessage && (
                <p className="text-red-300 text-sm bg-red-500/20 border border-red-400/30 rounded-lg px-3 py-2">
                  {errorMessage}
                </p>
              )}

              <button
                type="submit"
                disabled={isPending}
                className="
                  w-full flex items-center justify-center gap-3
                  bg-white hover:bg-blue-50
                  disabled:opacity-60 disabled:cursor-not-allowed
                  text-gray-800 font-semibold py-3 rounded-xl
                  transition-all shadow-md hover:shadow-lg
                  text-sm
                "
              >
                {isPending ? (
                  <span className="flex items-center gap-2">
                    <SpinnerIcon />
                    確認中...
                  </span>
                ) : (
                  <>
                    <GoogleIcon />
                    Googleでログイン
                  </>
                )}
              </button>
            </form>

            <p className="text-center text-blue-300/50 text-xs pt-1">
              Powered by PEGASUS AI
            </p>
          </div>
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

function SpinnerIcon() {
  return (
    <svg
      className="animate-spin"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-[#9db3c8]">
          <div className="text-white/70 text-sm tracking-widest animate-pulse">
            LOADING...
          </div>
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
