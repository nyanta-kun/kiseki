/**
 * `lib/utils.ts` の純ロジックのテスト。
 *
 * 対象は **DOM を使わない関数だけ**（方針は vitest.config.ts の冒頭）。
 * ここで守りたいのは「見た目」ではなく、**間違えても画面が壊れないので
 * 気づけない種類のロジック**——枠番の割り当て、JST 固定の日付、しきい値。
 */

import { describe, expect, it, vi, afterEach } from "vitest";

import {
  EV_HIGHLIGHT_THRESHOLD,
  calcEV,
  calcShareRatio,
  evClass,
  formatDate,
  horseNumToFrame,
  indexColor,
  raceClassShort,
  todayYYYYMMDD,
} from "@/lib/utils";

describe("horseNumToFrame — 馬番から枠番（JRA標準）", () => {
  // 🔴 ここを間違えると枠色が全馬ずれるが、画面は普通に描画されるので気づけない。
  it("8頭以下は 1馬1枠", () => {
    expect(horseNumToFrame(1, 8)).toBe(1);
    expect(horseNumToFrame(8, 8)).toBe(8);
    expect(horseNumToFrame(5, 5)).toBe(5);
  });

  it("9頭は外枠（8枠）から2頭目が入る", () => {
    expect(horseNumToFrame(8, 9)).toBe(8);
    expect(horseNumToFrame(9, 9)).toBe(8);
    expect(horseNumToFrame(7, 9)).toBe(7);
  });

  it("16頭は全枠2頭ずつ", () => {
    for (let waku = 1; waku <= 8; waku++) {
      expect(horseNumToFrame(waku * 2 - 1, 16)).toBe(waku);
      expect(horseNumToFrame(waku * 2, 16)).toBe(waku);
    }
  });

  it("17頭は7枠が3頭", () => {
    expect(horseNumToFrame(13, 17)).toBe(7);
    expect(horseNumToFrame(15, 17)).toBe(7);
    expect(horseNumToFrame(16, 17)).toBe(8);
    expect(horseNumToFrame(17, 17)).toBe(8);
  });

  it("18頭は7枠・8枠が3頭ずつ", () => {
    expect(horseNumToFrame(13, 18)).toBe(7);
    expect(horseNumToFrame(15, 18)).toBe(7);
    expect(horseNumToFrame(16, 18)).toBe(8);
    expect(horseNumToFrame(18, 18)).toBe(8);
  });

  it("どの頭数でも枠は 1〜8 に収まり、単調non-decreasing", () => {
    for (let total = 1; total <= 18; total++) {
      let prev = 0;
      for (let hn = 1; hn <= total; hn++) {
        const w = horseNumToFrame(hn, total);
        expect(w).toBeGreaterThanOrEqual(1);
        expect(w).toBeLessThanOrEqual(8);
        expect(w).toBeGreaterThanOrEqual(prev);
        prev = w;
      }
    }
  });
});

describe("todayYYYYMMDD — JST 固定", () => {
  // 🔴 サーバ(Docker/UTC)とクライアントで日付が食い違うと、
  //    「今日のレースが出ない」形で壊れる。TZ はこのリポジトリの再発領域。
  afterEach(() => vi.useRealTimers());

  it("UTC の深夜でも JST の日付を返す", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-01T15:30:00Z")); // JST 09-02 00:30
    expect(todayYYYYMMDD()).toBe("20260902");
  });

  it("UTC 15:00 ちょうどが JST の日付境界", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-01T14:59:59Z"));
    expect(todayYYYYMMDD()).toBe("20260901");
    vi.setSystemTime(new Date("2026-09-01T15:00:00Z"));
    expect(todayYYYYMMDD()).toBe("20260902");
  });

  it("月・日は必ず2桁に詰める", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-04T03:00:00Z"));
    expect(todayYYYYMMDD()).toBe("20260104");
  });
});

describe("formatDate", () => {
  it("YYYYMMDD を曜日つきに変換する", () => {
    expect(formatDate("20260902")).toBe("9月2日(水)");
  });
});

describe("EV まわり", () => {
  it("しきい値はバックエンドの sweet_spot EV 下限と同じ 1.2", () => {
    // 🔴 backend/src/indices/buy_signal.py の SWEET_SPOT_MIN_EV と対。
    //    片方だけ動かすと「バッジは付くのに推奨に出ない」等の食い違いになる。
    expect(EV_HIGHLIGHT_THRESHOLD).toBe(1.2);
  });

  it("EV = 単勝オッズ × 勝率。どちらか欠けたら null", () => {
    expect(calcEV(0.1, 12)).toBeCloseTo(1.2);
    expect(calcEV(null, 12)).toBeNull();
    expect(calcEV(0.1, null)).toBeNull();
  });

  it("しきい値ちょうどは high 側に入る", () => {
    expect(evClass(1.2)).toBe("ev-badge-high");
    expect(evClass(1.19)).toBe("ev-badge-mid");
    expect(evClass(0.89)).toBe("ev-badge-low");
    expect(evClass(null)).toBe("");
  });
});

describe("calcShareRatio — 確率シェアの均等比", () => {
  it("全馬同確率なら 1.0（ランダム水準）", () => {
    const probs = [0.1, 0.1, 0.1, 0.1];
    expect(calcShareRatio(0.1, probs)).toBeCloseTo(1.0);
  });

  it("合計が0・確率が0以下・空配列は null", () => {
    expect(calcShareRatio(0.1, [0, 0])).toBeNull();
    expect(calcShareRatio(0, [0.1])).toBeNull();
    expect(calcShareRatio(null, [0.1])).toBeNull();
    expect(calcShareRatio(0.1, [])).toBeNull();
  });

  it("null 混じりの配列でも合計に効かせない", () => {
    expect(calcShareRatio(0.2, [0.2, null, 0.2])).toBeCloseTo(1.5);
  });
});

describe("indexColor — 指数の色分け境界", () => {
  it("境界値はそれぞれ上の帯に入る", () => {
    expect(indexColor(65)).toContain("green-700");
    expect(indexColor(64.9)).toContain("green-600");
    expect(indexColor(55)).toContain("green-600");
    expect(indexColor(45)).toContain("gray-700");
    expect(indexColor(35)).toContain("orange-600");
    expect(indexColor(34.9)).toContain("red-600");
  });

  it("null は専用の色", () => {
    expect(indexColor(null)).toBe("text-gray-400");
  });
});

describe("raceClassShort", () => {
  it("条件戦のクラスを短縮する", () => {
    expect(raceClassShort("4歳以上2勝クラス")).toBe("2勝");
    expect(raceClassShort("3歳未勝利")).toBe("未勝利");
    expect(raceClassShort("オープン")).toBeNull();
    expect(raceClassShort(null)).toBeNull();
  });
});
