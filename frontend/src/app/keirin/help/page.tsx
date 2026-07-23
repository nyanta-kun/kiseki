import Link from "next/link";
import { ArrowLeft, Bike } from "lucide-react";

// ---------------------------------------------------------------------------
// 静的データ（ランク体系 2026-07-17 再設計＋2026-07-19 S1新設計導入・2026-07-21 SS/S再編）
// 現行は S1/SS/S の3ペーパーランク。旧S1(6車三連単)/A(一致波乱二連単)は正規プロトコル
// 再検証で不合格のため 2026-07-17 全廃。S2(旧U)/S3(旧M)は対象レース数・的中率・期待値の
// 観点で継続困難と判断し 2026-07-21 全廃（内部rankコード7PLUS_U/7PLUS_Mへの新規書き込み終了）。
// SS/S は元々1つだったS4ランク（単勝×複勝指数トップ3重なり軸×波乱度選出）を、軸2車が
// WINTICKET公式◎◯と重なる数に応じて表示ランクを分割したもの（内部rankコードはSS/Sいずれも
// SEVEN_S4のまま・gate_labelカラムで区別）。正規プロトコルの検証/テスト分割は未実施のため
// test=full の全期間実績を両方に表示する。
// 「テスト」欄は正規プロトコル（学習〜2025-03-31・検証2025-04-01〜2026-03-31で条件選択・
// テスト2026-04-01〜07-15で1回評価）の数値。「全期間実績」は2024-01-01〜のpicks_history
// 実精算（四半期walk-forwardモデルでリークなし再構築・2026-07-19確定）。
// ---------------------------------------------------------------------------

