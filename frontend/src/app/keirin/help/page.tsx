import Link from "next/link";
import { ArrowLeft, Bike } from "lucide-react";

// ---------------------------------------------------------------------------
// 静的データ（ランク体系 2026-07-17 再設計: 現行は S2/S3 の2ペーパーランクのみ。
// S1(6車三連単)/A(一致波乱二連単)は正規プロトコル再検証で不合格のため 2026-07-17 全廃。
// 数値は実精算方式 / 正規プロトコル: 学習 〜2025-03-31・検証 2025-04-01〜2026-03-31 の
// 1年で条件選択・テスト 2026-04-01〜07-15 で1回評価・以後 本番フォワード）
// ---------------------------------------------------------------------------

const RANKS = [
  {
    key: "S2",
    bg: "#0e7490",
    label: "S2",
    title: "S2ランク（旧U・波乱ライン連れ込み／ペーパー検証中）",
    subtitle: "7車 ｜ 三連複 2車軸流し ｜ 検証記録のみ・賭けなし",
    test: "117.1%",
    testSub: "正規プロトコル: 検証1年 127.8%(320R) → テスト 117.1%(87R・的中14.9%)（実精算・ペーパー）",
    condition:
      "指数エントロピー ≥ 1.84 かつ 盤面min三連複 ≥ 4.3 かつ 穴（市場4-7位∧モデル3位内∧ライン先頭/番手）× 同ライン「逃」相方 ｜ 目オッズ ≥ 15倍のみ",
    detail:
      "波乱見込みレースで、市場評価は低いがモデル評価が高い「穴」と同ラインの逃げ相方を2車軸にした三連複流し。ライン連れ込み（同ライン2車が共に上位に来る）を狙う。正規プロトコル（1年検証で条件選択→テスト期間で1回評価）に合格した2ランクのうちの1つ。live 100R 以上で採否判定。",
    investment: "ペーパートレード（名目 100円/点）",
  },
  {
    key: "S3",
    bg: "#7c3aed",
    label: "S3",
    title: "S3ランク（◎不一致×軸信頼／ペーパー検証中・2026-07-17新定義）",
    subtitle: "7車 ｜ 三連複 2車軸流し ｜ 検証記録のみ・賭けなし",
    test: "104.4%",
    testSub: "正規プロトコル: 検証1年 111.8%(221R) → テスト 104.4%(62R・的中12.9%)（実精算・ペーパー）",
    condition:
      "WINTICKET◎ ≠ システム◎（モデル1位）かつ gap12 ≥ 0.10（軸信頼ゲート） ｜ システム◎×同ライン「逃」相方の三連複流し・目オッズ ≥ 15倍のみ",
    detail:
      "外部予想（WINTICKET◎）と当システムの◎が割れたレースのうち、モデルが1位を明確に抜けていると見るレース（gap12≥0.10。不一致システム◎の3着内率が68%→73%に上がる帯）で、システム◎と同ラインの逃げ相方を2車軸にした三連複流し。旧定義の波乱ゲート（エントロピー/盤面minオッズ）は2026-07-17に廃止し軸信頼ゲートへ転換した。S2と同一買い目になる場合はS2優先で記録しない。live 100R 以上で採否判定。",
    investment: "ペーパートレード（名目 100円/点・約0.6R/日）",
  },
];

// 正規プロトコル（学習〜2025-03-31・検証=2025-04-01〜2026-03-31で条件選択・
// テスト=2026-04-01〜07-15で1回評価）の期間別成績（実精算・2026-07-17検証）
const PROTOCOL_RESULTS = [
  { month: "検証 2025-04〜2026-03", s2: "127.8%（320R）", s3: "111.8%（221R）" },
  { month: "テスト 2026-04〜07-15", s2: "117.1%（87R・的中14.9%）", s3: "104.4%（62R・的中12.9%）" },
];

