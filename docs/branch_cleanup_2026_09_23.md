# ローカルにしか無いブランチの棚卸しと整理手順（2026-09-23）

作業を mac mini へ移すにあたって、**この Mac の `~/GitHub/kiseki` にしか存在しない
コミット**を洗い出した記録。`git clone` では付いてこないものが対象。

> 🔴 **2026-09-23 に退避は済んでいる。** 下表の「要保全」6本は origin へ push 済みなので、
> **いま mac mini でクローンすれば何も失われない。** 残っているのは
> 「取り込み済みの4本をいつ消すか」と「保全した3本をどう決着させるか」だけ。

## 0. 洗い方（再現手順）

```bash
# ① origin のどこからも辿れないコミットを持つブランチ
for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do
  git branch -r --contains "$(git rev-parse "$b")" | grep -q . || echo "$b"
done

# ② そのブランチが「追加した」ファイルのうち main に無いもの（＝未取り込みの実体）
#    🔴 `git diff origin/main $b` で判断してはいけない。ブランチが main から
#       遅れているだけで巨大な差分が出て、取り込み済みかどうかが分からない。
python3 - <<'PY'
import subprocess
def sh(*c): return subprocess.run(c, capture_output=True, text=True).stdout
for b in ["<ブランチ名>"]:
    mb = sh("git", "merge-base", "origin/main", b).strip()
    added = [l.split("\t", 1)[1] for l in sh("git", "diff", "--name-status", f"{mb}..{b}").splitlines()
             if l.startswith("A\t")]
    missing = [f for f in added if subprocess.run(
        ["git", "cat-file", "-e", f"origin/main:{f}"], capture_output=True).returncode != 0]
    print(b, f"新規{len(added)}件 / main に無い{len(missing)}件", missing[:5])
PY
```

## 1. 一覧

| ブランチ | 最終 | 新規追加 | main に無い | 判定 |
|---|---|---:|---:|---|
| `pr462` | 2026-09-04 | 25 | **0** | 🗑 取り込み済み・削除可 |
| `feat/jra-pog-draft` | 2026-09-10 | 3 | **0** | 🗑 取り込み済み・削除可 |
| `feat/jra-pog-graded-panel` | 2026-09-10 | 3 | **0** | 🗑 取り込み済み・削除可 |
| `feat/jra-heihachi-backtest` | 2026-09-06 | 3 | **0** | 🗑 取り込み済み・削除可 |
| `feat/jra-lap-features` | 2026-09-17 | 10 | **10** | 🟢 **進行中**（push 済・worktree あり） |
| `feat/jra-recommend-confidence-table` | 2026-08-24 | 4 | **4** | 🟡 **塩漬け**（push 済・要判断） |
| `feat/keirin-tf-core-redesign` | 2026-08-23 | 66 | **3** | 🟠 **63件は取り込み済み・残り3件は死んだ**（push 済） |

stash（**push されないので消えると戻せない**）も同様にブランチ化して退避した:

| 退避先ブランチ | 元 | 中身 |
|---|---|---|
| `wip/stash-0-payoutfloor30k20260921` | `stash@{0}` | 計画払戻の床3万（2026-09-21 に見送り決定） |
| `wip/stash-1-20260831maintypelabpyori` | `stash@{1}` | 2026-08-31 の main 作業ツリー残置（16ファイル） |
| `wip/stash-2-chihou-dm-cherrypick` | `stash@{2}` | dm cherry-pick 前の作業（追跡4ファイルのみ） |

🔴 **`stash@{2}` はそのままでは push できなかった。** 未追跡 189 件（**`Claude.dmg` 270MB**）
が巻き込まれており GitHub に弾かれる。`git commit-tree stash@{2}^{tree} -p stash@{2}^1`
で**追跡ファイルだけの commit** を作り直して push した。
＝ **stash は「うっかり巻き込んだ巨大ファイル」を抱えたまま何ヶ月も残る**。

## 2. それぞれ何をしようとしていたか / どう決着させるか

### 🟢 `feat/jra-lap-features`（進行中・worktree `~/GitHub/kiseki-wt/jra/lap-features`）

**目的**: netkeiba が「走行データ マスター」（個別ラップ・走行距離・距離補正タイム）を
公開したが、SuperPremium 契約では**1着馬1頭しか返らない**（全頭は別契約「マスターコース」）。
そこで先に、**追加費用ゼロで手元にある** JRA-VAN の `races.lap_times` と走破タイム・
上がり3F から過去走7特徴を作り、v28 単勝ヘッドに足して効くかを測る。
ここで何も出なければマスターコースを契約する価値も低い、という判断材料にする。
事前登録は `docs/jra_lap_feature_plan_2026_09_16.md`（**結果を見る前に書いてある**）。

**決着のさせ方**: 事前登録どおり測り切って採否を出す。採用なら v28 の特徴量へ足して
`inference_v28.py` で全期間バックフィル → デプロイ → 当日/翌日の calculate の3段
（CLAUDE.md「版を上げたら3段」）。不採用なら**結果を doc に書いてからブランチを消す**
（否定結果こそ残す価値がある）。

