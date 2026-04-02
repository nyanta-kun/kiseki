import Link from "next/link";
import { auth } from "@/auth";
import { redirect } from "next/navigation";
import { updateUser } from "./actions";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

type User = {
  id: number;
  email: string;
  name: string | null;
  image_url: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
};

async function fetchUsers(): Promise<User[]> {
  const res = await fetch(`${BACKEND_URL}/admin/users`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });
  if (!res.ok) return [];
  return res.json() as Promise<User[]>;
}

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

export default async function AdminPage() {
  const session = await auth();
  if (session?.user?.role !== "admin") redirect("/races");

  const users = await fetchUsers();

  return (
    <div className="min-h-screen bg-[#f0f5fb]">
      {/* ヘッダー */}
      <header className="bg-[#0d1f35] text-white px-6 py-4 flex items-center gap-4">
        <Link href="/races" className="text-white/60 hover:text-white text-sm transition-colors">
          ← レース一覧
        </Link>
        <h1 className="text-lg font-bold">管理画面 — ユーザー管理</h1>
        <span className="ml-auto text-xs text-white/50">{session.user?.email}</span>
      </header>

      <main className="p-6 max-w-5xl mx-auto">
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
                  <th className="px-4 py-3 text-left">ID</th>
                  <th className="px-4 py-3 text-left">メールアドレス</th>
                  <th className="px-4 py-3 text-left">名前</th>
                  <th className="px-4 py-3 text-left">ロール</th>
                  <th className="px-4 py-3 text-left">有効</th>
                  <th className="px-4 py-3 text-left">最終ログイン</th>
                  <th className="px-4 py-3 text-left">登録日</th>
                  <th className="px-4 py-3 text-left">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {users.map((user) => (
                  <tr key={user.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3 text-gray-400">{user.id}</td>
                    <td className="px-4 py-3 font-medium text-gray-800">{user.email}</td>
                    <td className="px-4 py-3 text-gray-600">{user.name ?? "—"}</td>
                    <td className="px-4 py-3">
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
                    <td className="px-4 py-3">
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
                    <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                      {formatDate(user.last_login_at)}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                      {formatDate(user.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {/* ロール切り替え */}
                        <form
                          action={async () => {
                            "use server";
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
                            "use server";
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
                    <td colSpan={8} className="px-4 py-8 text-center text-gray-400 text-sm">
                      ユーザーが存在しません
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
