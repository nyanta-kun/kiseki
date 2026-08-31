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
KEIRIN_STRATEGY = (Path(__file__).resolve().parents[2]
                   / "keirin" / "src" / "strategy_wt.py")

#: 表示ラベル（例: "7SS"/"7H1"）の集合。これが正本。
LABELS = set(_PAPER_RANK_LABELS.values())


def test_labels_match_keirin_current_paper_ranks():
    """**上流の単一正本と一致すること**（2026-08-12 追加）。

    `_PAPER_RANK_LABELS` は keirin 側 `CURRENT_PAPER_RANKS` の手動複製で、
    このファイルの他のテストは全て「`_PAPER_RANK_LABELS` にあるものが
    フロントにもあるか」だけを見ている。つまり **複製し忘れたランクは
    どのテストからも要求されず、静かに Web から消える**。

    実際 2026-08-12 に RANK_7H3 を新設したとき、keirin 側は完結していたのに
    kiseki 側の複製を忘れ、**入稿設定画面に 7H3 が出ない**まま気づけなかった
    （DB に行はあるのに画面のリストで捨てられていた）。ここで上流と突き合わせる。

    ⚠️ keirin は import できない（別 venv・`src` パッケージ名の衝突）ので
       ソースを読んで正規表現で拾う。書き方が変わったら落ちるが、
       黙って消えるより落ちるほうがよい。
    """
    if not KEIRIN_STRATEGY.exists():        # keirin を含まないチェックアウト
        pytest.skip(f"keirin が見つかりません: {KEIRIN_STRATEGY}")
    text = KEIRIN_STRATEGY.read_text(encoding="utf-8")
    block = re.search(
        r"CURRENT_PAPER_RANKS: tuple\[PaperRankSpec, \.\.\.\] = \((.*?)\n\)",
        text, re.DOTALL)
    assert block, (
        "keirin 側 CURRENT_PAPER_RANKS の宣言を見つけられなかった。"
        "書き方が変わったならこのテストのパターンも更新すること。")
    upstream = set(re.findall(r'PaperRankSpec\(\s*"[^"]+",\s*"#[^"]+",\s*"([^"]+)"',
                              block.group(1)))
    assert upstream, "CURRENT_PAPER_RANKS から表示ラベルを1つも拾えなかった"
    assert upstream == LABELS, (
        "keirin 側 CURRENT_PAPER_RANKS と kiseki 側 _PAPER_RANK_LABELS が食い違う。\n"
        f"  keirin にあって kiseki に無い: {sorted(upstream - LABELS)}\n"
        f"  kiseki にあって keirin に無い: {sorted(LABELS - upstream)}")


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


def _ordered(fragment: str) -> list[str]:
    """宣言の中の表示ラベルを**出現順**で拾う（順序を検査するため）。"""
    return [s for s in re.findall(r'"([^"]+)"', fragment) if s in LABELS]


def _extract_ordered(rel: str, pattern: str) -> list[str]:
    text = _read(rel)
    matches = re.findall(pattern, text, re.DOTALL)
    assert len(matches) == 1, (
        f"{rel} の宣言 `{pattern}` が {len(matches)} 件マッチした（1件であるべき）")
    return _ordered(matches[0])


