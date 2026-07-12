import Link from "next/link";
import { ArrowLeft, Bike } from "lucide-react";

// ---------------------------------------------------------------------------
// 静的データ（ランク体系 2026-07-10〜 / 検証はクリーン分割: 学習 2022-12〜2026-03・
// 時系列CV検証・テスト 2026-04〜06（学習未使用）・2026-07〜 本番フォワード）
// ---------------------------------------------------------------------------

const RANKS = [
  {
    key: "SS",
    bg: "#d97706",
    label: "SS",
    title: "SSランク",
    subtitle: "7車以上 ｜ 三連複 ｜ 全目購入",
    test: "213.9%",
    testSub: "3.8R/日・345R・的中33.3%",
    condition: "全目オッズ min ≥ 7.0倍 かつ gap12 ≥ 0.10 かつ gap23 ≥ 1pt",
    detail:
      "「どの相手が来ても7倍以上つく」レースだけを、指数1・2位を軸に全相手へ流す。的中条件は軸2車が3着以内に入ることだけ（3着は誰でもよい）ため的中率が高く、順当決着で配当が安いレースはレースごと見送る。",
    investment: "全相手点数 × 100円/レース（7車=500円）",
  },
  {
    key: "S",
    bg: "#1d4ed8",
    label: "S",
    title: "Sランク",
    subtitle: "7車以上 ｜ 三連単フォーメーション ｜ 1着固定",
    test: "142.1%",
    testSub: "10.0R/日・910R・的中19.0%",
    condition: "全目オッズ min ≥ 10倍 かつ gap12 ≥ 0.15",
    detail:
      "1着=指数1位固定・2着=指数2,3位・3着=全通りの三連単フォーメーション。的中条件は「1着が指数1位 かつ 2着が指数2,3位」。gap12が大きい=1位の1着信頼度が高いレースに限定し、全目10倍以上のレース単位ガミ条件で安配当レースを除外する。",
    investment: "2×(車数-2)点 × 100円/レース（7車=1,000円）",
  },
  {
    key: "S+",
    bg: "#4338ca",
    label: "S+",
    title: "S+（Sランク増額）",
    subtitle: "7車以上 ｜ 三連単フォーメーション ｜ 増額200円/点",
    test: "237.2%",
    testSub: "1.9R/日・169R・的中27.2%",
    condition: "Sの条件 かつ gap12 ≥ 0.25 かつ gap34 ≥ 0.04",
    detail:
      "Sランクのうち、1位が突出し（gap12≥0.25）かつ2着候補（指数2,3位）が4位以下と明確に離れているレース。的中率・回収率ともSランク内で最良の帯のため、賭金を2倍（200円/点）に増額する。",
    investment: "2×(車数-2)点 × 200円/レース（7車=2,000円）",
  },
];

// クリーン検証 月別（テスト 2026-04〜06 = 学習未使用 / 2026-07〜 = 本番フォワード）
const MONTHLY = [
  { month: "2026-04", ss: "210.1%", s: "139.7%", kind: "テスト" },
  { month: "2026-05", ss: "192.3%", s: "163.1%", kind: "テスト" },
  { month: "2026-06", ss: "242.7%", s: "123.7%", kind: "テスト" },
  { month: "2026-07", ss: "163.2%", s: "181.0%", kind: "フォワード（〜7/9）" },
];

