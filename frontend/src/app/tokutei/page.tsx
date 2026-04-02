import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "特定商取引法に基づく表記 | GallopLab",
  robots: { index: false },
};

export default function TokuteiPage() {
  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      <main id="main-content" className="max-w-2xl mx-auto px-4 py-12">
        <h1 className="text-2xl font-bold text-gray-800 mb-6">特定商取引法に基づく表記</h1>

        <section className="text-sm text-gray-700 leading-relaxed">
          <dl className="divide-y divide-gray-200 border-t border-b border-gray-200">
            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">販売業者</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">GallopLab（ギャロップラボ）</dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">運営責任者</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">[運営責任者氏名]</dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">所在地</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">[バーチャルオフィス住所を記入予定]</dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">電話番号</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                [電話番号を記入予定]
                <br />
                <span className="text-gray-500">受付時間: 平日10:00〜17:00</span>
              </dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">メールアドレス</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                <a href="mailto:contact@galloplab.com" className="underline text-blue-600 hover:text-blue-800">
                  contact@galloplab.com
                </a>
              </dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">サービス名称</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">GallopLab 競馬AI指数サービス</dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">役務の対価</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">月額2,980円（税込）</dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">支払方法</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                クレジットカード（Visa / Mastercard / American Express / JCB）
              </dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">支払時期</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                お申し込み日に初回課金、以降毎月同日に自動更新
              </dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">役務の提供時期</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">決済完了後、即時ご利用いただけます</dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">解約方法</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                マイページ &gt; サブスクリプション設定 &gt; 解約手続きよりお手続きください
              </dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">解約の効力発生時期</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                解約手続き完了後、当月末日まで継続してご利用いただけます。翌月以降の課金は停止します。
              </dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">返金ポリシー</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                デジタルコンテンツの性質上、原則として返金はお受けしておりません。
                ただし、未提供期間がある場合は個別にご対応いたします。
              </dd>
            </div>

            <div className="py-4 sm:grid sm:grid-cols-3 sm:gap-4">
              <dt className="font-semibold text-gray-800">動作環境</dt>
              <dd className="mt-1 sm:mt-0 sm:col-span-2">
                Google Chrome / Safari 最新版推奨（スマートフォン・PC対応）
              </dd>
            </div>
          </dl>

          <p className="text-xs text-gray-400 pt-6">最終更新: 2026年4月2日</p>
        </section>
      </main>
    </div>
  );
}
