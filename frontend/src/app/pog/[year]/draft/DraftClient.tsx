"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Card, CardHeader } from "../../ui";
import {
  computeConfirmTargets,
  computeDisplayOrders,
  toApiTargets,
  type DraftCell,
} from "@/lib/pogDraft";
import {
  deletePick,
  getDraftBoard,
  getDraftRolls,
  savePick,
  saveRoll,
  searchDraftHorses,
  setOrder,
  setSkip,
  setVisible,
  type DraftBoard,
  type DraftPick,
  type DraftRoll,
} from "../../draftActions";

/**
 * POG ドラフトの盤面。
 *
 * 行 = 巡（`draft_order`）／列 = 参加者。セルがその人のその巡の指名。
 *
 * 🔴 **伏せ札の出し分けはサーバがやる。** ここでは受け取ったものを描くだけで、
 * 「自分のだけ見せる」判定をクライアントに置かない（描画を間違えると
 * 公開前の指名が見えてしまう）。
 *
 * ⚠️ 更新は WebSocket の「変わった」通知で**読み直す**。通知には中身が
 * 載っていないので、盤面は必ずサーバから取り直す。
 */
export function DraftClient({
  year,
  me,
  isAdmin,
  initialBoard,
}: {
  year: number;
  me: number;
  isAdmin: boolean;
  /** 🔴 初回はサーバ側で取って渡す。effect の中で取りに行くと
   *  「effect の中で setState」になり再描画が連鎖する（eslint が拾う）。
   *  読み直しは WebSocket の通知や操作の後にだけ走らせる。 */
  initialBoard: DraftBoard;
}) {
  const [board, setBoard] = useState<DraftBoard>(initialBoard);
  const [rolls, setRolls] = useState<Record<number, DraftRoll[]>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 🔴 「今どの巡か」は盤面から**導く**。state に持って effect の中で
  //    書き戻すと再描画が連鎖する（eslint の react-hooks が拾う）。
  //    人が巡を動かしたときだけ override が入る。
  const [roundOverride, setRoundOverride] = useState<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const reload = useCallback(async () => {
    const r = await getDraftBoard(year);
    if (r.error) setMsg(r.error);
    if (r.data) setBoard(r.data);
  }, [year]);

  // 「変わった」通知を受けたら読み直す。
  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_API_URL ?? "";
    if (!base) return;
    const url = base.replace(/^http/, "ws").replace(/\/api\/?$/, "");
    let ws: WebSocket;
    try {
      ws = new WebSocket(`${url}/api/pog/draft/ws?year=${year}`);
    } catch {
      return; // WS が張れなくても盤面は手動で読み直せる
    }
    wsRef.current = ws;
    ws.onmessage = () => void reload();
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 25000);
    return () => {
      clearInterval(ping);
      ws.close();
    };
  }, [year, reload]);

  async function run(fn: () => Promise<{ error?: string }>, ok: string) {
    setBusy(true);
    const r = await fn();
    setMsg(r.error ?? ok);
    await reload();
    setBusy(false);
  }

  const round = roundOverride ?? Math.max(board.max_draft_order || 1, 1);
  const setRound = setRoundOverride;
  // 各巡が「何頭目」の段階か。確定時の枠番はこれを使う（巡の番号ではない）。
  const displayOrders = computeDisplayOrders(
    board.picks as DraftCell[],
    board.members.length,
  );

  const rows = Array.from(
    { length: Math.max(board.max_draft_order, round) },
    (_, i) => i + 1,
  );
  const cell = (u: number, d: number): DraftPick | undefined =>
    board.picks.find((p) => p.user_id === u && p.draft_order === d);

  return (
    <div className="space-y-4">
      {msg && (
        <p
          className="rounded-lg px-3 py-2 text-xs"
          style={{ background: "var(--pog-accent-soft)", color: "var(--pog-accent)" }}
          role="status"
        >
          {msg}
        </p>
      )}

      <Card>
        <CardHeader title="盤面" meta={`${board.members.length} 人 / ${rows.length} 巡`} />
        {/* 参加者ぶん列が並ぶので横スクロールは避けられない。
            ⚠️ 「巡」の列だけ `sticky left-0` で残す。これが無いと横に流したとき
               何巡目の行を見ているのか分からなくなる（移設元もそうだった）。 */}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-xs">
          <thead>
            <tr className="border-b-2 border-neutral-300 dark:border-neutral-600">
              <th
                className="sticky left-0 z-10 px-2 py-2 text-left"
                style={{ background: "var(--pog-card)" }}
              >
                巡
              </th>
              {board.members.map((m) => (
                <th key={m.user_id} className="px-1 py-2 text-left">
                  {m.name}
                  {m.user_id === me && " (自分)"}
                </th>
              ))}
              {isAdmin && <th className="px-1 py-2">操作</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => {
              const inRound = board.picks.filter((p) => p.draft_order === d);
              const anyHidden = inRound.some((p) => !p.visible);
              return (
                <tr key={d} className="border-b border-neutral-200 dark:border-neutral-700">
                  <td
                    className="sticky left-0 z-10 px-2 py-2 align-top font-bold tabular-nums"
                    style={{ background: "var(--pog-card)" }}
                  >
                    {d}
                  </td>
                  {board.members.map((m) => {
                    const p = cell(m.user_id, d);
                    return (
                      <td key={m.user_id} className="px-1 py-2 align-top">
                        {p ? (
                          <div
                            className={
                              p.pick_order === 0
                                ? "text-neutral-400 line-through"
                                : p.pick_order
                                  ? "font-medium"
                                  : ""
                            }
                          >
                            <div className="truncate">
                              {p.netkeiba_horse_id
                                ? (p.horse_name ?? "（未命名）")
                                : "— 不参加"}
                            </div>
                            {p.sire && (
                              <div className="truncate text-[10px] text-neutral-500">
                                父 {p.sire}
                              </div>
                            )}
                            {!p.visible && (
                              <span className="text-[10px] text-amber-600">伏せ</span>
                            )}
                            {isAdmin && p.netkeiba_horse_id && (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => {
                                  // 🔴 誰を更新するかは `lib/pogDraft.ts` が決める。
                                  //    ここで「押した人以外を 0」にすると、
                                  //    別の馬を指名した人まで落としてしまう。
                                  const targets = computeConfirmTargets(
                                    inRound as DraftCell[],
                                    m.user_id,
                                    displayOrders[d] ?? 1,
                                  );
                                  if (targets.length === 0) return;
                                  void run(
                                    () => setOrder(year, d, toApiTargets(targets)),
                                    `${d}巡目を更新しました（${targets.length}人）`,
                                  );
                                }}
                                className="mt-0.5 rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] text-white"
                              >
                                {p.pick_order == null
                                  ? "確定"
                                  : p.pick_order > 0
                                    ? "確定を戻す"
                                    : "この人を勝ちに"}
                              </button>
                            )}
                          </div>
                        ) : (
                          <span className="text-neutral-300">—</span>
                        )}
                      </td>
                    );
                  })}
                  {isAdmin && (
                    <td className="px-1 py-2 align-top">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          run(
                            () => setVisible(year, d, anyHidden),
                            anyHidden ? `${d}巡目を公開しました` : `${d}巡目を伏せました`,
                          )
                        }
                        className="rounded border border-neutral-300 px-1.5 py-0.5 text-[10px]"
                      >
                        {anyHidden ? "公開" : "伏せる"}
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
          </table>
        </div>
      </Card>

      {/* PC は指名フォームとサイコロを横に並べる。ドラフト中は両方を同時に
          使うので、縦積みだとサイコロが画面外に落ちる。 */}
      <div className="grid gap-4 lg:grid-cols-2">
      <PickForm
        year={year}
        me={me}
        isAdmin={isAdmin}
        members={board.members}
        round={round}
        setRound={setRound}
        onDone={(m) => {
          setMsg(m);
          void reload();
        }}
      />

      <DiceBox
        year={year}
        me={me}
        isAdmin={isAdmin}
        round={round}
        rolls={rolls[round] ?? []}
        refresh={async () => {
          const r = await getDraftRolls(year, round);
          if (r.data) setRolls((prev) => ({ ...prev, [round]: r.data! }));
        }}
        onMsg={setMsg}
      />
      </div>

      {isAdmin && (
        <div
          className="rounded-xl border p-3 shadow-sm"
          style={{ background: "var(--pog-card)", borderColor: "var(--pog-card-border)" }}
        >
          <h3 className="mb-1 text-xs font-bold text-surface-heading">
            未入力の人を飛ばす（{round} 巡目）
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {board.members.map((m) => (
              <button
                key={m.user_id}
                type="button"
                disabled={busy}
                onClick={() =>
                  run(() => setSkip(year, round, m.user_id, true), `${m.name} を不参加にしました`)
                }
                className="rounded border border-neutral-300 px-2 py-0.5 text-[11px]"
              >
                {m.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** 指名フォーム。候補を検索して選ぶ。 */
function PickForm({
  year,
  me,
  isAdmin,
  members,
  round,
  setRound,
  onDone,
}: {
  year: number;
  me: number;
  isAdmin: boolean;
  members: { user_id: number; name: string | null }[];
  round: number;
  setRound: (n: number) => void;
  onDone: (msg: string) => void;
}) {
  // POG は「年度の 2 年前に生まれた馬」を指名する（2026年度 → 2024年産）。
  const birthYear = year - 2;
  const [target, setTarget] = useState(me);
  const [name, setName] = useState("");
  const [sire, setSire] = useState("");
  const [items, setItems] = useState<Record<string, string>[]>([]);
  const [busy, setBusy] = useState(false);

  async function search() {
    setBusy(true);
    const r = await searchDraftHorses(birthYear, { name, sire });
    setItems(r.data?.items ?? []);
    if (r.error) onDone(r.error);
    setBusy(false);
  }

  async function pick(id: string, label: string) {
    setBusy(true);
    const r = await savePick(year, target, round, id);
    onDone(r.error ?? `${round} 巡目に ${label} を指名しました（伏せた状態です）`);
    setBusy(false);
  }

  return (
    <div
      className="rounded-xl border p-3 shadow-sm"
      style={{ background: "var(--pog-card)", borderColor: "var(--pog-card-border)" }}
    >
      <h3 className="mb-2 text-xs font-bold text-surface-heading">
        指名する（{birthYear} 年産）
      </h3>
      <div className="mb-2 flex flex-wrap items-end gap-2 text-xs">
        <label className="flex flex-col gap-0.5">
          <span className="text-neutral-500">巡</span>
          <input
            type="number"
            min={1}
            value={round}
            onChange={(e) => setRound(Number(e.target.value))}
            className="w-16 rounded border border-neutral-300 px-2 py-1"
          />
        </label>
        {isAdmin && (
          <label className="flex flex-col gap-0.5">
            <span className="text-neutral-500">誰の指名か（代理入力）</span>
            <select
              value={target}
              onChange={(e) => setTarget(Number(e.target.value))}
              className="rounded border border-neutral-300 px-2 py-1"
            >
              {members.map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-0.5">
          <span className="text-neutral-500">馬名</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-36 rounded border border-neutral-300 px-2 py-1"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-neutral-500">父</span>
          <input
            value={sire}
            onChange={(e) => setSire(e.target.value)}
            className="w-36 rounded border border-neutral-300 px-2 py-1"
          />
        </label>
        <button
          type="button"
          onClick={search}
          disabled={busy}
          className="rounded bg-emerald-600 px-3 py-1.5 text-white disabled:opacity-50"
        >
          検索
        </button>
      </div>

      {items.length > 0 && (
        <ul className="max-h-56 space-y-1 overflow-y-auto text-xs">
          {items.map((h) => (
            <li key={h.netkeiba_horse_id} className="flex items-center gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  pick(h.netkeiba_horse_id, h.name ?? "（未命名）")
                }
                className="rounded border border-emerald-500 px-2 py-0.5 text-emerald-700 dark:text-emerald-400"
              >
                指名
              </button>
              <span className="min-w-0 flex-1 truncate">
                {h.name || "（未命名）"}
                <span className="ml-2 text-neutral-500">
                  父 {h.sire} / 母 {h.broodmare}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        onClick={async () => {
          setBusy(true);
          const r = await deletePick(year, target, round);
          onDone(r.error ?? `${round} 巡目の指名を取り消しました`);
          setBusy(false);
        }}
        disabled={busy}
        className="mt-2 text-[11px] text-neutral-500 underline"
      >
        この巡の指名を取り消す
      </button>
    </div>
  );
}

/**
 * サイコロ。同じ馬を指名した人どうしで振り、合計が大きい方が勝ち。
 *
 * ⚠️ 出目はクライアントで作る（移設元と同じ）。**勝敗を決めるのは人**で、
 * 管理者が盤面のセルを押して確定する。
 */
function DiceBox({
  year,
  me,
  isAdmin,
  round,
  rolls,
  refresh,
  onMsg,
}: {
  year: number;
  me: number;
  isAdmin: boolean;
  round: number;
  rolls: DraftRoll[];
  refresh: () => Promise<void>;
  onMsg: (m: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<[number, number, number] | null>(null);

  useEffect(() => {
    void refresh();
    // round が変わったら読み直す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [round, year]);

  async function roll() {
    setBusy(true);
    const dice: [number, number, number] = [
      1 + Math.floor(Math.random() * 6),
      1 + Math.floor(Math.random() * 6),
      1 + Math.floor(Math.random() * 6),
    ];
    setLast(dice);
    const r = await saveRoll(year, me, round, dice);
    onMsg(
      r.error ??
        `${dice[0]} + ${dice[1]} + ${dice[2]} = ${dice[0] + dice[1] + dice[2]}`,
    );
    await refresh();
    setBusy(false);
  }

  return (
    <div
      className="rounded-xl border p-3 shadow-sm"
      style={{ background: "var(--pog-card)", borderColor: "var(--pog-card-border)" }}
    >
      <h3 className="mb-2 text-xs font-bold text-surface-heading">
        サイコロ（{round} 巡目）
      </h3>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={roll}
          disabled={busy}
          className="rounded bg-amber-600 px-3 py-1.5 text-xs text-white disabled:opacity-50"
        >
          振る
        </button>
        {last && (
          <span className="text-lg tabular-nums">
            🎲 {last[0]} {last[1]} {last[2]}
            <span className="ml-2 font-bold">= {last[0] + last[1] + last[2]}</span>
          </span>
        )}
      </div>
      {rolls.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs">
          {rolls.map((r) => (
            <li key={r.user_id} className="tabular-nums">
              {r.die1} {r.die2} {r.die3} = <b>{r.sum}</b>
              <span className="ml-2 text-neutral-500">user {r.user_id}</span>
            </li>
          ))}
        </ul>
      )}
      {isAdmin && (
        <p className="mt-2 text-[11px] text-neutral-500">
          勝者は上の盤面で「この人が勝ち」を押して確定します。
        </p>
      )}
    </div>
  );
}
