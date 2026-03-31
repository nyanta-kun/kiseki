import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "利用規約 | kiseki",
  robots: { index: false },
};

export default function TermsPage() {
  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      <main id="main-content" className="max-w-2xl mx-auto px-4 py-12">
        <h1 className="text-2xl font-bold text-gray-800 mb-6">利用規約</h1>

        <section className="space-y-6 text-sm text-gray-700 leading-relaxed">
          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">1. サービス概要</h2>
            <p>
              kiseki（以下「本サービス」）は、JV-Linkデータを元にした競馬予測指数・期待値分析を
              提供する個人向けWebシステムです。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">2. 免責事項</h2>
            <p>
              本サービスが提供する競馬予測指数・期待値分析は情報提供を目的とするものであり、
              馬券的中・利益獲得を保証するものではありません。
              馬券の購入・投票はユーザー自身の判断と責任で行ってください。
              本サービスの情報を参考にした馬券購入による損失について、
              サービス管理者は一切の責任を負いません。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">3. 禁止事項</h2>
            <ul className="list-disc list-inside space-y-1">
              <li>本サービスのデータ・コンテンツの無断転載・二次配布</li>
              <li>本サービスへの不正アクセス・過負荷をかける行為</li>
              <li>その他、法令または公序良俗に違反する行為</li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">4. サービスの変更・終了</h2>
            <p>
              サービス管理者は予告なく本サービスの内容を変更・停止・終了することができます。
              これによりユーザーに生じた損害について責任を負いません。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">5. 準拠法</h2>
            <p>本規約は日本法に準拠し、解釈されるものとします。</p>
          </div>

          <p className="text-xs text-gray-400 pt-4">最終更新: 2026年3月31日</p>
        </section>
      </main>
    </div>
  );
}
