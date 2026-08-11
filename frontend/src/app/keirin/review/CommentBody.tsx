"use client";

/**
 * 入稿コメントの表示（2026-08-12 新設）。
 *
 * netkeirin のコメント欄は script/style/iframe 以外の HTML タグを許容するため、
 * keirin 側（`scripts/netkeirin_submit_wt.py::_build_entry_table`）は出走表を
 * **HTML の `<table>` として本文に埋め込んでいる**。承認画面ではそれが生タグの
 * まま出ていて読めなかったので、表として描き直す。
 *
 * 🔴 **`dangerouslySetInnerHTML` は使わない。** コメント本文は入稿の生成物とはいえ
 *    選手名などの外部由来テキストを含む。ここで解釈するのは
 *    **`<table>/<thead>/<tbody>/<tr>/<th>/<td>` だけ**で、それ以外のタグが来たら
 *    解釈せず生テキストとして出す（＝黙って消えない）。
 *
 * ⚠️ 現在タグ入力があるのは表だけ。他の書式（強調・リンク等）を入稿側で使い始めたら
 *    ここに足すこと。足さないと**生タグのまま画面に出る**（消えはしない）。
 */

type Cell = { text: string; align: string | null };
type TableBlock = { kind: "table"; head: Cell[]; rows: Cell[][] };
type TextBlock = { kind: "text"; text: string };
type Block = TableBlock | TextBlock;

const TABLE_RE = /<table\b[^>]*>([\s\S]*?)<\/table>/gi;
const ROW_RE = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
const CELL_RE = /<(th|td)\b([^>]*)>([\s\S]*?)<\/\1>/gi;
const ALIGN_RE = /align\s*=\s*"?([a-z]+)"?/i;

/** HTML 実体参照を戻す（`html.escape` の逆。エスケープしているのは5種のみ）。 */
function unescapeHtml(s: string): string {
  return s
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#(?:39|x27);/g, "'")
    .replace(/&amp;/g, "&")
    .trim();
}

function parseCells(rowHtml: string): Cell[] {
  const cells: Cell[] = [];
  for (const m of rowHtml.matchAll(CELL_RE)) {
    const align = ALIGN_RE.exec(m[2])?.[1] ?? null;
    cells.push({ text: unescapeHtml(m[3]), align });
  }
  return cells;
}

/** コメント本文を「テキスト」と「表」のブロック列に分解する。 */
export function parseCommentBlocks(comment: string): Block[] {
  const blocks: Block[] = [];
  let cursor = 0;
  for (const m of comment.matchAll(TABLE_RE)) {
    const start = m.index ?? 0;
    if (start > cursor) blocks.push({ kind: "text", text: comment.slice(cursor, start) });
    const rows = [...m[1].matchAll(ROW_RE)].map((r) => parseCells(r[1]));
    // 見出し行は <thead> の有無ではなく「<th> を含む行か」で判定する
    // （入稿側が thead を省いても表として読めるように）。
    const hasHead = /<th\b/i.test(m[1]) && rows.length > 0;
    blocks.push({
      kind: "table",
      head: hasHead ? rows[0] : [],
      rows: hasHead ? rows.slice(1) : rows,
    });
    cursor = start + m[0].length;
  }
  if (cursor < comment.length) blocks.push({ kind: "text", text: comment.slice(cursor) });
  return blocks;
}

function alignClass(align: string | null): string {
  if (align === "center") return "text-center";
  if (align === "right") return "text-right";
  return "text-left";
}

export default function CommentBody({ comment }: { comment: string | null }) {
  if (!comment) {
    return <p className="text-xs text-gray-500 dark:text-gray-400">（未設定）</p>;
  }
  const blocks = parseCommentBlocks(comment);
  return (
    <div className="space-y-2">
      {blocks.map((b, i) =>
        b.kind === "text" ? (
          b.text.trim() === "" ? null : (
            <p key={i} className="whitespace-pre-wrap text-xs text-gray-700 dark:text-gray-300">
              {b.text.trim()}
            </p>
          )
        ) : (
          <div key={i} className="overflow-x-auto">
            <table className="text-xs tabular-nums">
              {b.head.length > 0 && (
                <thead className="text-gray-500 dark:text-gray-400">
                  <tr>
                    {b.head.map((c, j) => (
                      <th key={j} className={`px-2 py-0.5 font-medium ${alignClass(c.align)}`}>
                        {c.text}
                      </th>
                    ))}
                  </tr>
                </thead>
              )}
              <tbody>
                {b.rows.map((r, j) => (
                  <tr key={j} className="border-t border-gray-100 dark:border-gray-700">
                    {r.map((c, k) => (
                      <td key={k} className={`px-2 py-0.5 ${alignClass(c.align)}`}>
                        {c.text}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ),
      )}
    </div>
  );
}
