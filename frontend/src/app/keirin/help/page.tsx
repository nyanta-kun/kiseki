import Link from "next/link";
import { ArrowLeft, Bike } from "lucide-react";

// ---------------------------------------------------------------------------
// 静的データ（ランク体系 2026-08-01 是正版）
//
// 【2026-08-01 是正の経緯】keirin リポジトリ（別リポジトリ・commit f31f84b,
// 2026-07-31）が内部rank名を全面改名した（"RANK_" + 表示ラベル方式へ統一。
// 旧 SEVEN_S7→RANK_7S・SEVEN_7A→RANK_7A・NINE_S9→RANK_9S・NINE_9A→RANK_9A。
// 表示ラベル自体は変更なし）。同日、旧 S1（win軸1着固定×3着内モデル相手2車・
// 三連単2点流し・SEVEN_S1）は正規プロトコルの再検証でも黒字を維持できず全廃、
// gate_label('SS'/'S')によるSEVEN_S7/NINE_S9の7SS/9SS・7S/9S分岐も廃止された
// （commit e994758。rank_7s_gate_label()は常に"S"のみを返す。旧7SS/9SSは
// Sへ吸収）。同時に、空いた"7SS"という表示ラベルを再利用する形で、
// RANK_7SS（波乱軸選出・穴レース検知・commit dc89f14）という全く新しい
// 独立戦略が導入された。
//
// 現行は RANK_7S / RANK_7A / RANK_9S / RANK_9A の4ペーパーランク
// （表示ラベル 7S/7A/9S/9A）。RANK_7SS は 2026-08-02 に全廃（ROI73.5%・
// n=16,298 と控除率75%を下回り続けたため）。数値は keirin側 src/strategy_wt.py の
// CURRENT_PAPER_RANKS（2026-07-31時点の単一正本ロールアップ）に基づく。
// ---------------------------------------------------------------------------