const TERMS = [
  {
    term: "gap12",
    def: "AIモデルが予測した「指数1位の3着内確率 − 2位の確率」の差。大きいほど軸の優位性が高い。SSは≥0.10、Sは≥0.15、S+は≥0.25。",
  },
  {
    term: "gap23 / gap34",
    def: "指数2位と3位の確率差（gap23・SSの条件）／3位と4位の確率差（gap34・S+の条件）。2着候補の質を測る。",
  },
  {
    term: "ガミ条件（レース単位）",
    def: "購入する全買い目の最低オッズによるレース選別。1目でも閾値未満（SS=7倍・S=10倍）ならレースごと見送る。順当決着で配当が安すぎるレースを外すことが回収率の源泉。買い目単位のカットは行わない。",
  },
  {
    term: "テスト回収率",
    def: "学習に一切使っていない 2026-04〜06 の91日間での回収率。モデルは 2022-12〜2026-03 で学習し、検証（時系列CV）も学習期間内で完結。リークなし。",
  },
  {
    term: "フォワード回収率",
    def: "2026-07-01 以降の前向き検証。本番モデル（学習 ≤2026-06-30）にとって完全に未知の期間。",
  },
  {
    term: "三連複 / 三連単F",
    def: "三連複=1〜3着を順不同で当てる（SS）。三連単F=着順まで指定するフォーメーション買い（S/S+・1着固定×2着2頭×3着全通り）。",
  },
];

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function KeirinHelpPage() {
  return (
    <div className="w-full sm:max-w-3xl sm:mx-auto px-3 sm:px-4 py-4 space-y-5">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Link
          href="/keirin"
          className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 transition-colors"
        >
          <ArrowLeft size={16} />
          戻る
        </Link>
        <div className="flex items-center gap-2 ml-1">
          <Bike size={20} className="text-blue-500" />
          <h1 className="text-lg font-extrabold tracking-widest text-gray-950">KEIRIN</h1>
          <span className="text-sm font-semibold text-gray-500">推奨ガイド</span>
        </div>
      </div>

      {/* 概要 */}
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-2">
        <h2 className="text-sm font-bold text-gray-800">システム概要</h2>
        <p className="text-xs sm:text-sm text-gray-600 leading-relaxed">
          LightGBMモデルによる出走選手の3着内確率予測をもとに、期待回収率が高いレースだけを自動推奨します。
          候補は毎朝8:00（日中）と16:00（夜の部）に生成し、最終判定は発走15分前のオッズで確定します。
          モデルは 2026-06-30 以前のデータのみで学習（学習/検証/テストを時系列分割・リークなし）。
        </p>
        <div className="grid grid-cols-2 gap-2 pt-1">
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">SSランク テスト回収率</p>
            <p className="text-lg font-bold text-emerald-600">213.9%</p>
            <p className="text-xs text-gray-400">3.8R/日 ・ 345R ・ 的中33.3%</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">Sランク テスト回収率</p>
            <p className="text-lg font-bold text-emerald-600">142.1%</p>
            <p className="text-xs text-gray-400">10.0R/日 ・ 910R ・ 的中19.0%</p>
          </div>
        </div>
        <p className="text-xs text-gray-400">
          ★ テスト = 学習に未使用の 2026-04〜06（91日）。2026-07以降は本番フォワード検証。
        </p>
      </section>

      {/* ランク説明 */}
      <section className="space-y-3">
        <h2 className="text-sm font-bold text-gray-700 px-1">ランク説明</h2>
        {RANKS.map((r) => (
          <div key={r.key} className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
            <div className="flex items-center gap-3 px-4 py-3 bg-gray-50 border-b border-gray-100">
              <span
                style={{ background: r.bg }}
                className="inline-flex items-center justify-center w-9 h-7 rounded-full text-xs font-bold text-white flex-shrink-0"
              >
                {r.label}
              </span>
              <div>
                <p className="font-semibold text-gray-800 text-sm">{r.title}</p>
                <p className="text-xs text-gray-400">{r.subtitle}</p>
              </div>
              <div className="ml-auto text-right">
                <p className="text-xs text-gray-400">テスト</p>
                <p className="text-sm font-bold text-emerald-600">{r.test}</p>
              </div>
            </div>
            <div className="px-4 py-3 space-y-2">
              <div>
                <p className="text-xs font-medium text-gray-500 mb-0.5">選定条件</p>
                <p className="text-xs sm:text-sm text-gray-700 font-mono bg-gray-50 rounded px-2 py-1">{r.condition}</p>
              </div>
              <p className="text-xs sm:text-sm text-gray-600">{r.detail}</p>
              <p className="text-xs text-gray-400">検証: {r.testSub} ／ 投資: {r.investment}</p>
            </div>
          </div>
        ))}
      </section>

      {/* 月別回収率 */}
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
        <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50">
          <h2 className="text-sm font-bold text-gray-800">月別回収率（クリーン検証）</h2>
          <p className="text-xs text-gray-400 mt-0.5">
            テスト = 学習未使用期間 ／ フォワード = 本番モデルの前向き検証
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs sm:text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">月</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">SS（三連複）</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S計（三連単）</th>
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">区分</th>
              </tr>
            </thead>
            <tbody>
              {MONTHLY.map((m) => (
                <tr key={m.month} className="border-b border-gray-50 last:border-0">
                  <td className="py-1.5 px-3 text-gray-600">{m.month.replace("-", "/")}</td>
                  <td className="py-1.5 px-3 text-right font-semibold text-emerald-600">{m.ss}</td>
                  <td className="py-1.5 px-3 text-right font-semibold text-emerald-600">{m.s}</td>
                  <td className="py-1.5 px-3 text-gray-400 text-xs">{m.kind}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* 用語解説 */}
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-3">
        <h2 className="text-sm font-bold text-gray-800">用語解説</h2>
        <dl className="space-y-2.5">
          {TERMS.map((t) => (
            <div key={t.term}>
              <dt className="text-xs font-bold text-gray-700">{t.term}</dt>
              <dd className="text-xs text-gray-500 mt-0.5 leading-relaxed">{t.def}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* 注意事項 */}
      <section className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-1.5">
        <h2 className="text-sm font-bold text-amber-800">注意事項</h2>
        <ul className="text-xs text-amber-700 space-y-1 list-disc list-inside">
          <li>バックテスト結果は過去データによるもの。将来の回収率を保証しない。</li>
          <li>ガミ判定は発走15分前オッズで行うため、最終オッズ基準の検証値とは対象の出入りが多少ある。</li>
          <li>三連単（S/S+）は分散が大きく単月赤字が起こり得る。週次〜月次での評価を推奨。</li>
          <li>欠車（出走取消）は、軸欠車=レース無効（返還）、相手欠車=その目のみ除外として扱う。</li>
          <li>SSとS/S+は同一レースに重複して出ることがある（券種が別）。両方独立して購入。</li>
          <li>本ランク体系のlive検証開始: 2026-07-10〜。それ以前の期間も全て現行体系の条件で遡及再判定した実績を表示している（旧・買い目カット方式の行は存在しない）。</li>
        </ul>
      </section>

      {/* フッター */}
      <div className="pb-4 text-center">
        <Link
          href="/keirin"
          className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 font-medium"
        >
          <ArrowLeft size={14} />
          ピック一覧に戻る
        </Link>
      </div>
    </div>
  );
}
