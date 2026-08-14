"use client";

/**
 * 入稿案の確認・承認 UI（2026-08-11 新設）。
 *
 * レースごとに「入稿内容・期待値・最低/最高払戻・選手ごとの各入着率」を出す。
 * 操作はレース単位の入稿／取消と、場単位のまとめ入稿。
 *
 * 表示は **場別（場ごとに畳める）** と **発走時刻順** を切り替えられる
 * （2026-08-12 追加）。当日の進行を追うときは時刻順、場をまとめて承認するときは
 * 場別、と目的が違う。**選んだ並べ方は localStorage に覚える**ので、
 * 入稿・取消のたびに走る再描画やリロードで戻らない（2026-08-14）。
 *
 * 場は**既定で畳んである**（2026-08-14）。上から順に見ていく使い方なので、
 * 全部開いていると目的の場まで遠い。
 *
 * 🔴 **承認制の ON/OFF はこの画面にはない**（2026-08-12 に `/admin` の設定タブへ移動）。
 *    確認・承認の作業画面に「承認制そのものを切る」スイッチが同居していると、
 *    レースを見ている最中に誤って全体設定を倒しうる。
 */
import { useEffect, useMemo, useState, useSyncExternalStore, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, ChevronDown, ChevronRight, Settings } from "lucide-react";

import type { KeirinProposal, KeirinProposalEntry } from "@/lib/api";
import { makeRaceNormalizer } from "@/lib/keirinProb";

import {
  approveKeirinRaceAction,
  approveKeirinVenueAction,
  cancelKeirinAllAction,
  cancelKeirinSubmissionAction,
  cancelKeirinVenueAction,
} from "../actions";

import CommentBody from "./CommentBody";

const MARK_LABEL: Record<number, string> = { 1: "◎", 2: "○", 3: "▲", 4: "△", 5: "☆" };

/** 一覧の並べ方。場別は場ごとに畳める。 */
type ViewMode = "venue" | "time";

/** 並べ方の保存先。リロード・画面更新をまたいで引き継ぐ（2026-08-14・ユーザー要望）。
 *
 * ⚠️ localStorage は React の外にある状態なので `useSyncExternalStore` で読む。
 *    `useState` + `useEffect` で復元すると
 *    ①SSR の初期HTMLと食い違って hydration が壊れる
 *    ②effect 内の setState が lint (react-hooks/set-state-in-effect) で弾かれる。
 *    サーバー側スナップショットは既定値を返し、hydration 後に実値へ入れ替わる。
 */
const VIEW_STORAGE_KEY = "keirin.review.view";

let viewListeners: Array<() => void> = [];

function subscribeView(cb: () => void): () => void {
  viewListeners.push(cb);
  window.addEventListener("storage", cb);
  return () => {
    viewListeners = viewListeners.filter((l) => l !== cb);
    window.removeEventListener("storage", cb);
  };
}

function getViewSnapshot(): ViewMode {
  try {
    return window.localStorage.getItem(VIEW_STORAGE_KEY) === "time" ? "time" : "venue";
  } catch {
    return "venue";           // プライベートブラウジング等
  }
}

const getViewServerSnapshot = (): ViewMode => "venue";

function setStoredView(v: ViewMode): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, v);
  } catch {
    // 保存できなくても切り替えは効かせる（この描画中は listeners 経由で反映）
  }
  for (const l of viewListeners) l();
}