const RANKS = [
  {
    key: "7S",
    bg: "#16a34a",
    label: "7S",
    title: "7Sランク（単勝×複勝指数トップ3重なり軸×波乱度選出／内部名RANK_7S）",
    subtitle: "7車 ｜ 三連複 2軸総流し(5点)",
    test: "79.1%",
    testSub: "honest全期間実績（正規プロトコルの検証/テスト分割は本ランクでは未実施）",
    full: "79.1%",
    fullSub: "全期間実績（2024-01-01〜2026-07-31）: 6,572R・的中率と月次内訳はkeirin側strategy_wt.py参照",
    condition:
      "軸2車 = 単勝指数上位3∩複勝指数上位3の重なり車 ｜ ゲート: axis_sum(軸2車の複勝指数合計)≤1.5 ∧ フィールド指数エントロピー≤1.8329（2026-07-31改定でaxis_sum上限を1.3→1.5へ緩和・mark3ゲートは撤廃） ｜ 軸2車がWT◎◯と完全一致（重なり2）は除外 ｜ 三連複 軸2車+残り5車のいずれか1車の5点（オッズ下限なし）",
    detail:
      "単勝モデルと複勝モデルの両方が上位に評価する2車を軸にする設計。2026-07-21にWT公式◎◯との軸重なり数で7SS/7Sへ表示分割していたが、2026-07-31にgate_label分岐そのものが廃止され単一ランク「7S」に統合された（旧7SS＝重なり0の選出はSへ吸収）。同日、mark3（◎◯△2車一致除外）ゲートを撤廃し、axis_sum上限を1.3→1.5へ緩和したところ、月次vintageモデルでのhonest全期間検証で的中率41.0%・ROI79.3%・月次ROI標準偏差17.3・1日平均6.94件（旧: 的中34.0%・ROI78.5%・標準偏差43.7・0.61件/日）と全指標で改善した。",
    investment: "名目 100円/点・5点/レース・約6.9件/日",
  },
  {
    key: "7A",
    bg: "#78716c",
    label: "7A",
    title: "7Aランク（7Sの境界ランク・2ゲート中1つだけ不合格／内部名RANK_7A）",
    subtitle: "7車 ｜ 三連複 2軸総流し(5点) ｜ 7Sより低配当帯を狙いボリュームを増やす枠",
    test: "77.4%",
    testSub: "honest全期間実績（正規プロトコルの検証/テスト分割は本ランクでは未実施）",
    full: "77.4%",
    fullSub: "全期間実績（2024-01-01〜2026-07-31）: 11,419R",
    condition:
      "軸2車 = 7Sと同じ選定（単勝指数上位3∩複勝指数上位3の重なり車） ｜ 7Sの2条件（axis_sum≤1.5・entropy≤1.8329）のうち、ちょうど1つだけ不合格の候補を採用（0個=7S本体・2個とも不合格は対象外） ｜ 三連複 軸2車+残り5車のいずれか1車の5点（オッズ下限なし）",
    detail:
      "7Sはボリュームが小さいため、ROIはやや落ちても的中率のあるゾーンで推奨数を増やしたいというユーザー要望を受けて2026-07-27に新設。当初は3ゲート（axis_sum・entropy・mark3）のうち1つだけ不合格の候補が対象だったが、2026-07-31に7S本体のmark3ゲート撤廃に合わせて2ゲート化した。honest全期間再検証: 旧3ゲート版4,691R・的中42.8%・ROI81.4%・月次標準偏差22.0 → 新2ゲート版8,306R・的中44.8%・ROI77.6%・月次標準偏差13.4（件数は約1.8倍に増加）。新7Aと新7Sの重複選出はないことを検算済み。",
    investment: "名目 100円/点・5点/レース・約8.8件/日",
  },
  {
    key: "9S",
    bg: "#0891b2",
    label: "9S",
    title: "9Sランク（7Sの9車立て版・独立ランク／内部名RANK_9S）",
    subtitle: "9車 ｜ 三連複 2軸総流し(7点)",
    test: "79.2%",
    testSub: "honest全期間実績（正規プロトコルの検証/テスト分割は本ランクでは未実施）",
    full: "79.2%",
    fullSub: "全期間実績（2024-01-11〜2026-06-12）: 109R",
    condition:
      "軸2車 = 7Sと同じ選定ロジック（単勝指数上位3∩複勝指数上位3の重なり車・車数非依存の実装を再利用） ｜ ゲート: フィールド指数エントロピー≤1.9938 ∧ 軸2車のWT公式印◎◯△一致数≤1（axis_sum閾値は9車では未較正のため導入せず。mark3ゲートは7S側と異なり撤廃していない） ｜ 軸2車がWT◎◯と完全一致（重なり2）は除外 ｜ 三連複 軸2車+残り7車のいずれか1車の7点（オッズ下限なし）",
    detail:
      "9車立ては全レースの約8.0%（7車85.5%に次ぐ規模）。2026-08開催予定の「ドリームレース」（S級・過去3回全て9車立て）をターゲットに含めるため、7車専用だった7Sロジックを9車立てへ移植し独立ランクとして新設した（2026-07-26）。entropyゲート（S9_ENTROPY_MAX=1.9938）は2024Q1のみで決定し残り9四半期にブラインド適用、9四半期全てで方向一致を確認。7Sと異なり、2026-07-31のmark3ゲート撤廃・axis_sum緩和は9車側には適用していない（9車特有の再検証を実施していないため）。ボリュームが小さいため専用の日次capは設けていない。",
    investment: "名目 100円/点・7点/レース・少数（月あたり数件規模）",
  },
  {
    key: "9A",
    bg: "#64748b",
    label: "9A",
    title: "9Aランク（9Sの境界ランク・2ゲート中1つだけ不合格／内部名RANK_9A）",
    subtitle: "9車 ｜ 三連複 2軸総流し(7点)",
    test: "71.9%",
    testSub: "honest全期間実績（正規プロトコルの検証/テスト分割は本ランクでは未実施）",
    full: "71.9%",
    fullSub: "全期間実績（2024-01-05〜2026-07-23）: 967R",
    condition:
      "軸2車 = 7S/9Sと同じ選定（単勝指数上位3∩複勝指数上位3の重なり車） ｜ 9Sの2条件（entropy≤1.9938・軸2車のWT◎◯△一致数≤1）のうち、ちょうど1つだけ不合格の候補を採用（0個=9S本体・2個とも不合格は対象外） ｜ 三連複 軸2車+残り7車のいずれか1車の7点（オッズ下限なし）",
    detail:
      "9Sはボリュームが小さいため、7Aと同じ発想で2026-07-27に新設した境界ランク。9S側は2026-07-31のゲート簡素化を行っていないため、7Aと異なり条件・数値とも据え置きのまま。",
    investment: "名目 100円/点・7点/レース・約0.5〜1.7件/日",
  },
];

