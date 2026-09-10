import type { ReactNode } from "react";

import type { PogGroup } from "@/lib/pog";
import { PogSectionNav } from "./PogSectionNav";
import { YearTabs } from "./YearTabs";

/**
 * POG 全ページの外枠。
 *
 * 🔴 **移設元は各ページが自前で `max-w-3xl` の 1 カラムを敷いていた**ため、
 * PC で見ると横 1,000px 以上が空白のまま、順位表もスコアも中央の細い帯に
 * 押し込まれていた。外枠をここに集約して、
 *
 *   - 面（背景）は `--page-bg-pog`
 *   - 幅は `max-w-6xl`（PC で 2〜3 カラムを組める）
 *   - 見出し・年度・ページタブの位置は全ページ共通
 *
 * を保証する。個々のページは**中身のグリッドだけ**を書けばよい。
 *
 * ⚠️ ボトムナビ（スマホ）が画面下 14 単位を覆うので、下余白は
 *    ルートレイアウトの `pb-14 md:pb-0` に任せる。ここで足すと二重になる。
 */
export function PogShell({
  title,
  description,
  year,
  groups,
  /** 年度を切り替えたときの行き先。`"/pog/:year/horses"` のように書く。 */
  yearBasePath,
  showYear = true,
  /** 見出しの右に置く操作（絞り込みなど）。 */
  toolbar,
  children,
}: {
  title: string;
  description?: ReactNode;
  year: number;
  groups: PogGroup[];
  yearBasePath?: string;
  /**
   * 年度セレクタを出すか。
   *
   * ⚠️ 年度に依らないページ（兄弟馬）では **false にする**。出したままだと
   *    選んでも同じ URL に戻るだけの「効かないセレクタ」になる。
   */
  showYear?: boolean;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  // 面は `min-h-screen`。他の柱のページ（`/races` `/chihou/races`）と同じ組み方に
  // 揃えてある。`flex-1` だと中身が短いページで面が途中で切れる。
  // 面は `min-h-screen`。他の柱のページ（`/races` `/chihou/races`）と同じ組み方に
  // 揃えてある。`flex-1` だと中身が短いページで面が途中で切れる。
  //
  // 🔴 **スマホは縦の予算がすべて**（2026-09-10 の指摘）。
  //    iPhone の実効高さから サイトヘッダ 56px とボトムナビ 56px を引くと、
  //    ページが使えるのは 500〜560px しかない。外枠でここを削っておかないと
  //    主役の一覧が画面外へ落ちる。内訳:
  //
  //      見出し + 年度       約 26px（同じ行に並べる）
  //      説明文             約 26px（11px × 最大2行）
  //      ページタブ         約 30px
  //      ------------------------------
  //      外枠の合計         約 100px
  //
  //    余白も `py-2 px-3`（PC は `py-6 px-4`）まで詰める。
  //
  //    🔴 **実測の予算は 677px**（iPhone 390×844・Safari 上部バー展開時の可視 web view。
  //       ユーザーのスクリーンショット 1170×2532 から割り出した）。ここから
  //       サイトヘッダ 60px とボトムナビ 56px を引いた残りに全部を収める。
  //       2026-09-10 の実測:
  //
  //         順位表  7人 599px / 8人 640px / 9人 680px
  //         スコア  9人 668px
  //
  //    ⚠️ 縦を足す変更をするときは、この数字を更新できるか確かめること。
  //       「1 行 40px を足す」は 9 人ぶんの一覧では収まりの成否を分ける。
  return (
    <div className="min-h-screen" style={{ background: "var(--page-bg-pog)" }}>
      <div className="mx-auto max-w-6xl px-3 py-2 md:px-4 md:py-6">
        {/* 見出しと年度。スマホでは**同じ行**に置く（折り返すと 1 行ぶん損する）。 */}
        <div className="flex items-center justify-between gap-3">
          <h1 className="min-w-0 truncate text-base font-bold text-surface-heading md:text-2xl">
            {title}
          </h1>
          {showYear && (
            <div className="min-w-0 shrink-0 md:max-w-[60%]">
              <YearTabs groups={groups} current={year} basePath={yearBasePath} />
            </div>
          )}
        </div>
        {description && (
          <p className="mt-0.5 text-[11px] leading-tight text-surface-muted md:mt-1 md:text-sm">
            {description}
          </p>
        )}

        {/* ページタブ */}
        <div className="mb-2 mt-2 md:mb-4 md:mt-3">
          <PogSectionNav year={year} />
        </div>

        {toolbar && <div className="mb-2 md:mb-3">{toolbar}</div>}

        <main id="main-content">{children}</main>
      </div>
    </div>
  );
}
