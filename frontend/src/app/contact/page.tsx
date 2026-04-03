import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "お問い合わせ | GallopLab",
  robots: { index: false },
};

export default function ContactPage() {
  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      <main id="main-content" className="max-w-2xl mx-auto px-4 py-12">
        <h1 className="text-2xl font-bold text-gray-800 mb-6">お問い合わせ</h1>

        <section className="text-sm text-gray-700 leading-relaxed space-y-6">
          <p>
            GallopLab に関するご質問・ご意見・不具合報告は、以下のメールアドレスまでお送りください。
          </p>

          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <dl className="space-y-4">
              <div>
                <dt className="font-semibold text-gray-800 mb-1">メールアドレス</dt>
                <dd>
                  <a
                    href="mailto:contact@galloplab.com"
                    className="text-blue-600 underline hover:text-blue-800"
                  >
                    contact@galloplab.com
                  </a>
                </dd>
              </div>
              <div>
                <dt className="font-semibold text-gray-800 mb-1">対応時間</dt>
                <dd className="text-gray-600">平日 10:00〜17:00（土日祝・年末年始を除く）</dd>
              </div>
              <div>
                <dt className="font-semibold text-gray-800 mb-1">回答までの目安</dt>
                <dd className="text-gray-600">3営業日以内</dd>
              </div>
            </dl>
          </div>

          <p className="text-xs text-gray-400">
            ※ お問い合わせの内容によっては、回答できない場合があります。
          </p>
        </section>
      </main>
    </div>
  );
}
