"use client";

import { useState } from "react";

import {
  createPogGroup,
  deletePogGroup,
  getPogGroup,
  replacePogMembers,
  type PogGroupDetail,
} from "./pogActions";
import type { User } from "./AdminTabs";

/**
 * POG グループ管理（sekito `PogManagementPage` の移設先）。
 *
 * 年に一度しか使わない画面なので、作り込みより**取り違えないこと**を優先する。
 * 削除は年度を打ち直させる（指名が道連れで消えるため）。
 */
export function PogTab({ users }: { users: User[] }) {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [group, setGroup] = useState<PogGroupDetail | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [newName, setNewName] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());

  async function load(y: number) {
    setBusy(true);
    setMsg(null);
    const r = await getPogGroup(y);
    setGroup(r.data ?? null);
    setPicked(new Set((r.data?.members ?? []).map((m) => m.user_id)));
    if (r.error) setMsg(r.error);
    setBusy(false);
  }

  async function onCreate() {
    setBusy(true);
    const r = await createPogGroup(
      year,
      newName.trim() || null,
      [...picked].map((id) => ({ user_id: id, nickname: null })),
    );
    setMsg(r.error ?? `${year} 年度を作成しました`);
    if (r.data) setGroup(r.data);
    setBusy(false);
  }

  async function onSaveMembers() {
    setBusy(true);
    const r = await replacePogMembers(
      year,
      [...picked].map((id) => ({
        user_id: id,
        nickname: group?.members.find((m) => m.user_id === id)?.nickname ?? null,
      })),
    );
    setMsg(r.error ?? "メンバーを更新しました（指名は消していません）");
    if (r.data) setGroup(r.data);
    setBusy(false);
  }

  async function onDelete() {
    if (confirmText !== String(year)) {
      setMsg(`確認欄に ${year} と入力してください`);
      return;
    }
    setBusy(true);
    const r = await deletePogGroup(year, Number(confirmText));
    setMsg(
      r.error ??
        `${year} 年度を削除しました（指名 ${r.data?.deleted_picks} 件・メンバー ${r.data?.deleted_members} 人）`,
    );
    if (!r.error) {
      setGroup(null);
      setConfirmText("");
    }
    setBusy(false);
  }

  const toggle = (id: number) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="space-y-6 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-neutral-500">年度</span>
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="w-28 rounded border border-neutral-300 px-2 py-1"
          />
        </label>
        <button
          type="button"
          onClick={() => load(year)}
          disabled={busy}
          className="rounded bg-emerald-600 px-3 py-1.5 text-white disabled:opacity-50"
        >
          読み込む
        </button>
      </div>

      {msg && (
        <p className="rounded bg-neutral-100 px-3 py-2 text-xs">{msg}</p>
      )}

      {group ? (
        <>
          <div>
            <h3 className="mb-1 font-bold">
              {group.year} 年度 {group.name ?? ""}
              <span className="ml-2 text-xs font-normal text-neutral-500">
                指名 {group.pick_count} 件
              </span>
            </h3>
            <ul className="text-xs text-neutral-600">
              {group.members.map((m) => (
                <li key={m.user_id}>
                  {m.nickname ?? m.name ?? m.email}（指名 {m.pick_count} 件）
                </li>
              ))}
            </ul>
          </div>

          <details className="rounded border border-rose-300 p-3">
            <summary className="cursor-pointer font-bold text-rose-700">
              この年度を削除する
            </summary>
            <p className="mt-2 text-xs text-rose-700">
              🔴 {group.year} 年度の**指名 {group.pick_count} 件が一緒に消えます**。
              戻せません。よければ確認欄に <code>{group.year}</code> と入力してください。
            </p>
            <div className="mt-2 flex gap-2">
              <input
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={String(group.year)}
                className="w-28 rounded border border-neutral-300 px-2 py-1"
              />
              <button
                type="button"
                onClick={onDelete}
                disabled={busy || confirmText !== String(group.year)}
                className="rounded bg-rose-600 px-3 py-1.5 text-white disabled:opacity-40"
              >
                削除する
              </button>
            </div>
          </details>
        </>
      ) : (
        <div>
          <h3 className="mb-1 font-bold">{year} 年度を作る</h3>
          <label className="mb-2 flex flex-col gap-1">
            <span className="text-xs text-neutral-500">グループ名（任意）</span>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="赤兎"
              className="w-48 rounded border border-neutral-300 px-2 py-1"
            />
          </label>
        </div>
      )}

      <div>
        <h3 className="mb-1 font-bold">メンバー</h3>
        <div className="mb-2 grid max-h-64 grid-cols-2 gap-x-4 gap-y-1 overflow-y-auto text-xs md:grid-cols-3">
          {users.map((u) => (
            <label key={u.id} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={picked.has(u.id)}
                onChange={() => toggle(u.id)}
              />
              <span className="truncate">{u.name ?? u.email}</span>
            </label>
          ))}
        </div>
        <button
          type="button"
          onClick={group ? onSaveMembers : onCreate}
          disabled={busy}
          className="rounded bg-emerald-600 px-3 py-1.5 text-white disabled:opacity-50"
        >
          {group ? "メンバーを保存" : "この内容で作成"}
        </button>
        {group && (
          <p className="mt-1 text-xs text-neutral-500">
            外したメンバーの指名は残します（過去の記録として要るため）。
          </p>
        )}
      </div>
    </div>
  );
}