def _submit_priority() -> list[str]:
    """keirin 側 `netkeirin_submit_wt.RANK_CONFIGS` の定義順＝入稿の優先順位。"""
    path = KEIRIN_STRATEGY.parent.parent / "scripts" / "netkeirin_submit_wt.py"
    if not path.exists():
        pytest.skip(f"keirin が見つかりません: {path}")
    text = path.read_text(encoding="utf-8")
    block = re.search(r"RANK_CONFIGS[^=]*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert block, "RANK_CONFIGS の宣言を見つけられなかった"
    order = re.findall(r'^\s{4}"([0-9A-Z]+)":', block.group(1), re.MULTILINE)
    assert order, "RANK_CONFIGS からキーを1件も拾えなかった"
    return order


def test_display_order_is_by_car_count_then_submit_priority():
    """🔴 表示順は「車数（7車→9車）＞入稿の優先順位」であること（2026-08-14）。

    ユーザー指定の並び。以前は定義順が増築のたびに崩れ、7車と9車が
    交互に並んでいた（7SS/7S/7A/9C/7T1/7C）。**入稿の優先順位は
    keirin 側 `RANK_CONFIGS` の定義順が正本**なので、そこから導いて突き合わせる。

    優先順位を入れ替えたのに表示順を直し忘れると、画面の並びが
    「どの商品が先に取るか」を説明しなくなる。
    """
    priority = _submit_priority()
    actual = list(_PAPER_RANK_LABELS.values())
    assert set(actual) == set(priority), (
        "表示ランクと入稿設定のランク集合が食い違う\n"
        f"  表示のみ: {sorted(set(actual) - set(priority))}\n"
        f"  入稿のみ: {sorted(set(priority) - set(actual))}")

    def _cars(label: str) -> int:
        return int(label[0])

    expected = sorted(priority, key=lambda r: (_cars(r), priority.index(r)))
    assert actual == expected, (
        f"表示順が「車数＞優先順位」になっていない。\n"
        f"  期待: {expected}\n  実際: {actual}")


@pytest.mark.parametrize(("rel", "pattern"), [
    ("app/keirin/page.tsx", r"const RANK_ORDER = \[([^\]]*)\]"),
    ("app/keirin/settings/page.tsx",
     r"const RANK_ORDER: LegacyRankKey\[\] =([^;]*);"),
])
def test_frontend_rank_order_matches_backend_order(rel, pattern):
    """🔴 フロントの並びが backend の定義順と**同じ順序**であること。

    既存の検査は集合の包含しか見ておらず、**並びのズレは素通り**していた
    （実際 page.tsx と backend で 7C / 7T1 の前後が入れ替わっていた）。
    """
    assert _extract_ordered(rel, pattern) == list(_PAPER_RANK_LABELS.values()), \
        f"{rel} の RANK_ORDER が backend の定義順と違う"


def test_help_page_rank_order_matches_backend_order():
    """ヘルプのカード順も同じ並びに保つ（説明を探す位置が画面ごとに変わらないように）。"""
    text = _read("app/keirin/help/page.tsx")
    order = [k for k in re.findall(r'^\s*key: "([^"]+)",', text, re.MULTILINE)
             if k in LABELS]
    assert order == list(_PAPER_RANK_LABELS.values()), \
        "help/page.tsx のカード順が backend の定義順と違う"


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
        r"const RANK_ORDER: LegacyRankKey\[\] =([^;]*);",
    )
    assert LABELS <= found, \
        f"settings/page.tsx の RANK_ORDER に不足: {sorted(LABELS - found)}"


def test_keirin_page_rank_order_covers_all_labels():
    """トップページのサマリー「ランク別」展開の並び。

    ⚠️ 2026-08-28〜: 型ラボのプランも**ここに載せる**（`visible_rank_labels` が
       返すので、無いと「ランク別」に出ない）。許容集合は既存ランク＋型ラボ。
    """
    allowed = LABELS | _type_lab_labels()
    found = _extract("app/keirin/page.tsx", r"const RANK_ORDER = \[([^\]]*)\]")
    assert allowed <= found, f"keirin/page.tsx の RANK_ORDER に不足: {sorted(allowed - found)}"
    assert found <= allowed, \
        f"keirin/page.tsx の RANK_ORDER に未知のランク: {sorted(found - allowed)}"


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


# ── 廃止済みだが実際に売ったランク（2026-08-16 追加）─────────────────
#
# 🔴 実害: 2026-08-14 に 7A を RANK_7S へ統合した直後から、`netkeirin_submissions`
#    の 7A(137件)・7SS(31件)・9A(13件)・9S(1件) = **182件の実入稿が全部「非」バッジ**
#    になっていた（例: 2026-08-14 岐阜1R `20260814_43_01`）。
#
#    機序: 入稿だけの行（ゲート未通過・看板の穴埋め）は SQL が
#    `'RANK_' || ns.rank_key` で rank を合成するため allowlist を通らず
#    `_display_rank()` へ到達する。そこで未知だと**内部名がそのまま画面へ漏れ**、
#    フロントの `RANK_STYLE` に無いので「非」になる。
#
#    ⚠️ 「売っていないランクだから落とす」では直らない。**実際に売った商品**
#       なので一覧にも成績にも出す必要がある（2026-08-11 の方針）。

_HISTORICAL_SUBMISSION_RANK_KEYS = (
    # `netkeirin_submissions.rank_key` に実在する値（現行 + 廃止済み）。
    # 入稿したことがあるランクを消す運用は無いので、増えることはあっても減らない。
    "7S", "7C", "7B", "7T1", "7H1", "7H2", "9C", "9H1",   # 現行
    "7A", "7SS", "9A", "9S",                              # 廃止済み（売った実績あり）
)


def test_display_rank_never_leaks_internal_names():
    """🔴 `_display_rank()` が内部名（`RANK_*`）を画面へ返さないこと。

    返した瞬間フロントの `RANK_STYLE` から外れて「非」になる。
    """
    from src.api.keirin_router import _display_rank

    for key in _HISTORICAL_SUBMISSION_RANK_KEYS:
        got = _display_rank(f"RANK_{key}")
        assert not got.startswith("RANK_"), (
            f"rank_key={key} の表示名が内部名のまま漏れています（{got}）。"
            "backend の _LEGACY_RANK_LABELS に追加してください。")


