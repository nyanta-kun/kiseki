import Link from "next/link";
import { ArrowLeft, Bike } from "lucide-react";

// ---------------------------------------------------------------------------
// 静的データ
// ---------------------------------------------------------------------------

const RANKS = [
  {
    key: "7SS",
    bg: "#d97706",
    label: "7SS",
    title: "SSランク",
    subtitle: "7車以上 ｜ 三連複 ｜ 厳選1〜3点",
    hold: "137.0%",
    val: "159.8%",
    condition: "gap12 ≥ 0.07 かつ ガミ目カット後の残り点数 ≤ 3",
    detail:
      "全買い目のうちオッズ5倍未満のものを除外し、残った買い目が1〜3点のレースのみ購入。点数が絞られるほど高回収。",
    breakdown: [
      { label: "1点残り", roi: "2,105.9%", count: "22R/月" },
      { label: "2点残り", roi: "440.7%", count: "1.6R/日" },
      { label: "3点残り", roi: "105.9%", count: "14.7R/日" },
    ],
    investment: "残り点数 × 100円/レース",
  },
  {
    key: "7S",
    bg: "#0891b2",
    label: "7S",
    title: "Sランク",
    subtitle: "7車以上 ｜ 三連複 ｜ 全相手流し",
    hold: "~143%",
    val: "129.9%(S/A合算)",
    condition: "全買い目オッズ min ≥ 5.0倍（ガミ無し） かつ gap12 ≥ 0.10",
    detail:
      "軸1・2位間の確率差が0.10以上で、かつ全買い目が5倍以上のレース。高い確信度で全相手に流す。",
    breakdown: [],
    investment: "全相手点数 × 100円/レース",
  },
  {
    key: "7A",
    bg: "#0d9488",
    label: "7A",
    title: "Aランク",
    subtitle: "7車以上 ｜ 三連複 ｜ 全相手流し",
    hold: "~138%",
    val: "129.9%(S/A合算)",
    condition: "全買い目オッズ min ≥ 5.0倍（ガミ無し） かつ gap12 ∈ [0.07, 0.10)",
    detail:
      "ガミ無し条件を満たしつつ gap12 がAランク域。Sランクより件数が約7倍多く分散投資に適する。",
    breakdown: [],
    investment: "全相手点数 × 100円/レース",
  },
  {
    key: "SS",
    bg: "#b45309",
    label: "SS",
    title: "SS・Sランク（6車以下）",
    subtitle: "6車以下 ｜ 三連単 / 三連複",
    hold: "—",
    val: "—",
    condition: "モデルスコア上位＋独自条件（詳細非公開）",
    detail: "6車以下レース向けの高優先度推奨。三連単または三連複で購入。",
    breakdown: [],
    investment: "指定点数 × 100円",
  },
  {
    key: "WIDE",
    bg: "#7c3aed",
    label: "W",
    title: "WIDEランク",
    subtitle: "ワイド",
    hold: "—",
    val: "—",
    condition: "上位2車の確率差が小さくオッズ妙味のあるレース",
    detail: "三連系では拾いにくいレースでワイドに特化した推奨。",
    breakdown: [],
    investment: "指定点数 × 100円",
  },
  {
    key: "B",
    bg: "#6b7280",
    label: "B",
    title: "Bランク",
    subtitle: "補助推奨",
    hold: "—",
    val: "—",
    condition: "条件を部分的に満たすレース",
    detail: "SS/S/A条件を一部満たすが確信度がやや低い。参考として表示。",
    breakdown: [],
    investment: "指定点数 × 100円",
  },
];

const MONTHLY_ROI = [
  { month: "2025-07", roi: "206.5%" },
  { month: "2025-08", roi: "186.5%" },
  { month: "2025-09", roi: "132.2%" },
  { month: "2025-10", roi: "139.3%" },
  { month: "2025-11", roi: "127.1%" },
  { month: "2025-12", roi: "179.6%" },
  { month: "2026-01", roi: "152.3%" },
  { month: "2026-02", roi: "156.9%" },
  { month: "2026-03", roi: "138.6%" },
  { month: "2026-04", roi: "134.9%" },
  { month: "2026-05", roi: "145.4%" },
  { month: "2026-06", roi: "117.4%（部分）" },
];

