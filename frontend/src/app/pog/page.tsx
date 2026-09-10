import { redirect } from "next/navigation";

import { fetchPogGroups } from "@/lib/pog";

/** `/pog` は最新年度へ送る。年度の一覧は各ページのセレクタが持つ。 */
export default async function PogIndexPage() {
  const groups = await fetchPogGroups();
  if (groups.length === 0) {
    // 年度が 1 つも無いと `PogShell`（年度タブ・ページタブ）が組めないので、
    // ここだけは素の面に文言を出す。
    return (
      <main className="min-h-screen p-6" style={{ background: "var(--page-bg-pog)" }}>
        <p className="mx-auto max-w-6xl text-sm text-surface-muted">
          POG のグループがまだありません。
        </p>
      </main>
    );
  }
  redirect(`/pog/${groups[0].year}`);
}
