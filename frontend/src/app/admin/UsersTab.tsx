"use client";

import { useState } from "react";
import { updateUser } from "./actions";
import type { User, InvitationCode } from "./AdminTabs";
import { CodesTab } from "./CodesTab";

const PAGE_SIZE = 10;

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface UsersTabProps {
  users: User[];
  codes: InvitationCode[];
}

export function UsersTab({ users, codes }: UsersTabProps) {
  const [page, setPage] = useState(0);

  const totalPages = Math.ceil(users.length / PAGE_SIZE);
  const pageUsers = users.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div className="space-y-8">
      {/* ユーザーテーブル */}
      <div className="bg-white rounded-xl shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
          <h2 className="font-semibold text-gray-800">
            登録ユーザー
            <span className="ml-2 text-sm font-normal text-gray-400">{users.length}件</span>
          </h2>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                <th className="px-4 py-3 text-left whitespace-nowrap">ID</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">メールアドレス</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">名前</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">予想家名</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">公開</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">ロール</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">有効</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">プレミアム</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">期限</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">最終ログイン</th>
                <th className="px-4 py-3 text-left whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {pageUsers.map((user) => (
                <tr key={user.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3 text-gray-400 whitespace-nowrap">{user.id}</td>
                  <td className="px-4 py-3 font-medium text-gray-800 whitespace-nowrap">{user.email}</td>
                  <td className="px-4 py-3 text-gray-600 whitespace-nowrap">{user.name ?? "—"}</td>
                  <td className="px-4 py-3 text-gray-600 whitespace-nowrap">{user.yoso_name ?? "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                        user.is_yoso_public
                          ? "bg-green-100 text-green-700"
                          : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {user.is_yoso_public ? "公開" : "非公開"}
                    </span>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                        user.role === "admin"
                          ? "bg-purple-100 text-purple-700"
                          : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {user.role}
                    </span>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                        user.is_active
                          ? "bg-green-100 text-green-700"
                          : "bg-red-100 text-red-600"
                      }`}
                    >
                      {user.is_active ? "有効" : "無効"}
                    </span>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                        user.is_premium
                          ? "bg-blue-100 text-blue-700"
                          : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {user.is_premium ? "有" : "無"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                    {user.access_expires_at
                      ? formatDate(user.access_expires_at)
                      : user.is_premium
                      ? "無期限"
                      : "—"}
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                    {formatDate(user.last_login_at)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <div className="flex items-center gap-2">
                      {/* ロール切り替え */}
                      <form
                        action={async () => {
                          await updateUser(user.id, {
                            role: user.role === "admin" ? "member" : "admin",
                          });
                        }}
                      >
                        <button
                          type="submit"
                          className="px-2 py-1 rounded text-xs border border-gray-200 hover:border-purple-300 hover:text-purple-700 transition-colors"
                        >
                          {user.role === "admin" ? "→ member" : "→ admin"}
                        </button>
                      </form>

                      {/* 有効/無効切り替え */}
                      <form
                        action={async () => {
                          await updateUser(user.id, { is_active: !user.is_active });
                        }}
                      >
                        <button
                          type="submit"
                          className={`px-2 py-1 rounded text-xs border transition-colors ${
                            user.is_active
                              ? "border-gray-200 hover:border-red-300 hover:text-red-600"
                              : "border-gray-200 hover:border-green-300 hover:text-green-600"
                          }`}
                        >
                          {user.is_active ? "無効化" : "有効化"}
                        </button>
                      </form>
                    </div>
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={11} className="px-4 py-8 text-center text-gray-400 text-sm">
                    ユーザーが存在しません
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ページングコントロール */}
        {totalPages > 1 && (
          <div className="px-6 py-3 border-t border-gray-100 flex items-center justify-between text-sm">
            <span className="text-gray-500 text-xs">
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, users.length)} / {users.length}件
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="px-3 py-1 rounded border border-gray-200 text-xs hover:border-gray-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                前へ
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="px-3 py-1 rounded border border-gray-200 text-xs hover:border-gray-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                次へ
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 招待コード管理 */}
      <CodesTab codes={codes} />
    </div>
  );
}
