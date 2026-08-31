"""勝ち上がり条件を **DB だけ**から復元できるかを確かめる（調査専用）。

advancementConditionText は 2026-04-19 頃より前の開催では空なので過去に遡れない。
代わりに「今日の着順が、翌日どのクラスのレースへ回されるかをどれだけ決めているか」
を実測し、`race_type` ラベルとの違いを見る。
"""
from __future__ import annotations

import os

import pandas as pd
import psycopg2

# 番組の格付け（大きいほど上位）。勝ち上がりの向きを測るためだけの順序。
RANK = {"チャレンジ一般": 0, "一般": 1, "チャレンジ選抜": 1, "選抜": 2, "特一般": 2,
        "チャレンジ準決勝": 3, "準決勝": 3, "特選": 3, "特秀": 4,
        "チャレンジ決勝": 5, "決勝": 5, "ガールズ決勝": 5}

SQL = """
SELECT r.cup_id, r.day_index, r.race_date, r.race_key, r.race_type, r.grade,
       e.player_id, e.finish_order
FROM keirin.wt_entries e JOIN keirin.wt_races r USING (race_key)
WHERE r.cancel = 0 AND r.race_date >= '2024-01-01' AND e.finish_order >= 1
"""


def main() -> None:
    with psycopg2.connect(os.environ["KEIRIN_DB_URL"]) as c:
        df = pd.read_sql(SQL, c)
    nxt = df[["cup_id", "day_index", "player_id", "race_type"]].copy()
    nxt["day_index"] -= 1
    nxt = nxt.rename(columns={"race_type": "next_type"})
    m = df.merge(nxt, on=["cup_id", "day_index", "player_id"], how="left")
    m["next_rank"] = m["next_type"].map(RANK)
    m = m.dropna(subset=["next_rank"])
    m["n"] = m.groupby("race_key")["player_id"].transform("size")
    m = m[m["n"] >= 6]

    out = []
    for rt, g in m.groupby("race_type"):
        if len(g) < 500:
            continue
        # 「今日の着順 → 翌日の格」の相関（負なら着順が良いほど上のレースへ行く）
        rho = g[["finish_order", "next_rank"]].corr(method="spearman").iloc[0, 1]
        top = g[g.finish_order == 1]["next_rank"].mean()
        bot = g[g.finish_order >= 6]["next_rank"].mean()
        out.append((rt, len(g), rho, top, bot, top - bot,
                    g["next_rank"].std()))
    res = pd.DataFrame(out, columns=["race_type", "n", "spearman", "1着の翌日格",
                                     "6着以下の翌日格", "差", "翌日格sd"])
    res = res.sort_values("差", ascending=False)
    pd.set_option("display.width", 160)
    print(res.to_string(index=False, float_format=lambda v: f"{v:7.3f}"))


if __name__ == "__main__":
    main()
