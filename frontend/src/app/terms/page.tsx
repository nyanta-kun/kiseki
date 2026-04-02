import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "利用規約 | GallopLab",
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
              GallopLab（以下「本サービス」）は、JRA-VAN Data Lab のデータをもとにAIアルゴリズムで
              競馬予測指数・期待値分析を算出し、合理的な馬券購入判断を支援する競馬情報サービスです。
              月額有料のサブスクリプション制を採用しており、一部機能は無料でご利用いただけます。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">2. 定義</h2>
            <ul className="list-disc list-inside space-y-1">
              <li>「本サービス」とは、GallopLab が提供する競馬AI指数・期待値分析サービスを指します。</li>
              <li>「会員」とは、本規約に同意のうえ本サービスに登録したユーザーを指します。</li>
              <li>「有料会員」とは、月額サブスクリプションを契約中の会員を指します。</li>
              <li>「コンテンツ」とは、本サービスが提供する指数・期待値・分析情報等を指します。</li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">3. 会員登録・アカウント</h2>
            <p>
              本サービスへの登録は Google アカウントによる OAuth 認証で行います。
              登録にあたり、Google アカウントに紐づくメールアドレスおよび氏名を取得します。
              会員はアカウント情報を適切に管理し、第三者に貸与・譲渡してはなりません。
              不正利用が判明した場合、本サービスは予告なくアカウントを停止できます。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">4. 料金・支払い・サブスクリプション</h2>
            <ul className="list-disc list-inside space-y-1">
              <li>月額料金: 2,980円（税込）</li>
              <li>
                支払方法: クレジットカード（Visa / Mastercard / American Express / JCB）。
                決済は Stripe Inc. を通じて行われます。
              </li>
              <li>
                自動更新: サブスクリプションは毎月自動で更新されます。
                解約しない限り、毎月同日に料金が請求されます。
              </li>
              <li>
                解約方法: マイページ &gt; サブスクリプション設定 &gt; 解約手続きよりお手続きください。
              </li>
              <li>
                解約後の利用: 解約手続き完了後、当月末日まで引き続きサービスをご利用いただけます。
                翌月以降の課金は停止します。
              </li>
              <li>
                返金ポリシー: デジタルコンテンツの性質上、原則として返金はお受けしておりません。
                ただし、未提供期間がある場合は個別にご対応いたします。
              </li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">5. 無料プラン</h2>
            <p>
              毎開催の各競馬場において1レース目（第1競走）の全指数・期待値は、
              会員登録なしで無料公開しています。
              それ以外のレースの数値は有料会員限定のコンテンツです。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">6. 禁止事項</h2>
            <ul className="list-disc list-inside space-y-1">
              <li>本サービスが提供する予想情報・指数・期待値の転売・有償配布・二次配布</li>
              <li>コンテンツの無断転載・スクレイピング・データの商業利用</li>
              <li>本サービスへの不正アクセス・過負荷をかける行為</li>
              <li>他の会員または第三者へのなりすまし行為</li>
              <li>その他、法令または公序良俗に違反する行為</li>
            </ul>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">7. 免責事項</h2>
            <p>
              本サービスが提供する競馬予測指数・期待値分析はAIアルゴリズムによる算出結果であり、
              情報提供を目的とするものです。馬券的中・利益獲得を保証するものではありません。
              馬券の購入・投票はユーザー自身の判断と責任で行ってください。
              本サービスの情報を参考にした馬券購入による損失について、
              運営管理者は一切の責任を負いません。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">8. サービスの変更・終了</h2>
            <p>
              運営管理者は、サービスの品質維持・改善のため予告なく本サービスの内容を変更・停止・
              終了することができます。サービス終了の場合は、可能な限り事前にお知らせします。
              これによりユーザーに生じた損害について責任を負いません。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">9. 知的財産権</h2>
            <p>
              本サービスに関する著作権・商標権その他の知的財産権はすべて運営管理者に帰属します。
              本規約に定める範囲を超えた使用・複製・転載等は一切禁止します。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">10. 個人情報の取り扱い</h2>
            <p>
              個人情報の収集・利用・管理については、
              別途定める<a href="/privacy" className="underline text-blue-600 hover:text-blue-800">プライバシーポリシー</a>に従います。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">11. 準拠法・管轄裁判所</h2>
            <p>
              本規約は日本法に準拠し、解釈されるものとします。
              本サービスに関連する紛争については、東京地方裁判所を第一審の専属的合意管轄裁判所とします。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">12. 利用規約の変更</h2>
            <p>
              運営管理者は、必要に応じて本規約を変更することができます。
              変更後の規約は本サービス上に掲示した時点で効力を生じます。
              重要な変更の場合は登録メールアドレスへの通知またはサービス内での告知を行います。
            </p>
          </div>

          <div>
            <h2 className="font-semibold text-base text-gray-800 mb-2">13. 問い合わせ先</h2>
            <p>
              本規約に関するお問い合わせは下記までご連絡ください。
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
