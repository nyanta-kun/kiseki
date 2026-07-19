"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import Image from "next/image";
import { signIn } from "next-auth/react";

function LoginForm() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/races";
  const error = searchParams.get("error");

  const errorMessage =
    error === "account_suspended"
      ? "このGoogleアカウントは登録されていません。管理者にお問い合わせください。"
      : error
      ? "ログインに失敗しました。もう一度お試しください。"
      : null;

  return (
    <div
      className="h-screen w-full flex flex-col md:flex-row items-center justify-center relative overflow-hidden"
      style={{ background: "#090d1a" }}
    >
      <h1 className="sr-only">GallopLab ログイン</h1>
      {/* ---- 左（PC）/ 上（スマホ）: image.png ---- */}
      <div className="relative z-10 flex items-center justify-center w-full md:w-1/2 md:h-full flex-shrink-0 mt-8 md:mt-0">
        <Image
          src="/images/image.png"
          alt="GallopLab"
          width={600}
          height={600}
          priority
          className="w-auto max-h-52 md:max-h-[70vh] object-contain select-none"
          style={{
            mixBlendMode: "screen",
            maskImage:
              "linear-gradient(to right, transparent 0%, black 18%, black 82%, transparent 100%), " +
              "linear-gradient(to bottom, transparent 0%, black 18%, black 82%, transparent 100%)",
            WebkitMaskImage:
              "linear-gradient(to right, transparent 0%, black 18%, black 82%, transparent 100%), " +
              "linear-gradient(to bottom, transparent 0%, black 18%, black 82%, transparent 100%)",
            maskComposite: "intersect",
            WebkitMaskComposite: "source-in",
          }}
        />
      </div>

      {/* ---- 右（PC）/ 下（スマホ）: ログインカード ---- */}
      <div className="relative z-10 w-full max-w-sm mx-6 md:mx-0 md:mr-16 lg:mr-24 mb-8 md:mb-0 flex-shrink-0">
        <div
          className="rounded-2xl overflow-hidden border"
          style={{
            background: "rgba(6, 14, 36, 0.96)",
            borderColor: "rgba(0, 180, 255, 0.45)",
            boxShadow:
              "0 0 0 1px rgba(0,180,255,0.1), 0 0 40px rgba(0,180,255,0.12), 0 16px 48px rgba(0,0,0,0.7)",
          }}
        >
          <div className="px-8 py-8 space-y-5">
            <p className="text-white/90 text-xs text-center leading-relaxed">
              登録済みのGoogleアカウントでログインしてください
            </p>

            {errorMessage && (
              <p className="text-red-300 text-sm bg-red-500/20 border border-red-400/30 rounded-lg px-3 py-2">
                {errorMessage}
              </p>
            )}

            <button
              type="button"
              onClick={() => signIn("google", { callbackUrl })}
              className="w-full flex items-center justify-center gap-3 font-semibold py-3 rounded-xl transition-all text-sm"
              style={{
                background: "linear-gradient(135deg, #00aaee 0%, #0055cc 100%)",
                color: "#fff",
                boxShadow: "0 0 20px rgba(0,180,255,0.3)",
              }}
            >
              <GoogleIcon />
              Googleでログイン
            </button>

            <p className="text-center text-[#00c8ff]/70 text-xs">
              Powered by GallopLab
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

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen flex items-center justify-center bg-[#090d1a]">
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
