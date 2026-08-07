"""競輪ランク一覧の「手書き二重管理」に対する回帰テスト（2026-08-06）。

## 背景（実際に起きた事故）

競輪の表示ランク一覧は backend の `_PAPER_RANK_LABELS` を単一正本とする設計だが、
**フロントエンド側には同じ一覧が4箇所に手書きで存在する**。この二重管理は
2026-08-03〜08-06 のあいだに繰り返し事故を起こした:

- 7B を新設したが `_RANK_COND_MAP` に入れ忘れ → 統計ページで「7B」を選ぶと
  **未知キーが黙って全ランクへフォールバック**し、全ランクの数字が 7B として
  表示されていた（エラーも警告も出ない）
- 7SS を新設したが keirin 側 `RANK_ORDER` に入れ忘れ → `enabled=True` なのに
  **一度も netkeirin へ入稿されなかった**（fail-closed で無警告）
- netkeirin 設定画面の一覧に 7B が無く、保存しようとすると 400

いずれも「動かない」のではなく「**黙って違う数字を出す / 何もしない**」ため、
ユーザーが画面を突き合わせるまで検知できなかった。

## 何を守るか

`_PAPER_RANK_LABELS` に載っている表示ラベルが、フロントの各一覧にも
**漏れなく載っていること**。逆向き（フロントにあって backend に無い）も見る。

新ランクを足すときは backend の `_PAPER_RANK_LABELS` とフロント4箇所を
同時に直す。このテストが落ちたら、どこか1箇所を忘れている。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.api.keirin_router import _MANUAL_RANK_KEYS, _PAPER_RANK_LABELS

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: 表示ラベル（例: "7SS"/"7H1"）の集合。これが正本。
LABELS = set(_PAPER_RANK_LABELS.values())


def _read(rel: str) -> str:
    path = FRONTEND / rel
    if not path.exists():          # frontend を含まないチェックアウトでは skip
        pytest.skip(f"frontend が見つかりません: {path}")
    return path.read_text(encoding="utf-8")


def _quoted(fragment: str) -> set[str]:
    """`"7S" | "7A"` や `["7S", "7A"]` からダブルクォート内の文字列を拾う。"""
    return set(re.findall(r'"([^"]+)"', fragment))


def _extract(rel: str, pattern: str) -> set[str]:
    """ファイル内の1箇所だけマッチする宣言から、ラベル集合を取り出す。"""
    text = _read(rel)
    matches = re.findall(pattern, text, re.DOTALL)
    assert len(matches) == 1, (
        f"{rel} の宣言 `{pattern}` が {len(matches)} 件マッチした（1件であるべき）。"
        "宣言の書き方が変わったならこのテストのパターンも更新すること。"
    )
    return _quoted(matches[0])


def test_stats_rank_type_covers_all_labels():
    """`KeirinStatsRank`（統計ページのランク絞り込み型）。

    ここが漏れると、そのランクを選んでも **全ランクの数字が表示される**
    （backend が未知キーを黙って全体へフォールバックするため）。
    """
    found = _extract("lib/api.ts", r"export type KeirinStatsRank =([^;]*);")
    assert LABELS <= found, f"api.ts の KeirinStatsRank に不足: {sorted(LABELS - found)}"
    assert found - LABELS == {"all"}, f"KeirinStatsRank に余計な値: {sorted(found - LABELS - {'all'})}"


def test_netkeirin_rank_type_covers_all_labels():
    """`NetkeirinRankKey`（自動入稿設定の型）。'_global' は全体ON/OFFの特殊行。"""
    found = _extract("lib/api.ts", r"export type NetkeirinRankKey =([^;]*);")
    assert LABELS <= found, f"api.ts の NetkeirinRankKey に不足: {sorted(LABELS - found)}"
    assert found - LABELS == {"_global"}, \
        f"NetkeirinRankKey に余計な値: {sorted(found - LABELS - {'_global'})}"


def test_stats_page_rank_filters_cover_all_labels():
    """統計ページの絞り込みボタン（`RankFilter` 型）。"""
    found = _extract("app/keirin/stats/page.tsx", r"type RankFilter =([^;]*);")
    assert LABELS <= found, f"stats/page.tsx の RankFilter に不足: {sorted(LABELS - found)}"


def test_settings_page_rank_order_covers_all_labels():
    """入稿設定画面の並び（ここに無いランクは画面から ON/OFF できない）。"""
    found = _extract(
        "app/keirin/settings/page.tsx",
        r"const RANK_ORDER: NetkeirinRankKey\[\] =([^;]*);",
    )
    assert LABELS <= found, \
        f"settings/page.tsx の RANK_ORDER に不足: {sorted(LABELS - found)}"


def test_keirin_page_rank_order_covers_all_labels():
    """トップページのサマリー「ランク別」展開の並び。"""
    found = _extract("app/keirin/page.tsx", r"const RANK_ORDER = \[([^\]]*)\]")
    assert LABELS <= found, f"keirin/page.tsx の RANK_ORDER に不足: {sorted(LABELS - found)}"
    assert found <= LABELS, \
        f"keirin/page.tsx の RANK_ORDER に未知のランク: {sorted(found - LABELS)}"


def test_keirin_page_label_and_badge_maps_cover_all_labels():
    """バッジのラベル／配色。抜けるとそのランクだけ色無し・ラベル無しで出る。"""
    for name, pattern in (
        ("RANK_LABEL", r"const RANK_LABEL: Record<string, string> = \{(.*?)\n\};"),
        ("RANK_BADGE_STYLE", r"const RANK_BADGE_STYLE: Record<string, string> = \{(.*?)\n\};"),
    ):
        found = _extract("app/keirin/page.tsx", pattern)
        assert LABELS <= found, f"keirin/page.tsx の {name} に不足: {sorted(LABELS - found)}"


def test_help_page_documents_every_rank():
    """ヘルプのランク解説。**Web に出るランクは必ず説明がある**状態を保つ。"""
    text = _read("app/keirin/help/page.tsx")
    documented = set(re.findall(r'^\s*key: "([^"]+)",', text, re.MULTILINE))
    assert LABELS <= documented, \
        f"help/page.tsx にランク解説が無い: {sorted(LABELS - documented)}"


def test_manual_submit_ranks_are_a_subset_of_labels():
    """手動入稿の許可ランクは表示ランクの部分集合であること。

    2券種の 7H1 は「軸2車を選ぶ」UIでは買い目を表現できないため**含めない**
    のが正しい。ここでは「表示ランクに存在しないキーを許可していないか」だけを見る。
    """
    assert set(_MANUAL_RANK_KEYS) <= LABELS, \
        f"_MANUAL_RANK_KEYS に表示ランク外のキー: {sorted(set(_MANUAL_RANK_KEYS) - LABELS)}"
    assert "7H1" not in _MANUAL_RANK_KEYS, \
        "7H1 は2券種のため手動入稿（軸2車指定）では表現できない"


def test_frontend_manual_submit_ranks_match_backend():
    """🔴 フロントの手動入稿の選択肢が backend の許可リストと一致すること。

    2026-08-03 の 7B 新設から 2026-08-08 まで、フロントの `MANUAL_SUBMIT_RANKS` と
    `ManualKeirinRankKey` は "7B" を含む一方 backend の `_MANUAL_RANK_KEYS` には
    無く、**UI では選べるのに送信すると必ず 400「不正なrank_key」**になっていた。

    既存の `test_manual_submit_ranks_are_a_subset_of_labels` は backend 内部の
    包含関係しか見ておらず、フロントとの一致は検査範囲外だったため検出できなかった。

    backend が受け付けないキーをフロントが出してはいけない（押すたびにエラー）。
    逆向き（backend にあってフロントに無い）は「まだ UI を作っていない」だけなので許す。
    """
    text = _read("app/keirin/page.tsx")
    m = re.search(
        r"const MANUAL_SUBMIT_RANKS:[^=]*=\s*\{(.*?)\n\};", text, re.DOTALL)
    assert m, "page.tsx の MANUAL_SUBMIT_RANKS が見つからない（定義の書き方を変えたら本テストも追随させること）"
    front = set(re.findall(r'key:\s*"([^"]+)"', m.group(1)))
    assert front, "MANUAL_SUBMIT_RANKS から key を1件も抽出できなかった"

    allowed = set(_MANUAL_RANK_KEYS)
    assert front <= allowed, (
        f"フロントが backend の許可外ランクを選ばせている: {sorted(front - allowed)}。"
        " 送信すると必ず 400 になる。backend の _MANUAL_RANK_KEYS へ足すか、"
        " フロントの選択肢から外すこと")

    # 型（ManualKeirinRankKey）も同じ集合であること
    api = _read("lib/api.ts")
    tm = re.search(r"export type ManualKeirinRankKey\s*=\s*([^;]+);", api)
    assert tm, "api.ts の ManualKeirinRankKey が見つからない"
    typed = set(re.findall(r'"([^"]+)"', tm.group(1)))
    assert typed == allowed, (
        f"ManualKeirinRankKey({sorted(typed)}) が backend の "
        f"_MANUAL_RANK_KEYS({sorted(allowed)}) と一致しない")