### 🟡 `feat/jra-recommend-confidence-table`（2026-08-24・塩漬け1ヶ月）

**目的**: 中央の「推奨」タブを**単勝信頼度ボード**へ置き換える。全出走馬を
`win_probability`（is_win 較正ヘッド）の降順に並べ、単勝オッズと「オッズ×単勝信頼度」
（＝単勝EV・1.0 が損益分岐）を添えて見せる UI。

**いま main にあるもの**: 推奨は `hit_tier` 方式（1レース1推奨＝指数1位馬＋tier）で、
`calculate_recommend_rank`（market_agree 第一分岐）が正本。**信頼度ボードは入っていない**。

**決着のさせ方（要判断）**: これは「1レース1推奨」を「全馬の一覧」へ**置き換える**提案で、
現行の設計方針と正面からぶつかる。3択:
1. **破棄** — 現行の hit_tier を維持するなら、これは方針違いなので消す（推奨）
2. **併置** — 推奨タブは残し、別タブとして入れる。UI の枠がもう1つ要る
3. **再設計** — v26 の `win_probability` を前提に書かれているが、**本番は v28 で
   単勝ヘッドが38列に変わっている**（`models/v28_iswin_calib.txt`）。そのまま出すと
   古いヘッドの説明になるので、採るなら書き直しが要る

⚠️ **1ヶ月塩漬けの間に前提（v26→v28）が動いている。** どれを採るにせよ、
`jra_confidence_board.py` の docstring は書き直しが要る。

### 🟠 `feat/keirin-tf-core-redesign`（2026-08-23・33コミット中24が docs）

**目的**: 競輪を三連単主軸へ再設計する一連の検証。**66個の新規ファイルのうち63個は
すでに main にある**（検証記録・ハーネスは別 PR で取り込み済み）。

**残っている3ファイルは `RANK_7T2`（三連単の一撃枠）の実装だけ**:
`keirin/scripts/build_7t2_candidates.py` / `rebuild_7t2_walkforward_pg.py` /
`keirin/tests/test_rank_7t2.py`。

🔴 **これは死んでいる。** 旧ランク体系は **2026-09-22 に本番から破棄**され
（`KEIRIN_ALLOW_OLD_RANKS=1` ガード・`tests/test_old_ranks_retired.py` が復活を止める）、
いま売っているのは型ラボだけ。7T2 を入れる先が無い。

**決着のさせ方**: **破棄**。ただし「三連単の一撃枠を作ろうとして何を測ったか」は
`keirin/docs/strategy_rebuild_2026_08.md` と `product_portfolio_redesign_2026_08.md`
（どちらも main にある）に残っているので、消しても知見は失われない。

### 🗑 取り込み済み4本

| ブランチ | 中身 | main での姿 |
|---|---|---|
| `pr462` | 総合指数 v28（複勝を独立ヘッドへ） | `COMPOSITE_VERSION = 28`・`models/v28_*` |
| `feat/jra-pog-draft` | POG ドラフトのバックエンド移植 | `backend/src/api/pog_draft_router.py` |
| `feat/jra-pog-graded-panel` | POG「今週の重賞」パネル | `backend/src/services/graded_races.py` |
| `feat/jra-heihachi-backtest` | 平八の閾値スライダーと2025年検証欄 | `backend/src/services/jra_heihachi_picks.py` |

```bash
git branch -D pr462 feat/jra-pog-draft feat/jra-pog-graded-panel feat/jra-heihachi-backtest
```

⚠️ **squash マージされたブランチは `git branch -d` では消せない**（「マージされていない」と
言われる）。`-D` が要る。だからこそ**手で消さないと溜まり続ける**。

## 3. worktree（13個）

`~/GitHub/kiseki-wt/` 配下。ブランチ自体は origin にあるので、**mac mini では
必要なものだけ作り直せばよい**（`bash scripts/dev/wt.sh new <柱> <トピック>`）。
いま実体が要るのは `jra/lap-features` だけ。

```bash
git worktree list                      # 一覧
bash scripts/dev/wt.sh rm <パス>        # 片付け
```

🔴 **worktree には `keirin/data/`（モデル212MB）も `.venv` も無い。** 競輪の検証を
worktree で回すなら symlink が要る（`keirin-payout-band-redesign-2026-09-22` メモの
「検証を回すときの罠」）。

## 4. この状態にしないための運用

1. **PR をマージしたらローカルブランチも消す。** `gh pr merge --delete-branch` は
   **リモートしか消さない**。ローカルは残る。
2. **stash を跨いで放置しない。** 1日で決着しないなら `git branch wip/<名前> stash@{0}`
   でブランチにして push する。stash は push されず、巨大ファイルを巻き込んでも気づけない。
3. **月1で棚卸しする。** §0 の2行で「origin に無いコミットを持つブランチ」は数秒で出る。

```bash
# 取り込み済みのローカルブランチを一括で消す（origin にあるものだけ）
for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do
  [ "$b" = main ] && continue
  git branch -r --contains "$(git rev-parse "$b")" | grep -q . && git branch -D "$b"
done
```
