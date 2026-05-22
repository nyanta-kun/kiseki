import { fetchAnagusaRules, AnagusaRuleItem } from "@/lib/api";
import { AnagusaRulePanel } from "./AnagusaRulePanel";

type Props = {
  date: string;
};

export async function AnagusaRuleView({ date }: Props) {
  let items: AnagusaRuleItem[] = [];
  try {
    items = await fetchAnagusaRules(date);
  } catch {
    // SSR失敗時は空配列でクライアントに渡す
  }
  return <AnagusaRulePanel initialItems={items} date={date} />;
}
