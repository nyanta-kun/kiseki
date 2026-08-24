"""取り消したレースの「そのまま売っていたら」サマリーの規則を固定する（2026-08-24）。

ユーザー要望「レビューページのサマリー表示の下に、入稿したが取り消したレースを
そのまま入稿していたらのサマリーを追加して」（母集団は**取り消したレースのみ**）。

🔴 **実績ではない。** 売っていないので netkeirin の成績にも上の `summary` にも
   入らない。落とした判断が正しかったかを見るための参考値。
   既存の約束（`winning_combos` の項）と同じ扱いで、**文言で必ず区別する**。

ここで固定するのは4つ:
  1. 採点は**実績と同じ経路**（`_fetch_settled_submissions`＝確定オッズ）を通ること
  2. 母集団が**取消のみ**（`deleted_at IS NOT NULL`）であること
  3. 実績サマリーの母集団に取消が**混ざらない**こと
  4. 画面が参考値だと分かる見出しを必ず出すこと
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ROUTER = (ROOT / "backend" / "src" / "api" / "keirin_router.py").read_text("utf-8")


def test_取消のみを採点する口がある():
    assert "deleted_only: bool = False" in ROUTER, "取消のみの引数が無い"
    m = re.search(r'deleted_cond = \("([^"]+)" if deleted_only\s*\n?\s*else "([^"]+)"\)',
                  ROUTER)
    assert m, "deleted_cond の定義が見つからない"
    assert m.group(1) == "ns.deleted_at IS NOT NULL", "取消のみの条件が違う"
    assert m.group(2) == "ns.deleted_at IS NULL", "既定が『売った分だけ』でない"


def test_採点は実績と同じ経路を通る():
    """🔴 画面で `bet_detail` のオッズから計算しない。実績は確定オッズ採点なので
       別経路にすると2つの表が別の作り方の数字になる。

    ⚠️ 2026-08-24 に取得位置を item ループの前へ移した（行へも配るため）。
       位置に依存しない形で、**同じ変数がサマリーの元になっている**ことを見る。
    """
    assert "deleted_only=True)" in ROUTER, "取消のみの採点を呼んでいない"
    assert 'c_bet = sum(x["bet"] for x in cancelled_settled)' in ROUTER, \
        "サマリーが同じ採点結果から作られていない"


def test_実績サマリーには取消が混ざらない():
    """🔴 `sold` は submitted / published だけ。取消を足す口を作らない。"""
    m = re.search(r"sold = \[x for x in items if x\[.status.\] in \(([^)]+)\)\]", ROUTER)
    assert m, "実績サマリーの母集団が見つからない"
    assert "DELETED" not in m.group(1).upper()


def test_APIが両方を返す():
    assert '"summary": summary,' in ROUTER
    assert '"summary_cancelled": summary_cancelled,' in ROUTER


def test_画面が参考値だと分かる見出しを出す():
    tsx = (ROOT / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text("utf-8")
    assert "summaryCancelled" in tsx, "画面が受け取っていない"
    assert "参考値" in tsx, "参考値だと分かる文言が無い"
    assert "実績には含みません" in tsx, "実績と区別する文言が無い"
    # 🔴 見出しは caption で必ず出す（同じ形の表が2つ並ぶため）
    assert "caption" in tsx


def test_取消サマリーの未確定数は実数を返す():
    """🔴 当初 0 固定だったのを 2026-08-24 に是正した。

    画面が**常時表示**になったことで、「取消14件のうち確定2件」が
    「予想数 2レース」としか出ず**残り12件が消えたように見える**問題があった。
    """
    assert '"n_pending": 0,' not in ROUTER, "未確定数がまだ 0 固定のまま"
    assert "n_cancelled = sum(1 for x in items" in ROUTER, "取消の総数を数えていない"
    assert "max(0, n_cancelled - len(cancelled_settled))" in ROUTER


def test_両方のサマリーを常時表示する():
    """🔴 確定0件でも隠さない（2026-08-24・ユーザー要望）。

    以前は `n_races > 0` で隠していたため、朝は売った分が未確定で
    **取消サマリーだけが出て「それが実績」と読める**状態になっていた。
    """
    tsx = (ROOT / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text("utf-8")
    assert "summary.n_races > 0 &&" not in tsx, "実績サマリーを件数で隠している"
    assert "summaryCancelled.n_races > 0 &&" not in tsx, "取消サマリーを件数で隠している"
    # 🔴 2枚並ぶので**両方に見出し**が要る（無名だと残った1枚が実績と読まれる）
    assert 'caption="売った分（実績）"' in tsx, "実績側の見出しが無い"
    assert "参考値・実績には含みません" in tsx, "取消側の見出しが無い"


def test_取消行にも確定成績を配る():
    """🔴 サマリーが「N レース的中」と言うのに、一覧のどれか分からなかった。

    取消行は `result` が付かない設計（実績に混ぜないため）なので、
    **別キー `result_if_sold`** で同じ採点結果を配る。
    """
    assert '"result_if_sold"' in ROUTER, "取消行へ結果を配っていない"
    assert "by_key_cancelled" in ROUTER
    # 🔴 `result` には入れない（実績＝netkeirin の成績とサマリーの元）
    assert 'it["result"] = None if got is None' in ROUTER, "実績の付け方が変わった"


def test_取消の採点は一度だけ取る():
    """⚠️ 行への配布とサマリーで二重に取ると、同じクエリが2回走る。"""
    assert ROUTER.count("deleted_only=True)") == 1, "取消の採点を2回取っている"


def test_カードの参考値は確定オッズを優先する():
    """🔴 カードとサマリーの基準を揃える（実測 16,910円 ↔ 20,710円 の食い違い）。"""
    tsx = (ROOT / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text("utf-8")
    fn = tsx[tsx.index("const hypothetical = useMemo("):]
    fn = fn[:fn.index("}, [")]
    assert "p.result_if_sold" in fn, "確定オッズの採点を使っていない"
    # 未確定のあいだのフォールバックは残す（確定したら切り替わる）
    assert "line.odds" in fn, "未確定時のフォールバックが消えている"


def test_ページが受け渡している():
    page = (ROOT / "frontend" / "src" / "app" / "keirin" / "review"
            / "page.tsx").read_text("utf-8")
    assert "summaryCancelled={proposals.summary_cancelled}" in page
