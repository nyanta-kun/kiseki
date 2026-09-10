/**
 * POG の画面で使う小さな部品。
 *
 * 🔴 **カードの面は `--pog-card` トークンを使う。** `bg-white` 直書きにすると
 * 暗いテーマで面だけが白く残る（`globals.css` の「面のトークン」参照）。
 * ここに集めてあるので、色を変えるときはこのファイルだけを見ればよい。
 */

import type { ReactNode } from "react";

/** カード。POG の一覧・パネルはすべてこれで包む。 */
export function Card({
  children,
  className = "",
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "section" | "li" | "article";
}) {
  return (
    <Tag
      className={`rounded-xl border shadow-sm ${className}`}
      style={{
        background: "var(--pog-card)",
        borderColor: "var(--pog-card-border)",
      }}
    >
      {children}
    </Tag>
  );
}

/** カードの見出し帯。件数などの補足を右に置ける。 */
export function CardHeader({
  title,
  meta,
  action,
}: {
  title: ReactNode;
  meta?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      className="flex items-baseline gap-2 border-b px-4 py-2.5"
      style={{ borderColor: "var(--pog-card-border)" }}
    >
      <h2 className="min-w-0 flex-1 truncate text-sm font-bold text-surface-heading">
        {title}
      </h2>
      {meta && <span className="shrink-0 text-xs text-surface-muted">{meta}</span>}
      {action}
    </div>
  );
}

/**
 * 数字を 1 つ見せるタイル。順位表の上に並べる。
 *
 * ⚠️ 数字は `tabular-nums`。等幅にしないと桁が変わるたびに横幅が揺れる。
 */
export function StatTile({
  label,
  value,
  sub,
  accent = false,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  accent?: boolean;
}) {
  return (
    <Card className="px-3 py-2.5">
      <div className="text-[11px] leading-tight text-surface-muted">{label}</div>
      <div
        className="mt-0.5 text-lg font-bold tabular-nums leading-tight"
        style={accent ? { color: "var(--pog-accent)" } : undefined}
      >
        {value}
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-surface-muted">{sub}</div>}
    </Card>
  );
}

/** 何も無いときの表示。ページごとに書き分けると文言が散らかる。 */
export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <Card className="px-4 py-10 text-center text-sm text-surface-muted">
      {children}
    </Card>
  );
}

/**
 * 順位のメダル。1〜3 位だけ色を付け、4 位以降は数字だけにする。
 *
 * ⚠️ 色だけで順位を伝えない（色覚特性への配慮）。数字を必ず併記する。
 */
export function RankBadge({ rank }: { rank: number }) {
  const palette: Record<number, string> = {
    1: "bg-amber-100 text-amber-800 ring-amber-300 dark:bg-amber-900/40 dark:text-amber-200 dark:ring-amber-700",
    2: "bg-slate-100 text-slate-700 ring-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-600",
    3: "bg-orange-100 text-orange-800 ring-orange-300 dark:bg-orange-900/40 dark:text-orange-200 dark:ring-orange-700",
  };
  const cls = palette[rank] ?? "text-surface-muted ring-transparent";
  // ⚠️ スマホでは一回り小さくする。行の高さは丸の直径で決まるので、ここが
  //    28px だと 1 行 44px になり、9 人で 1 画面に収まらない。
  return (
    <span
      className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold tabular-nums ring-1 md:h-7 md:w-7 md:text-sm ${cls}`}
    >
      {rank}
    </span>
  );
}

/** 成績「1-2-0-3」の表示。0 は薄くして、勝った数が目に入るようにする。 */
export function RecordPills({
  win,
  place,
  show,
  out,
}: {
  win: number;
  place: number;
  show: number;
  out: number;
}) {
  const cells: [number, string][] = [
    [win, "text-amber-600 dark:text-amber-400"],
    [place, "text-sky-600 dark:text-sky-400"],
    [show, "text-emerald-600 dark:text-emerald-400"],
    [out, "text-surface-muted"],
  ];
  return (
    <span className="inline-flex items-baseline gap-0.5 tabular-nums" aria-label={`${win}勝${place}着${show}着 着外${out}`}>
      {cells.map(([n, cls], i) => (
        <span key={i} className={n > 0 ? cls : "opacity-40"}>
          {i > 0 && <span className="mx-0.5 opacity-40">-</span>}
          {n}
        </span>
      ))}
    </span>
  );
}

/** 重賞の格バッジ。G1 だけ強く出す。 */
export function GradeBadge({ grade }: { grade: string | null }) {
  if (!grade) return null;
  const cls = grade.endsWith("1")
    ? "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200"
    : grade.endsWith("2")
      ? "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-200"
      : "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300";
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold ${cls}`}>
      {grade}
    </span>
  );
}


/**
 * スマホ用の折りたたみ。
 *
 * 🔴 **「1 画面に収める」ための道具。** 順位表の下に「今週の出走」「今週の重賞」を
 * 開いたまま積むと、主役の順位表が画面外へ押し出される（2026-09-10 の指摘）。
 * 畳んでおけば見出し 1 行ぶん（約 38px）で済み、要るときだけ開ける。
 *
 * ⚠️ `<details>` を使うのは JavaScript を足さずに済むから。同じことを state で
 *    やるとサーバコンポーネントをクライアント化することになる。
 *
 * ⚠️ PC ではこれを使わない（横に並べる余地があるので畳む必要がない）。
 *    呼ぶ側で `md:hidden` を付けること。
 */
export function Collapsible({
  title,
  meta,
  children,
  defaultOpen = false,
  className = "",
}: {
  title: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <details
      open={defaultOpen}
      className={`rounded-xl border shadow-sm ${className}`}
      style={{
        background: "var(--pog-card)",
        borderColor: "var(--pog-card-border)",
      }}
    >
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2.5 text-sm font-bold text-surface-heading marker:content-['']">
        <span
          aria-hidden="true"
          className="text-xs text-surface-muted transition-transform"
          style={{ color: "var(--pog-accent)" }}
        >
          ▸
        </span>
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {meta && <span className="shrink-0 text-xs font-normal text-surface-muted">{meta}</span>}
      </summary>
      <div style={{ borderTop: "1px solid var(--pog-card-border)" }}>{children}</div>
    </details>
  );
}
