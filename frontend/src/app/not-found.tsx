import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center" style={{ background: "#f0f5fb" }}>
      <div className="text-center px-4">
        <p className="text-5xl mb-4"><span aria-hidden="true">🏇</span></p>
        <h1 className="text-xl font-bold text-gray-800 mb-2">ページが見つかりません</h1>
        <p className="text-gray-500 text-sm mb-6">
          お探しのページは存在しないか、移動した可能性があります。
        </p>
        <Link
          href="/races"
          className="px-5 py-2.5 bg-green-700 text-white text-sm rounded-lg font-medium hover:bg-green-800 transition-colors"
        >
          レース一覧へ戻る
        </Link>
      </div>
    </div>
  );
}
