"use client";

import { createInvitationCode, toggleInvitationCode } from "./actions";
import type { InvitationCode } from "./AdminTabs";

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

function grantTypeLabel(type: string): string {
  return type === "unlimited" ? "無期限" : type === "weeks" ? "週数" : "特定日";
}

interface CodesTabProps {
  codes: InvitationCode[];
}

export function CodesTab({ codes }: CodesTabProps) {
  return (
    <div className="bg-white rounded-xl shadow-sm overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100">
        <h2 className="font-semibold text-gray-800">
          招待コード
          <span className="ml-2 text-sm font-normal text-gray-400">{codes.length}件</span>
        </h2>
      </div>

      {/* 新規作成フォーム */}
      <div className="px-6 py-4 bg-gray-50 border-b border-gray-100">
        <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-3">新規作成</h3>
        <form action={async (fd: FormData) => { await createInvitationCode(fd); }} className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">付与種別</label>
            <select
              name="grant_type"
              required
              className="px-3 py-1.5 rounded border border-gray-200 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              <option value="unlimited">無期限</option>
              <option value="weeks">週数</option>
              <option value="date">特定日</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">週数（weeks時）</label>
            <input
              name="weeks_count"
              type="number"
              min={1}
              max={52}
              placeholder="4"
              className="w-24 px-3 py-1.5 rounded border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">特定日（date時）</label>
            <input
              name="target_date"
              type="date"
              className="px-3 py-1.5 rounded border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">最大使用回数</label>
            <input
              name="max_uses"
              type="number"
              min={1}
              defaultValue={1}
              className="w-20 px-3 py-1.5 rounded border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>
          <div className="flex flex-col gap-1 flex-1 min-w-40">
            <label className="text-xs text-gray-500">メモ（任意）</label>
            <input
              name="note"
              type="text"
              placeholder="例: note記事用"
              className="px-3 py-1.5 rounded border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
            />
          </div>
          <button
            type="submit"
            className="px-4 py-1.5 rounded bg-[#0d1f35] text-white text-sm font-medium hover:bg-[#1a3a5c] transition-colors"
          >
            作成
          </button>
        </form>
      </div>

      {/* コード一覧 */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
              <th className="px-4 py-3 text-left whitespace-nowrap">コード</th>
              <th className="px-4 py-3 text-left whitespace-nowrap">種別</th>
              <th className="px-4 py-3 text-left whitespace-nowrap">詳細</th>
              <th className="px-4 py-3 text-left whitespace-nowrap">使用</th>
              <th className="px-4 py-3 text-left whitespace-nowrap">状態</th>
              <th className="px-4 py-3 text-left whitespace-nowrap">メモ</th>
              <th className="px-4 py-3 text-left whitespace-nowrap">作成日</th>
              <th className="px-4 py-3 text-left whitespace-nowrap">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {codes.map((code) => (
              <tr
                key={code.id}
                className={`hover:bg-gray-50 transition-colors ${!code.is_active ? "opacity-50" : ""}`}
              >
                <td className="px-4 py-3 font-mono text-xs font-semibold text-gray-800 tracking-wider whitespace-nowrap">
                  {code.code}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
                    {grantTypeLabel(code.grant_type)}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                  {code.grant_type === "weeks" && code.weeks_count
                    ? `${code.weeks_count}週`
                    : code.grant_type === "date" && code.target_date
                    ? code.target_date
                    : "—"}
                </td>
                <td className="px-4 py-3 text-xs text-gray-600 whitespace-nowrap">
                  {code.use_count} / {code.max_uses}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                      code.is_active
                        ? "bg-green-100 text-green-700"
                        : "bg-gray-100 text-gray-500"
                    }`}
                  >
                    {code.is_active ? "有効" : "無効"}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">{code.note ?? "—"}</td>
                <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                  {formatDate(code.created_at)}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <form action={async () => { await toggleInvitationCode(code.id, !code.is_active); }}>
                    <button
                      type="submit"
                      className={`px-2 py-1 rounded text-xs border transition-colors ${
                        code.is_active
                          ? "border-gray-200 hover:border-red-300 hover:text-red-600"
                          : "border-gray-200 hover:border-green-300 hover:text-green-600"
                      }`}
                    >
                      {code.is_active ? "無効化" : "有効化"}
                    </button>
                  </form>
                </td>
              </tr>
            ))}
            {codes.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-400 text-sm">
                  招待コードがありません
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
