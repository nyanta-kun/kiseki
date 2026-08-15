"""ライブ経路が「見送り（miwokuri=True）」を書かないことを構造的に固定する。

## 守る不変条件（2026-08-15・ユーザー指示）

> 「入稿時点で見送りを行いません。見送り判定はなくして下さい。これは全ランク同様です」

netkeirin へは**候補が立ったレースをそのまま出している**ので、「見送った」という
状態は運用に存在しない。にもかかわらず 3 経路が picks_history を
`miwokuri=True` へ更新しており、**実際に売った商品が Web で「見送り」と表示**
されることがあった:

  1. `notify_prerace_wt._mark_paper_miwokuri()` … 15分前判定が skip のとき
  2. `notify_results_wt` の一括処理 … 発走時刻を過ぎた bet_amount=0 の候補行
  3. `write_candidates_wt._save_initial_gami()` … 朝の最安オッズ < 7.0

発覚は 7T1（2026-08-15）。**7T1 は発走前判定を持たない**ため候補行が
bet_amount=0 のまま残り、必ず 2 に拾われていた。

⚠️ **判定そのもの（`skip_reason`）は残す。** 欠車・盤面不一致・オッズ欠損は
   「買えなかった」事実でログには要る。消したのは **DB の見送りフラグだけ**。

⚠️ 廃止済みランク（`7PLUS_*`）の採点コードは対象外。候補生成が止まっており
   到達しないうえ、過去日の再採点結果を変えないため触らない。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

#: ライブ経路（当日の picks_history を書くもの）。
LIVE_SCRIPTS = (
    "notify_prerace_wt.py",
    "notify_results_wt.py",
    "write_candidates_wt.py",
)

#: `miwokuri` を真へ**更新する**SQL / 代入。
#: ⚠️ `WHERE miwokuri=TRUE` は**読み取り**（見送り行の遡及採点）なので除外する。
#:    ここを雑に引っかけると、読み取りまで禁止してしまう。
_WRITE_TRUE = re.compile(
    r"""(SET\s+miwokuri\s*=\s*(True|TRUE|1)\b)"""      # UPDATE ... SET miwokuri = True
    r"""|(\bmiwokuri\s*=\s*True\b)""",                  # miwokuri = True（キーワード引数含む）
    re.IGNORECASE,
)
#: 条件句（読み取り）だけの行を落とすための判定。
_READ_ONLY = re.compile(r"\bWHERE\b", re.IGNORECASE)


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """コメント・docstring を除いた「実際に動く行」を返す。

    見送りを**やめた理由**は各ファイルへコメントとして残してあるので、
    素朴に grep すると自分のコメントで落ちる。
    """
    out: list[tuple[int, str]] = []
    in_doc = False
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        # 素朴な三重引用符トラッキング（本リポジトリの docstring は行頭・行末で閉じる）
        if line.count('"""') == 1:
            in_doc = not in_doc
            continue
        if in_doc or line.startswith("#") or line.startswith('"""'):
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            out.append((i, code))
    return out


@pytest.mark.parametrize("name", LIVE_SCRIPTS)
def test_live_path_never_sets_miwokuri_true(name: str) -> None:
    """ライブ経路に miwokuri を真にする実コードが無いこと。"""
    path = SCRIPTS / name
    assert path.exists(), f"{name} が見つからない（改名したらこのテストも直すこと）"
    hits = [(n, c) for n, c in _code_lines(path)
            if _WRITE_TRUE.search(c) and not (
                _READ_ONLY.search(c) and "SET" not in c.upper())]
    assert not hits, (
        f"{name} が見送り（miwokuri=True）を書いている: {hits}\n"
        "入稿は候補が立ったレースをそのまま出しており、見送りという状態は運用に無い。"
    )


def test_mark_paper_miwokuri_is_gone() -> None:
    """撤去した関数が名前ごと復活していないこと（呼び出しも含む）。"""
    src = (SCRIPTS / "notify_prerace_wt.py").read_text(encoding="utf-8")
    code = "\n".join(c for _, c in _code_lines(SCRIPTS / "notify_prerace_wt.py"))
    assert "_mark_paper_miwokuri" not in code, (
        "_mark_paper_miwokuri が復活している。skip 時は何も書かず、"
        "夜間の walk-forward 再構築に上書きを任せること。"
    )
    # 理由がコメントとして残っていること（次に触る人が経緯を追えるように）
    assert "見送り" in src


def test_skip_reason_is_kept() -> None:
    """`skip_reason`（買えなかった理由のログ）まで消していないこと。"""
    code = "\n".join(c for _, c in _code_lines(SCRIPTS / "notify_prerace_wt.py"))
    assert code.count("skip_reason") >= 10, (
        "skip_reason が失われている。消したのは DB の見送りフラグだけで、"
        "欠車・盤面不一致・オッズ欠損の記録は残す。"
    )
