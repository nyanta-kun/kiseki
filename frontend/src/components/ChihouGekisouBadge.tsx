/**
 * 地方「激走 / 見送り」バッジ（一覧・レース詳細・推奨タブで共通）。
 *
 * 判定の正本は backend `indices/chihou_gekisou.py`。フロントは API の値を描くだけで、
 * 条件を再実装しないこと。
 *
 * 🔴 激走は「当たりやすさ」の印で、期待値の印ではない（前向き確認で複勝的中 23.8%・
 *    人気薄全体の約2倍、回収率 0.79）。文言で収支を謳わないこと。
 */
import type { ChihouGekisouStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

export const GEKISOU_DESC =
  "人気馬が複勝圏を埋めきれず、人気薄が割り込めそうなレースの最有力の人気薄（6番人気以下・1レース1頭）";
export const MIOKURI_DESC = "人気馬が複勝圏を埋めそうで、人気薄の割り込む余地が小さいレース";

type Props = {
  status: ChihouGekisouStatus | undefined;
  /** true = 最新オッズでの暫定（発走前の記録がまだ無い） */
  provisional?: boolean;
  size?: "xs" | "sm";
};

export function ChihouGekisouBadge({ status, provisional = false, size = "xs" }: Props) {
  if (status !== "gekisou" && status !== "miokuri") return null;
  const isGekisou = status === "gekisou";
  const label = isGekisou ? "激走" : "見送り";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border font-bold whitespace-nowrap leading-none",
        size === "xs" ? "text-[10px] px-1.5 py-0.5" : "text-xs px-2 py-1",
        isGekisou
          ? "bg-rose-50 text-rose-700 border-rose-300"
          : "bg-gray-50 text-gray-500 border-gray-300",
        provisional && "border-dashed",
      )}
      title={`${isGekisou ? GEKISOU_DESC : MIOKURI_DESC}${provisional ? "（発走前の最新オッズでの暫定）" : ""}`}
    >
      {label}
      {provisional && <span className="ml-0.5 font-normal opacity-70">候補</span>}
    </span>
  );
}