def test_every_submitted_rank_has_a_frontend_badge():
    """入稿しうる全ランクが、フロントの `RANK_STYLE` に載っていること。

    載っていないと「非」バッジになり、当日のサマリー上でもランク不明として並ぶ。
    """
    from src.api.keirin_router import _display_rank

    styles = _extract(
        "app/keirin/page.tsx",
        r"const RANK_STYLE: Record<string, \{ bg: string; text: string; label: string \}> = \{(.*?)\n\};",
    )
    missing = sorted({_display_rank(f"RANK_{k}") for k in _HISTORICAL_SUBMISSION_RANK_KEYS}
                     - styles)
    assert not missing, (
        f"frontend の RANK_STYLE に無い表示ランク: {missing}（「非」バッジになります）")


def test_legacy_labels_are_not_in_the_allowlist():
    """🔴 廃止ランクを `_PAPER_RANK_LABELS` へ足して直してはいけない。

    あちらは `_VALID_PICK_RANKS` / `_RANKS_ALL` を導出しており、足すと
    `picks_history` に残る廃止ランクの行（RANK_9A 1,046件・RANK_9S 179件・
    SEVEN_S1 3件）が**過去日の集計へ遡って混ざる**。表示名だけを与えること。
    """
    from src.api.keirin_router import _LEGACY_RANK_LABELS, _PAPER_RANK_LABELS

    overlap = set(_LEGACY_RANK_LABELS) & set(_PAPER_RANK_LABELS)
    assert not overlap, f"廃止ランクが allowlist にも入っています: {sorted(overlap)}"


def test_legacy_labels_are_not_remapped_to_successors():
    """⚠️ 後継ランクへ寄せない（7A→7S・9A→9C 等にしない）。

    売ったのはその当時の商品なので、寄せると廃止済みランクの成績が
    現行ランクの成績として読まれる。
    """
    from src.api.keirin_router import _LEGACY_RANK_LABELS

    for internal, label in _LEGACY_RANK_LABELS.items():
        assert label not in LABELS, (
            f"{internal} の表示名 {label} が現行ランクと同じです（成績が混ざります）")


# ───────────────────── 型ラボのプラン（2026-08-28〜） ─────────────────────
#
# 🔴 型ラボは `picks_history` に行を持たないので `_PAPER_RANK_LABELS` を通らない。
#    そのぶん**別の場所に写しが増える**ので、ここで機械的に突き合わせる。

def _signboard_labels(src: str) -> set[str]:
    """看板枠として**入稿しうる**プラン。`SIGNBOARD_TYPES` の型だけ。"""
    import re

    m = re.search(r"SIGNBOARD_TYPES: tuple\[str, \.\.\.\] = \(([^)]*)\)", src)
    assert m, "keirin/src/type_lab.py の SIGNBOARD_TYPES を読めない"
    return {f"{t}_sign" for t in re.findall(r'"([^"]+)"', m.group(1))}


def _type_lab_labels() -> set[str]:
    from src.api.keirin_router import TYPE_LAB_RANK_LABELS

    return set(TYPE_LAB_RANK_LABELS.values())


def test_type_lab_labels_match_keirin_sell_plans():
    """🔴 backend の写しが keirin 側の正本と一致すること。

    ずれると「設定画面で ON にできない」「一覧が『非』になる」という形で出る。

    🔴 比べる相手は `SELL_PLANS`（7車の固定集合）**ではなく**入稿しうるプラン全体。
       9車の型F は決勝以外で `F_hit` を売るので（2026-08-30）、`SELL_PLANS` と
       比べると新しい商品が「backend のみ」に見えて偽陽性になる。
    """
    import re

    src = (KEIRIN_STRATEGY.parent / "type_lab.py").read_text(encoding="utf-8")
    m = re.search(r"SELL_PLANS: tuple\[str, \.\.\.\] = \(([^)]*)\)", src)
    assert m, "keirin/src/type_lab.py の SELL_PLANS を読めない"
    canonical = set(re.findall(r'"([^"]+)"', m.group(1)))

    # 型F が売るプラン（決勝＝表・それ以外＝既定）も入稿しうる。
    # 🔴 **2026-08-31 に 9車専用ではなくなった**ので名前が `TYPE_F_SELL_*` に変わった
    #    （旧名は別名として残っているが、値の定義はこちらにしかない）。
    m = re.search(r"TYPE_F_SELL_BY_RACE_TYPE = \{([^}]*)\}", src)
    assert m, "TYPE_F_SELL_BY_RACE_TYPE を読めない"
    canonical |= set(re.findall(r':\s*"([^"]+)"', m.group(1)))
    m = re.search(r'TYPE_F_SELL_DEFAULT = "([^"]+)"', src)
    assert m, "TYPE_F_SELL_DEFAULT を読めない"
    canonical.add(m.group(1))

    # 🔴 **backend 側は看板枠 `{型}_sign` を6型ぶん先回りで持つ**（2026-08-31）。
    #    実際に売るのは keirin 側 `SIGNBOARD_TYPES` の型だけだが、ダイヤルを
    #    回した瞬間に設定画面から消えると「ON にできない」事故になるので、
    #    最初から全部並べてある。**不足は許さない**ので検出力は落ちない。
    labels = _type_lab_labels()
    canonical |= _signboard_labels(src)
    assert canonical <= labels, (
        "TYPE_LAB_RANK_LABELS に keirin 側のプランが足りない\n"
        f"    keirin のみ: {sorted(canonical - labels)}")
    assert all(k in canonical or k.endswith("_sign") for k in labels), (
        "TYPE_LAB_RANK_LABELS に素性の分からないプランがある\n"
        f"    backend のみ: {sorted(labels - canonical)}")


