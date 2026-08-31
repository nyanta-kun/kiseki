"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { ArrowLeft, Bike, Loader2 } from "lucide-react";
import {
  fetchNetkeirinSettings,
  type NetkeirinRankKey, type NetkeirinSetting, type TypeLabRankKey,
} from "@/lib/api";
import { saveNetkeirinSettings } from "./actions";

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------

// 2026-08-01〜: S1（2026-07-31全廃）・9SS（gate_label分岐廃止に伴い消滅）は対象外
// （backend/src/api/keirin_router.py の NETKEIRIN_RANK_KEYS と揃える）。
// 2026-08-02〜: 7SS（波乱軸選出・穴レース検知）も全廃したため対象外。
// 2026-08-05〜: 同じ "7SS" ラベルで別戦略を新設したため復活（keirin PR#10）。
// ⚠️ netkeirin_settings に旧7SSの行（enabled=false・title「穴の二軸」）が残っている
// ため、この画面で有効化しないと新7SSは自動入稿されない（_is_enabled は行が
// 存在すればその値に従う。fail-open するのは行が無い場合だけ）。
// 並び順は Web 全体で「車数（7車→9車）＞入稿の優先順位」に統一。
// 入稿の優先順位は keirin 側 netkeirin_submit_wt.RANK_CONFIGS の定義順が正本
// （7H2 > 9H1 > 7S > 9C > 7B > 7C > 7T1 > 7H1 > 7M1）。ここは設定画面の表示順。
// ⚠️ 2026-08-15 に 7H1 を最下位へ移した（三連単一本化の検証が終わるまで
//    enabled=false のため、有効化しても他ランクの母集団を奪わない位置に置く）。
/** 既存ランク（型ラボのプランを除く）。テンプレートを編集できるのはこちらだけ。 */
type LegacyRankKey = Exclude<NetkeirinRankKey, TypeLabRankKey>;

const RANK_ORDER: LegacyRankKey[] =
  ["7H2", "7T1", "7T3", "7S", "7B", "7C", "7H1", "7M1", "9H1", "9C"];

// 型ラボのプラン（2026-08-28 の全面移行〜）。**ON/OFF だけを出す。**
// 🔴 文面（タイトル・コメント・印）は `netkeirin_settings` ではなく keirin 側
//    `src/type_lab_submission.py` が正本で、ここでテンプレートを書いても
//    型ラボの入稿には**反映されない**。効かない入力欄を出すほうが有害なので
//    トグルだけにしてある。
// 🔴 1レースの型（A〜F）が売るプランをちょうど1つ決めるので、**型ごとに1つ**。
//    ここを OFF にすると、その型のレースは入稿されない（他の型が肩代わりしない）。
// 並びは型の順（A→F）。入稿の優先順位という概念は型ラボには無い（型が排他）。
const TYPE_LAB_ORDER: TypeLabRankKey[] =
  ["A_hit", "A_trio", "A_ana", "A_sign",
   "B_hit", "B_sign", "C_hit", "C_sign", "D_hit", "D_sign",
   "E_hit", "E_sign", "F_pay", "F_hit", "F_sign"];