const RANKS = [
  {
    key: "SS+",
    bg: "#a16207",
    label: "SS+",
    title: "SS+ランク（SS内、軸2車の級班に各グレード最上位(S1級/A1級)を含まない観察用サブランク・2026-07-23導入）",
    subtitle: "7車 ｜ 三連複 2軸総流し(5点) ｜ SSの内訳（買い目・投資額はSSと同一）",
    test: "362.1%",
    testSub: "honest全期間実績のみ（正規プロトコルの検証/テスト分割は本ランクでは未実施）",
    full: "362.1%",
    fullSub: "全期間実績（2024-01-01〜2026-07-23）: 365R・的中40.8%・ROI 362.1%",
    condition:
      "軸2車 = 単勝率上位3∩複勝率上位3の重なり車 ｜ SS+: 軸2車がWINTICKET公式◎◯と全く重ならず、かつ軸2車の級班に各グレード最上位(S1級・A1級)を含まない場合に採用 ｜ 三連複 軸2車+残り5車のいずれか1車の5点（オッズ下限なし・買い目はSSと同一）",
    detail:
      "SSのうち、軸2車の級班が各グレード内の最上位クラス（S1級・A1級）を含まないレースだけを抽出した観察用の内訳区分。「格上」認定選手が軸に含まれると回収率がやや下がる傾向があるかを検証する目的の表示分岐で、買い目・投資額はSSと同一（集計上の二重計上はしない）。",
    investment: "名目 100円/点・5点/レース・SSの内訳のため合算には含めない",
  },
  {
    key: "SS",
    bg: "#ca8a04",
    label: "SS",
    title: "SSランク（単勝×複勝指数トップ3重なり軸×波乱度選出／軸2車がWT◎◯と完全不一致・2026-07-21導入）",
    subtitle: "7車 ｜ 三連複 2軸総流し(5点)",
    test: "232.8%",
    testSub: "honest全期間実績のみ（正規プロトコルの検証/テスト分割は本ランクでは未実施）",
    full: "232.8%",
    fullSub: "全期間実績（2024-01-01〜2026-07-20・四半期walk-forward）: 943R・約1.0R/日・的中39.4%・ROI 232.8%",
    condition:
      "軸2車 = 単勝指数上位3∩複勝指数上位3の重なり車 ｜ SS: 軸2車がWINTICKET公式◎◯と全く重ならない場合に無条件で採用 ｜ 三連複 軸2車+残り5車のいずれか1車の5点（オッズ下限なし）",
    detail:
      "単勝モデルと複勝モデルの両方が上位に評価する2車を軸にする設計のうち、軸2車がWINTICKET公式の◎◯（人気予想）と一切重ならないレースだけを抽出した最上位区分。市場のコンセンサスから外れた軸ほど回収率が高いという発見（2026-07-21、重なり0=ROI408.1%／重なり1=148.7%／重なり2=完全一致=75.7%赤字と判明）を受け、旧S4ランクをWT◎◯重なり数でSS/Sの2表示ランクへ分割。母数は少ないが的中率39.4%・ROI232.8%と全ランク中最高。",
    investment: "名目 100円/点・5点/レース・約1.0R/日",
  },
  {
    key: "S",
    bg: "#16a34a",
    label: "S",
    title: "Sランク（単勝×複勝指数トップ3重なり軸×波乱度選出／軸2車の片方だけがWT◎◯と重なる・2026-07-21導入）",
    subtitle: "7車 ｜ 三連複 2軸総流し(5点)",
    test: "120.6%",
    testSub: "honest全期間実績のみ（正規プロトコルの検証/テスト分割は本ランクでは未実施）",
    full: "120.6%",
    fullSub: "全期間実績（2024-01-01〜2026-07-20・四半期walk-forward）: 8,984R・約9.6R/日・的中36.0%・ROI 120.6%",
    condition:
      "軸2車 = 単勝指数上位3∩複勝指数上位3の重なり車 ｜ S: 軸2車の片方だけがWT◎◯と重なる場合にaxis_sum(軸2車の複勝指数合計)昇順で日次10件採用 ｜ 三連複 軸2車+残り5車のいずれか1車の5点（オッズ下限なし）",
    detail:
      "SSと同じ軸選定ロジックのうち、軸2車の片方だけがWINTICKET公式の◎◯と重なるレースを対象に、波乱度指数(axis_sum)が小さい順に日次10件を機械的に採用する主力区分。母数はSSの約9.5倍（8,984R・約9.6R/日）と多く、的中率もSS(39.4%)に迫る36.0%を確保しつつROI120.6%で黒字を維持する。",
    investment: "名目 100円/点・5点/レース・約9.6R/日",
  },
  {
    key: "S1",
    bg: "#d97706",
    label: "S1",
    title: "S1ランク（win軸1着固定×3着内モデル相手2車・2026-07-19導入）",
    subtitle: "7車 ｜ 三連単 2点流し",
    test: "146.0%",
    testSub: "正規プロトコル: 検証 171.6%(15.2R/日・的中18.1%) → テスト 146.0%(15.3R/日・的中18.2%)（実精算）",
    full: "182.5%",
    fullSub: "全期間実績（2024-01-01〜2026-07-22）: 6,426R（約6.9R/日）・的中10.6%・ROI 182.5%",
    condition:
      "軸 = 1着専用モデルのレース内1位（固定） × 相手 = 3着内モデルで軸を除いた上位2頭(p1,p2) × top3_gap(p1-p2の3着内確率差) ≥ 0.15 × 軸の単勝勝率 ≤ 50%（本命決着＝低配当レースを除外） × 軸の級班がS1級・A1級（各グレード最上位）でない ｜ 三連単 軸→p1→p2, 軸→p2→p1 の2点流し（目オッズ下限なし）",
    detail:
      "「1着になるか」だけを学習した専用モデルで軸を固定し、3着内モデルで相手2車を選ぶ新設計。top3_gap閾値は当初0.15→同日中に0.22へ引き上げていたが、2026-07-21に高配当（万車券含む）の取りこぼしを防ぐ方針へ再設計し0.15へ戻したうえ、軸の単勝勝率が高い（本命決着になりやすい）レースを除外する条件を追加。2026-07-22には軸がS1級・A1級（各グレード内の格上認定選手）の場合を除外する条件も追加。的中率は変えずに母数を絞り込みROI・配当の質を上げる狙い（5万円以上の高配当payoutは85.7%が残存）。SS/Sとの重複はわずかとほぼ独立。",
    investment: "名目 100円/点・約6.9R/日",
  },
];

// 正規プロトコル（学習〜2025-03-31・検証=2025-04-01〜2026-03-31で条件選択・
// テスト=2026-04-01〜07-15で1回評価）の期間別成績（実精算）。
// SS/Sはこの正規プロトコルによる検証/テスト分割を経ていない（旧S4からの分割ランクのため）。
const PROTOCOL_RESULTS = [
  { month: "検証 2025-04〜2026-03", s1: "171.6%（15.2R/日）" },
  { month: "テスト 2026-04〜07-15", s1: "146.0%（15.3R/日）" },
];

