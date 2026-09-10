import { requireMenu } from "@/lib/menu";

/** 中央の実績。中央競馬の可視性に従う（`menuOfPath` と同じ束ね方）。 */
export default async function ResultsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireMenu("jra");
  return <>{children}</>;
}
