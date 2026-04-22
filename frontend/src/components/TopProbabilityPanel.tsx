import { fetchChihouTopProbability, fetchJraTopProbability, TopProbHorse } from "@/lib/api";

function formatPostTime(t: string | null): string {
  if (!t || t.length < 4) return "-";
  return `${t.slice(0, 2)}:${t.slice(2, 4)}`;
}

function formatOdds(v: number | null): string {
  if (v === null) return "-";
  return `${v.toFixed(1)}倍`;
}

function formatPosition(pos: number | null): string {
  if (pos === null) return "-";
  return `${pos}着`;
}

function positionColor(pos: number | null): string {
  if (pos === 1) return "text-amber-600 font-bold";
  if (pos === 2) return "text-gray-500 font-semibold";
  if (pos === 3) return "text-orange-500 font-semibold";
  if (pos !== null) return "text-gray-400";
  return "text-gray-300";
}

function TopProbTable({ horses, accentColor }: { horses: TopProbHorse[]; accentColor: string }) {
  if (horses.length === 0) {
    return (
      <p className="text-sm text-gray-400 py-3 px-1">なし</p>
    );
  }

  return (
    <div className="overflow-x-auto -mx-1">
      <table className="w-full text-sm border-collapse min-w-[380px]">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">発走</th>
            <th className="text-left py-1.5 px-1 font-medium whitespace-nowrap">競馬場</th>
            <th className="text-center py-1.5 px-1 font-medium">R</th>
            <th className="text-left py-1.5 px-1 font-medium">馬名</th>
            <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">勝率</th>
            <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">単オッズ</th>
            <th className="text-right py-1.5 px-1 font-medium whitespace-nowrap">着順</th>
          </tr>
        </thead>
        <tbody>
          {horses.map((h, i) => (
            <tr
              key={i}
              className="border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors"
            >
              <td className="py-2 px-1 text-gray-500 whitespace-nowrap tabular-nums">
                {formatPostTime(h.post_time)}
              </td>
              <td className="py-2 px-1 font-medium text-gray-700 whitespace-nowrap">
                {h.course_name}
              </td>
              <td className="py-2 px-1 text-center text-gray-500">{h.race_number}R</td>
              <td className="py-2 px-1 font-semibold text-gray-800">
                <span className="text-xs text-gray-400 mr-1">{h.horse_number}番</span>
                {h.horse_name ?? "-"}
              </td>
              <td className="py-2 px-1 text-right">
                <span
                  className="text-xs font-bold px-1.5 py-0.5 rounded"
                  style={{ color: accentColor, background: `${accentColor}18` }}
                >
                  {Math.round(h.win_probability * 100)}%
                </span>
              </td>
              <td className="py-2 px-1 text-right text-gray-600 tabular-nums">
                {formatOdds(h.win_odds)}
              </td>
              <td className={`py-2 px-1 text-right tabular-nums ${positionColor(h.finish_position)}`}>
                {formatPosition(h.finish_position)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 地方競馬用
// ---------------------------------------------------------------------------

export async function ChihouTopProbabilityPanel({ date }: { date: string }) {
  let horses: TopProbHorse[] = [];
  try {
    horses = await fetchChihouTopProbability(date);
  } catch {
    return null;
  }

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 mb-4">
      <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
        <span
          className="inline-block w-2.5 h-2.5 rounded-full"
          style={{ background: "var(--chihou-primary)" }}
          aria-hidden="true"
        />
        本日の注目馬（勝率50%以上）
      </h2>
      <TopProbTable horses={horses} accentColor="var(--chihou-primary)" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// JRA用
// ---------------------------------------------------------------------------

export async function JraTopProbabilityPanel({ date }: { date: string }) {
  let horses: TopProbHorse[] = [];
  try {
    horses = await fetchJraTopProbability(date);
  } catch {
    return null;
  }

  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm p-4 mb-4">
      <h2 className="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5">
        <span
          className="inline-block w-2.5 h-2.5 rounded-full bg-green-700"
          aria-hidden="true"
        />
        本日の注目馬（勝率50%以上）
      </h2>
      <TopProbTable horses={horses} accentColor="#15803d" />
    </div>
  );
}
