// 競輪の買い目文字列（picks_history.pred_combo）を画面表示用に畳む純粋関数。
//
// 7H1 は三連複+三連単の併買のため、keirin 側は全目を列挙した文字列を書き込む:
//   "三複:1=3=7,1=3=5,1=3=4,… / 三単:7-3-1,7-3-5,7-3-4,…"
// これは画面上ほぼ読めないので、フォーメーション表記へ畳む:
//   三複 → "1,3,4,5,7 BOX"     （買い目集合がその5車のBOX全通りに一致するときのみ）
//   三単 → "7-1,3-1,3,4,5,6"   （1着1車 × 2着n車 × 3着m車の直積に一致するときのみ）
//
// ⚠️ **畳めない構造なら元の列挙をそのまま返す。** 省略して誤った買い目を見せるより冗長を選ぶ。
// 書式の正本は keirin リポジトリの scripts/build_7h1_candidates.py /
// scripts/backfill_7h1_rank_wt.py（picks_history 全 2,423 件で畳み込み可能を確認済み）。
//
// page.tsx は "use client" のため、テスト可能な純粋関数はここへ置く。

/** 三連複の全目が「N車BOX」ならその表記を返す。違えば null。 */
export function foldTrioBox(body: string): string | null {
  const combos = body.split(",").filter(Boolean);
  const sets = combos.map((c) => c.split("=").map(Number));
  if (sets.some((s) => s.length !== 3 || s.some((n) => !Number.isFinite(n)))) return null;
  const cars = [...new Set(sets.flat())].sort((a, b) => a - b);
  const key = (s: number[]) => [...s].sort((a, b) => a - b).join("=");
  const got = new Set(sets.map(key));
  if (got.size !== combos.length) return null;
  let expected = 0;
  for (let i = 0; i < cars.length; i++)
    for (let j = i + 1; j < cars.length; j++)
      for (let k = j + 1; k < cars.length; k++) {
        expected++;
        if (!got.has(key([cars[i], cars[j], cars[k]]))) return null;
      }
  if (expected !== got.size) return null;
  return `${cars.join(",")} BOX`;
}

/** 三連単の全目が「1着-2着候補-3着候補」の直積ならその表記を返す。違えば null。 */
export function foldTrifectaFormation(body: string): string | null {
  const combos = body.split(",").filter(Boolean);
  const legs = combos.map((c) => c.split("-").map(Number));
  if (legs.some((l) => l.length !== 3 || l.some((n) => !Number.isFinite(n)))) return null;
  if (new Set(legs.map((l) => l[0])).size !== 1) return null;
  const first = legs[0][0];
  const seconds = [...new Set(legs.map((l) => l[1]))].sort((a, b) => a - b);
  const thirds = [...new Set(legs.map((l) => l[2]))].sort((a, b) => a - b);
  const got = new Set(legs.map((l) => l.join("-")));
  if (got.size !== legs.length) return null;
  // 直積から「1着・2着と重複する3着」を除いたものと完全一致するときだけ畳める
  let expected = 0;
  for (const s of seconds)
    for (const t of thirds) {
      if (t === first || t === s) continue;
      expected++;
      if (!got.has(`${first}-${s}-${t}`)) return null;
    }
  if (expected !== got.size) return null;
  return `${first}-${seconds.join(",")}-${thirds.join(",")}`;
}

const MULTI_BET_LABEL: Record<string, string> = { 三複: "3連複", 三単: "3連単" };

/**
 * 「三複:… / 三単:…」形式の pred_combo を券種ごとの表示行へ整形する。
 * **券種ごとに1行**（横に繋げると折り返しでどちらの券種か読めなくなるため）。
 * 該当しない形式（従来の1券種ランク）は null を返し、呼び出し側の既存表示に任せる。
 */
export function formatMultiBetComboLines(pred: string): string[] | null {
  if (!pred.includes(":")) return null;
  const out: string[] = [];
  for (const part of pred.split("/")) {
    const m = /^(三複|三単):(.+)$/.exec(part.trim());
    if (!m) return null;
    const n = m[2].split(",").filter(Boolean).length;
    const folded = m[1] === "三複" ? foldTrioBox(m[2]) : foldTrifectaFormation(m[2]);
    out.push(`${MULTI_BET_LABEL[m[1]]} ${folded ?? m[2]} (${n}点)`);
  }
  return out;
}
