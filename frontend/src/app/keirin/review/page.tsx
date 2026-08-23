/**
 * netkeirin 入稿の確認・承認画面（2026-08-11 新設）。
 *
 * 承認制（`netkeirin_settings._global.require_approval`）が ON のとき、朝の入稿
 * バッチは netkeirin へ出さず「入稿案」だけを作る。この画面でオッズ・推奨買い目・
 * コメントを確認し、レース単位／場単位で入稿する。
 *
 * 🔴 承認しても **買い目は再計算されない**。keirin 側が入稿案の時点で保存した
 *    買い目をそのまま送る。画面で見たものと違うものが入稿されては確認にならない。
 *
 * サーバーコンポーネントでデータを取り、操作は client 側（ReviewClient）へ渡す
 * （`feedback_nextjs_client_server` の境界に従う）。
 */
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { fetchKeirinProposals } from "@/lib/api";

import ReviewClient from "./ReviewClient";

export const dynamic = "force-dynamic";

/** JST の今日（`feedback_jst_timezone`: 日付生成は必ず timeZone を明示する）。 */
function todayJst(): string {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" });
}

export default async function KeirinReviewPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const session = await auth();
  if (session?.user?.role !== "admin") redirect("/keirin");

  const { date } = await searchParams;
  const target = /^\d{4}-\d{2}-\d{2}$/.test(date ?? "") ? (date as string) : todayJst();

  // 承認制の ON/OFF は 2026-08-12 に `/admin` の設定タブへ移した（この画面は
  // レースを見て承認する作業に専念する）。ここで取得・表示する必要はない。
  const proposals = await fetchKeirinProposals(target).catch(() => ({
    date: target, n_proposed: 0, n_unpublished: 0, summary: undefined,
    summary_cancelled: undefined, items: [],
  }));

  return (
    <ReviewClient
      date={target}
      items={proposals.items}
      nProposed={proposals.n_proposed}
      nUnpublished={proposals.n_unpublished ?? 0}
      summary={proposals.summary}
      summaryCancelled={proposals.summary_cancelled}
    />
  );
}
