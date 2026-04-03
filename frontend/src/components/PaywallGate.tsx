"use client";

import React from "react";

type Props = {
  isPremium: boolean;
  raceNumber: number;
  children: React.ReactNode;
};

/**
 * ペイウォールゲート。
 * - NEXT_PUBLIC_SHOW_FOOTER が "false" の場合はペイウォール無効（開発環境等）
 * - 1Rは無料公開
 * - 有料会員（isPremium）は無条件で表示
 * - それ以外はBlur Gate（ぼかし + 購入訴求オーバーレイ）を表示
 */
export function PaywallGate({ isPremium, raceNumber, children }: Props) {
  const paywallEnabled = process.env.NEXT_PUBLIC_PAID_MODE === "true";
  const isFree = !paywallEnabled || isPremium || raceNumber === 1;

  if (isFree) {
    return <>{children}</>;
  }

  return (
    <div className="relative">
      {/* コンテンツはぼかして背後に表示 */}
      <div className="select-none pointer-events-none blur-md opacity-60" aria-hidden="true">
        {children}
      </div>

      {/* オーバーレイ */}
      <div
        className="absolute inset-0 flex items-center justify-center bg-white/30 backdrop-blur-sm rounded-xl"
        role="region"
        aria-label="有料会員限定コンテンツ"
      >
        <div className="bg-white rounded-2xl shadow-lg px-6 py-5 text-center border border-green-200 mx-4 max-w-xs">
          <div className="text-2xl mb-2">🔒</div>
          <p className="font-bold text-gray-800 text-sm">有料会員限定</p>
          <p className="text-xs text-gray-500 mt-1 leading-relaxed">
            全指数・期待値チャートは<br />有料会員のみ閲覧できます
          </p>
          <a
            href="/pricing"
            className="mt-3 block px-4 py-2 bg-[#1a5c38] text-white rounded-lg text-sm font-medium hover:bg-[#14472c] transition-colors"
          >
            7日間無料で試す
          </a>
          <p className="text-xs text-gray-400 mt-2">月額2,980円 いつでも解約可</p>
        </div>
      </div>
    </div>
  );
}
