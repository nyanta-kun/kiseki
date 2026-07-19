import Link from "next/link";
import { ArrowLeft, Bike } from "lucide-react";

// ---------------------------------------------------------------------------
// 静的データ（ランク体系 2026-07-17 再設計＋2026-07-19 S1新設計導入・全ランク honest 再集計）
// 現行は S1/S2/S3 の3ペーパーランク。旧S1(6車三連単)/A(一致波乱二連単)は正規プロトコル
// 再検証で不合格のため 2026-07-17 全廃。
// 「テスト」欄は正規プロトコル（学習〜2025-03-31・検証2025-04-01〜2026-03-31で条件選択・
// テスト2026-04-01〜07-15で1回評価）の数値。「全期間実績」は2024-01-01〜のpicks_history
// 実精算（四半期walk-forwardモデルでリークなし再構築・2026-07-19確定）。
// ---------------------------------------------------------------------------

const RANKS = [
  {
    key: "S1",
    bg: "#d97706",
    label: "S1",
    title: "S1ランク（win軸1着固定×3着内モデル相手2車／ペーパー検証中・2026-07-19導入）",
    subtitle: "7車 ｜ 三連単 2点流し ｜ 検証記録のみ・賭けなし",
    test: "146.0%",
    testSub: "正規プロトコル: 検証 171.6%(15.2R/日・的中18.1%) → テスト 146.0%(15.3R/日・的中18.2%)（実精算・ペーパー）",
    full: "123.0%",
    fullSub: "全期間実績（2024-01-01〜2026-07-18）: 14,363R（約15.3R/日）・的中17.3%・ROI 123.0%",
    condition:
      "軸 = 1着専用モデルのレース内1位（固定） × 相手 = 3着内モデルで軸を除いた上位2頭(p1,p2) × top3_gap(p1-p2の3着内確率差) ≥ 0.22 ｜ 三連単 軸→p1→p2, 軸→p2→p1 の2点流し（目オッズ下限なし）",
    detail:
      "「1着になるか」だけを学習した専用モデルで軸を固定し、3着内モデルで相手2車を選ぶ新設計。閾値は当初0.15で採用したが、母数を1日15R程度に絞り的中率を上げたいという要望を受け同日中に0.22へ引き上げ（0.08〜0.20の単調改善帯の自然な延長）。的中率16.7%→18.1%・母数は約半分に減少。S2/S3との重複はわずか4.3%とほぼ独立。",
    investment: "ペーパートレード（名目 100円/点・約15R/日）",
  },
  {
    key: "S2",
    bg: "#0e7490",
    label: "S2",
    title: "S2ランク（旧U・波乱ライン連れ込み／ペーパー検証中）",
    subtitle: "7車 ｜ 三連複 2車軸流し ｜ 検証記録のみ・賭けなし",
    test: "117.1%",
    testSub: "正規プロトコル: 検証1年 127.8%(320R) → テスト 117.1%(87R・的中14.9%)（実精算・ペーパー）",
    full: null,
    fullSub: null,
    condition:
      "指数エントロピー ≥ 1.84 かつ 盤面min三連複 ≥ 4.3 かつ 穴（市場4-7位∧モデル3位内∧ライン先頭/番手）× 同ライン「逃」相方 ｜ 目オッズ ≥ 15倍のみ",
    detail:
      "波乱見込みレースで、市場評価は低いがモデル評価が高い「穴」と同ラインの逃げ相方を2車軸にした三連複流し。ライン連れ込み（同ライン2車が共に上位に来る）を狙う。正規プロトコル（1年検証で条件選択→テスト期間で1回評価）に合格したランクの1つ。live 100R 以上で採否判定。",
    investment: "ペーパートレード（名目 100円/点）",
  },
  {
    key: "S3",
    bg: "#7c3aed",
    label: "S3",
    title: "S3ランク（◎不一致×軸信頼／ペーパー検証中・2026-07-19 3way OR拡張＋モデルリーク修正）",
    subtitle: "7車 ｜ 三連複 2車軸流し ｜ 検証記録のみ・賭けなし",
    test: "154.3%",
    testSub: "正規プロトコル: 検証1年 158.6%(671R) → テスト 154.3%(186R・目≥15限定)（実精算・ペーパー）",
    full: "95.9%",
    fullSub: "全期間実績（2024-01-01〜2026-07-18）: 3,114R・ROI 95.9%（ゲート別: win_rank 119.1%が最強／gap12 87.9%／ratio 88.2%）",
    condition:
      "WINTICKET◎ ≠ システム◎（モデル1位）かつ [gap12 ≥ 0.10（軸信頼ゲート） または 1着モデル内順位 ≥ 3位（勝ちきれない評価ゲート） または p_win/p_top3比 ≤ 0.30（勝ちきれない評価ゲートの連続量版）] ｜ システム◎×同ライン「逃」相方の三連複流し・目オッズ ≥ 15倍のみ",
    detail:
      "外部予想（WINTICKET◎）と当システムの◎が割れたレースを3つの独立シグナルで拾う。①gap12≥0.10（不一致システム◎の3着内率が68%→73%に上がる帯）②1着専用モデル（3着内モデルとは別に学習）内でシステム◎が3位以下に評価される帯③システム◎の「1着モデル確率÷3着内モデル確率」比が0.30以下の帯（②の連続量版）。2026-07-19、②③の計算に使う本番モデルが過去picks_history再構築時に未来データを学習済みの状態でスコアリングしていた問題（モデルリーク）が発覚し、四半期ウォークフォワードモデルで全期間を honest 再構築した（詳細は下部注意事項）。S2と同一買い目になる場合はS2優先で記録しない。live 100R 以上で採否判定。",
    investment: "ペーパートレード（名目 100円/点・約1.5R/日）",
  },
];

