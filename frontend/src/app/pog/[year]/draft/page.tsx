import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { auth } from "@/auth";
import { fetchPogGroups } from "@/lib/pog";
import { getDraftBoard } from "../../draftActions";
import { PogShell } from "../../PogShell";
import { EmptyState } from "../../ui";
import { DraftClient } from "./DraftClient";

export const metadata: Metadata = {
  title: "POG ドラフト | GallopLab",
};

export default async function PogDraftPage({
  params,
}: {
  params: Promise<{ year: string }>;
}) {
  const { year: raw } = await params;
  const year = Number(raw);
  if (!Number.isInteger(year)) notFound();

  // 🔴 ドラフトは書き込み画面。ログインしていないと誰の指名か決まらない。
  //    DB のユーザー ID は `db_id`。`user.id` は Auth.js 自身の ID で別物。
  const session = await auth();
  const userId = session?.user?.db_id;
  if (typeof userId !== "number") redirect("/login");

  const groups = await fetchPogGroups();
  if (!groups.some((g) => g.year === year)) notFound();

  // 初回の盤面はここで取る（伏せ札の出し分けはサーバが担う）。
  const board = await getDraftBoard(year);

  return (
    <PogShell
      title={`POG ドラフト ${year}`}
      description="指名は伏せた状態で入り、管理者が巡ごとに一斉公開します。同じ馬が重なったらサイコロで決め、負けた人は次の巡で指名し直します。"
      year={year}
      groups={groups}
      yearBasePath="/pog/:year/draft"
    >
      {board.data ? (
        <DraftClient
          year={year}
          me={userId}
          isAdmin={session?.user?.role === "admin"}
          initialBoard={board.data}
        />
      ) : (
        <EmptyState>盤面を読めませんでした: {board.error}</EmptyState>
      )}
    </PogShell>
  );
}