// 🔴 `Record<TypeLabRankKey, ...>` にしておく。プランを増やしたらここが
//    型エラーになり、ラベルの付け忘れに気づける。
const TYPE_LAB_LABEL: Record<TypeLabRankKey, string> = {
  A_hit: "型A 鉄板（三連単・1着=◎/2着=○ 固定で3着流し 3〜5点）",
  A_trio: "型A 鉄板・三連複（◎○の2車軸＋相手2点／順序を捨てて当たる回数を取る）",
  A_ana: "型A 波乱狙い（三連単・◎を外した6車から確率上位 5点・「穴狙い」付与）",
  B_hit: "型B 堅い・中（三連単・確率上位から想定平均払戻3万円の床まで 3〜8点）",
  C_hit: "型C 崩れ筋（三連単・予測20倍以上から確率上位 12点）",
  D_hit: "型D 混戦・軸あり（三連複・◎○の2車軸＋相手4点／最人気の相手を1車外す）",
  E_hit: "型E 混戦・中（三連単・予測30倍以上から確率上位 14点）",
  F_pay: "型F 大混戦（三連単・1着=◎固定/2着2車/3着流し 4点・「穴狙い」付与）",
  F_hit: "型F 大混戦・9車の決勝以外（三連単・軸2車＋相手2車の6順列 12点）",
  // 🔴 看板枠（2026-08-31）。**実際に売るのは keirin 側 `SIGNBOARD_TYPES` の型だけ**
  //    （既定は型F）。ここを ON にしても、その型が `SIGNBOARD_TYPES` に無ければ
  //    看板枠は組まれない——このトグルは「入稿を止める」ためのもの。
  A_sign: "型A 看板枠（三連単・当たれば15万円を狙うダッチ 2〜4点・「穴狙い」付与）",
  B_sign: "型B 看板枠（三連単・当たれば15万円を狙うダッチ 2〜4点・「穴狙い」付与）",
  C_sign: "型C 看板枠（三連単・当たれば15万円を狙うダッチ 2〜4点・「穴狙い」付与）",
  D_sign: "型D 看板枠（三連単・当たれば15万円を狙うダッチ 2〜4点・「穴狙い」付与）",
  E_sign: "型E 看板枠（三連単・当たれば15万円を狙うダッチ 2〜4点・「穴狙い」付与）",
  F_sign: "型F 看板枠（三連単・当たれば15万円を狙うダッチ 2〜4点・「穴狙い」付与）",
};

// 🔴 型ラボのプランは `TYPE_LAB_LABEL` が持つので**ここからは除く**。
//    含めると「テンプレートを編集できるランク」として扱われる。
const RANK_LABEL: Record<LegacyRankKey, string> = {
  _global: "全体",
  "7S": "7S（7車・三連複2軸流し5点）",
  "7B": "7B（◎◯一致・順序/相手で不一致・相手絞り3点）",
  // 9C（2026-08-14）: 旧 9S/9A を置換した9車のベースモデル。
  "9C": "9C（9車・ベースモデル/三連複 軸2車＋相手3〜7点・旧9S/9Aを統合）",
  // 7M1: 入稿の優先順位は最下位（7H1 の下）。重なったら必ず譲る。
  "7M1": "7M1（中間層・混戦×印不一致/三連複 軸2車＋相手1〜4点・○△は後回し、○が人気なら○1点）",
  // 7H1 は 2026-08-15 に三連単一本化（三連複BOX分を三連単へ振り直し）。
  // プレビューの {axis1}/{axis2} は他ランク向けの変数で、7H1 の本文では使わない
  // （タイトルは 2026-08-09 から {shape} でレース依存になった）。
  "7H1": "7H1（7車・穴推奨/本命バスト型・三連単フォーメーション8点・「穴狙い」付与）",
  "7H2": "7H2（7車・穴推奨/印なし2軸・三連単10点×700円+三連複BOX10点×300円・「穴狙い」付与）",
  // 9H1 も 7H1 と同じく本文は固定文（プレビューの {axis1}/{axis2} は使わない）。
  "9H1": "9H1（9車・穴推奨/高配当狙い・三連単フォーメーション6点・「穴狙い」付与）",
  // 7T1 は三連単の単一券種（2026-08-13 に旧 7H3 を置換）。**点数がレースごとに
  // 変わる**（払戻20万円に届く点だけを買う）のが他ランクと違うので明記する。
  "7T1": "7T1（7車・高配当/三連単・決勝限定×別ライン・軸2車固定+3着流し1〜5点可変・「穴狙い」付与）",
  "7T3": "7T3（7車・中配当/三連単・決勝限定・予測30倍以上から確率上位5点均等・ライン条件なし・「穴狙い」付与）",
  // 7C の相手点数は落差カットで可変。2026-08-15 から「1点に縮むなら相手の
  // 2,3番手2点へ差し替え」なので下限は2点（1点買いは出ない）。
  "7C": "7C（7車・ベースモデル/終日の二軸・三連複 軸2車＋相手2〜5点・他ランクと併存）",
};