// 正規プロトコル（学習〜2025-03-31・検証=2025-04-01〜2026-03-31で条件選択・
// テスト=2026-04-01〜07-15で1回評価）の期間別成績（実精算・2026-07-19検証）
const PROTOCOL_RESULTS = [
  { month: "検証 2025-04〜2026-03", s1: "171.6%（15.2R/日）", s2: "127.8%（320R）", s3: "158.6%（671R）" },
  { month: "テスト 2026-04〜07-15", s1: "146.0%（15.3R/日）", s2: "117.1%（87R・的中14.9%）", s3: "154.3%（186R・目≥15限定）" },
];

const TERMS = [
  {
    term: "単勝指数・複勝指数（2026-07-19導入・2026-07-19レース内正規化）",
    def: "出走表に表示するAIモデルの予測確率。単勝指数=1着専用モデル(1着モデル)の予測確率、複勝指数=3着内モデルの予測確率が元。各選手独立モデルの生確率のためレース内合計が揃わないので、表示時にロジット(対数オッズ)空間で一律シフトして単勝=合計100%・複勝=合計300%(出走3名未満のレースは出走数×100%)になるよう補正している(単純な比例配分だと個々の値が100%を超えて頭打ちしやすいため)。既存の「指数」列（競走得点）とは別軸の情報として単→複→指数の順に並べている。2024年以降の過去レースにも同じ四半期ウォークフォワードモデルで遡及反映済み。",
  },
  {
    term: "top3_gap",
    def: "S1の相手選定に使う指標。3着内モデルで軸（1着モデル1位）を除いた残り車の中で、1位評価(p1)と2位評価(p2)の3着内確率差。大きいほどp1がp2より明確に格上と評価されている。S1のゲート条件（≥0.22）。",
  },
  {
    term: "gap12",
    def: "AIモデルが予測した「指数1位の3着内確率 − 2位の確率」の差。大きいほど軸（モデル1位）の優位性が高い。S3の軸信頼ゲートの1つ（≥0.10・不一致レースでのシステム◎の3着内率が68%→73%に上がる帯）。",
  },
  {
    term: "1着モデル",
    def: "3着内モデルとは別に「1着になるか」だけを学習したAIモデル（2026-07-19導入）。3着内モデルより判別力が高い（AUC0.83 vs 0.78）。S1では軸選定、S3ではシステム◎がこのモデルで低評価（3位以下）なレースを狙うゲートに使う。単勝指数の元にもなっている。",
  },
  {
    term: "p_win/p_top3比",
    def: "システム◎の「1着モデル確率 ÷ 3着内モデル確率」。1着モデル内順位（離散量）の連続量版で、低いほど「3着内には来るが勝ちきれない」度合いが強い。S3の軸信頼ゲートの3つ目（≤0.30）。加法差（両モデル確率の引き算）は判別力がなく、乗法比のみ有効と判明（2026-07-19検証）。",
  },
  {
    term: "指数エントロピー",
    def: "レース内の予測確率分布の混戦度（S2の条件・≥1.84）。大きいほど「どの車も来うる」波乱見込みレース。",
  },
  {
    term: "ペーパートレード",
    def: "実際には賭けず、発走15分前に確定した買い目を記録して成績だけを追う検証方式（現在は全ランク）。live実測で優位性が確認できたランクのみ実賭けに昇格する。",
  },
  {
    term: "正規プロトコル（テスト回収率）",
    def: "学習=2025-03-31以前・検証=2025-04-01〜2026-03-31の1年（条件選択はここだけ）・テスト=2026-04-01〜07-15（選択条件のみ1回評価）の時系列3分割。テスト期間を条件探しに使わないため「直近だけ良い」レジーム依存の条件を検出・排除できる。現行のS1/S2/S3はこのプロトコルの合格ランク。",
  },
  {
    term: "四半期ウォークフォワードモデル・モデルリーク修正（2026-07-19）",
    def: "各モデルをその四半期のテスト窓より前のデータだけで学習し、過去のレースを常に「当時知り得た情報」だけでスコアリングする方式。本番モデルは日次で全期間再学習するため、これをそのまま過去のpicks_history再構築に使うと未来のデータを知った状態で過去を採点してしまう（モデルリーク）。2026-07-19にS1・S3の1着専用モデルでこの問題が発覚し、四半期ごとの専用モデル群で全期間を再構築した（各ランクの「全期間実績」はこの修正後の数値）。",
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
    def: "1〜3着を順不同で当てる券種。S2/S3は三連複の2車軸流し。",
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
        <div className="grid grid-cols-3 gap-2 pt-1">
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">S1 テスト回収率</p>
            <p className="text-lg font-bold text-amber-600">146.0%</p>
            <p className="text-xs text-gray-400">15.3R/日 ・ 的中18.2%</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">S2 テスト回収率</p>
            <p className="text-lg font-bold text-cyan-600">117.1%</p>
            <p className="text-xs text-gray-400">87R ・ 的中14.9%</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">S3 テスト回収率</p>
            <p className="text-lg font-bold text-violet-600">154.3%</p>
            <p className="text-xs text-gray-400">186R ・ 目≥15限定</p>
          </div>
        </div>
        <p className="text-xs text-gray-400">
          全期間実績（2024-01-01〜2026-07-18・実精算）: S1 123.0%(14,363R) ／ S3 95.9%(3,114R)。
          正規プロトコルのテスト値とは評価対象期間・母集団が異なるため一致しない（詳細は各ランク説明参照）。
        </p>
        <p className="text-xs text-gray-400">
          ★ 正規プロトコル = 学習〜2025-03-31・検証 2025-04-01〜2026-03-31（条件選択）・テスト 2026-04-01〜07-15（1回評価）。
          以後は本番フォワード。全ランクがペーパートレード（賭けなし・記録のみ）検証中。
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
          <h2 className="text-sm font-bold text-gray-800">正規プロトコル 期間別回収率（S1/S2/S3）</h2>
          <p className="text-xs text-gray-400 mt-0.5">
            条件選択は検証期間のみで行い、テスト期間は選択済み条件を1回だけ評価（リークなし・実精算）
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs sm:text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">期間</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S1</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S2</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S3</th>
              </tr>
            </thead>
            <tbody>
              {PROTOCOL_RESULTS.map((m) => (
                <tr key={m.month} className="border-b border-gray-50 last:border-0">
                  <td className="py-1.5 px-3 text-gray-600">{m.month}</td>
                  <td className="py-1.5 px-3 text-right font-semibold text-amber-600">{m.s1}</td>
                  <td className="py-1.5 px-3 text-right font-semibold text-cyan-600">{m.s2}</td>
                  <td className="py-1.5 px-3 text-right font-semibold text-violet-600">{m.s3}</td>
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
          <li>S1/S2/S3 の過去実績はOOS評価モデルによる遡及再判定（買い目は発走前オッズ盤面基準）。早期年の成績が低く見えるのは学習データが少ない時期のモデルで判定しているため（リークなし遡及の仕様）。</li>
          <li>S/S+ランク（三連単F）・旧Aランク（買い目カット方式）・旧S1（7車三連複）・旧S1（6車三連単）・A（一致波乱二連単）は優位性が確認できなかったため廃止済み（現行S1とは無関係の別設計）。</li>
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
