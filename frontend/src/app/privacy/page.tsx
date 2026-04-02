import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "プライバシーポリシー | GallopLab",
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
            <ul className="list-disc list-inside space-y-1">
              <li>
                <span className="font-medium">Google アカウント認証情報:</span>{" "}
                Google OAuth 認証を通じて、メールアドレス・氏名・プロフィール画像を取得します。
                これらは会員識別およびサービス提供のために使用します。
              </li>
              <li>
                <span className="font-medium">決済情報:</span>{" "}
                クレジットカード情報は決済代行会社 Stripe Inc.（米国）が管理します。
                本サービスはカード番号・有効期限・セキュリティコードを保持しません。
              </li>
              <li>
                <span className="font-medium">アクセスログ:</span>{" "}
                IPアドレス・ブラウザ情報・アクセス日時等のログを収集します。
                これらはセキュリティ管理およびサービス改善のために使用します。
              </li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">2. 利用目的</h2>
            <ul className="list-disc list-inside space-y-1">
              <li>本サービスの提供・認証・課金処理</li>
              <li>サービスに関する重要なシステム通知（障害・メンテナンス・規約変更等）</li>
              <li>不正利用の検知・防止</li>
              <li>サービスの品質向上および新機能開発のための統計分析</li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">3. 第三者への提供</h2>
            <p className="mb-2">
              本サービスは以下の第三者にデータを提供します。それ以外への提供・販売・貸与は一切行いません。
            </p>
            <ul className="list-disc list-inside space-y-1">
              <li>
                <span className="font-medium">Stripe Inc.（米国）:</span>{" "}
                サブスクリプション課金処理のため決済データを提供します。
                米国への個人データ国際移転が発生します。
                Stripe のプライバシーポリシーは{" "}
                <a
                  href="https://stripe.com/jp/privacy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline text-blue-600 hover:text-blue-800"
                >
                  stripe.com/jp/privacy
                </a>{" "}
                をご参照ください。
              </li>
              <li>
                <span className="font-medium">Google LLC:</span>{" "}
                Google OAuth 認証のためにデータのやり取りが発生します。
                Google のプライバシーポリシーは Google 社のサイトをご参照ください。
              </li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">4. Cookie の使用</h2>
            <p>
              本サービスはセッション管理のために Cookie（認証トークン）を使用します。
              これらは機能上必須の Cookie であり、サービスの利用に不可欠です。
              Google OAuth 認証に伴い Google のサービスが Cookie を設定する場合があります。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">5. データの保管・管理</h2>
            <p>
              取得した個人情報はアクセス制限・暗号化等の適切なセキュリティ対策を講じたサーバーで管理します。
              認証セッションは暗号化された Cookie として保管されます。
              退会後は法令で定める保存期間を除き、速やかに削除します。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">6. 競馬データについて</h2>
            <p>
              本サービスが表示する競馬情報（レース成績・オッズ等）は JRA-VAN Data Lab
              から取得したデータをもとにした指数・分析情報です。
              JRA が提供するレース映像・写真等の著作物は一切使用していません。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">7. 保有個人データの開示・訂正・削除の請求</h2>
            <p>
              ご自身の保有個人データについて開示・訂正・削除をご希望の場合は、下記の問い合わせ先までご連絡ください。
              ご本人確認のうえ、合理的な期間内に対応します。
              <br />
              問い合わせ先:{" "}
              <a href="mailto:contact@galloplab.com" className="underline text-blue-600 hover:text-blue-800">
                contact@galloplab.com
              </a>
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">8. 個人情報管理責任者</h2>
            <p>
              GallopLab 運営管理者
              <br />
              問い合わせ:{" "}
              <a href="mailto:contact@galloplab.com" className="underline text-blue-600 hover:text-blue-800">
                contact@galloplab.com
              </a>
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">9. 本ポリシーの変更</h2>
            <p>
              本ポリシーの内容は法令の変更やサービス内容の変化に応じて改定することがあります。
              重要な変更の場合は、サービス内またはメールにてお知らせします。
              変更後のポリシーは本ページに掲示した時点で効力を生じます。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">10. お問い合わせ</h2>
            <p>
              プライバシーに関するご質問・ご相談は下記までご連絡ください。
              <br />
              メールアドレス:{" "}
              <a href="mailto:contact@galloplab.com" className="underline text-blue-600 hover:text-blue-800">
                contact@galloplab.com
              </a>
            </p>
          </div>

          <p className="text-xs text-gray-400 pt-4">最終更新: 2026年4月2日</p>
        </section>
      </main>
    </div>
  );
}