def test_type_lab_plans_are_editable_in_settings():
    """入稿設定の許可リストに入っていること（無いと画面から ON/OFF できない）。"""
    from src.api.keirin_router import NETKEIRIN_RANK_KEYS

    assert _type_lab_labels() <= set(NETKEIRIN_RANK_KEYS)


def test_type_lab_plans_have_a_display_name():
    """`_display_rank` が内部名を漏らさないこと（漏れると「非」バッジになる）。"""
    from src.api.keirin_router import _display_rank

    for label in _type_lab_labels():
        assert _display_rank(f"RANK_{label}") == label


def test_settings_page_lists_every_type_lab_plan():
    """設定画面の型ラボ欄。ここに無いプランは画面から ON/OFF できない。"""
    found = _extract("app/keirin/settings/page.tsx",
                     r"const TYPE_LAB_ORDER: TypeLabRankKey\[\] =([^;]*);")
    assert _type_lab_labels() <= found, \
        f"settings/page.tsx の TYPE_LAB_ORDER に不足: {sorted(_type_lab_labels() - found)}"


def test_keirin_page_has_a_badge_for_every_type_lab_plan():
    """一覧のランクバッジ。抜けるとそのプランだけ内部名が漏れて「非」になる。"""
    found = _extract("app/keirin/page.tsx",
                     r"const RANK_STYLE: Record<string, \{ bg: string; text: string; label: string \}> = \{(.*?)\n\};")
    assert _type_lab_labels() <= found, \
        f"keirin/page.tsx の RANK_STYLE に不足: {sorted(_type_lab_labels() - found)}"


def test_summary_visible_ranks_include_type_lab():
    """🔴 `visible_rank_labels` に型ラボが載ること。

    ここが `_PAPER_RANK_LABELS` だけだと、既存ランクを全部 OFF にした瞬間に
    **空リストが返り、サマリーの「ランク別」展開が丸ごと消える**
    （フロントは `RANK_ORDER.filter(r => allow.includes(r))` で絞るため）。
    """
    import inspect

    from src.api import keirin_router as m

    src = inspect.getsource(m.visible_rank_labels)
    assert "TYPE_LAB_RANK_LABELS" in src, \
        "visible_rank_labels が型ラボを見ていない（ランク別展開が空になる）"


def test_stats_rank_filter_knows_type_lab():
    """🔴 `/stats` の絞り込みが型ラボの名前を知っていること。

    知らないキーは `None`（＝全ランク）へ落ちるので、**全体の数字を
    「A_hit」として出す**（2026-08-05 の 7B の事故と同型）。
    """
    import inspect

    from src.api import keirin_router as m

    src = inspect.getsource(m.get_stats)
    assert "TYPE_LAB_RANK_LABELS" in src, "get_stats の _all_labels に型ラボが無い"


def test_stats_page_rank_filters_include_type_lab():
    """統計ページの絞り込みチップ。backend の許可集合とそろえる。"""
    found = _extract("app/keirin/stats/page.tsx", r"type RankFilter =([^;]*);")
    assert _type_lab_labels() <= found, \
        f"stats/page.tsx の RankFilter に不足: {sorted(_type_lab_labels() - found)}"


def test_keirin_page_summary_maps_include_type_lab():
    """サマリーのラベル／バッジ配色。抜けるとそのプランだけ色無しで出る。"""
    for name, pattern in (
        ("RANK_LABEL", r"const RANK_LABEL: Record<string, string> = \{(.*?)\n\};"),
        ("RANK_BADGE_STYLE", r"const RANK_BADGE_STYLE: Record<string, string> = \{(.*?)\n\};"),
    ):
        found = _extract("app/keirin/page.tsx", pattern)
        assert _type_lab_labels() <= found, \
            f"keirin/page.tsx の {name} に不足: {sorted(_type_lab_labels() - found)}"