function yen(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${Math.round(n).toLocaleString()}円`;
}

function hhmm(startAt: number | null): string {
  if (!startAt) return "--:--";
  // `feedback_jst_timezone`: 日時表示は必ず timeZone を明示する（サーバーはUTC）
  return new Date(startAt * 1000).toLocaleTimeString("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 「—」or「12.3%」。正規化できない（全車欠損）ときは「—」。 */
function pct(v: number | null): string {
  return v == null ? "—" : `${v.toFixed(1)}%`;
}

function EntryTable({ entries, axis1, axis2 }: {
  entries: KeirinProposalEntry[];
  axis1: number | null;
  axis2: number | null;
}) {
  // 🔴 並びは**車番順ではなく強い順**（1着率 → 2着内率 → 3着内率 の降順）。
  //    承認時に見たいのは「誰が上位か」で、車番は既に列にある。
  //    欠損は末尾へ送り、全て同値なら車番順に落ち着かせる。
  const sorted = useMemo(() => {
    const cmp = (a: number | null, b: number | null) =>
      (b ?? -Infinity) - (a ?? -Infinity);
    return [...entries].sort(
      (a, b) =>
        cmp(a.pred_win_pct, b.pred_win_pct) ||
        cmp(a.pred_top2_pct, b.pred_top2_pct) ||
        cmp(a.pred_top3_pct, b.pred_top3_pct) ||
        a.frame_no - b.frame_no,
    );
  }, [entries]);

  // レース内合計を揃えてから出す（生確率のままだと1着率の合計が10%等になる）。
  // ⚠️ netkeirin 入稿コメントの出走表と**同じ正規化**であること。片方だけ変えると
  //    顧客に見せている表と承認画面の表が食い違う。
  const normWin = makeRaceNormalizer(entries.map((e) => e.pred_win_pct), 1);
  const normTop2 = makeRaceNormalizer(
    entries.map((e) => e.pred_top2_pct), Math.min(entries.length, 2));
  const normTop3 = makeRaceNormalizer(
    entries.map((e) => e.pred_top3_pct), Math.min(entries.length, 3));

  if (entries.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-gray-500 dark:text-gray-400">
          <tr className="text-left">
            <th className="py-1 pr-2">車</th>
            <th className="py-1 pr-2">選手</th>
            <th className="py-1 pr-2">印</th>
            <th className="py-1 pr-2 text-right">得点</th>
            <th className="py-1 pr-2 text-right">1着率</th>
            {/* 2着内率は 2026-08-12 追加。それ以前のレースは「—」になる。 */}
            <th className="py-1 pr-2 text-right">2着内率</th>
            <th className="py-1 pr-2 text-right">3着内率</th>
            <th className="py-1 pr-2">ライン</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((e) => {
            const isAxis = e.frame_no === axis1 || e.frame_no === axis2;
            return (
              <tr
                key={e.frame_no}
                className={isAxis ? "font-semibold text-blue-700 dark:text-blue-300" : ""}
              >
                <td className="py-0.5 pr-2">{e.frame_no}</td>
                <td className="py-0.5 pr-2">{e.name ?? "—"}</td>
                <td className="py-0.5 pr-2">
                  {e.prediction_mark ? (MARK_LABEL[e.prediction_mark] ?? "") : ""}
                </td>
                <td className="py-0.5 pr-2 text-right tabular-nums">
                  {e.race_point?.toFixed(2) ?? "—"}
                </td>
                <td className="py-0.5 pr-2 text-right tabular-nums">
                  {pct(normWin(e.pred_win_pct))}
                </td>
                <td className="py-0.5 pr-2 text-right tabular-nums">
                  {pct(normTop2(e.pred_top2_pct))}
                </td>
                <td className="py-0.5 pr-2 text-right tabular-nums">
                  {pct(normTop3(e.pred_top3_pct))}
                </td>
                <td className="py-0.5 pr-2">
                  {e.line_group ? `${e.line_group}-${e.line_pos ?? ""}` : "単騎"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * 入稿・取消の締切（発走15分前）。
 *
 * 🔴 判定の正本は `backend/src/services/keirin_submission_window.py`。
 *    ここは表示のためだけの写しで、**API 側でも必ず弾く**（画面を信用しない）。
 *    値がずれたら `backend/tests/test_keirin_submission_window.py` が落ちる。
 */
const SUBMIT_DEADLINE_SEC = 15 * 60;

/** 締切を過ぎているか。発走時刻が取れない行は「締切前」扱い（＝操作を許す）。 */
function isClosed(startAt: number | null, nowSec: number): boolean {
  if (startAt === null) return false;
  return startAt - SUBMIT_DEADLINE_SEC - nowSec <= 0;
}

function RaceCard({ p, busy, closed, onApprove, onCancel, canForceCancel, onForceCancel }: {
  p: KeirinProposal;
  busy: boolean;
  /** 発走15分前を過ぎた＝netkeirin が受け付けないので入稿・取消できない。 */
  closed: boolean;
  onApprove: () => void;
  onCancel: () => void;
  /** 通常の取消が「netkeirin に見つからない」で失敗した後だけ true。 */
  canForceCancel: boolean;
  onForceCancel: () => void;
}) {
  const [open, setOpen] = useState(false);
  const d = p.bet_detail;
  return (
    <div className="rounded border border-gray-200 p-3 dark:border-gray-700">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">
          {p.venue_name}
          {p.race_no}R
        </span>
        <span className="text-xs text-gray-500">{hhmm(p.start_at)}</span>
        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs dark:bg-gray-800">
          {p.rank_key}
        </span>
        {/* 勝負アイコン「自信あり」。**1日1レースだけ**なので目立たせる。
            選定は keirin の pick_confident_race_wt.py（当日全レースの期待値比較）。 */}
        {p.is_confident && (
          <span
            className="rounded bg-yellow-400 px-1.5 py-0.5 text-xs font-semibold text-yellow-950 dark:bg-yellow-500 dark:text-yellow-950"
            title="本日の「自信あり」に選ばれたレース（期待値が当日最高）。netkeirin では1日1つしか付けられません"
          >
            ★自信
          </span>
        )}
        {/* 看板は一覧と同じ★で揃える（2026-08-14・ユーザー要望）。
            画面ごとに「看板」「★」と表記が割れていると同じ意味だと分からない。 */}
        {p.is_marquee && (
          <span
            className="text-amber-500 dark:text-amber-400 text-sm"
            title="看板レース（決勝・特選クラス）"
          >
            ★
          </span>
        )}
        {/* 🔴 「穴埋め」バッジは 2026-08-14 に削除（ユーザー要望・一覧と揃える）。
            出自は API の `origin` に残るので、分析（/keirin/stats の分析タブ）では
            引き続き「看板の穴埋め」として区別できる。 */}
        {p.origin === "manual" && (
          <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-800 dark:bg-purple-900 dark:text-purple-200">
            手動
          </span>
        )}
        <span
          className={
            p.status === "proposed"
              ? "rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800 dark:bg-blue-900 dark:text-blue-200"
              : p.status === "submitted"
                ? "rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-800 dark:bg-green-900 dark:text-green-200"
                : "rounded bg-gray-200 px-1.5 py-0.5 text-xs text-gray-600 dark:bg-gray-700"
          }
        >
          {p.status === "proposed" ? "未入稿" : p.status === "submitted" ? "入稿済" : "取消"}
        </span>
        {closed && p.status !== "deleted" && (
          <span
            className="rounded bg-gray-300 px-1.5 py-0.5 text-xs text-gray-700 dark:bg-gray-600 dark:text-gray-200"
            title="発走15分前を過ぎたため netkeirin が入稿・取消を受け付けません"
          >
            締切
          </span>
        )}
        <span className="ml-auto flex gap-2">
          {p.status === "proposed" && (
            <button
              type="button"
              disabled={busy || closed}
              onClick={onApprove}
              title={closed ? "発走15分前を過ぎたため入稿できません" : undefined}
              className="rounded bg-blue-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            >
              このレースを入稿
            </button>
          )}
          {p.status !== "deleted" && (
            <button
              type="button"
              disabled={busy || closed}
              onClick={onCancel}
              title={closed ? "発走15分前を過ぎたため取消できません" : undefined}
              className="rounded border border-red-300 px-3 py-1 text-xs text-red-700 disabled:opacity-50 dark:border-red-700 dark:text-red-300"
            >
              取消
            </button>
          )}
          {/* 🔴 通常の取消が失敗したときにだけ出す。netkeirin 側で先に消していると
              item_id が引けず、従来はそこで止まって **DB も更新されない**ままだった。
              常時出すと「netkeirin に残っているのに記録だけ消す」事故になる。 */}
          {canForceCancel && p.status !== "deleted" && (
            <button
              type="button"
              disabled={busy}
              onClick={onForceCancel}
              title="netkeirin 側は操作せず、記録だけを取消にします"
              className="rounded bg-red-700 px-3 py-1 text-xs text-white disabled:opacity-50"
            >
              強制取消（記録のみ）
            </button>
          )}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <div>
          <span className="text-gray-500">投資</span> {yen(d?.total)}
        </div>
        <div>
          <span className="text-gray-500">最低払戻</span>{" "}
          <span className={p.gami_risk ? "font-semibold text-red-600 dark:text-red-400" : ""}>
            {yen(p.min_payout)}
          </span>
        </div>
        <div>
          <span className="text-gray-500">最高払戻</span> {yen(p.max_payout)}
        </div>
        <div>
          {/* 🔴 「自信あり」の選定に使った期待値を優先して出す（全点を予測オッズで
              統一したもの）。板由来の `expected_value` は夜開催で板が育っておらず
              終日の比較に使えないため、選定の根拠にはならない。
              選定前（confident_ev が未算出）のときだけ従来の板由来を出す。 */}
          <span className="text-gray-500">
            期待値{p.confident_ev !== null && <span className="text-[10px]">(予測)</span>}
          </span>{" "}
          {p.confident_ev !== null
            ? p.confident_ev.toFixed(2)
            : p.expected_value === null ? "—" : p.expected_value.toFixed(2)}
          {/* 🔴 予測オッズ由来が混ざっているなら黙って出さない。
              板の実測値と同じ顔で並べると「実際にこの払戻」と読まれる。 */}
          {p.odds_has_predicted && (
            <span
              className="ml-1 text-[10px] text-indigo-500 dark:text-indigo-300"
              title="板に無いオッズをオッズ生成モデルの予測値で補って計算しています"
            >
              *予測含む
            </span>
          )}
        </div>
      </div>
      {p.gami_risk && (
        <p className="mt-1 text-xs text-red-600 dark:text-red-400">
          ⚠️ 当たっても投資を下回る目があります（最低払戻 &lt; 投資）
        </p>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-2 text-xs text-blue-600 underline dark:text-blue-400"
      >
        {open ? "詳細を閉じる" : "買い目・文面・選手を見る"}
      </button>

      {open && (
        <div className="mt-2 space-y-3 border-t border-gray-200 pt-2 dark:border-gray-700">
          <div>
            <p className="text-xs font-medium text-gray-600 dark:text-gray-300">タイトル</p>
            <p className="text-sm">{p.title || "（未設定）"}</p>
          </div>
          <div>
            <p className="text-xs font-medium text-gray-600 dark:text-gray-300">コメント</p>
            {/* コメントには HTML タグ入力（現状は出走表の <table> のみ）が混ざる。
                生タグのままでは読めないので解釈して表として出す（CommentBody）。 */}
            <CommentBody comment={p.comment} />
          </div>
          <div>
            <p className="text-xs font-medium text-gray-600 dark:text-gray-300">
              買い目（配分の出どころ: {d?.source ?? "均等"}）
            </p>
            <div className="overflow-x-auto">
              <table className="text-xs">
                <tbody>
                  {(d?.lines ?? []).map((l, i) => (
                    <tr key={`${l.combo}-${i}`}>
                      <td className="py-0.5 pr-3">{l.bet_type}</td>
                      <td className="py-0.5 pr-3 font-mono">{l.combo}</td>
                      <td className="py-0.5 pr-3 text-right tabular-nums">
                        {l.stake.toLocaleString()}円
                      </td>
                      <td className="py-0.5 pr-3 text-right tabular-nums text-gray-500">
                        {l.odds === null
                          ? "オッズ未取得"
                          : `${l.odds.toFixed(1)}倍${l.odds_source === "predicted" ? "（予測）" : ""}`}
                      </td>
                      <td className="py-0.5 text-right tabular-nums">
                        {l.odds === null ? "—" : yen(l.stake * l.odds)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <EntryTable entries={p.entries} axis1={p.axis1} axis2={p.axis2} />
        </div>
      )}
    </div>
  );
}

export default function ReviewClient({ date, items, nProposed }: {
  date: string;
  items: KeirinProposal[];
  nProposed: number;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [msg, setMsg] = useState<string | null>(null);
  /**
   * 締切判定に使う現在時刻（秒）。
   *
   * 🔴 サーバー側で計算すると **描画した瞬間の値で固まる**。この画面は上から順に
   *    確認していく作りで長時間開いたままになるため、開いている間に締切を跨ぐ。
   *    30秒ごとに進めてボタンの活殺を追従させる。
   * 🔴 初期値を `Date.now()` にすると SSR とクライアントで値が食い違い
   *    hydration エラーになる。マウント後に入れる（それまでは締切前扱い）。
   */
  const [nowSec, setNowSec] = useState<number>(0);
  useEffect(() => {
    const tick = () => setNowSec(Math.floor(Date.now() / 1000));
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);
  // 並べ方は localStorage が正本（詳細は VIEW_STORAGE_KEY のコメント）。
  const view = useSyncExternalStore(subscribeView, getViewSnapshot, getViewServerSnapshot);
  // 開いた場。**既定は全て畳んだ状態**（2026-08-14・ユーザー要望）。
  // 上から順に確認していく使い方なので、全部開いていると目的の場まで遠い。
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // 通常の取消が「netkeirin 側に見つからない」で失敗したレース。
  // 🔴 そのとき **DB も更新されていない**（取消したはずの行が生き残る）。
  //    強制取消の口をここで初めて出す。常時出すと、netkeirin に残っている
  //    商品を消したつもりで記録だけ消す事故につながる。
  const [forceTargets, setForceTargets] = useState<Record<string, boolean>>({});
  // 取消できる＝まだ生きている下書き（未入稿・入稿済の両方）。
  const nAliveAll = useMemo(
    () => items.filter((p) => p.status !== "deleted" && !isClosed(p.start_at, nowSec)).length,
    [items, nowSec],
  );

  const byVenue = useMemo(() => {
    const m = new Map<string, KeirinProposal[]>();
    for (const p of items) {
      const list = m.get(p.venue_name) ?? [];
      list.push(p);
      m.set(p.venue_name, list);
    }
    return [...m.entries()];
  }, [items]);

  // 発走時刻順。⚠️ **発走時刻が取れない行を先頭に混ぜない**（`start_at` は
  // null がありうる）。時刻不明はまとめて末尾へ送り、場・R番号で安定させる。
  const byTime = useMemo(
    () =>
      [...items].sort((a, b) => {
        const at = a.start_at ?? Infinity;
        const bt = b.start_at ?? Infinity;
        return (
          at - bt ||
          a.venue_name.localeCompare(b.venue_name, "ja") ||
          a.race_no - b.race_no
        );
      }),
    [items],
  );

  const run = (
    fn: () => Promise<{
      ok: boolean; message: string; n_ok?: number; n_ng?: number;
      results?: { ok: boolean; message: string }[];
    }>,
    /** 失敗が「netkeirin に見つからない」だったときに強制取消を出すためのキー */
    onNotFound?: string,
  ) => {
    setMsg(null);
    startTransition(async () => {
      const r = await fn();
      setMsg(
        r.n_ok === undefined
          ? r.message
          : `成功${r.n_ok}件 / 失敗${r.n_ng ?? 0}件: ${r.message}`,
      );
      // 明細側にしかメッセージが載らないので、両方を見て判定する
      const detail = (r.results ?? []).map((x) => x.message).join(" ");
      if (!r.ok && onNotFound && `${r.message} ${detail}`.includes("見つかりません")) {
        setForceTargets((prev) => ({ ...prev, [onNotFound]: true }));
      }
      // 状態が変わるのでサーバーから取り直す。
      // 🔴 `window.location.reload()` を使ってはいけない。ページ全体が再読込され
      //    **スクロール位置が先頭へ戻る**。この画面は上から順に確認していく作りなので、
      //    1件処理するたび先頭へ飛ばされると作業にならない（2026-08-13 修正）。
      //    `router.refresh()` はサーバーコンポーネントを取り直して差分だけ描き替える
      //    ので、位置も開閉状態も保たれる。
      if (r.ok) router.refresh();
    });
  };

  /** RaceCard 1枚分。場別・時刻順のどちらからも同じものを出す。 */
  const raceCard = (p: KeirinProposal) => (
    <RaceCard
      key={`${p.race_key}-${p.rank_key}`}
      p={p}
      busy={pending}
      closed={isClosed(p.start_at, nowSec)}
      onApprove={() => run(() => approveKeirinRaceAction(p.race_key, p.rank_key))}
      onCancel={() => {
        if (!window.confirm(`${p.venue_name}${p.race_no}R (${p.rank_key}) の入稿を取り消します。よろしいですか？`)) return;
        run(
          () => cancelKeirinSubmissionAction(p.race_key, p.rank_key),
          `${p.race_key}-${p.rank_key}`,
        );
      }}
      canForceCancel={!!forceTargets[`${p.race_key}-${p.rank_key}`]}
      onForceCancel={() => {
        if (!window.confirm(
          `${p.venue_name}${p.race_no}R (${p.rank_key}) の記録だけを取消にします。\n\n`
          + "netkeirin 側には何もしません。netkeirin にまだ商品が残っている場合は、\n"
          + "先に netkeirin 側で削除してください。よろしいですか？",
        )) return;
        run(() => cancelKeirinSubmissionAction(p.race_key, p.rank_key, true));
      }}
    />
  );

  return (
    <div className="mx-auto max-w-5xl p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        {/* 一覧へ戻る導線（設定・推奨ガイドと同じ形）。無いとブラウザの戻るしかない。 */}
        <Link
          href="/keirin"
          className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 transition-colors"
        >
          <ArrowLeft size={16} />
          戻る
        </Link>
        <h1 className="text-lg font-semibold">入稿の確認・承認</h1>
        {/* 入稿設定はここからだけ辿れる（2026-08-14 にトップのヘッダーから移設）。
            ランクごとの ON/OFF・文面はこの画面での確認とセットで触るものなので、
            確認画面の中に置くほうが導線として自然。 */}
        <Link
          href="/keirin/settings"
          className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
          aria-label="入稿設定"
        >
          <Settings size={15} />
          入稿設定
        </Link>
        <span className="text-sm text-gray-500">{date}</span>
        <span className="text-sm">
          未入稿 <span className="font-semibold">{nProposed}</span> 件
        </span>
        {/* 🔴 この日の下書きを全部消す。最も戻しにくい操作なので、
            件数を出したうえで **確認を2回** 挟む。日付は必ず添える
            （API・CLI の両方でも日付無しの全件取消は弾く）。 */}
        {nAliveAll > 0 && (
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              if (!window.confirm(
                `${date} の下書きを全件（${nAliveAll}件）取り消します。\n\n`
                + "netkeirin の公開待ち下書きもすべて削除されます。\n"
                + "この操作は元に戻せません。よろしいですか？",
              )) return;
              if (!window.confirm(
                `最終確認：${date} の ${nAliveAll}件 をすべて取り消します。`,
              )) return;
              run(() => cancelKeirinAllAction(date));
            }}
            className="rounded bg-red-700 px-3 py-1 text-xs text-white disabled:opacity-50"
          >
            この日を全件取消（{nAliveAll}件）
          </button>
        )}
        <div className="ml-auto flex rounded border border-gray-300 text-xs dark:border-gray-600">
          {([["venue", "場別"], ["time", "発走時刻順"]] as const).map(([v, label]) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setStoredView(v)}
              className={`px-3 py-1 first:rounded-l last:rounded-r ${view === v
                ? "bg-blue-600 text-white"
                : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <p className="mb-3 rounded bg-gray-50 p-2 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">
        承認制の ON/OFF は{" "}
        <Link href="/admin" className="text-blue-600 underline dark:text-blue-400">
          管理 → 設定
        </Link>{" "}
        にあります。OFF のときは朝のバッチが従来どおり netkeirin へ下書きを自動作成し、
        ON にすると承認するまで netkeirin へは何も出ません。
        <br />
        期待値は<strong>異常値の検知が目的</strong>で、購入判断の根拠には使えません
        （市場は効率的で、モデル由来の期待値による選別は繰り返し否定されています）。
        実用上は<strong>最低払戻がガミ域に入っていないか</strong>を見てください。
      </p>

      {msg && (
        <p className="mb-3 rounded border border-blue-200 bg-blue-50 p-2 text-sm dark:border-blue-800 dark:bg-blue-950">
          {msg}
        </p>
      )}

      {items.length === 0 && (
        <p className="text-sm text-gray-500">この日の入稿はありません。</p>
      )}

      {view === "time" && <div className="space-y-2">{byTime.map(raceCard)}</div>}

      {view === "venue" && byVenue.map(([venue, races]) => {
        // 🔴 締切（発走15分前）を過ぎたレースは数に入れない。入れると
        //    「N件をまとめて入稿」の N が実際に通る件数と食い違い、
        //    押した後に「成功3件/失敗2件」と出て初めて分かることになる。
        const nProp = races.filter(
          (r) => r.status === "proposed" && !isClosed(r.start_at, nowSec)).length;
        // 取消できる＝まだ生きている下書き（未入稿・入稿済の両方）で、かつ締切前。
        const nAlive = races.filter(
          (r) => r.status !== "deleted" && !isClosed(r.start_at, nowSec)).length;
        const isOpen = !!expanded[venue];
        return (
          <section key={venue} className="mb-6">
            <div className="mb-2 flex items-center gap-3">
              {/* 見出しごと開閉のトグルにする（畳んだときに何件あるかは右に残す）。 */}
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => setExpanded((prev) => ({ ...prev, [venue]: !isOpen }))}
                className="flex items-center gap-1 font-semibold hover:text-blue-700 dark:hover:text-blue-300"
              >
                {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                {venue}
              </button>
              <span className="text-xs text-gray-500">
                {races.length}件（未入稿 {nProp}）
              </span>
              {nProp > 0 && (
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => run(() => approveKeirinVenueAction(date, venue))}
                  className="rounded bg-blue-700 px-3 py-1 text-xs text-white disabled:opacity-50"
                >
                  この場をまとめて入稿（{nProp}件）
                </button>
              )}
              {/* 🔴 まとめて消す操作。件数を出したうえで二段で確認する
                  （元は事故防止のため用意していなかった機能）。 */}
              {nAlive > 0 && (
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => {
                    if (!window.confirm(
                      `${venue} の下書き ${nAlive}件 を取り消します。\n\n`
                      + "netkeirin の公開待ち下書きも削除されます。よろしいですか？",
                    )) return;
                    if (!window.confirm(`本当に ${venue} の ${nAlive}件 を取り消しますか？`)) return;
                    run(() => cancelKeirinVenueAction(date, venue));
                  }}
                  className="rounded border border-red-400 px-3 py-1 text-xs text-red-700 disabled:opacity-50 dark:border-red-600 dark:text-red-300"
                >
                  この場をまとめて取消（{nAlive}件）
                </button>
              )}
            </div>
            {isOpen && <div className="space-y-2">{races.map(raceCard)}</div>}
          </section>
        );
      })}
    </div>
  );
}