const TERMS = [
  {
    term: "単勝率・複勝率（2026-07-19導入・2026-07-19レース内正規化・2026-07-23ラベル変更）",
    def: "出走表に表示するAIモデルの予測確率。単勝率=1着専用モデル(1着モデル)の予測確率、複勝率=3着内モデルの予測確率が元。各選手独立モデルの生確率のためレース内合計が揃わないので、表示時にロジット(対数オッズ)空間で一律シフトして単勝=合計100%・複勝=合計300%(出走3名未満のレースは出走数×100%)になるよう補正している(単純な比例配分だと個々の値が100%を超えて頭打ちしやすいため)。既存の「競走得点」列とは別軸の情報として単→複→競走得点の順に並べている。2024年以降の過去レースにも同じ四半期ウォークフォワードモデルで遡及反映済み。",
  },
  {
    term: "top3_gap",
    def: "S1の相手選定に使う指標。3着内モデルで軸（1着モデル1位）を除いた残り車の中で、1位評価(p1)と2位評価(p2)の3着内確率差。大きいほどp1がp2より明確に格上と評価されている。S1のゲート条件（≥0.15・2026-07-21改定）。",
  },
  {
    term: "軸級班フィルター（2026-07-22導入）",
    def: "S1の軸選手が各グレード内の最上位クラス（S1級・A1級）の場合を除外する条件。「格上」認定選手が軸だと市場も同じ判断をしており配当が低くなりやすいと判明（的中率は変えず母数を約半分に絞りROIを底上げ・5万円以上の高配当は85.7%が残存）。",
  },
  {
    term: "gap12",
    def: "AIモデルが予測した「指数1位の3着内確率 − 2位の確率」の差。大きいほど軸（モデル1位）の優位性が高い。旧S3ランク（2026-07-21廃止）の軸信頼ゲートの1つだった指標（≥0.10・不一致レースでのシステム◎の3着内率が68%→73%に上がる帯）。",
  },
  {
    term: "1着モデル",
    def: "3着内モデルとは別に「1着になるか」だけを学習したAIモデル（2026-07-19導入）。3着内モデルより判別力が高い（AUC0.83 vs 0.78）。S1では軸選定に使う。単勝指数の元にもなっている。",
  },
  {
    term: "p_win/p_top3比",
    def: "「1着モデル確率 ÷ 3着内モデル確率」。1着モデル内順位（離散量）の連続量版で、低いほど「3着内には来るが勝ちきれない」度合いが強い。旧S3ランク（2026-07-21廃止）の軸信頼ゲートに使われていた指標。加法差（両モデル確率の引き算）は判別力がなく、乗法比のみ有効と判明（2026-07-19検証）。",
  },
  {
    term: "指数エントロピー",
    def: "レース内の予測確率分布の混戦度。大きいほど「どの車も来うる」波乱見込みレース。旧S2ランク（2026-07-21廃止）の条件（≥1.84）に使われていた指標。",
  },
  {
    term: "axis_sum（波乱度指数）",
    def: "SS/Sの軸2車（単勝指数上位3∩複勝指数上位3の重なり車）の複勝指数合計。Sランクはこの値が小さい順に日次10件を採用する。",
  },
  {
    term: "SS+（2026-07-23追加・観察用サブランク）",
    def: "SSランクのうち、軸2車の級班に各グレード内の最上位クラス（S1級・A1級）を含まないレースの表示上の内訳。買い目・投資額はSSと同一で、集計上もSSに含まれる（トップラインの合算には二重計上しない）。軸が「格上」でないSSがより回収率が高いか観察する目的の表示分岐。",
  },
  {
    term: "正規プロトコル（テスト回収率）",
    def: "学習=2025-03-31以前・検証=2025-04-01〜2026-03-31の1年（条件選択はここだけ）・テスト=2026-04-01〜07-15（選択条件のみ1回評価）の時系列3分割。テスト期間を条件探しに使わないため「直近だけ良い」レジーム依存の条件を検出・排除できる。現行ランクではS1のみこのプロトコルの合格ランク（SS/Sは旧S4からの分割ランクのためプロトコル未実施・honest全期間実績で運用判定）。",
  },
  {
    term: "四半期ウォークフォワードモデル・モデルリーク修正（2026-07-19）",
    def: "各モデルをその四半期のテスト窓より前のデータだけで学習し、過去のレースを常に「当時知り得た情報」だけでスコアリングする方式。本番モデルは日次で全期間再学習するため、これをそのまま過去のpicks_history再構築に使うと未来のデータを知った状態で過去を採点してしまう（モデルリーク）。2026-07-19にS1・旧S3の1着専用モデルでこの問題が発覚し、四半期ごとの専用モデル群で全期間を再構築した（各ランクの「全期間実績」はこの修正後の数値）。",
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
    term: "三連複",
    def: "1〜3着を順不同で当てる券種。SS/Sは三連複の2軸総流し（軸2車+残り5車のいずれか1車の5点）。",
  },
  {
    term: "三連単",
    def: "1〜3着を着順まで当てる券種。S1は三連単の2点流し（軸→p1→p2, 軸→p2→p1）。",
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
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">SS+ 全期間回収率</p>
            <p className="text-lg font-bold" style={{ color: "#a16207" }}>362.1%</p>
            <p className="text-xs text-gray-400">365R ・ 的中40.8%</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">SS 全期間回収率</p>
            <p className="text-lg font-bold" style={{ color: "#ca8a04" }}>232.8%</p>
            <p className="text-xs text-gray-400">943R ・ 的中39.4%</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">S 全期間回収率</p>
            <p className="text-lg font-bold text-green-600">120.6%</p>
            <p className="text-xs text-gray-400">8,984R ・ 的中36.0%</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">S1 テスト回収率</p>
            <p className="text-lg font-bold text-amber-600">146.0%</p>
            <p className="text-xs text-gray-400">15.3R/日 ・ 的中18.2%</p>
          </div>
        </div>
        <p className="text-xs text-gray-400">
          全期間実績（SS+/SS/S=2026-07-23・S1=2026-07-22・実精算）: SS+ 362.1%(365R) ／ SS 232.8%(943R) ／ S 120.6%(8,984R) ／ S1 182.5%(6,426R)。
          SS+/SS/Sは正規プロトコルのテスト分割を経ていない全期間honest実績（詳細は各ランク説明参照）。
        </p>
        <p className="text-xs text-gray-400">
          ★ 正規プロトコル = 学習〜2025-03-31・検証 2025-04-01〜2026-03-31（条件選択）・テスト 2026-04-01〜07-15（1回評価）。
          以後は本番フォワード。
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

      {/* 正規プロトコル 期間別回収率 */}
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
        <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50">
          <h2 className="text-sm font-bold text-gray-800">正規プロトコル 期間別回収率（S1）</h2>
          <p className="text-xs text-gray-400 mt-0.5">
            条件選択は検証期間のみで行い、テスト期間は選択済み条件を1回だけ評価（リークなし・実精算）。
            SS/Sは旧S4からの分割ランクのためこのプロトコルは未実施（全期間honest実績のみ）。
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs sm:text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">期間</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S1</th>
              </tr>
            </thead>
            <tbody>
              {PROTOCOL_RESULTS.map((m) => (
                <tr key={m.month} className="border-b border-gray-50 last:border-0">
                  <td className="py-1.5 px-3 text-gray-600">{m.month}</td>
                  <td className="py-1.5 px-3 text-right font-semibold text-amber-600">{m.s1}</td>
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
          <li>2026-07-17 にランク体系を再設計: 正規プロトコル（1年検証→テスト1回評価）で合格した S2・S3 の2ランクのみを継続。旧S1（6車三連単）と A（一致波乱二連単）は検証ROI100%超の条件が存在せず<b>完全廃止</b>（実績行はアーカイブへ退避済み）。S3 は同日、波乱ゲート（エントロピー/盤面minオッズ）を廃止し軸信頼ゲート（gap12≥0.10）の新定義へ変更した。</li>
          <li>2026-07-19、「win軸1着固定×3着内モデル相手2車」という新設計で <b>S1を再導入</b>（三連単2点流し）。top3_gap閾値は当初0.15で採用したが、母数を1日15R程度に絞り的中率を上げたいというユーザー要望を受け同日中に<b>0.22へ引き上げ</b>（的中率16.7%→18.1%）。</li>
          <li><b>【重要】2026-07-19、S1・S3の軸に使う1着専用モデルにモデルリークが発覚</b>: 本番モデルは全期間を毎回再学習するため、過去のpicks_history再構築にそのまま使うと未来のデータを知った状態で過去のレースを採点してしまっていた（gap12側は元々四半期ごとの専用モデルで対応済みだったが、1着専用モデル側だけ対応が漏れていた）。四半期ウォークフォワードモデル群を新規学習し全期間を再構築、honestな実績値に更新済み（各ランク説明の「実績」欄）。</li>
          <li>同日、出走表に <b>単勝指数・複勝指数</b> を追加（既存の指数=競走得点とは別列）。2024年以降の過去レースにも遡及反映済み（直近1週間程度は反映待ちの場合あり）。</li>
          <li>現在は<b>全ランクがペーパートレード</b>（実際の賭けなし・記録のみ）。live 100R 以上の実測で優位性が確認できたランクのみ実賭けに昇格する。</li>
          <li>2026-07-16 に指数へ競走得点トレンド4特徴を追加（選手の成長・好不調を反映／モデル44特徴化）。2026-07-18 にレース単位のS/B取得・上がりタイム由来4特徴を追加（48特徴化）。2026-07-19 に「1着になるか」だけを学習する専用モデルを新規導入し、S3の軸信頼ゲートに統合（win_rankゲート）。同日、win_rankの連続量版（p_win/p_top3比）を検証しゲートへ3項目目としてOR追加（ratioゲート）。本ページの検証数値は都度新指数で再計算済み。</li>
          <li>表示回収率は実精算方式（落車・失格は外れ計上・欠車のみ返還。軸欠車=レース返還・相手欠車=当該目のみ返還）。</li>
          <li>S1/SS/S の過去実績はOOS評価モデルによる遡及再判定（買い目は発走前オッズ盤面基準）。早期年の成績が低く見えるのは学習データが少ない時期のモデルで判定しているため（リークなし遡及の仕様）。</li>
          <li>S/S+ランク（三連単F）・旧Aランク（買い目カット方式）・旧S1（7車三連複）・旧S1（6車三連単）・A（一致波乱二連単）は優位性が確認できなかったため廃止済み（現行S1とは無関係の別設計）。</li>
          <li><b>2026-07-21、S2・S3の購入条件を厳選</b>（購入機会が減ってもよいので的中率を上げてROIを改善したいというユーザー要望）。S3はgap12/ratioゲートをhonest全期間再構築で単独赤字（87.9%/88.2%）と判明したため外し<b>win_rank単独ゲート</b>に絞り、買い目オッズ下限もS2と分離して15→20倍に引き上げ（全期間ROI 95.9%→<b>120.4%</b>に黒字転換）。S2は盤面min三連複オッズ下限を4.3→4.5へ引き上げ（直近窓では的中率・ROIとも改善）。ただし<b>S2は全期間honestでは4.3=81.6%→4.5=84.8%と依然として損失圏内</b>（2024〜2025年前半が低迷し2025年Q3以降に大きく改善する時系列トレンドがあるため、直近実績を重視して8月末の採否判定を行う）。両ランクとも2024-01-01〜の過去分をwalk-forwardモデルで再構築済み。</li>
          <li><b>2026-07-21、S4の選出方式をWINTICKET◎◯重なり考慮版へ変更</b>。軸2車がWT公式予想の◎◯と重なるほどROIが下がるのではというユーザー仮説を検証したところ、的中率はほぼ横ばい(33〜37%)なのに重なり数に応じてROIが単調悪化（重なり0=408.1%／重なり1=148.7%／重なり2=完全一致=<b>75.7%赤字</b>）と判明。重なり0は無条件で全件採用・重なり1はaxis_sum昇順で固定10件・重なり2は完全除外する方式に変更し、全期間ROIは128.1%→<b>131.3%</b>に改善（1日あたりの採用本数は約10.8Rに微増）。過去分もwalk-forwardモデルで再構築済み。</li>
          <li><b>同日、表示ランクをSS/Sの2ランクへ再編</b>。内部rankコード（SEVEN_S4）は変更せず、軸2車がWT◎◯と全く重ならない選出を<b>SS</b>（943R・的中39.4%・ROI232.8%）、片方だけ重なる選出を<b>S</b>（8,984R・的中36.0%・ROI120.6%）として表示・集計・通知を分離した（内訳の区別は既存のgate_labelカラムを流用）。</li>
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
