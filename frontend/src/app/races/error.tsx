"use client";

import { useEffect } from "react";

export default function RacesError({
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
    <div className="py-12 text-center px-4">
      <p className="text-4xl mb-3">🏇</p>
      <p className="text-gray-600 text-sm mb-4">レース情報の取得に失敗しました</p>
      <button
        onClick={reset}
        className="px-4 py-2 bg-green-700 text-white text-sm rounded-lg font-medium hover:bg-green-800 transition-colors"
      >
        再読み込み
      </button>
    </div>
  );
}
