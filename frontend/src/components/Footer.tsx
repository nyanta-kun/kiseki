import Link from "next/link";

export function Footer() {
  return (
    <footer className="mt-auto py-6 px-4 border-t border-gray-200" style={{ background: "#f0f5fb" }}>
      <div className="max-w-3xl mx-auto space-y-3">
        {/* 免責事項 */}
        <p className="text-xs text-gray-400 text-center leading-relaxed">
          本サービスの情報は予測指数であり、馬券的中・利益を保証するものではありません。
          馬券の購入は自己責任においてお願いいたします。
        </p>

        {/* ナビゲーションリンク */}
        <nav aria-label="フッターナビゲーション">
          <ul className="flex flex-wrap justify-center gap-x-4 gap-y-1">
            <li>
              <Link href="/privacy" className="text-xs text-gray-500 hover:text-gray-700 transition-colors">
                プライバシーポリシー
              </Link>
            </li>
            <li>
              <Link href="/terms" className="text-xs text-gray-500 hover:text-gray-700 transition-colors">
                利用規約
              </Link>
            </li>
            <li>
              <Link href="/tokutei" className="text-xs text-gray-500 hover:text-gray-700 transition-colors">
                特定商取引法に基づく表記
              </Link>
            </li>
            <li>
              <Link href="/contact" className="text-xs text-gray-500 hover:text-gray-700 transition-colors">
                お問い合わせ
              </Link>
            </li>
          </ul>
        </nav>

        {/* 著作権表示 */}
        <p className="text-xs text-gray-400 text-center">
          &copy; 2026 GallopLab. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
