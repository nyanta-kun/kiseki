"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { ArrowLeft, Bike, Loader2 } from "lucide-react";
import { fetchNetkeirinSettings, type NetkeirinRankKey, type NetkeirinSetting } from "@/lib/api";
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
// （7H1 > 7H2 > 9H1 > 7SS > 7S > 7A > 7C > 7T1 > 7B）。ここは設定画面の表示順。
const RANK_ORDER: NetkeirinRankKey[] =
  ["7H1", "7H2", "7S", "7C", "7T1", "7B", "9H1", "9C"];

const RANK_LABEL: Record<NetkeirinRankKey, string> = {
  _global: "全体",
  "7S": "7S（7車・三連複2軸流し5点）",
  "7B": "7B（◎◯一致・順序/相手で不一致・相手絞り3点）",
  // 9C（2026-08-14）: 旧 9S/9A を置換した9車のベースモデル。
  "9C": "9C（9車・ベースモデル/三連複 軸2車＋相手3〜7点・旧9S/9Aを統合）",
  // 7H1 は唯一の2券種ランク。プレビューの {axis1}/{axis2} は他ランク向けの変数で、
  // 7H1 の本文では使わない（タイトルは 2026-08-09 から {shape} でレース依存になった）。
  "7H1": "7H1（7車・穴推奨/本命バスト型・三連単F8点+三連複BOX・「穴狙い」付与）",
  "7H2": "7H2（7車・穴推奨/印なし2軸・三連単10点×700円+三連複BOX10点×300円・「穴狙い」付与）",
  // 9H1 も 7H1 と同じく本文は固定文（プレビューの {axis1}/{axis2} は使わない）。
  "9H1": "9H1（9車・穴推奨/高配当狙い・三連単フォーメーション6点・「穴狙い」付与）",
  // 7T1 は三連単の単一券種（2026-08-13 に旧 7H3 を置換）。**点数がレースごとに
  // 変わる**（払戻20万円に届く点だけを買う）のが他ランクと違うので明記する。
  "7T1": "7T1（7車・高配当/三連単・決勝系レース×別ライン限定・軸2車固定+3着流し1〜5点可変・「穴狙い」付与）",
  "7C": "7C（7車・ベースモデル/終日の二軸・三連複 軸2車＋相手4〜5点・他ランクと併存）",
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
  return { rank_key, enabled: true, title_template: "", comment_template: "" };
}

// ---------------------------------------------------------------------------
// トグルスイッチ（frontend/src/app/my/KeirinSettings.tsx と同じマークアップ）
// ---------------------------------------------------------------------------

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 flex-shrink-0 ${
        checked ? "bg-blue-500" : "bg-gray-300"
      }`}
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
        // 全廃済みランク（S1/9SS等）の過去分の行がDBに残っている場合があるため、
        // 現行ランク（RANK_ORDER + '_global'）以外は取り込まない。取り込んでしまうと
        // 保存時のObject.values(rows)に混入し、バックエンドのNETKEIRIN_RANK_KEYS
        // allowlist検証で保存リクエスト全体が400エラーになってしまうため
        // （2026-08-01是正: S1='S1'/9SS='9SS'の既存行(enabled=false)で実際に確認）。
        for (const row of data) {
          if (row.rank_key === "_global" || (RANK_ORDER as string[]).includes(row.rank_key)) {
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
          {/* 全体ON/OFF */}
          <section className="bg-white rounded-xl border border-gray-100 shadow-sm p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-bold text-gray-800">全体の自動入稿</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  OFFにすると、各ランクのON/OFFに関わらず自動入稿を停止します。
                </p>
              </div>
              <Toggle
                checked={rows._global.enabled}
                onChange={(v) => update("_global", { enabled: v })}
              />
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
                    <label className="text-xs font-medium text-gray-500 mb-1 block">
                      タイトルテンプレート
                    </label>
                    <input
                      type="text"
                      value={row.title_template}
                      onChange={(e) => update(rank, { title_template: e.target.value })}
                      className="w-full px-3 py-2 rounded-lg text-sm border border-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-300"
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
                      className="w-full px-3 py-2 rounded-lg text-sm border border-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-300 font-mono"
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
