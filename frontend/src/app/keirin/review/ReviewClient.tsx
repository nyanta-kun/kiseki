"use client";

/**
 * 入稿案の確認・承認 UI（2026-08-11 新設）。
 *
 * 場でまとめ、レースごとに「入稿内容・期待値・最低/最高払戻・選手ごとの各入着率」を
 * 出す。操作はレース単位の入稿／取消と、場単位のまとめ入稿。
 */
import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import type { KeirinProposal, KeirinProposalEntry } from "@/lib/api";

import {
  approveKeirinRaceAction,
  approveKeirinVenueAction,
  cancelKeirinSubmissionAction,
  setKeirinApprovalModeAction,
} from "../actions";

const MARK_LABEL: Record<number, string> = { 1: "◎", 2: "○", 3: "▲", 4: "△", 5: "☆" };

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

function EntryTable({ entries, axis1, axis2 }: {
  entries: KeirinProposalEntry[];
  axis1: number | null;
  axis2: number | null;
}) {
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
            <th className="py-1 pr-2 text-right">3着内率</th>
            <th className="py-1 pr-2">ライン</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => {
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
                  {e.pred_win_pct?.toFixed(1) ?? "—"}%
                </td>
                <td className="py-0.5 pr-2 text-right tabular-nums">
                  {e.pred_top3_pct?.toFixed(1) ?? "—"}%
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

function RaceCard({ p, busy, onApprove, onCancel }: {
  p: KeirinProposal;
  busy: boolean;
  onApprove: () => void;
  onCancel: () => void;
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
        {p.is_marquee && (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-900 dark:text-amber-200">
            看板
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
        <span className="ml-auto flex gap-2">
          {p.status === "proposed" && (
            <button
              type="button"
              disabled={busy}
              onClick={onApprove}
              className="rounded bg-blue-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            >
              このレースを入稿
            </button>
          )}
          {p.status !== "deleted" && (
            <button
              type="button"
              disabled={busy}
              onClick={onCancel}
              className="rounded border border-red-300 px-3 py-1 text-xs text-red-700 disabled:opacity-50 dark:border-red-700 dark:text-red-300"
            >
              取消
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
          <span className="text-gray-500">期待値</span>{" "}
          {p.expected_value === null ? "—" : p.expected_value.toFixed(2)}
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
            <p className="whitespace-pre-wrap text-xs text-gray-700 dark:text-gray-300">
              {p.comment || "（未設定）"}
            </p>
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
                        {l.odds === null ? "オッズ未取得" : `${l.odds.toFixed(1)}倍`}
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

export default function ReviewClient({ date, items, nProposed, requireApproval }: {
  date: string;
  items: KeirinProposal[];
  nProposed: number;
  requireApproval: boolean;
}) {
  const [pending, startTransition] = useTransition();
  const [msg, setMsg] = useState<string | null>(null);
  const [mode, setMode] = useState(requireApproval);

  const byVenue = useMemo(() => {
    const m = new Map<string, KeirinProposal[]>();
    for (const p of items) {
      const list = m.get(p.venue_name) ?? [];
      list.push(p);
      m.set(p.venue_name, list);
    }
    return [...m.entries()];
  }, [items]);

  const run = (fn: () => Promise<{ ok: boolean; message: string; n_ok?: number; n_ng?: number }>) => {
    setMsg(null);
    startTransition(async () => {
      const r = await fn();
      setMsg(
        r.n_ok === undefined
          ? r.message
          : `成功${r.n_ok}件 / 失敗${r.n_ng ?? 0}件: ${r.message}`,
      );
      // 状態が変わるのでサーバーから取り直す
      if (r.ok) window.location.reload();
    });
  };

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
        <span className="text-sm text-gray-500">{date}</span>
        <span className="text-sm">
          未入稿 <span className="font-semibold">{nProposed}</span> 件
        </span>
        <label className="ml-auto flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={mode}
            disabled={pending}
            onChange={(e) => {
              const next = e.target.checked;
              setMode(next);
              run(() => setKeirinApprovalModeAction(next));
            }}
          />
          承認制にする
        </label>
      </div>

      <p className="mb-3 rounded bg-gray-50 p-2 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">
        承認制が OFF のときは、朝のバッチが従来どおり netkeirin へ下書きを自動作成します。
        ON にすると、承認するまで netkeirin へは何も出ません。
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

      {byVenue.length === 0 && (
        <p className="text-sm text-gray-500">この日の入稿はありません。</p>
      )}

      {byVenue.map(([venue, races]) => {
        const nProp = races.filter((r) => r.status === "proposed").length;
        return (
          <section key={venue} className="mb-6">
            <div className="mb-2 flex items-center gap-3">
              <h2 className="font-semibold">{venue}</h2>
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
            </div>
            <div className="space-y-2">
              {races.map((p) => (
                <RaceCard
                  key={`${p.race_key}-${p.rank_key}`}
                  p={p}
                  busy={pending}
                  onApprove={() => run(() => approveKeirinRaceAction(p.race_key, p.rank_key))}
                  onCancel={() => {
                    if (!window.confirm(`${p.venue_name}${p.race_no}R (${p.rank_key}) の入稿を取り消します。よろしいですか？`)) return;
                    run(() => cancelKeirinSubmissionAction(p.race_key, p.rank_key));
                  }}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