const TERMS = [
  {
    term: "単勝率・複勝率",
    def: "出走表に表示するAIモデルの予測確率。単勝率=1着専用モデルの予測確率、複勝率=3着内モデルの予測確率が元。各選手独立モデルの生確率のためレース内合計が揃わないので、表示時にロジット(対数オッズ)空間で一律シフトして単勝=合計100%・複勝=合計300%(出走3名未満のレースは出走数×100%)になるよう補正している。7S/7A/9S/9Aの軸選定に使う。",
  },
  {
    term: "指数エントロピー",
    def: "レース内の予測確率分布の混戦度。7S/7A/9S/9Aでは「entropyが低いほど軸に確率が集中し高配当」という独立シグナルとして採用（7S/7A: ENTROPY_MAX=1.8329・9S/9A: ENTROPY_MAX=1.9938）。低いentropy＝軸2車に予測確率が集中し残り車が拮抗している状態が、三連複高配当の的中と強く相関することを四半期walk-forwardで確認済み。",
  },
  {
    term: "axis_sum（波乱度指数）",
    def: "7S/7Aの軸2車（単勝指数上位3∩複勝指数上位3の重なり車）の複勝指数合計。2026-07-24にaxis_sum≤1.3として導入（三連複5倍未満になりやすい極端な人気決着を除外）、2026-07-31にmark3ゲート撤廃とあわせて1.5へ緩和した。9S/9Aは9車では未較正のため導入していない。",
  },
  {
    term: "mark3ゲート（廃止済み・7S/7Aは2026-07-31撤廃・9S/9Aは継続使用）",
    def: "軸2車のうち2車がWINTICKET公式印◎◯△（mark1/2/3=本命/対抗/穴）のいずれかと一致するレースを除外するゲート（2026-07-27導入）。市場人気と重なるレースはROIが下がると判明したための措置だったが、2026-07-31の月次vintageモデルによるhonest再検証で、7S/7Aについてはaxis_sum≤1.5との組み合わせによりmark3ゲート無しの方が的中率・ROIとも上回ることが分かったため撤廃した。9S/9Aは9車特有の再検証を行っていないため据え置きで継続使用している。",
  },
  {
    term: "実精算方式（2026-07-15〜）",
    def: "指数・買い目は発走前のオッズ盤面掲載車で作成し、落車・失格・棄権が絡んだ買い目は購入のまま外れ計上（返還しない）。欠車のみ返還。実際の車券精算と同一ルール。旧表示（完走者だけで指数を組み直す方式）は落車を事前に知っている前提になり回収率を過大評価していたため全面改定した。",
  },
  {
    term: "四半期ウォークフォワードモデル・モデルリーク修正（2026-07-19）",
    def: "各モデルをその四半期のテスト窓より前のデータだけで学習し、過去のレースを常に「当時知り得た情報」だけでスコアリングする方式。本番モデルは日次で全期間再学習するため、これをそのまま過去のpicks_history再構築に使うと未来のデータを知った状態で過去を採点してしまう（モデルリーク）。7S/9S系はこの方式で全期間を再構築済み。",
  },
  {
    term: "フォワード回収率",
    def: "2026-07-01 以降の前向き検証。本番モデル（学習 ≤2026-06-30）にとって完全に未知の期間。",
  },
  {
    term: "三連複",
    def: "1〜3着を順不同で当てる券種。現行の全ランク（7S/7A/9S/9A）はいずれも三連複の2軸総流し（軸2車+残りのいずれか1車。7車=5点・9車=7点）。",
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
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 pt-1">
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">7S 全期間回収率</p>
            <p className="text-lg font-bold" style={{ color: "#16a34a" }}>79.1%</p>
            <p className="text-xs text-gray-400">6,572R</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">7A 全期間回収率</p>
            <p className="text-lg font-bold" style={{ color: "#78716c" }}>77.4%</p>
            <p className="text-xs text-gray-400">11,419R</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">9S 全期間回収率</p>
            <p className="text-lg font-bold" style={{ color: "#0891b2" }}>79.2%</p>
            <p className="text-xs text-gray-400">109R</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">9A 全期間回収率</p>
            <p className="text-lg font-bold" style={{ color: "#64748b" }}>71.9%</p>
            <p className="text-xs text-gray-400">967R</p>
          </div>
        </div>
        <p className="text-xs text-gray-400">
          全期間実績（2026-07-31時点・実精算）: 7S 79.1%(6,572R) ／ 7A 77.4%(11,419R) ／
          9S 79.2%(109R) ／ 9A 71.9%(967R)。
          いずれも正規プロトコルのテスト分割を経ていない全期間honest実績（詳細は各ランク説明参照）。
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
              <p className="text-xs text-gray-400">検証: {r.testSub}</p>
              {r.full && (
                <p className="text-xs text-gray-400">
                  実績: <span className="font-semibold text-gray-600">{r.full}</span>　{r.fullSub}
                </p>
              )}
              <p className="text-xs text-gray-400">投資: {r.investment}</p>
            </div>
          </div>
        ))}
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
          <li>
            <b>2026-08-01、ランク体系の内部rank名を全面改名</b>（keirin側commit f31f84b）。
            内部rankを「RANK_」+表示ラベル方式へ統一した（旧SEVEN_S7→RANK_7S・
            SEVEN_7A→RANK_7A・NINE_S9→RANK_9S・NINE_9A→RANK_9A）。表示ラベル自体
            （7S/7A/9S/9A）は変更していない。
          </li>
          <li>
            <b>2026-07-31、S1（win軸1着固定×3着内モデル相手2車・三連単2点流し）を全廃</b>
            （旧SEVEN_S1）。同日、SEVEN_S7/NINE_S9をgate_label（&quot;SS&quot;/&quot;S&quot;）で7SS/9SS・
            7S/9Sに分岐していたロジックも廃止し、単一ランクへ統合した（旧7SS/9SS
            ＝軸2車がWT◎◯と全く重ならない選出はSへ吸収）。
          </li>
          <li>
            <b>2026-08-02、「7SS」（RANK_7SS・波乱軸選出/穴レース検知）を全廃</b>。
            2026-07-31に「高配当の的中頻度（見せ場）」を目的として導入したが、
            live実績が全期間 16,298R・回収率 73.5%（2026年の月次も1月以外すべて70%以下）
            と控除率75%を下回り続け、有効な推奨として成立していないと判断したため
            推奨・表示・集計のすべてから削除した。将来、期待できる条件が見つかった
            場合は再設定する。
          </li>
          <li>
            <b>2026-07-31、7S/7Aのmark3ゲートを撤廃・axis_sum上限を1.3→1.5へ緩和</b>。
            月次vintageモデルによるhonest全期間再検証の結果、的中率・ROI・月次ROI
            標準偏差の全てで改善したため（詳細は各ランク説明参照）。9S/9Aは9車特有の
            再検証を行っていないため変更していない。
          </li>
          <li>2026-07-17 にランク体系を再設計: 正規プロトコル（1年検証→テスト1回評価）で合格した S2・S3 の2ランクのみを継続。旧S1（6車三連単）と A（一致波乱二連単）は検証ROI100%超の条件が存在せず<b>完全廃止</b>（実績行はアーカイブへ退避済み）。</li>
          <li>2026-07-19、「win軸1着固定×3着内モデル相手2車」という新設計で S1を再導入（三連単2点流し）したが、2026-07-31に前述の通り全廃した。</li>
          <li>2026-07-21、S2(7PLUS_U)/S3(7PLUS_M)は対象レース数・的中率・期待値の観点で継続困難と判断し全廃（行はアーカイブへ退避済み・新規生成なし）。</li>
          <li>2026-07-21、S7（当時の内部名SEVEN_S4）の選出方式をWINTICKET◎◯重なり考慮版へ変更。軸2車がWT公式予想の◎◯と重なるほどROIが下がるという仮説を検証し、重なり数に応じた表示分割（7SS/7S）を導入した（同分岐は2026-07-31に廃止・上記参照）。</li>
          <li>2026-07-24、7S/7Aにaxis_sum上限フィルタを追加（三連複が5倍未満になりやすい極端な人気決着レースを除外）。</li>
          <li>2026-07-26、指数エントロピーによる追加フィルタを発見・7S/7Aに導入。同日、S7ロジックの9車立て版としてS9（現RANK_9S）を独立ランクとして新設した。</li>
          <li>2026-07-27、軸2車のWINTICKET公式印◎◯△（mark1/2/3）のうち2つと一致する場合を除外するmark3ゲートを7S/9S両方に追加（2026-07-31に7S側は撤廃・9S側は継続）。同日、境界ランク7A/9Aを新設。</li>
          <li>現在は<b>全ランクがペーパートレード</b>（実際の賭けなし・記録のみ）。live 100R 以上の実測で優位性が確認できたランクのみ実賭けに昇格する。</li>
          <li>表示回収率は実精算方式（落車・失格は外れ計上・欠車のみ返還。軸欠車=レース返還・相手欠車=当該目のみ返還）。</li>
          <li>7S/7A/9S/9A の過去実績はOOS評価モデルによる遡及再判定（買い目は発走前オッズ盤面基準）。早期年の成績が低く見えるのは学習データが少ない時期のモデルで判定しているため（リークなし遡及の仕様）。</li>
          <li>S/S+ランク（三連単F）・旧Aランク（買い目カット方式）・旧S1（7車三連複）・旧S1（6車三連単）・A（一致波乱二連単）・S1（win軸新設計）は優位性が確認できなかった、または控除率の壁を超えられなかったため廃止済み。</li>
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
