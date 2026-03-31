import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "プライバシーポリシー | kiseki",
  robots: { index: false },
};

export default function PrivacyPage() {
  return (
    <div className="min-h-screen" style={{ background: "#f0f5fb" }}>
      <main id="main-content" className="max-w-2xl mx-auto px-4 py-12">
        <h1 className="text-2xl font-bold text-gray-800 mb-6">プライバシーポリシー</h1>

        <section className="space-y-6 text-sm text-gray-700 leading-relaxed">
          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">1. 収集する情報</h2>
            <p>
              本サービスは Google アカウント認証を使用します。認証時に Google から提供される
              ユーザー識別子（sub）を使用してセッション管理を行います。
              メールアドレスや氏名等の個人情報は本サービスのデータベースには保存しません。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">2. 利用目的</h2>
            <p>収集した情報は、本サービスへのログイン認証のためにのみ使用します。</p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">3. Cookie の使用</h2>
            <p>
              本サービスはセッション管理のために Cookie（認証トークン）を使用します。
              これらは機能上必須の Cookie であり、サービスの利用に不可欠です。
              Google OAuth 認証に伴い Google のサービスが Cookie を設定する場合があります。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">4. 第三者への提供</h2>
            <p>収集した個人情報を第三者に提供・販売・貸与することは一切ありません。</p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">5. データの保管</h2>
            <p>
              認証セッションは暗号化されたCookieとして保管されます。
              セッションの有効期限は限定的であり、ブラウザを閉じると終了します。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">6. 競馬データについて</h2>
            <p>
              本サービスが表示する競馬情報（レース成績・オッズ等）はJRA-VAN Data Lab
              から取得したデータを元にした指数・分析情報です。
              JRAが提供するレース映像・写真等の著作物は一切使用していません。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">7. 本ポリシーの変更</h2>
            <p>本ポリシーの内容は予告なく変更される場合があります。</p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">8. お問い合わせ</h2>
            <p>プライバシーに関するご質問は、サービス管理者までお問い合わせください。</p>
          </div>

          <p className="text-xs text-gray-400 pt-4">最終更新: 2026年3月31日</p>
        </section>
      </main>
    </div>
  );
}
