import Link from "next/link";
import { PlacePickMonth, PlacePickRow, fetchPlacePicks } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = { date: string };

/**
 * 推奨タブの中身 = 当月の複勝ピック一覧（結果つき）。
 *
 * 2026-09-26 に平八バッジの一覧から置き換えた。入力は前向き記録（発走約10分前の
 * スナップショット）で、判定は backend の `services/jra_place_pick.py`。
 * レース詳細の「複勝」バッジと同じ関数を通るので、一覧とバッジはずれない。
 */
export async function PlacePicksView({ date }: Props) {
  const month = date.slice(0, 6);
  let data: PlacePickMonth | null = null;
  try {
    data = await fetchPlacePicks(month);
  } catch {
    // 取得失敗時は下の案内を出す
  }

  const label = `${Number(month.slice(0, 4))}年${Number(month.slice(4, 6))}月`;
  if (!data) {
    return (
      <div className="text-center py-12 text-gray-400 text-sm">
        複勝ピックを取得できませんでした
      </div>
    );
  }
  const s = data.summary;

  return (
    <div className="space-y-3">
      <section className="bg-white rounded-xl border border-gray-100 p-3 shadow-sm">
        <h2 className="text-sm font-bold text-gray-800">{label}の複勝ピック</h2>
        <p className="text-[11px] text-gray-500 mt-0.5 leading-relaxed">
          1レース最大1頭・該当なしは見送り。条件: 8頭以上 ∧ 複勝3.0〜4.0倍未満 ∧
          複勝確率5位以内 ∧ 単勝÷複勝≤3.5（発走約10分前のオッズで確定）
        </p>
        <dl className="grid grid-cols-4 gap-2 mt-2 text-center">
          <Stat label="対象" value={`${s.n_picks}点`} />
          <Stat
            label="的中"
            value={`${s.n_hits}/${s.n_settled}`}
            sub={s.hit_rate != null ? `${(s.hit_rate * 100).toFixed(1)}%` : "—"}
          />
          <Stat
            label="払戻/投資"
            value={`${s.payout.toLocaleString()}円`}
            sub={`${s.invest.toLocaleString()}円`}
          />
          <Stat
            label="回収率"
            value={s.roi != null ? `${(s.roi * 100).toFixed(0)}%` : "—"}
            emphasize={s.roi != null && s.roi >= 1}
          />
        </dl>
        <p className="text-[10px] text-gray-400 mt-2 leading-relaxed">
          探索時（2026-03〜09・8頭以上）は118点・的中43.2%・回収率133%。7月以降も条件選びに
          使ったため、採否は10月以降の成績で判断する（回収率の保証ではない）。
        </p>
      </section>

      {data.picks.length === 0 ? (
        <div className="text-center py-8 text-gray-400 text-sm">
          今月の対象はまだありません
        </div>
      ) : (
        <ul className="bg-white rounded-xl border border-gray-100 shadow-sm divide-y divide-gray-100">
          {data.picks.map((p) => (
            <PickItem key={p.race_id} p={p} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value, sub, emphasize = false }: {
  label: string; value: string; sub?: string; emphasize?: boolean;
}) {
  return (
    <div className="rounded-lg bg-gray-50 py-1.5 min-w-0">
      <dt className="text-[10px] text-gray-500">{label}</dt>
      <dd className={cn("text-sm font-bold tabular-nums", emphasize ? "text-emerald-700" : "text-gray-800")}>
        {value}
      </dd>
      {sub && <dd className="text-[10px] text-gray-500 tabular-nums">{sub}</dd>}
    </div>
  );
}

function StatusBadge({ p }: { p: PlacePickRow }) {
  const base = "text-[11px] px-1.5 py-0.5 rounded font-bold whitespace-nowrap tabular-nums";
  switch (p.status) {
    case "hit":
      return (
        <span className={cn(base, "bg-emerald-600 text-white")}>
          {p.finish_position}着 {p.place_payout?.toLocaleString()}円
        </span>
      );
    case "miss":
      return (
        <span className={cn(base, "bg-gray-100 text-gray-500")}>
          {p.finish_position ? `${p.finish_position}着` : "着外"}
        </span>
      );
    case "void":
      return <span className={cn(base, "bg-amber-50 text-amber-700")}>返還</span>;
    default:
      return <span className={cn(base, "bg-blue-50 text-blue-700")}>発走前</span>;
  }
}

function PickItem({ p }: { p: PlacePickRow }) {
  const md = `${Number(p.date.slice(4, 6))}/${Number(p.date.slice(6, 8))}`;
  const post = p.post_time ? `${p.post_time.slice(0, 2)}:${p.post_time.slice(2, 4)}` : "";
  return (
    <li className="px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <Link
          href={`/races/${p.race_id}`}
          className="text-xs text-gray-600 hover:underline truncate min-w-0"
        >
          {md} {p.course_name}{p.race_number}R {post}
          <span className="text-gray-400">
            {" "}· {p.surface}{p.distance} · {p.field_size}頭
          </span>
        </Link>
        <StatusBadge p={p} />
      </div>
      <div className="flex items-center gap-2 mt-0.5 min-w-0">
        <span className="shrink-0 inline-flex items-center justify-center w-5 h-5 rounded bg-gray-800 text-white text-[11px] font-bold">
          {p.horse_number}
        </span>
        <span className="font-medium text-sm text-gray-800 truncate min-w-0">
          {p.horse_name ?? "—"}
        </span>
        <span className="ml-auto shrink-0 text-[11px] text-gray-500 tabular-nums whitespace-nowrap">
          単{p.pre_win_odds?.toFixed(1) ?? "—"} 複{p.pre_place_odds?.toFixed(1) ?? "—"}
          {p.pop_rank != null && ` · ${p.pop_rank}人気`}
        </span>
      </div>
    </li>
  );
}
