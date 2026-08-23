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
import { ArrowLeft, ChevronDown, ChevronRight, ChevronUp, Settings } from "lucide-react";

import type {
  KeirinProposal, KeirinProposalEntry, KeirinProposalSummary,
} from "@/lib/api";
import { makeRaceNormalizer } from "@/lib/keirinProb";

import {
  approveKeirinAllAction,
  approveKeirinRaceAction,
  approveKeirinVenueAction,
  cancelKeirinAllAction,
  cancelKeirinPicksAction,
  publishKeirinAllAction,
  publishKeirinRaceAction,
  publishKeirinVenueAction,
  fetchKeirinPublishWaitAction,
  syncKeirinPublishStatusAction,
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

/** 軸の表示ラベル（「3 山田太郎」）。名前が引けない場合は車番だけ。 */
function axisLabel(entries: KeirinProposalEntry[], frameNo: number): string {
  const name = entries.find((e) => e.frame_no === frameNo)?.name;
  return name ? `${frameNo} ${name}` : `${frameNo}`;
}

/**
 * 買い目の表記を比較用のキーへ揃える。
 *
 * 🔴 **当たりかどうかの判定はここではない**（サーバーの `winning_combos`）。
 *    ここがやるのは表記ゆれの吸収だけ。三連複（`=`）は車番昇順、
 *    三連単（`-`）は着順そのまま。実データ2,970点は全て昇順だったが、
 *    生成側が変わったときに**静かに赤字が出なくなる**のを防ぐ。
 */
function comboKey(combo: string): string {
  if (combo.includes("=")) {
    return combo
      .split("=")
      .map((x) => Number(x))
      .sort((a, b) => a - b)
      .join("=");
  }
  return combo;
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

  // 確定後は着順を出す（2026-08-22・ユーザー要望）。**1件でも着順が入っていれば確定扱い**。
  // 🔴 `finish_order` は **発走前が null・欠車/失格が 0**。0 を null と同じに扱うと
  //    「走ったが着外」が「まだ走っていない」に化ける。
  const settled = entries.some((e) => e.finish_order !== null && e.finish_order > 0);

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
            {settled && <th className="py-1 pr-2">着順</th>}
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
                {/* 3着以内は色を付けて拾えるようにする。0＝欠車・失格は「—」ではなく
                    明示する（着外と未出走を混ぜない）。 */}
                {settled && (
                  <td className="py-0.5 pr-2 tabular-nums">
                    {e.finish_order === null ? (
                      <span className="text-gray-400">—</span>
                    ) : e.finish_order === 0 ? (
                      <span className="text-gray-400">欠</span>
                    ) : e.finish_order <= 3 ? (
                      <span className="font-bold text-red-600 dark:text-red-400">
                        {e.finish_order}着
                      </span>
                    ) : (
                      <span className="text-gray-500">{e.finish_order}着</span>
                    )}
                  </td>
                )}
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

/**
 * 「いま前後」ブロックが拾う幅（片側・秒）。2026-08-21 ユーザー要望。
 *
 * 推奨が多い日は一覧が長く、**いま手を打つべきレース**が上下に埋もれる。
 * 表示時刻の前後30分だけを集めた節を先頭に置いて、開いたまま放置しても
 * そこだけ見れば間に合うようにする。
 *
 * ⚠️ この節は**再掲であって分割ではない**。下の「発走前 / 発走済」にも同じ
 *    レースが出る。分割にすると、畳んだときに一覧から消えて見落とす。
 */
const NOW_WINDOW_SEC = 30 * 60;

/** 1入稿を一意に指す鍵。1レースに複数ランクが並びうるので race_key だけでは足りない。 */
function pickKey(p: { race_key: string; rank_key: string }): string {
  return `${p.race_key}#${p.rank_key}`;
}

/** 締切を過ぎているか。発走時刻が取れない行は「締切前」扱い（＝操作を許す）。 */
function isClosed(startAt: number | null, nowSec: number): boolean {
  if (startAt === null) return false;
  return startAt - SUBMIT_DEADLINE_SEC - nowSec <= 0;
}

function RaceCard({ p, busy, closed, onApprove, onPublish,
                   onCancel, canForceCancel, onForceCancel }: {
  p: KeirinProposal;
  busy: boolean;
  /** 発走15分前を過ぎた＝netkeirin が受け付けないので入稿・取消・公開できない。 */
  closed: boolean;
  /** netkeirin へ入稿するだけ（公開はしない）。 */
  onApprove: () => void;
  /** 公開する。**入稿前なら入稿の上で公開**（公開は不可逆）。 */
  onPublish: () => void;
  onCancel: () => void;
  /** 通常の取消が「netkeirin に見つからない」で失敗した後だけ true。 */
  canForceCancel: boolean;
  onForceCancel: () => void;
}) {
  const [open, setOpen] = useState(false);
  const d = p.bet_detail;
  // 軸は最大2車。片方しか無いランクもあるので、null を落として取れている分だけ扱う。
  const axes = [p.axis1, p.axis2].filter((n): n is number => n !== null);
  // 🔴 **売っていないレース（取消・入稿前）は `result` が付かない**。
  //    確定済みなのに「… 未確定」と出ると、落とした判断が正しかったかを
  //    カードの一覧で追えない（2026-08-22・ユーザー要望）。
  //    買い目と当たり目は手元にあるので、**買っていれば幾ら返ったか**を出す。
  //    ⚠️ **「的中」とは呼ばない。** 買っていない以上これは実績ではなく参考値で、
  //       サマリーの回収率にも入っていない。文言で必ず区別する。
  const wonKeys = new Set((p.winning_combos ?? []).map(comboKey));
  const hypothetical = useMemo(() => {
    if (p.result || wonKeys.size === 0) return null;   // 売った分は実績を出す
    const line = (d?.lines ?? []).find((l) => wonKeys.has(comboKey(l.combo)));
    if (!line || line.odds === null) return null;
    return { payout: Math.round(line.stake * line.odds), bet: d?.total ?? 0 };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.result, p.winning_combos, d]);
  // 🔴 取消・自信ありは**カード全体**で分かるようにする（2026-08-16・ユーザー要望）。
  //    小さなバッジだけだと、一覧をスクロールしているときに見落とす。
  // 🔴 取消は**グレーアウト**（2026-08-22・ユーザー要望）。以前は
  //    `opacity-50 line-through` だったが、取り消し線が数字の上に重なって
  //    **取り消したレースのその後（着順・払戻）が読めなかった**。取消は
  //    「もう操作しない」という意味であって「見なくていい」ではない——
  //    落とした判断が正しかったかは確定後に確認する。
  //    → 地色をグレーにし文字を淡くするだけにして、内容は読めるまま残す。
  const cardCls = p.status === "deleted"
    ? "rounded border border-gray-200 bg-gray-100 p-3 text-gray-500 dark:border-gray-700 dark:bg-gray-800/60 dark:text-gray-400"
    : p.is_confident
      ? "rounded border-2 border-yellow-400 bg-yellow-50 p-3 dark:border-yellow-500 dark:bg-yellow-950/30"
      : "rounded border border-gray-200 p-3 dark:border-gray-700";
  return (
    <div className={cardCls}>
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
                : p.status === "published"
                  ? "rounded bg-emerald-600 px-1.5 py-0.5 text-xs text-white"
                  : "rounded bg-gray-200 px-1.5 py-0.5 text-xs text-gray-600 dark:bg-gray-700"
          }
        >
          {/* 入稿済＝netkeirin へ送っただけ（公開待ち）。公開済＝顧客から見える。 */}
          {p.status === "proposed" ? "未入稿"
            : p.status === "submitted" ? "入稿済(未公開)"
              : p.status === "published" ? "公開済" : "取消"}
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
              入稿
            </button>
          )}
          {/* 🔴 「公開」は**入稿前なら入稿の上で公開**する（2026-08-16・ユーザー指定）。
              入稿済かどうかを人に意識させないための1ボタンで、判断は CLI 側が持つ
              （`_publishable` が proposed も拾い、`_run` が先に入稿する）。
              🔴 公開は不可逆（netkeirin の文言「公開後は修正できなくなります」）。 */}
          {(p.status === "proposed" || p.status === "submitted") && (
            <button
              type="button"
              disabled={busy || closed}
              onClick={onPublish}
              title={closed ? "発走15分前を過ぎたため公開できません"
                : p.status === "proposed"
                  ? "入稿したうえで公開します（公開後は修正できません）"
                  : "netkeirin で公開します（公開後は修正できません）"}
              className="rounded bg-emerald-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            >
              公開
            </button>
          )}
          {/* 🔴 取消は**公開後・締切後は効かない**ので押せなくする（NOP）。
              netkeirin の `delete` が効くのは公開待ちまでで、締切後は下書き削除も
              受け付けない。押せてしまうと「押したのに消えていない」に見える。 */}
          {p.status !== "deleted" && p.status !== "published" && (
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

      {/* 🔴 軸は**カードを畳んだままでも**見えるようにする（2026-08-16・ユーザー要望）。
          どの2車を軸にした買い目なのかは承認判断の中心で、これを見るためだけに
          1件ずつ「詳細を開く」のは一覧をスクロールする使い方と噛み合わない。
          軸が1車だけのランク（高配当系）もあるので、取れている分だけ出す。 */}
      {axes.length > 0 && (
        <p className="mt-2 text-xs">
          <span className="text-gray-500">{axes.length >= 2 ? "二軸" : "軸"}</span>{" "}
          <span className="font-semibold text-blue-700 dark:text-blue-300">
            {axes.map((n) => axisLabel(p.entries, n)).join(" / ")}
          </span>
        </p>
      )}

      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        <div>
          <span className="text-gray-500">投資</span> {yen(d?.total)}
        </div>
        <div>
          <span className="text-gray-500">最低払戻</span>{" "}
          <span className={p.gami_risk ? "font-semibold text-red-600 dark:text-red-400" : ""}>
            {yen(p.min_payout)}
          </span>
          {/* 🔴 `min_payout` は入稿時点の板由来で楽観的（実測 中央 確定/表示 0.860・
              45%が0.8倍未満）。下振れ側が出せるなら必ず併記する。これを出さないと
              「当たればこの額」と読まれ、確定後に下がって初めて気づくことになる。 */}
          {p.min_payout_low !== null && (
            <span
              className="ml-1 text-[10px] text-amber-600 dark:text-amber-400"
              title="確定までにオッズが下振れした場合の払戻（下側25%分位）。承認判断はこちらを見てください。"
            >
              下振れ {yen(p.min_payout_low)}
            </span>
          )}
        </div>
        <div>
          <span className="text-gray-500">最高払戻</span> {yen(p.max_payout)}
        </div>
        {/* 🔴 確定成績。**未確定は「—」**で 0円と区別する（発走前に「払戻0円」と
            出ると外れたように見える）。ガミ（当たったが払戻<投資）も明示する。
            ⚠️ 2026-08-21: 他の数値と同じ見え方だと一覧で結果を追えないため、
               **バッジにして地色ごと変える**（ユーザー要望）。色だけに頼らず
               記号（✓ / △ / ✗ / …）も必ず添える。 */}
        <div>
          <span className="text-gray-500">結果</span>{" "}
          {p.result == null && hypothetical ? (
            // 買っていないレースの参考値。実績（緑・赤）とは別の色にして混ぜない。
            <span
              className="inline-flex items-center rounded border border-dashed border-gray-400 px-1.5 py-0.5 text-xs font-semibold text-gray-600 dark:border-gray-500 dark:text-gray-300"
              title="このレースは売っていません。買い目が当たっていた場合の払戻（参考値）で、回収率には入りません。"
            >
              参考 買っていれば {yen(hypothetical.payout)}
            </span>
          ) : p.result == null && (p.winning_combos ?? []).length > 0 ? (
            <span
              className="inline-flex items-center rounded border border-dashed border-gray-400 px-1.5 py-0.5 text-xs font-semibold text-gray-500 dark:border-gray-500 dark:text-gray-400"
              title="このレースは売っていません。買い目は当たっていません（参考）。"
            >
              参考 外れ
            </span>
          ) : p.result == null ? (
            <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-300">
              … 未確定
            </span>
          ) : p.result.net_hit ? (
            <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-bold bg-emerald-600 text-white dark:bg-emerald-500">
              ✓ 的中 {yen(p.result.payout)}
            </span>
          ) : p.result.hit ? (
            <span
              className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-bold bg-amber-500 text-white"
              title="当たりましたが払戻が投資を下回りました（ガミ）"
            >
              △ ガミ {yen(p.result.payout)}
            </span>
          ) : (
            <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-bold bg-rose-600 text-white dark:bg-rose-500">
              ✗ 不的中
            </span>
          )}
        </div>
        <div>
          {/* 🔴 「自信あり」の選定に使った期待値を優先して出す（全点を予測オッズで
              統一したもの）。選定前（confident_ev が未算出）のときだけ
              `expected_value` を出す。
              ⚠️ 2026-08-21 から**出どころのラベルは出さない**（ユーザー判断）。
                 入稿の配分・足切り・表示オッズが全て予測オッズに揃ったため、
                 「予測かどうか」は画面で区別する意味が無くなった。 */}
          <span className="text-gray-500">期待値</span>{" "}
          {p.confident_ev !== null
            ? p.confident_ev.toFixed(2)
            : p.expected_value === null ? "—" : p.expected_value.toFixed(2)}
        </div>
        {/* 落車リスク（レースの波乱度の一部）。
            🔴 **大々的に出さない**（2026-08-21 ユーザー判断）。落車は常に存在する
               リスクで、警告として出すと毎回目に入るだけで判断を助けない。
               有用性を後から確かめられるよう、**数値だけ静かに常時出す**
               （高いときだけ出すと、当たり外れとの対応を目視で追えない）。
            🔴 **netkeirin の入稿データには含めない。** 表示専用。 */}
        {p.crash_risk != null && (
          <div className="text-gray-400 dark:text-gray-500">
            <span>落車</span> {(p.crash_risk * 100).toFixed(2)}%
          </div>
        )}
      </div>
      {p.gami_risk && (
        <p className="mt-1 text-xs text-red-600 dark:text-red-400">
          ⚠️ 当たっても投資を下回る目があります（
          {p.gami_risk_is_conservative ? "下振れ時の払戻" : "最低払戻"} &lt; 投資）
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
              買い目
            </p>
            <div className="overflow-x-auto">
              <table className="text-xs">
                <tbody>
                  {(d?.lines ?? []).map((l, i) => {
                    // 🔴 当たり目の判定は**サーバーが持つ**（`winning_combos`）。
                    //    同着では当たり目が複数になるので、実着順から組み立て直すと
                    //    必ず取りこぼす（2026-08-22 に採点側で実際に10件の
                    //    取りこぼしが見つかった型）。ここは一致を見るだけ。
                    const won = wonKeys.has(comboKey(l.combo));
                    return (
                    <tr
                      key={`${l.combo}-${i}`}
                      className={won ? "font-bold text-red-600 dark:text-red-400" : ""}
                    >
                      <td className="py-0.5 pr-3">{l.bet_type}</td>
                      <td className="py-0.5 pr-3 font-mono">
                        {won && <span className="mr-1">的中</span>}
                        {l.combo}
                      </td>
                      <td className="py-0.5 pr-3 text-right tabular-nums">
                        {l.stake.toLocaleString()}円
                      </td>
                      <td className="py-0.5 pr-3 text-right tabular-nums text-gray-500">
                        {l.odds === null
                          ? "オッズ未取得"
                          : `${l.odds.toFixed(1)}倍`}
                      </td>
                      <td className="py-0.5 text-right tabular-nums">
                        {l.odds === null ? "—" : yen(l.stake * l.odds)}
                      </td>
                    </tr>
                    );
                  })}
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

/** 当日サマリー。**netkeirin の表示と数字を合わせる**（回収率/的中率/予想数/購入/払戻/収支）。
 *
 * 🔴 **集計は確定した分だけ**（netkeirin の予想家成績と同じ）。未確定を購入へ
 *    混ぜると発走前の分だけ分母が膨らみ、回収率が 0% 近くに見えて「負けている」と
 *    誤読する。代わりに**予想数の横へ「未確定N」を併記**する。
 * 🔴 的中率はガミ（払戻<投資）を不的中と数える（netkeirin の表示と同じ）。
 */
function DaySummary({ s }: { s: KeirinProposalSummary }) {
  const cell = "border border-gray-200 px-3 py-1.5 dark:border-gray-700";
  const head = `${cell} bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300`;
  const pct = (v: number | null) => (v === null ? "—" : `${v.toFixed(1)}%`);
  const yenS = (v: number) => `${v > 0 ? "+" : ""}${v.toLocaleString()}円`;
  return (
    <table className="mb-3 w-full max-w-2xl border-collapse text-sm tabular-nums">
      <tbody>
        <tr>
          <th className={head}>回収率</th>
          <td className={`${cell} text-center`}>{pct(s.recovery_rate)}</td>
          <th className={head}>購入</th>
          <td className={`${cell} text-right`}>{s.bet.toLocaleString()}円</td>
        </tr>
        <tr>
          <th className={head}>的中率</th>
          <td className={`${cell} text-center`}>{pct(s.hit_rate)}</td>
          <th className={head}>払戻</th>
          <td className={`${cell} text-right`}>{s.payout.toLocaleString()}円</td>
        </tr>
        <tr>
          <th className={head}>予想数</th>
          <td className={`${cell} text-center`}>
            {s.n_races}レース
            {s.n_pending > 0 && (
              // 🔴 未確定は**常に改行**する（2026-08-16・ユーザー要望）。インラインだと
              //    セル幅次第で「30レース （未確定」で折り返し、件数が同じ行に並んで
              //    どちらの数字か読み取れなくなる。幅に依存せず必ず2行にする。
              <span className="block text-xs text-amber-600 dark:text-amber-400">
                （未確定{s.n_pending}）
              </span>
            )}
          </td>
          <th className={head}>収支</th>
          <td className={`${cell} text-right ${s.balance < 0
            ? "text-red-600 dark:text-red-400" : "text-emerald-700 dark:text-emerald-400"}`}>
            {yenS(s.balance)}
          </td>
        </tr>
      </tbody>
    </table>
  );
}

export default function ReviewClient({ date, items, nProposed, nUnpublished = 0, summary }: {
  date: string;
  items: KeirinProposal[];
  nProposed: number;
  /** 未公開（入稿済だが公開していない）件数。 */
  nUnpublished?: number;
  /** 当日サマリー（netkeirin と項目を合わせたもの）。 */
  summary?: KeirinProposalSummary;
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
  /**
   * netkeirin 側の公開待ち件数。**こちらの記録と食い違うことがある。**
   *
   * netkeirin は自分の画面からも公開できるので、そこで押されるとこちらは
   * `submitted`（公開待ち）のまま取り残される。2026-08-16 に35件、
   * 2026-08-19 に20件を実際に観測した。
   *
   * 🔴 `ok=false`（取得できなかった）と `count=0`（本当に0件）を必ず区別する。
   *    取れなかったのを0件と読むと「全部公開された」と誤って警告を出し、
   *    そのまま記録を書き換えると入稿を全部「公開済み」にしてしまう。
   */
  const [wait, setWait] = useState<{ ok: boolean; count: number } | null>(null);
  const reloadWait = () => {
    void fetchKeirinPublishWaitAction().then((r) => setWait({ ok: r.ok, count: r.count }));
  };
  useEffect(reloadWait, [date, nUnpublished]);
  // 食い違い＝こちらだけが「公開待ち」と思っている件数。
  // ⚠️ 逆（netkeirin のほうが多い）は**警告しない**。こちらに記録の無い入稿が
  //    netkeirin にあるだけで、status を書き換えて直せる話ではない。
  const nStale = wait?.ok ? Math.max(0, nUnpublished - wait.count) : 0;

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
  // 安い配当の一括取消ダイアログ。null なら閉じている。
  // 値は「取消対象にするか」を race_key+rank_key ごとに持つ（既定は全部 true）。
  const [cheapDialog, setCheapDialog] = useState<Record<string, boolean> | null>(null);
  // 取消できる＝まだ生きている下書き（未入稿・入稿済の両方）。
  // 🔴 **公開済みは取消できない**（netkeirin の `delete` が効くのは公開待ちまで）。
  //    件数に混ぜると一括取消が必ず一部失敗し、明細が読めなくなる。
  const nAliveAll = useMemo(
    () => items.filter((p) => p.status !== "deleted" && p.status !== "published"
      && !isClosed(p.start_at, nowSec)).length,
    [items, nowSec],
  );
  // 🔴 **想定払戻の平均が安いレース**（2026-08-24・ユーザー要望）。
  //    「リスクに見合わない配当」を人が確認して一括で取り消すための候補。
  //    ⚠️ **自動では落とさない。** 入稿はいったん通し、ここから人が消す。
  //       判定の正本は `keirin/src/stake_allocation.py::MIN_MEAN_PAYOUT`、
  //       印は API の `cheap_mean_payout`（画面で閾値を持たない）。
  //    ⚠️ 取消できる条件は一括取消と**同じ**にする（生きている・締切前）。
  //       ここだけ緩めると押した後に一部が必ず失敗する。
  const cheapTargets = useMemo(
    () => items.filter((p) => p.cheap_mean_payout
      && p.status !== "deleted" && p.status !== "published"
      && !isClosed(p.start_at, nowSec)),
    [items, nowSec],
  );

  // 一括承認できる＝まだ送っていない入稿案（proposed）で、かつ締切前。
  // 🔴 締切を過ぎた分を数に入れない（場単位と同じ規則）。入れると
  //    「N件を承認」の N が実際に通る件数と食い違い、押した後に初めて分かる。
  const nApprovableAll = useMemo(
    () => items.filter((p) => p.status === "proposed" && !isClosed(p.start_at, nowSec)).length,
    [items, nowSec],
  );
  // 一括公開できる＝**未入稿と公開待ちの両方**で、かつ締切前。
  // 🔴 未入稿を含めるのはレース単位の「公開」と同じ規則（入稿の上で公開する）。
  //    ここだけ公開待ちに絞ると、画面の件数と実際に公開される数が食い違う。
  const nPublishableAll = useMemo(
    () => items.filter(
      (p) => (p.status === "proposed" || p.status === "submitted")
        && !isClosed(p.start_at, nowSec)).length,
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
      // 🔴 失敗したときは**理由を必ず出す**（2026-08-16）。CLI の `_summarize` が
      //    `message` を返さなかった頃は、Server Action が既定文言で埋めるため
      //    「成功0件 / 失敗1件: 実行しました」という自己矛盾した表示になり、
      //    `results[]` にある本当の理由がどこにも出なかった（＝押しても
      //    「無反応」に見えた）。要約が理由を含まない場合に備えて明細も添える。
      const detail = (r.results ?? []).map((x) => x.message).join(" ");
      const reasons = [...new Set(
        (r.results ?? []).filter((x) => !x.ok).map((x) => x.message),
      )].join(" / ");
      const head = r.n_ok === undefined
        ? r.message
        : `成功${r.n_ok}件 / 失敗${r.n_ng ?? 0}件: ${r.message}`;
      setMsg(reasons && !head.includes(reasons) ? `${head}（${reasons}）` : head);
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
      onPublish={() => {
        const head = p.status === "proposed"
          ? `${p.venue_name}${p.race_no}R (${p.rank_key}) を入稿したうえで公開します。`
          : `${p.venue_name}${p.race_no}R (${p.rank_key}) を公開します。`;
        if (!window.confirm(`${head}\n\n🔴 公開後は修正できません。よろしいですか？`)) return;
        run(() => publishKeirinRaceAction(p.race_key, p.rank_key));
      }}
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
      {/* 🔴 スクロールが長い画面なので、下まで行っても離脱できるようにする
          （2026-08-21・ユーザー要望）。上部の「戻る」しか導線が無く、
          推奨が多い日は一覧へ帰るのに画面全体を巻き戻す必要があった。
          ⚠️ 「画面タップで出す」ではなく**一定量スクロールしたら出す**にした。
             タップ検知はカード内のボタン操作と区別できず、承認・公開のような
             取り返しのつかない操作の上に浮くと誤タップを誘発する。 */}
      <FloatingBack />
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
        {/* 未公開＝netkeirin へ送ったが公開していない。顧客からはまだ見えない。 */}
        <span className="text-sm">
          未公開 <span className="font-semibold text-emerald-700 dark:text-emerald-400">
            {nUnpublished}
          </span> 件
        </span>
        {/* この日の入稿案を全場まとめて承認して netkeirin へ送る（2026-08-16）。
            取消に全件があって承認だけ場単位止まりだったのを揃えた。
            🔴 外向きの操作なので取消と同じ作法 —— **件数を出して確認を1回**挟む。
               （取消ほど戻しにくくない＝送った後も個別・全件取消で消せるので二段にはしない）
            🔴 締切前の proposed だけを数える。submitted は含めない（二重入稿になる）。 */}
        {nApprovableAll > 0 && (
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              if (!window.confirm(
                `${date} の入稿案を全件（${nApprovableAll}件）netkeirin へ入稿します。\n\n`
                + "公開はしません（公開待ち一覧に並ぶだけ）。\n"
                + "よろしいですか？",
              )) return;
              run(() => approveKeirinAllAction(date));
            }}
            className="rounded bg-blue-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            title="この日の入稿案を全場まとめて netkeirin へ入稿します（公開はしません）"
          >
            入稿 {nApprovableAll}
          </button>
        )}
        {/* 🔴 「全件公開」は**未入稿も入稿の上で公開**する（レース単位のボタンと同じ規則）。
            公開は不可逆なので**二段確認**（取消と同じ重さで扱う）。 */}
        {nPublishableAll > 0 && (
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              if (!window.confirm(
                `${date} の ${nPublishableAll}件 を公開します。\n`
                + "（未入稿のものは入稿したうえで公開します）\n\n"
                + "🔴 公開後は修正できません。よろしいですか？")) return;
              if (!window.confirm(
                `最終確認：${date} の ${nPublishableAll}件 を公開します。`)) return;
              run(() => publishKeirinAllAction(date));
            }}
            className="rounded bg-emerald-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            title="この日の入稿を全場まとめて公開します（未入稿は入稿の上で公開・公開後は修正できません）"
          >
            公開 {nPublishableAll}
          </button>
        )}
        {/* 🔴 **想定払戻の平均が安いレースをまとめて取り消す**（2026-08-24・ユーザー要望）。
            「リスクに見合わない配当のレースは買わない」という方針の運用口。
            🔴 **押しただけでは消えない。** ダイアログで対象一覧（場・R・平均払戻）を
               出し、チェックを外せば個別に除外できる。全件取消と違って二段確認に
               しないのは、ダイアログ自体が確認になっているため。 */}
        {cheapTargets.length > 0 && (
          <button
            type="button"
            disabled={pending}
            onClick={() => setCheapDialog(
              Object.fromEntries(cheapTargets.map((p) => [pickKey(p), true])))}
            className="rounded bg-amber-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            title="想定払戻の平均が安いレースを一覧で確認してまとめて取り消します"
          >
            安い配当 {cheapTargets.length}
          </button>
        )}
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
            title="この日の下書きを全場まとめて取り消します（netkeirin の公開待ち下書きも削除）"
          >
            取消 {nAliveAll}
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

      {/* 🔴 netkeirin と記録が食い違っているときだけ出す警告（2026-08-19・ユーザー要望）。
          押すまで何も書き換えない —— 「公開された」のか「netkeirin 側で消された」のかは
          区別できない（netkeirin に公開済み一覧の API が無い）ので、人が確認してから。 */}
      {nStale > 0 && (
        <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-700 dark:bg-amber-950">
          <p className="mb-2">
            <strong>netkeirin と状態が食い違っています。</strong>
            {" "}こちらの記録では公開待ち <strong>{nUnpublished}</strong> 件ですが、
            netkeirin の公開待ちは <strong>{wait?.count ?? 0}</strong> 件です
            （差 {nStale} 件）。netkeirin の画面から直接公開されたと思われます。
          </p>
          <button
            type="button"
            disabled={pending}
            title="netkeirin の公開待ち一覧に無いものを『公開済み』にします（netkeirin は操作しません）"
            onClick={() => {
              if (!window.confirm(
                `${date} の記録 ${nStale}件 を「公開済み」に更新します。\n\n`
                + "netkeirin 側は操作しません（記録を実態へ合わせるだけです）。\n"
                + "⚠️ netkeirin 側で削除された分も「公開済み」になります。\n\n"
                + "よろしいですか？")) return;
              run(() => syncKeirinPublishStatusAction(date));
            }}
            className="rounded bg-amber-600 px-3 py-1 text-xs text-white disabled:opacity-50"
          >
            状態を合わせる（{nStale}件）
          </button>
        </div>
      )}
      {wait && !wait.ok && (
        <p className="mb-3 rounded border border-gray-300 bg-gray-50 p-2 text-xs text-gray-600 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300">
          netkeirin 側の公開待ち件数を取得できませんでした（食い違いの確認は保留しています）。
        </p>
      )}

      {msg && (
        <p className="mb-3 rounded border border-blue-200 bg-blue-50 p-2 text-sm dark:border-blue-800 dark:bg-blue-950">
          {msg}
        </p>
      )}

      {summary && summary.n_races > 0 && <DaySummary s={summary} />}

      {items.length === 0 && (
        <p className="text-sm text-gray-500">この日の入稿はありません。</p>
      )}

      {/* 「いま前後30分」— 表示時刻を挟んだ幅だけを集めた再掲（2026-08-21・ユーザー要望）。
          🔴 `nowSec === 0` は**まだマウントしていない**印なので描かない。0 のまま描くと
             窓が [-1800, +1800] になり、unix 秒の `start_at` は1件も入らないため
             「0件」の節が一瞬出てから消える（SSR とクライアントで見た目が食い違う）。
          🔴 `start_at === null` は**入れない**。窓に置けないので判定できない。
             「発走前 / 発走済」では時刻不明を**発走前へ寄せた**が、あれは
             「畳んだ側に隠すより開いた側へ出すほうが安全」という理由で、
             ここは逆に**入れると窓の意味が壊れる**（前後30分と言えなくなる）。
             時刻不明の行は下の一覧にそのまま出るので、ここから漏れても失われない。
          ⚠️ 表示は 30 秒ごとの `nowSec` に追従して勝手に入れ替わる。過去日を開いた
             ときは1件も入らず、節ごと出ない（正しい挙動）。 */}
      {nowSec > 0 && (() => {
        const from = nowSec - NOW_WINDOW_SEC;
        const to = nowSec + NOW_WINDOW_SEC;
        const near = byTime.filter(
          (p) => p.start_at !== null && p.start_at >= from && p.start_at <= to,
        );
        if (near.length === 0) return null;
        const open = expanded.now ?? true;   // 既定は開く（いま見るための節なので）
        return (
          <section className="mb-4 rounded border border-blue-200 bg-blue-50/50 p-3
                              dark:border-blue-900 dark:bg-blue-950/30">
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setExpanded((prev) => ({ ...prev, now: !open }))}
              className="flex items-center gap-1 font-semibold hover:text-blue-700
                         dark:hover:text-blue-300"
            >
              {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              いま前後30分
              <span className="ml-1 text-xs font-normal text-gray-500">
                {hhmm(from)}〜{hhmm(to)} ・ {near.length}件
              </span>
            </button>
            {open && <div className="mt-2 space-y-2">{near.map(raceCard)}</div>}
          </section>
        );
      })()}

      {/* 発走時刻順は **発走前 / 発走済** に分けてそれぞれ畳めるようにする
          （2026-08-21・ユーザー要望）。推奨が多い日はスクロールが長く、
          終わったレースが上に積もると「これから承認するもの」を探せない。
          🔴 判定は **実際の発走時刻**（2026-08-21 是正・ユーザー指摘）。
             当初は `isClosed`（発走15分前＝netkeirin の締切）で分けたが、
             それだと**締切を過ぎただけでまだ発走していないレースが「発走済」へ
             入る**。承認ボタンの有効/無効は締切で決まるが、この節の見出しが
             言っているのは「もう走ったか」なので、意味が違う。
             ⚠️ 発走時刻が取れない行は**発走前**へ入れる（畳んだ節に隠れて
                見落とすより、開いている側に出るほうが安全）。 */}
      {view === "time" && (() => {
        const started = (p: KeirinProposal) =>
          p.start_at !== null && p.start_at <= nowSec;
        const upcoming = byTime.filter((p) => !started(p));
        const finished = byTime.filter(started);
        const section = (
          key: "upcoming" | "finished", label: string, list: KeirinProposal[],
        ) => {
          if (list.length === 0) return null;
          // 既定は発走前だけ開く（`expanded` はキー未設定＝閉じる仕様なので明示する）
          const open = expanded[key] ?? (key === "upcoming");
          return (
            <section className="mb-4">
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setExpanded((prev) => ({ ...prev, [key]: !open }))}
                className="mb-2 flex items-center gap-1 font-semibold hover:text-blue-700 dark:hover:text-blue-300"
              >
                {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                {label}
                <span className="ml-1 text-xs font-normal text-gray-500">
                  {list.length}件
                </span>
              </button>
              {open && <div className="space-y-2">{list.map(raceCard)}</div>}
            </section>
          );
        };
        return (
          <div>
            {section("upcoming", "発走前", upcoming)}
            {section("finished", "発走済", finished)}
          </div>
        );
      })()}

      {view === "venue" && byVenue.map(([venue, races]) => {
        // 🔴 締切（発走15分前）を過ぎたレースは数に入れない。入れると
        //    「N件をまとめて入稿」の N が実際に通る件数と食い違い、
        //    押した後に「成功3件/失敗2件」と出て初めて分かることになる。
        const nProp = races.filter(
          (r) => r.status === "proposed" && !isClosed(r.start_at, nowSec)).length;
        // 取消できる＝まだ生きている下書き（未入稿・入稿済）で、かつ締切前。
        // 🔴 **公開済みは含めない**（2026-08-16）。netkeirin の `delete` が効くのは
        //    公開待ちまでで、含めると場単位の取消が必ず一部失敗する。
        const nAlive = races.filter(
          (r) => r.status !== "deleted" && r.status !== "published"
            && !isClosed(r.start_at, nowSec)).length;
        // 公開できる＝**未入稿と公開待ちの両方**で、かつ締切前
        // （レース単位・日単位と同じ規則。ここだけ公開待ちに絞ると
        //  画面の件数と実際に公開される数が食い違う）。
        const nPublishable = races.filter(
          (r) => (r.status === "proposed" || r.status === "submitted")
            && !isClosed(r.start_at, nowSec)).length;
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
                  title={`${venue} の入稿案をまとめて netkeirin へ入稿します（公開はしません）`}
                  onClick={() => run(() => approveKeirinVenueAction(date, venue))}
                  className="rounded bg-blue-700 px-3 py-1 text-xs text-white disabled:opacity-50"
                >
                  入稿 {nProp}
                </button>
              )}
              {/* 🔴 場ごとの公開（2026-08-19・ユーザー要望）。日単位と場単位で
                  取消・入稿は揃っていたのに公開だけレース単位と日単位しか無く、
                  場をまとめて売り出すのに1レースずつ押す必要があった。
                  規則はレース単位・日単位と同じ ——「未入稿は入稿の上で公開」。
                  🔴 公開は不可逆なので**二段確認**（日単位と同じ重さで扱う）。 */}
              {nPublishable > 0 && (
                <button
                  type="button"
                  disabled={pending}
                  title={`${venue} の入稿をまとめて公開します（未入稿は入稿の上で公開・公開後は修正できません）`}
                  onClick={() => {
                    if (!window.confirm(
                      `${venue} の ${nPublishable}件 を公開します。\n`
                      + "（未入稿のものは入稿したうえで公開します）\n\n"
                      + "🔴 公開後は修正できません。よろしいですか？")) return;
                    if (!window.confirm(
                      `最終確認：${venue} の ${nPublishable}件 を公開します。`)) return;
                    run(() => publishKeirinVenueAction(date, venue));
                  }}
                  className="rounded bg-emerald-600 px-3 py-1 text-xs text-white disabled:opacity-50"
                >
                  公開 {nPublishable}
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
                  title={`${venue} の下書きをまとめて取り消します（netkeirin の公開待ち下書きも削除）`}
                  className="rounded border border-red-400 px-3 py-1 text-xs text-red-700 disabled:opacity-50 dark:border-red-600 dark:text-red-300"
                >
                  取消 {nAlive}
                </button>
              )}
            </div>
            {isOpen && <div className="space-y-2">{races.map(raceCard)}</div>}
          </section>
        );
      })}

      {/* ── 安い配当の一括取消ダイアログ（2026-08-24・ユーザー要望）──
          🔴 **既定は全部チェック済み**（＝押した数がそのまま消える）。
             チェックを外したものは取消から外れる。
          🔴 平均払戻は API の `mean_payout`（入稿時点の予測オッズ×実配分）。
             画面では計算しない —— 正本は
             `keirin/src/stake_allocation.py::MIN_MEAN_PAYOUT` と
             `keirin_router._mean_payout`。
          🔴 **リストに載せる条件は平均払戻の1つだけ**（`cheap_mean_payout`）。
             最低払戻・ガミ・落車は**判断材料として並べているだけで選定には
             使っていない**。列が増えたときに「これも条件だ」と読まれないよう、
             見出しにも書いてある。選定条件を増やすなら API 側の印を増やすこと
             （画面で条件を足すと正本が画面へ散る）。 */}
      {cheapDialog !== null && (() => {
        const chosen = cheapTargets.filter((p) => cheapDialog[pickKey(p)]);
        return (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            role="dialog"
            aria-modal="true"
            aria-label="想定払戻の平均が安いレースの取消"
          >
            <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl dark:bg-gray-900">
              <div className="border-b p-4 dark:border-gray-700">
                <h2 className="text-base font-semibold">想定払戻の平均が安いレースを取り消す</h2>
                <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
                  入稿時点の買い目払戻の<strong>平均</strong>が安い＝リスクに見合わない、
                  と判定したレースです。<strong>チェックを外すと取消から除外</strong>できます。
                  <br />
                  ⚠️ 一覧に載せる条件は<strong>平均払戻だけ</strong>です。最低払戻・ガミ・
                  落車は判断材料として並べているだけで、選定には使っていません。
                </p>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-2">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-white text-xs text-gray-500 dark:bg-gray-900 dark:text-gray-400">
                    <tr>
                      <th className="w-10 p-2" />
                      <th className="p-2 text-left">場</th>
                      <th className="p-2 text-right">R</th>
                      <th className="p-2 text-left">ランク</th>
                      <th className="p-2 text-right">平均払戻</th>
                      {/* ⚠️ ここから右は**判断材料**であって選定条件ではない。 */}
                      <th className="p-2 text-right">最低払戻</th>
                      <th className="p-2 text-right">最高払戻</th>
                      <th className="p-2 text-right">落車</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cheapTargets.map((p) => {
                      const k = pickKey(p);
                      return (
                        <tr key={k} className="border-t dark:border-gray-800">
                          <td className="p-2 text-center">
                            <input
                              type="checkbox"
                              aria-label={`${p.venue_name}${p.race_no}R を取り消す`}
                              checked={cheapDialog[k] ?? false}
                              onChange={(e) => setCheapDialog(
                                { ...cheapDialog, [k]: e.target.checked })}
                            />
                          </td>
                          <td className="p-2">{p.venue_name}</td>
                          <td className="p-2 text-right tabular-nums">{p.race_no}R</td>
                          <td className="p-2 text-xs text-gray-500">{p.rank_key}</td>
                          <td className="p-2 text-right tabular-nums">
                            {p.mean_payout === null
                              ? "—"
                              : `${Math.round(p.mean_payout).toLocaleString()}円`}
                          </td>
                          {/* 🔴 最低払戻は**下振れ側を優先**する。`min_payout` は
                              入稿時点の板由来で楽観的（実測 中央 確定/表示 0.860）。
                              カードの表示と同じ規則。ガミ域は赤で出す。 */}
                          <td className="p-2 text-right tabular-nums">
                            <span className={p.gami_risk
                              ? "font-semibold text-red-600 dark:text-red-400" : ""}>
                              {yen(p.min_payout_low ?? p.min_payout)}
                            </span>
                            {p.gami_risk && (
                              <span
                                className="ml-1 text-[10px] text-red-600 dark:text-red-400"
                                title="当たっても投資額を下回りうる（ガミ）"
                              >
                                ガミ
                              </span>
                            )}
                          </td>
                          <td className="p-2 text-right tabular-nums text-gray-500">
                            {yen(p.max_payout)}
                          </td>
                          {/* ⚠️ 落車リスクは**表示だけ**（危険帯のほうが ROI は高い）。
                              取消の理由にはしない —— 判断材料として出すに留める。 */}
                          <td className="p-2 text-right tabular-nums text-gray-500">
                            {p.crash_risk == null
                              ? "—" : `${(p.crash_risk * 100).toFixed(2)}%`}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="flex items-center gap-2 border-t p-3 dark:border-gray-700">
                <button
                  type="button"
                  className="text-xs text-gray-600 underline dark:text-gray-400"
                  onClick={() => setCheapDialog(Object.fromEntries(
                    cheapTargets.map((p) => [pickKey(p), true])))}
                >
                  全部にチェック
                </button>
                <button
                  type="button"
                  className="text-xs text-gray-600 underline dark:text-gray-400"
                  onClick={() => setCheapDialog(Object.fromEntries(
                    cheapTargets.map((p) => [pickKey(p), false])))}
                >
                  全部外す
                </button>
                <span className="ml-auto text-sm">
                  取消 <strong>{chosen.length}</strong> 件
                </span>
                <button
                  type="button"
                  className="rounded border px-3 py-1 text-xs dark:border-gray-600"
                  onClick={() => setCheapDialog(null)}
                >
                  やめる
                </button>
                <button
                  type="button"
                  disabled={pending || chosen.length === 0}
                  className="rounded bg-red-700 px-3 py-1 text-xs text-white disabled:opacity-50"
                  onClick={() => {
                    setCheapDialog(null);
                    run(() => cancelKeirinPicksAction(chosen.map(
                      (p) => ({ raceKey: p.race_key, rankKey: p.rank_key }))));
                  }}
                >
                  {chosen.length}件を取り消す
                </button>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}


/** 左下に浮かせる「戻る」。一定量スクロールしたときだけ出す（2026-08-21 新設）。 */
function FloatingBack() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > 400);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  if (!show) return null;
  return (
    <div className="fixed bottom-4 left-4 z-40 flex flex-col gap-2">
      <Link
        href="/keirin"
        aria-label="一覧へ戻る"
        className="flex items-center gap-1 rounded-full bg-gray-800/90 px-3 py-2 text-sm text-white shadow-lg backdrop-blur hover:bg-gray-700 dark:bg-gray-200/90 dark:text-gray-900 dark:hover:bg-white"
      >
        <ArrowLeft size={16} />
        戻る
      </Link>
      {/* 画面先頭へ。畳んだ節を開き直すときに使う（戻るとは別の用途）。 */}
      <button
        type="button"
        aria-label="先頭へ"
        onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        className="flex items-center justify-center rounded-full bg-gray-800/70 px-3 py-2 text-sm text-white shadow-lg backdrop-blur hover:bg-gray-700 dark:bg-gray-200/70 dark:text-gray-900 dark:hover:bg-white"
      >
        <ChevronUp size={16} />
      </button>
    </div>
  );
}
