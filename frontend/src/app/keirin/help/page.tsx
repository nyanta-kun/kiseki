import Link from "next/link";
import { ArrowLeft, Bike } from "lucide-react";

// ---------------------------------------------------------------------------
// 静的データ（ランク体系 2026-07-16 名称整理: SS→S1・U→S2・M→S3・A新設。
// S/S+は2026-07-15全廃・数値は実精算方式 / 検証はクリーン分割: 学習 2022-12〜2026-03・
// 時系列CV検証・テスト 2026-04〜06（学習未使用）・2026-07〜 本番フォワード）
// ---------------------------------------------------------------------------

const RANKS = [
  {
    key: "S1",
    bg: "#d97706",
    label: "S1",
    title: "S1ランク（旧SS・実賭け）",
    subtitle: "7車 ｜ 三連複 ｜ 全目購入",
    test: "67.3%",
    testSub: "1.6R/日・189R・的中24.3%（実精算・条件見直し検討中）",
    condition: "全目オッズ min ≥ 7.0倍 かつ gap12 ≥ 0.10 かつ gap23 ≥ 1pt かつ 非選抜レース",
    detail:
      "「どの相手が来ても7倍以上つく」レースだけを、指数1・2位を軸に全相手へ流す。的中条件は軸2車が3着以内に入ることだけ（3着は誰でもよい）ため的中率が高く、順当決着で配当が安いレースはレースごと見送る。唯一の実賭けランク。",
    investment: "全相手点数 × 100円/レース（7車=500円）",
  },
  {
    key: "S2",
    bg: "#0e7490",
    label: "S2",
    title: "S2ランク（旧U・波乱ライン連れ込み／ペーパー検証中）",
    subtitle: "7車 ｜ 三連複 2車軸流し ｜ 検証記録のみ・賭けなし",
    test: "127.7%",
    testSub: "検証期間90R・的中14.4%（実精算・ペーパー）",
    condition:
      "指数エントロピー ≥ 1.84 かつ 盤面min三連複 ≥ 4.3 かつ 穴（市場4-7位∧モデル3位内∧ライン先頭/番手）× 同ライン「逃」相方 ｜ 目オッズ ≥ 15倍のみ",
    detail:
      "波乱見込みレースで、市場評価は低いがモデル評価が高い「穴」と同ラインの逃げ相方を2車軸にした三連複流し。ライン連れ込み（同ライン2車が共に上位に来る）を狙う。2026-08末に live 100R 以上で採否判定。",
    investment: "ペーパートレード（名目 100円/点）",
  },
  {
    key: "S3",
    bg: "#7c3aed",
    label: "S3",
    title: "S3ランク（旧M・◎不一致×システム◎／ペーパー検証中）",
    subtitle: "7車 ｜ 三連複 2車軸流し ｜ 検証記録のみ・賭けなし",
    test: "78.9%",
    testSub: "検証期間35R・的中8.6%（n小のため参考値・ペーパー）",
    condition:
      "WINTICKET◎ ≠ システム◎（モデル1位）かつ 指数エントロピー ≥ 1.84 かつ 盤面min三連複 ≥ 4.3 ｜ システム◎×同ライン「逃」相方の三連複流し・目オッズ ≥ 15倍のみ",
    detail:
      "外部予想（WINTICKET◎）と当システムの◎が割れた波乱見込みレースで、システム◎と同ラインの逃げ相方を2車軸にした三連複流し。S2と同一買い目になる場合はS2優先で記録しない。2026-08末に live 100R 以上で採否判定。",
    investment: "ペーパートレード（名目 100円/点）",
  },
  {
    key: "A",
    bg: "#059669",
    label: "A",
    title: "Aランク（◎一致×波乱×別ライン先頭・二連単／ペーパー検証中）",
    subtitle: "7車 ｜ 二連単 軸→全流し ｜ 検証記録のみ・賭けなし",
    test: "93.2%",
    testSub: "約9R/日・727R・的中15.7%（実精算・ペーパー）",
    condition:
      "WINTICKET◎ = システム◎（一致）かつ 指数エントロピー ≥ 1.84 ｜ 軸 = ◎と別ラインの先頭のうち競走得点最上位 ｜ 二連単 軸→全のうち目オッズ 5〜50倍のみ",
    detail:
      "◎が一致した波乱見込みレースでは◎の1着率は約34%まで低下する。その「◎が勝たない残り66%」を、別ライン先頭（得点最上位）の1着固定二連単で狙う。◎が1着なら自動的に外れる構造のため◎推奨と相補的。1日約10レースの推奨量を確保する的中率重視ランク（ROIは損益分岐圏）。live 100R 以上で採否判定。",
    investment: "ペーパートレード（名目 100円/点・平均3.3点/R）",
  },
];

// クリーン検証 月別（テスト 2026-04〜06 = 学習未使用 / 2026-07〜 = 本番フォワード）
const MONTHLY = [
  { month: "2026-03", ss: "27.6%", kind: "検証" },
  { month: "2026-04", ss: "63.2%", kind: "検証" },
  { month: "2026-05", ss: "83.4%", kind: "検証" },
  { month: "2026-06", ss: "94.2%", kind: "検証" },
  { month: "2026-07", ss: "76.5%", kind: "フォワード（〜7/15）" },
];

