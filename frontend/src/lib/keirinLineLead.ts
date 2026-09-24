/**
 * 逃げ先頭ライン（`L_lead`・2026-09-24〜・**検証中・入稿しない**）の検証 API クライアント。
 *
 * バックエンドは `backend/src/api/keirin_type_lab_router.py` の `GET /keirin/type-lab/line-lead`。
 * 集計の正本は `backend/src/services/keirin_line_lead_verify.py`（純関数）。
 *
 * 🔴 `@/lib/api` には置かない。あちらは柱が shared（横断）で、競輪の検証ページのために
 *    触ると並列開発の規約上「共通部分の変更」になる（`lib/pog.ts` と同じ判断）。
 */

const _rawBase =
  typeof window === "undefined"
    ? (process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");
const BASE_URL = _rawBase.replace(/\/api\/?$/, "").replace(/\/$/, "") + "/api";

export type LineLeadTally = {
  n: number;
  pending: number;
  invest: number;
  payout: number;
  hits: number;
  shown_hits: number;
  max_payout: number;
  roi: number | null;
};

export type LineLeadSummary = {
  lead: LineLeadTally;
  displaced: LineLeadTally;
  current: LineLeadTally;
  combined: LineLeadTally;
  lead_roi_wo_top3: number | null;
  lead_days_over_100: number;
  n_days: number;
  /** 実際に netkeirin へ出した分（型ラボが売らないレースへ1日5本・2026-09-24〜） */
  lead_sold?: LineLeadTally | null;
};

export type LineLeadDay = {
  date: string;
  lead: LineLeadTally;
  displaced: LineLeadTally;
  current: LineLeadTally;
  combined: LineLeadTally;
  diff: number;
};

export type LineLeadSold = {
  rank_key: string;
  bet: number;
  payout: number;
  settled: boolean;
  /** 買い目の表示（`"3連単 1-2-3 ×1,200円"`） */
  lines: string[];
  /** 入稿タイトル（買い手に見える商品名） */
  title: string | null;
};

export type LineLeadLeg = {
  combo: string;
  stake: number;
  pred_odds: number | null;
  /** この目で当たった */
  won: boolean;
};

export type LineLeadRace = {
  race_key: string;
  race_date: string;
  venue_name: string | null;
  race_no: number | null;
  race_type: string | null;
  start_at: number | null;
  combos: string[];
  stake: number;
  legs: LineLeadLeg[];
  invest: number;
  settled: boolean;
  hit: boolean;
  payout: number;
  win_combo: string | null;
  /** 決着した目の三連単確定オッズ（倍率・買っていなくても入る） */
  win_tf_odds: number | null;
  /** このレースで実際に netkeirin へ出したか（穴狙い） */
  submitted?: boolean;
  sold: LineLeadSold[];
};

export type LineLeadResponse = {
  date_from: string;
  date_to: string;
  summary: LineLeadSummary;
  days: LineLeadDay[];
  races: LineLeadRace[];
};

export async function fetchLineLead(params: { dateFrom?: string; dateTo?: string } = {},
                                    ): Promise<LineLeadResponse> {
  const q = new URLSearchParams();
  if (params.dateFrom) q.set("date_from", params.dateFrom);
  if (params.dateTo) q.set("date_to", params.dateTo);
  const s = q.toString();
  const res = await fetch(`${BASE_URL}/keirin/type-lab/line-lead${s ? `?${s}` : ""}`,
                          { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status} /keirin/type-lab/line-lead`);
  return res.json() as Promise<LineLeadResponse>;
}

/** 収支（払戻 − 投資）。 */
export const profit = (t: Pick<LineLeadTally, "invest" | "payout">): number => t.payout - t.invest;

/** 回収率の色。100% 以上＝緑・80% 以上＝灰・それ未満＝赤。未確定は薄灰。 */
export function roiTone(roi: number | null | undefined): string {
  if (roi == null) return "text-gray-400 dark:text-gray-500";
  if (roi >= 100) return "text-emerald-700 dark:text-emerald-300 font-semibold";
  if (roi >= 80) return "text-gray-800 dark:text-gray-200";
  return "text-rose-700 dark:text-rose-300";
}

/** UNIX 秒 → JST の "HH:MM"。取れなければ null。 */
export function hhmmJst(startAt: number | null | undefined): string | null {
  if (startAt == null || !Number.isFinite(startAt) || startAt <= 0) return null;
  const d = new Date((startAt + 9 * 3600) * 1000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

/** ISO 日付（YYYY-MM-DD）を n 日ずらす（端末のローカル日付基準）。 */
export function shiftDay(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 想定払戻（賭け金 × 予測オッズ）。予測オッズが無ければ null。 */
export function expectedPayout(leg: Pick<LineLeadLeg, "stake" | "pred_odds">): number | null {
  return leg.pred_odds == null || leg.pred_odds <= 0 ? null : Math.round(leg.stake * leg.pred_odds);
}