const TERMS = [
  {
    term: "gap12",
    def: "AIモデルが予測した「指数1位の3着内確率 − 2位の確率」の差。大きいほど軸（モデル1位）の優位性が高い。S3の軸信頼ゲートは≥0.10（不一致レースでのシステム◎の3着内率が68%→73%に上がる帯）。",
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
    def: "学習=2025-03-31以前・検証=2025-04-01〜2026-03-31の1年（条件選択はここだけ）・テスト=2026-04-01〜07-15（選択条件のみ1回評価）の時系列3分割。テスト期間を条件探しに使わないため「直近だけ良い」レジーム依存の条件を検出・排除できる。現行のS2/S3はこのプロトコルの合格ランク。",
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
    def: "1〜3着を順不同で当てる券種。現行ランク（S2/S3）はいずれも三連複の2車軸流し。",
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
            <p className="text-xs text-gray-500">S2ランク テスト回収率（正規プロトコル・実精算・ペーパー）</p>
            <p className="text-lg font-bold text-cyan-600">117.1%</p>
            <p className="text-xs text-gray-400">87R ・ 的中14.9%（検証1年 127.8%）</p>
          </div>
          <div className="bg-gray-50 rounded-lg p-2.5 text-center">
            <p className="text-xs text-gray-500">S3ランク テスト回収率（正規プロトコル・実精算・ペーパー）</p>
            <p className="text-lg font-bold text-violet-600">104.4%</p>
            <p className="text-xs text-gray-400">62R ・ 的中12.9%（検証1年 111.8%）</p>
          </div>
        </div>
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
              <p className="text-xs text-gray-400">検証: {r.testSub} ／ 投資: {r.investment}</p>
            </div>
          </div>
        ))}
      </section>

      {/* 正規プロトコル 期間別回収率 */}
      <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
        <div className="px-4 py-2.5 border-b border-gray-100 bg-gray-50">
          <h2 className="text-sm font-bold text-gray-800">正規プロトコル 期間別回収率（S2/S3）</h2>
          <p className="text-xs text-gray-400 mt-0.5">
            条件選択は検証期間のみで行い、テスト期間は選択済み条件を1回だけ評価（リークなし・実精算）
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs sm:text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="py-1.5 px-3 text-left text-xs text-gray-500 font-medium">期間</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S2</th>
                <th className="py-1.5 px-3 text-right text-xs text-gray-500 font-medium">S3</th>
              </tr>
            </thead>
            <tbody>
              {PROTOCOL_RESULTS.map((m) => (
                <tr key={m.month} className="border-b border-gray-50 last:border-0">
                  <td className="py-1.5 px-3 text-gray-600">{m.month}</td>
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
          <li>2026-07-17 にランク体系を再設計: 正規プロトコル（1年検証→テスト1回評価）で合格した <b>S2・S3 の2ランクのみ</b>を継続。S1（6車三連単）と A（一致波乱二連単）は検証ROI100%超の条件が存在せず<b>完全廃止</b>（実績行はアーカイブへ退避済み）。S3 は同日、波乱ゲート（エントロピー/盤面minオッズ）を廃止し軸信頼ゲート（gap12≥0.10）の新定義へ変更した。</li>
          <li>現在は<b>全ランクがペーパートレード</b>（実際の賭けなし・記録のみ）。live 100R 以上の実測で優位性が確認できたランクのみ実賭けに昇格する。</li>
          <li>2026-07-16 に指数へ競走得点トレンド4特徴を追加（選手の成長・好不調を反映／モデル44特徴化）。本ページの検証数値は新指数で再計算済み。</li>
          <li>表示回収率は実精算方式（落車・失格は外れ計上・欠車のみ返還。軸欠車=レース返還・相手欠車=当該目のみ返還）。</li>
          <li>S2/S3 の過去実績はOOS評価モデルによる遡及再判定（買い目は発走前オッズ盤面基準）。早期年の成績が低く見えるのは学習データが少ない時期のモデルで判定しているため（リークなし遡及の仕様）。</li>
          <li>S/S+ランク（三連単F）・旧Aランク（買い目カット方式）・旧S1（7車三連複）・S1（6車三連単）・A（一致波乱二連単）は優位性が確認できなかったため廃止済み。</li>
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