const TERMS = [
  {
    term: "gap12",
    def: "AIモデルが予測した「指数1位の3着内確率 − 2位の確率」の差。大きいほど軸の優位性が高い。S1は≥0.10。",
  },
  {
    term: "gap23",
    def: "指数2位と3位の確率差（S1の条件）。2着候補の質を測る。",
  },
  {
    term: "指数エントロピー",
    def: "レース内の予測確率分布の混戦度（S2/S3/Aの条件・≥1.84）。大きいほど「どの車も来うる」波乱見込みレース。",
  },
  {
    term: "ガミ条件（レース単位）",
    def: "購入する全買い目の最低オッズによるレース選別。1目でも閾値未満（S1=7倍）ならレースごと見送る。順当決着で配当が安すぎるレースを外すことが回収率の源泉。買い目単位のカットは行わない。",
  },
  {
    term: "ペーパートレード",
    def: "実際には賭けず、発走15分前に確定した買い目を記録して成績だけを追う検証方式（S2/S3/A）。live実測で優位性が確認できたランクのみ実賭けに昇格する。",
  },
  {
    term: "テスト回収率",
    def: "学習に一切使っていない 2026-04〜06 の91日間での回収率。モデルは 2022-12〜2026-03 で学習し、検証（時系列CV）も学習期間内で完結。リークなし。",
  },
  {
    term: "実精算方式（2026-07-15〜）",
    def: "指数・買い目は発走前のオッズ盤面掲載車で作成し、落車・失格・棄権が絡んだ買い目は購入のまま外れ計上（返還しない）。欠車のみ返還。実際の車券精算と同一ルール。旧表示（完走者だけで指数を組み直す方式）は落車を事前に知っている前提になり回収率を約2〜4倍過大評価していたため全面改定した。",
  },
  {
    term: "フォワード回収率",
    def: "2026-07-01 以降の前向き検証。本番モデル（学習 ≤2026-06-30）にとって完全に未知の期間。",
  },
  {
    term: "三連複・二連単",
    def: "三連複=1〜3着を順不同で当てる（S1/S2/S3）。二連単=1着と2着を着順どおりに当てる（A）。",
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
            <p className="text-xs text-gray-500">S1ランク 検証期間回収率（実精算・実賭け）</p>
            <p className="text-lg font-bold text-amber-600">67.3%</p>
            <p className="text-xs text-gray-400">1.6R/日 ・ 189R ・ 的中24.3%</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">Aランク 検証期間回収率（実精算・ペーパー）</p>
            <p className="text-lg font-bold text-emerald-600">93.2%</p>
            <p className="text-xs text-gray-400">約9R/日 ・ 727R ・ 的中15.7%</p>
          </div>
        </div>
        <p className="text-xs text-gray-400">
          ★ 検証期間 = 2026-06-30以前（S1は2026-03-01〜・S2/S3/Aは2026-04-13〜）。
          2026-07以降は本番フォワード。S2/S3/A はペーパートレード（賭けなし・記録のみ）検証中。
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
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S1（三連複）</th>
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">区分</th>
              </tr>
            </thead>
            <tbody>
              {MONTHLY.map((m) => (
                <tr key={m.month} className="border-b border-gray-50 last:border-0">
                  <td className="py-1.5 px-3 text-gray-600">{m.month.replace("-", "/")}</td>
                  <td className="py-1.5 px-3 text-right font-semibold text-emerald-600">{m.ss}</td>
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
          <li>2026-07-16 にランク名称を整理: SS→S1、U→S2、M→S3、A新設。定義・条件は名称変更前と同一（S1=旧SS）。</li>
          <li>2026-07-16 に指数へ競走得点トレンド4特徴を追加（選手の成長・好不調を反映／モデル44特徴化）。本ページの検証数値は新指数で全期間再計算済み。S1はROI低迷のため条件見直しを検討中。</li>
          <li>表示回収率は実精算方式（落車・失格は外れ計上・欠車のみ返還）。S1のテスト期間の実精算ROIは100%未満であり、現体系は損益分岐圏で運用・検証中。</li>
          <li>S2/S3/A はペーパートレード（実際の賭けなし・記録のみ）。live 100R 以上の実測で採否判定する。集計サマリー（投資・回収）には含めない。</li>
          <li>Aランクの過去実績（2026-04-13〜）はOOS評価モデルによる遡及再判定。買い目は発走前オッズ盤面基準・落車失格は外れ計上・欠車（出走取消）は返還として構築。</li>
          <li>2026-07-16 に4分戦見送り・ライン格差増額（200円/点）を廃止（実精算再検証で検証期間間の再現性なし）。現行の見送り条件は選抜レースのみ。</li>
          <li>ガミ判定は発走15分前オッズで行うため、最終オッズ基準の検証値とは対象の出入りが多少ある。</li>
          <li>欠車（出走取消）は、軸欠車=レース無効（返還）、相手欠車=その目のみ除外として扱う。</li>
          <li>S/S+ランク（三連単F）・旧Aランク（買い目カット方式）は優位性が確認できなかったため廃止済み。</li>
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