// プレビュー用のテンプレート変数置換（keirin側 netkeirin_submit_wt.py と同じ
// 固定辞書の逐次 str.replace 方式。未定義の{...}はそのまま素通しする）。
const PREVIEW_VARS: Record<string, string> = {
  "{venue}": "小倉",
  "{race_no}": "9",
  "{date}": "2026-07-28",
  "{axis1}": "1",
  "{axis2}": "2",
  // {shape} / {shape_note} の実文言は keirin 側 src/race_shape.py（SHAPE_TITLES /
  // SHAPE_NOTES）がランク×レース構造ごとに決める。{stake_note} は実際に入稿する
  // 買い目（均等か傾斜か）から決まる。ここで実文言のコピーを持つと、あちらを直した
  // ときに黙って食い違うので、プレビューでは差し込み位置だけを示す。
  "{shape}": "（レース見解）",
  "{shape_note}": "（レース見解の1〜2文）",
  "{stake_note}": "（賭け金の配分の説明）",
};

function applyPreview(template: string, rank: string): string {
  let s = template.replaceAll("{rank}", rank);
  for (const [k, v] of Object.entries(PREVIEW_VARS)) {
    s = s.replaceAll(k, v);
  }
  return s;
}

type EditState = Record<string, NetkeirinSetting>;

function emptyRow(rank_key: NetkeirinRankKey): NetkeirinSetting {
  // 🔴 `auto_publish` の既定は **false（＝承認制）**。公開は不可逆なので、
  //    値が取れなかったときに公開する側へ倒してはいけない。
  // 🔴 `axis_gate_enabled` の既定は **true（＝ゲートあり）**。現行の挙動。
  return { rank_key, enabled: true, title_template: "", comment_template: "",
           auto_publish: false, axis_gate_enabled: true };
}

// ---------------------------------------------------------------------------
// トグルスイッチ（frontend/src/app/my/KeirinSettings.tsx と同じマークアップ）
// ---------------------------------------------------------------------------

