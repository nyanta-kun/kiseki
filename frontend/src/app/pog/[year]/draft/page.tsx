import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { auth } from "@/auth";
import { fetchPogGroups } from "@/lib/pog";
import { getDraftBoard } from "../../draftActions";
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
  const session = await auth();
  const userId = Number(session?.user?.id);
  if (!Number.isInteger(userId)) redirect("/login");

  const groups = await fetchPogGroups();
  if (!groups.some((g) => g.year === year)) notFound();

  // 初回の盤面はここで取る（伏せ札の出し分けはサーバが担う）。
  const board = await getDraftBoard(year);
  if (!board.data) {
    return (
      <main className="mx-auto max-w-3xl p-4">
        <p className="text-sm text-rose-600">
          盤面を読めませんでした: {board.error}
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl p-4">
      <h1 className="mb-1 text-lg font-bold">POG ドラフト {year}</h1>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        指名は<strong>伏せた状態</strong>で入り、管理者が巡ごとに一斉公開します。
        同じ馬が重なったらサイコロで決め、負けた人は次の巡で指名し直します。
      </p>

      <DraftClient
        year={year}
        me={userId}
        isAdmin={session?.user?.role === "admin"}
        initialBoard={board.data}
      />

      <p className="mt-6 text-center text-sm">
        <Link
          href={`/pog/${year}`}
          className="text-emerald-600 underline dark:text-emerald-400"
        >
          順位表へ
        </Link>
      </p>
    </main>
  );
}
