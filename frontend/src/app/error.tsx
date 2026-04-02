"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center" style={{ background: "#f0f5fb" }}>
      <div className="text-center px-4">
        <p className="text-5xl mb-4"><span aria-hidden="true">⚠️</span></p>
        <h1 className="text-xl font-bold text-gray-800 mb-2">エラーが発生しました</h1>
        <p className="text-gray-500 text-sm mb-6">
          予期しないエラーが発生しました。しばらく経ってから再度お試しください。
        </p>
        <div className="flex gap-3 justify-center">
          <button
            onClick={reset}
            className="px-5 py-2.5 bg-green-700 text-white text-sm rounded-lg font-medium hover:bg-green-800 transition-colors"
          >
            再試行
          </button>
          <Link
            href="/races"
            className="px-5 py-2.5 bg-white border border-gray-300 text-gray-700 text-sm rounded-lg font-medium hover:bg-gray-50 transition-colors"
          >
            トップへ戻る
          </Link>
        </div>
      </div>
    </div>
  );
}