const TERMS = [
  {
    term: "gap12",
    def: "AIモデルが予測した「1位確率 − 2位確率」の差。大きいほど軸の優位性が高く、買い目を絞りやすい。",
  },
  {
    term: "gami（ガミ）",
    def: "購入する全買い目の中の最低オッズ。全点買った場合の「損確定」を示す指標。gami ≥ 5.0倍の場合はどの目が当たっても単純損はない。",
  },
  {
    term: "ガミ目カット",
    def: "三連複の各買い目（軸1-軸2-相手）のオッズが5.0倍未満のものを除外する操作。回収効率の低い目を省く。",
  },
  {
    term: "HOLD ROI",
    def: "学習データ外の直近期間（2026-03〜2026-06）での回収率。モデルのリークがない最も信頼性の高い指標。",
  },
  {
    term: "VAL ROI",
    def: "検証期間（2025-07〜2026-02）での回収率。学習時に参照しない期間で算出。",
  },
  {
    term: "三連複",
    def: "1・2・3着を順不同で的中させる馬券。三連単より的中しやすく、7車以上では配当も期待できる。",
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
          LightGBMモデルによる出走選手の確率予測をもとに、回収率が高いと判定されたレースを自動推奨します。
          推奨は毎朝8:00（日中レース）と16:00（夜の部）に生成されます。
        </p>
        <div className="grid grid-cols-2 gap-2 pt-1">
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">SSランク HOLD回収率</p>
            <p className="text-lg font-bold text-emerald-600">137%</p>
            <p className="text-xs text-gray-400">16.5R/日 ・ 1,716R検証</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">S/Aランク HOLD回収率</p>
            <p className="text-lg font-bold text-emerald-600">138%</p>
            <p className="text-xs text-gray-400">12.9R/日 ・ 1,381R検証</p>
          </div>
        </div>
        <p className="text-xs text-gray-400">
          ★ HOLD = 学習期間外（2026-03〜06）のリーク無し検証。全12ヶ月黒字。
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
              {r.hold !== "—" && (
                <div className="ml-auto text-right">
                  <p className="text-xs text-gray-400">HOLD</p>
                  <p className="text-sm font-bold text-emerald-600">{r.hold}</p>
                </div>
              )}
            </div>
            <div className="px-4 py-3 space-y-2">
              <div>
                <p className="text-xs font-medium text-gray-500 mb-0.5">選定条件</p>
                <p className="text-xs sm:text-sm text-gray-700 font-mono bg-gray-50 rounded px-2 py-1">{r.condition}</p>
              </div>
              <p className="text-xs sm:text-sm text-gray-600">{r.detail}</p>
              {r.breakdown.length > 0 && (
                <div className="pt-1">
                  <p className="text-xs font-medium text-gray-500 mb-1">残り点数別内訳（HOLD）</p>
                  <div className="grid grid-cols-3 gap-1">
                    {r.breakdown.map((b) => (
                      <div key={b.label} className="bg-emerald-50 rounded-lg p-2 text-center">
                        <p className="text-xs text-gray-500">{b.label}</p>
                        <p className="text-sm font-bold text-emerald-600">{b.roi}</p>
                        <p className="text-xs text-gray-400">{b.count}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <p className="text-xs text-gray-400">投資: {r.investment}</p>
            </div>
          </div>
        ))}
      </section>

      {/* SSランク月別ROI */}
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
        <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50">
          <h2 className="text-sm font-bold text-gray-800">SSランク 月別回収率（12ヶ月連続黒字）</h2>
          <p className="text-xs text-gray-400 mt-0.5">VAL(2025-07〜2026-02) + HOLD(2026-03〜06) 連続</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs sm:text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">月</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">回収率</th>
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">月</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">回収率</th>
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: Math.ceil(MONTHLY_ROI.length / 2) }, (_, i) => {
                const left = MONTHLY_ROI[i * 2];
                const right = MONTHLY_ROI[i * 2 + 1];
                return (
                  <tr key={left.month} className="border-b border-gray-50 last:border-0">
                    <td className="py-1.5 px-3 text-gray-600">{left.month.replace("-", "/")}</td>
                    <td className="py-1.5 px-3 text-right font-semibold text-emerald-600">{left.roi}</td>
                    {right ? (
                      <>
                        <td className="py-1.5 px-3 text-gray-600">{right.month.replace("-", "/")}</td>
                        <td className="py-1.5 px-3 text-right font-semibold text-emerald-600">{right.roi}</td>
                      </>
                    ) : (
                      <><td /><td /></>
                    )}
                  </tr>
                );
              })}
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
          <li>3バイアス修正済み（欠車生存バイアス・6車以下混入・週次再学習リーク）。</li>
          <li>オッズは推奨生成時点のもの。購入前に最新オッズを確認推奨。</li>
          <li>欠車（出走取消）があった場合、軸欠車はレース無効（返還）、相手欠車はその目のみ除外。</li>
          <li>SSとS/Aは同一レースに重複して出ることがある（別条件）。両方独立して購入。</li>
          <li>live検証開始: 2026-06-16〜（目安100R≈1週間で初回判断）。</li>
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
