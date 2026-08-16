"""二重入稿ガードが見るレース集合と、実際に入稿する候補集合を一致させる（2026-08-16）。

## 実障害

09:49 に公開済みだった京王閣12R(7S) が、13:00 の波で**入稿案として作り直された**。
`netkeirin_submissions` の行は `published` → `proposed` へ差し戻され、そのあと
確認画面で「公開」を押しても netkeirin 側に公開待ちが無いので失敗する、という形で
表面化した（ユーザーには「公開ボタンが無反応」に見えた）。

## 原因

候補JSONの読み方が2か所にあり、**別々に書かれていた**:

    main()          … `_load_candidates(..., cfg["file_key"])`          ← 1本だけ
    _process_rank() … `for _fk in cfg.get("file_keys") or [...]`        ← 全部

`RANK_CONFIGS["7S"]` は `file_key="s7"` と `file_keys=["s7","s7a","s7ss"]` の
両方を持つ。12R は `s7ss` にしか無いため `main()` が作る `all_race_keys` に入らず、
**`_already_submitted()` へ問い合わせすらされなかった**。ガードは正しく動いていて、
聞かれなかっただけ。

🔴 **`file_keys` を持つランクが1つでもあれば再発しうる。** 静的に固定する。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import scripts.netkeirin_submit_wt as m


def test_複数候補ファイルを持つランクが実在する():
    """この検査の前提。`file_keys` が全滅したらテストごと不要になる。"""
    multi = [k for k, cfg in m.RANK_CONFIGS.items() if cfg.get("file_keys")]
    assert multi, "file_keys を持つランクが無い（前提が変わったら本検査を見直す）"


@pytest.mark.parametrize("rank_key", sorted(m.RANK_CONFIGS))
def test_正本は宣言された候補ファイルを全て返す(rank_key):
    cfg = m.RANK_CONFIGS[rank_key]
    got = m._rank_file_keys(cfg)
    assert got == list(cfg.get("file_keys") or [cfg["file_key"]])
    # 1本目は必ず file_key（既存の挙動と互換）
    assert got[0] == cfg["file_key"] or cfg["file_key"] in got


def test_候補の読み込みは正本だけを通す():
    """🔴 `main()` と `_process_rank()` の**どちらも** `_rank_file_keys` を使うこと。

    `cfg["file_key"]` を直に `_load_candidates` へ渡している箇所が1つでも残ると、
    そこだけ1本しか読まず、上の実障害と同じ非対称が復活する。
    """
    src = Path(inspect.getfile(m)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_load_candidates"):
            continue
        # 第3引数（file key）が cfg["file_key"] の形なら違反
        if len(node.args) < 3:
            continue
        arg = node.args[2]
        if (isinstance(arg, ast.Subscript) and isinstance(arg.slice, ast.Constant)
                and arg.slice.value == "file_key"):
            offenders.append(node.lineno)
    assert not offenders, (
        f"cfg['file_key'] を直接 _load_candidates へ渡している行: {offenders}。"
        " _rank_file_keys() を使うこと（二重入稿ガードが素通りする）")


def test_重複判定のキー集合を作る側も全ファイルを読む():
    """`main()` の中で `_rank_file_keys` が呼ばれていること。

    ⚠️ 呼ばれなくても例外は出ず、**二重入稿が黙って通る**だけなので構造で固定する。
    """
    src = Path(inspect.getfile(m)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = {getattr(n.func, "id", "") for n in ast.walk(main_fn) if isinstance(n, ast.Call)}
    assert "_rank_file_keys" in calls