function Toggle({ checked, onChange, disabled = false, title }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  /** 触らせない（グレーアウト）。前提となる別の設定に従属しているときに使う。 */
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      title={title}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 flex-shrink-0 ${
        checked ? "bg-blue-500" : "bg-gray-300"
      } ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// ページ
// ---------------------------------------------------------------------------

export default function NetkeirinSettingsPage() {
  const [rows, setRows] = useState<EditState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [isPending, startTransition] = useTransition();
  // 無効ランクは既定で畳む（2026-08-14・ユーザー要望「無効は除外」）。
  // 🔴 **完全に消してはいけない。** ここは唯一の再有効化手段なので、
  //    画面から消すと二度と ON に戻せなくなる。畳むだけにする。
  const [showDisabled, setShowDisabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchNetkeirinSettings();
        if (cancelled) return;
        const next: EditState = { _global: emptyRow("_global") };
        for (const rank of RANK_ORDER) next[rank] = emptyRow(rank);
        for (const rank of TYPE_LAB_ORDER) next[rank] = emptyRow(rank);
        // 全廃済みランク（S1/9SS等）の過去分の行がDBに残っている場合があるため、
        // 現行ランク（RANK_ORDER + '_global'）以外は取り込まない。取り込んでしまうと
        // 保存時のObject.values(rows)に混入し、バックエンドのNETKEIRIN_RANK_KEYS
        // allowlist検証で保存リクエスト全体が400エラーになってしまうため
        // （2026-08-01是正: S1='S1'/9SS='9SS'の既存行(enabled=false)で実際に確認）。
        for (const row of data) {
          if (row.rank_key === "_global"
              || (RANK_ORDER as string[]).includes(row.rank_key)
              || (TYPE_LAB_ORDER as string[]).includes(row.rank_key)) {
            next[row.rank_key] = row;
          }
        }
        setRows(next);
      } catch {
        if (!cancelled) setError("設定の取得に失敗しました");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const update = (rank: string, patch: Partial<NetkeirinSetting>) => {
    setRows((prev) => (prev ? { ...prev, [rank]: { ...prev[rank], ...patch } } : prev));
  };

  const handleSave = () => {
    if (!rows) return;
    setSaveMsg(null);
    startTransition(async () => {
      const result = await saveNetkeirinSettings(Object.values(rows));
      setSaveMsg({ ok: result.ok, text: result.message });
    });
  };

  // 自動公開（`_global` 行だけが持つ）。読めないときは false＝承認制へ倒す。
  const autoPublish = rows?._global.auto_publish ?? false;
  // 軸信頼ゲート（`_global` 行だけが持つ）。**読めないときは true＝ゲートあり**へ倒す
  // （自動公開とは倒す向きが逆。あちらは「公開しない」・こちらは「絞る」が安全側）。
  const axisGate = rows?._global.axis_gate_enabled ?? true;

  return (
    <div className="w-full sm:max-w-3xl sm:mx-auto px-3 sm:px-4 py-4 space-y-5 pb-28">
      {/* ヘッダー */}
      <div className="flex items-center gap-3">
        <Link
          href="/keirin"
          className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 transition-colors"
        >
          <ArrowLeft size={16} />
          戻る
        </Link>
        <div className="flex items-center gap-2 ml-1">
          <Bike size={20} className="text-blue-500" />
          <h1 className="text-lg font-extrabold tracking-widest text-gray-950 dark:text-white">KEIRIN</h1>
          <span className="text-sm font-semibold text-gray-500 dark:text-gray-400">入稿設定</span>
        </div>
      </div>

      <p className="text-xs sm:text-sm text-gray-600 dark:text-gray-300 leading-relaxed">
        netkeirin（ウマい車券）への下書き自動入稿を、予想ランクごとに ON/OFF・タイトル・
        コメントのテンプレートで制御します。コメント末尾には、そのレースの出走選手ごとの
        1着率・3着内率テーブルが自動的に追加されます。
        テンプレートには <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{venue}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{race_no}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{rank}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{date}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{axis1}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{axis2}"}</code>{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{shape}"}</code>{" "}
        が使えます（axis1・axis2は各ランクの三連複2軸流しの軸2車）。
        <br />
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{shape}"}</code>{" "}
        はレースの構造（1車抜け／二枚看板／同ライン／別線対決／先行争い／混戦）から
        自動で選ばれる見解の一言です。文言はランクごとに決まっており、ここでは変更できません。
        タイトルは「狙い｜{"{shape}"}」の形を推奨します（会場・R番号は netkeirin の一覧に
        別途表示されるため、タイトルに入れると重複します）。
        <br />
        見解本文では{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{shape_note}"}</code>{" "}
        （同じ構造判定を1〜2文にしたもの）と{" "}
        <code className="bg-gray-100 text-gray-700 px-1 rounded">{"{stake_note}"}</code>{" "}
        （実際に入稿する買い目が均等か傾斜かに応じた配分の説明）が使えます。
        本文の冒頭は netkeirin 側でプレビュー表示されうるため、
        <strong>軸2車の車番は冒頭に置かない</strong>でください。
      </p>

      {loading && (
        <div className="space-y-3 animate-pulse">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-white rounded-xl border border-gray-100 h-24" />
          ))}
        </div>
      )}

      {error && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-3 py-3 text-sm text-amber-700">
          {error}
        </div>
      )}

      {rows && (
        <>
          {/* 自動公開 ＋ 全体ON/OFF（2026-08-29）。
              🔴 **並びは「自動公開 → 全体の自動入稿」**（2026-08-29・ユーザー指定）。
                 自動公開が上位のスイッチで、下の自動入稿はそれに従属して
                 グレーアウトする。従属する側を先に見せると、なぜ触れないのかが
                 その場で分からない。
              🔴 **自動公開 ON のとき自動入稿は ON 固定**。入稿しないものは公開
                 できないので、この2つが独立に動けると「公開する設定なのに何も
                 出ない」という読めない状態が作れる。UI で固定するだけでなく
                 バックエンド（`PUT /keirin/netkeirin-settings`）でも同じ整合を
                 取っている（API を直接叩かれても崩れないように）。 */}
          <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-bold text-gray-800">自動公開</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  ONにすると、入稿データの作成と同時に netkeirin へ入稿し、
                  <b>そのまま公開まで</b>行います（入稿確認は行いません）。
                  <br />
                  OFFのときは「入稿案」だけを作り、
                  <a href="/keirin/review" className="text-blue-600 underline">確認・承認画面</a>
                  で承認するまで netkeirin へは何も出ません。
                  <span className="block text-amber-600 mt-0.5">
                    ⚠️ 公開は取り消せません（netkeirin は公開後の修正ができません）。
                  </span>
                </p>
              </div>
              <Toggle
                checked={autoPublish}
                onChange={(v) =>
                  // 自動公開 ON は自動入稿 ON が前提。同時に立てる。
                  update("_global", v ? { auto_publish: true, enabled: true }
                                      : { auto_publish: false })}
              />
            </div>

            <div className="flex items-center justify-between gap-3 border-t border-gray-100 pt-4">
              <div>
                <p className="text-sm font-bold text-gray-800">全体の自動入稿</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  OFFにすると、各ランクのON/OFFに関わらず自動入稿を停止します。
                  {autoPublish && (
                    <span className="block text-amber-600 mt-0.5">
                      自動公開がONの間は変更できません（OFFにするには先に自動公開をOFF）。
                    </span>
                  )}
                </p>
              </div>
              <Toggle
                checked={autoPublish ? true : rows._global.enabled}
                disabled={autoPublish}
                title={autoPublish
                  ? "自動公開がONの間は自動入稿をOFFにできません"
                  : undefined}
                onChange={(v) => update("_global", { enabled: v })}
              />
            </div>

            {/* 軸信頼ゲート（2026-08-31）。
                🔴 **既定は ON。** 2026-08-27 の導入以来ずっと掛かっていたが
                   画面から見えなかったので、存在と効き具合を出す。
                🔴 看板レースは ON でも素通しする（`_passes_axis_gate`）。 */}
            <div className="flex items-center justify-between gap-3 border-t border-gray-100 pt-4">
              <div>
                <p className="text-sm font-bold text-gray-800">軸信頼ゲート</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  各プランの中で<b>軸信頼（上位2車の3着内率の合計）が下位2割</b>の
                  レースを入稿しません。
                  <br />
                  実測 2026-08-31 は 89レース → <b>72レース</b>に絞っています。
                  外した側は表示的中 18.7%・ROI 68.7% で、残す側より明確に弱い層です。
                  <span className="block text-gray-400 mt-0.5">
                    ※ 看板レース（決勝・特選クラス）はこのゲートを素通りします。
                  </span>
                  {!axisGate && (
                    <span className="block text-amber-600 mt-0.5">
                      ⚠️ OFF の間は下位2割も入稿します（件数は増え、質は下がります）。
                    </span>
                  )}
                </p>
              </div>
              <Toggle
                checked={axisGate}
                onChange={(v) => update("_global", { axis_gate_enabled: v })}
              />
            </div>
          </section>

          {/* 型ラボのプラン（2026-08-28 の全面移行〜）。**ON/OFF だけ。**
              🔴 文面は keirin 側 `src/type_lab_submission.py` が正本なので、
                 テンプレート欄は出さない（書いても反映されない＝有害）。 */}
          <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
            <div className="px-4 py-3 bg-gray-50 border-b border-gray-100">
              <div className="text-sm font-semibold text-gray-800">型ラボのプラン</div>
              <p className="mt-1 text-xs leading-relaxed text-gray-600">
                1レースの型（A〜F）が売るプランを<b>ちょうど1つ</b>決めます。
                OFF にするとその型のレースは入稿されません（他の型は肩代わりしません）。
                タイトル・コメント・印は<b>コード側が正本</b>で、この画面では編集できません。
              </p>
            </div>
            <div className="divide-y divide-gray-100">
              {TYPE_LAB_ORDER.map((rank) => (
                <div key={rank} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <span className="text-sm text-gray-700">{TYPE_LAB_LABEL[rank]}</span>
                  <Toggle
                    checked={rows[rank].enabled}
                    onChange={(v) => update(rank, { enabled: v })}
                  />
                </div>
              ))}
            </div>
          </section>

          {/* ランク別カード（有効なものだけ。並びはサマリーと同じ「車数＞優先順位」） */}
          {RANK_ORDER.filter((r) => rows[r].enabled).map((rank) => {
            const row = rows[rank];
            return (
              <section
                key={rank}
                className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden"
              >
                <div className="flex items-center justify-between gap-3 px-4 py-3 bg-gray-50 border-b border-gray-100">
                  <span className="text-sm font-semibold text-gray-800">{RANK_LABEL[rank]}</span>
                  <Toggle checked={row.enabled} onChange={(v) => update(rank, { enabled: v })} />
                </div>
                <div className="px-4 py-3 space-y-3">
                  <div>
                    {/* 🔴 入力欄には**必ず色を明示する**（2026-08-24 ユーザー報告）。
                        このカードは `bg-white` で dark: 変種を持たない一方、入力欄に
                        色指定が無いとページ側のダークモード用の**明るい文字色を継承**し、
                        白いカードの上で文字がほぼ見えなくなる（スマホのダークモードで発生）。
                        ⚠️ `dark:` 変種を足すのも誤り——カードが白のままなので、
                           入力欄だけ暗くなって不整合になる。 */}
                    <label className="text-xs font-medium text-gray-500 mb-1 block">
                      タイトルテンプレート
                    </label>
                    <input
                      type="text"
                      value={row.title_template}
                      onChange={(e) => update(rank, { title_template: e.target.value })}
                      className="w-full px-3 py-2 rounded-lg text-sm border border-gray-200 bg-white text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-300"
                      placeholder="自信の二軸｜{shape}"
                    />
                    {row.title_template && (
                      <p className="text-xs text-gray-400 mt-1 truncate">
                        プレビュー: {applyPreview(row.title_template, rank)}
                      </p>
                    )}
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-500 mb-1 block">
                      コメントテンプレート
                    </label>
                    <textarea
                      value={row.comment_template}
                      onChange={(e) => update(rank, { comment_template: e.target.value })}
                      rows={5}
                      className="w-full px-3 py-2 rounded-lg text-sm border border-gray-200 bg-white text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-300 font-mono"
                      placeholder="本日の二軸をお届けします。"
                    />
                    <p className="text-xs text-gray-400 mt-1">
                      ※ このテキストの末尾に選手成績テーブル（車番・印・選手名・1着率・3着内率）が自動追加されます。
                    </p>
                  </div>
                </div>
              </section>
            );
          })}

          {/* 無効のランク。既定で畳んでおく（一覧の主役は「いま売っている商品」）。
              ここでONに戻すと即座に上の一覧へ移動する（rows の enabled を直接見ているため）。 */}
          {RANK_ORDER.some((r) => !rows[r].enabled) && (
            <section className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
              <button
                type="button"
                onClick={() => setShowDisabled((v) => !v)}
                className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-gray-50 border-b border-gray-100 text-left"
              >
                <span className="text-sm font-semibold text-gray-500">
                  無効のランク（{RANK_ORDER.filter((r) => !rows[r].enabled).length}）
                </span>
                <span className="text-xs text-gray-400">{showDisabled ? "閉じる" : "開く"}</span>
              </button>
              {showDisabled && (
                <div className="divide-y divide-gray-100">
                  {RANK_ORDER.filter((r) => !rows[r].enabled).map((rank) => (
                    <div key={rank} className="flex items-center justify-between gap-3 px-4 py-2.5">
                      <span className="text-sm text-gray-600">{RANK_LABEL[rank]}</span>
                      <Toggle
                        checked={rows[rank].enabled}
                        onChange={(v) => update(rank, { enabled: v })}
                      />
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* 保存 */}
          <div className="sticky bottom-4 flex items-center gap-3 bg-white/90 backdrop-blur rounded-xl border border-gray-200 shadow-lg px-4 py-3">
            <button
              type="button"
              onClick={handleSave}
              disabled={isPending}
              className="px-4 py-2 rounded-lg text-sm font-semibold bg-blue-500 text-white hover:bg-blue-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              {isPending && <Loader2 size={14} className="animate-spin" />}
              {isPending ? "保存中..." : "保存"}
            </button>
            {saveMsg && (
              <p className={`text-sm ${saveMsg.ok ? "text-green-600" : "text-red-600"}`}>
                {saveMsg.text}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
