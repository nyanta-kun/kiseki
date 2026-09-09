import { redirect } from "next/navigation";

import { fetchPogGroups } from "@/lib/pog";

/** `/pog` は最新年度へ送る。年度の一覧は各ページのセレクタが持つ。 */
export default async function PogIndexPage() {
  const groups = await fetchPogGroups();
  if (groups.length === 0) {
    return (
      <main className="p-4">
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          POG のグループがまだありません。
        </p>
      </main>
    );
  }
  redirect(`/pog/${groups[0].year}`);
}
