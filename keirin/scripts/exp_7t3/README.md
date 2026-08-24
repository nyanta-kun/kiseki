# RANK_7T3 検証スクリプト（2026-08-24）

設計書は `keirin/docs/rank_7t3_design.md`。本文の数字はすべてここの出力。

## 実行順（`keirin/` 直下から）

```bash
# 0) 中間データ（scratchpad ではなく /tmp に吐く。数百MB あるので注意）
.venv/bin/python scripts/exp_7t3/board.py            # 予測オッズ板 48,541R・約6分
.venv/bin/python scripts/exp_7t3/run_design.py       # 行列化 → /tmp/design_mat.npz
.venv/bin/python scripts/exp_7t3/feat.py             # レース特徴 → /tmp/keirin_feat.pkl

# 1) 帯のフロンティア・源泉探索
.venv/bin/python scripts/exp_7t3/frontier.py
.venv/bin/python scripts/exp_7t3/sel_sweep.py
.venv/bin/python scripts/exp_7t3/source.py
.venv/bin/python scripts/exp_7t3/venue.py

# 2) 決勝の実力
.venv/bin/python scripts/exp_7t3/kessho.py
.venv/bin/python scripts/exp_7t3/kessho_win.py

# 3) 月次ライブ（vintage m2604〜m2608）
.venv/bin/python scripts/exp_7t3/months.py
.venv/bin/python scripts/exp_7t3/months_rep.py
.venv/bin/python scripts/exp_7t3/control.py

# 4) 7T1 との関係・優先順位
.venv/bin/python scripts/exp_7t3/overlap.py
.venv/bin/python scripts/exp_7t3/h2h.py
.venv/bin/python scripts/exp_7t3/t1_variants.py
.venv/bin/python scripts/exp_7t3/coexist.py
.venv/bin/python scripts/exp_7t3/dial.py
.venv/bin/python scripts/exp_7t3/cap.py
.venv/bin/python scripts/exp_7t3/prio.py
```

## 注意

- 🔴 **`/tmp` から Python を起動しない。** 古い `/tmp/timeit.py` が標準ライブラリを
  隠していて import エラーになる（このセッションで実際に踏んだ）。
- `tfprob.py` は位置別合成 Plackett-Luce の実装。他のスクリプトが import する。
  ファイル名を `select.py` のような標準ライブラリ名にしないこと（同じく踏んだ）。
- いくつかのスクリプトは前段の生成物（`/tmp/design_mat.npz` 等）に依存する。
  上の順で流すこと。
- 予測は vintage walk-forward、予測オッズは `odds_tf_n7.txt`（train_end 2025-12-31）。
  **探索窓（〜2025-12）はオッズモデルが in-sample** なので EV 系の数字は高めに出る。
