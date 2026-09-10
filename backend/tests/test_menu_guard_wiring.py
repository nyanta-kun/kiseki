"""表示メニューのガードとナビの配線が生きていることを固定する。

🔴 **この型の欠落は例外もログも型エラーも出さない。** セクションの
`layout.tsx` を消せばそのセクションは誰でも入れるようになり、ナビ側の
`buildNavItems` を通さない配列を書けばその表示先だけ規則から外れる。
どちらも画面は普通に描かれるので、人が気づくのは「見えないはずのものが
見えている」と言われたときになる。

同じ理由で作られた既存の検査: `test_frontend_display_flags_reachable.py`
（算出しているのに描画先が無い、を静的に捕まえる）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: セクションのディレクトリ → そこを守るべきメニューキー。
#: `/keirin` はここに無い。`proxy.ts` の admin ロールガードが担当している
#: （競輪は当面 admin 限定・`services/menu_access.KEIRIN_REQUIRES_ADMIN`）。
GUARDED_SECTIONS = {
    "races": "jra",
    "results": "jra",
    "yoso": "jra",
    "chihou": "chihou",
    "pog": "pog",
}


@pytest.mark.parametrize(("section", "key"), sorted(GUARDED_SECTIONS.items()))
def test_section_layout_guards_the_menu(section: str, key: str) -> None:
    """各セクションの layout.tsx が `requireMenu` を正しいキーで呼ぶ。

    ナビからリンクを消すだけでは URL 直打ちを止められない。実効ガードはここ。
    """
    layout = FRONTEND / "app" / section / "layout.tsx"
    assert layout.exists(), (
        f"frontend/src/app/{section}/layout.tsx が無い。"
        "消すとそのセクションは誰でも入れるようになる"
    )
    src = layout.read_text(encoding="utf-8")
    assert f'requireMenu("{key}")' in src, (
        f"{section}/layout.tsx が requireMenu(\"{key}\") を呼んでいない"
    )


def test_keirin_is_still_guarded_by_role() -> None:
    """競輪は `proxy.ts` のロールガードが守っている（メニュー側には無い）。

    ⚠️ `KEIRIN_REQUIRES_ADMIN` を False にするときは、このガードを
    `requireMenu("keirin")` へ置き換えること。
    """
    src = (FRONTEND / "proxy.ts").read_text(encoding="utf-8")
    assert 'pathname.startsWith("/keirin")' in src
    assert 'token.role !== "admin"' in src


def test_nav_components_share_one_item_builder() -> None:
    """3 つの表示先がすべて `buildNavItems` を通る。

    2026-09-10 まで 3 か所が別々に項目を並べており、**ハンバーガーにだけ
    POG が無かった**。
    """
    for name in ("AppNav.tsx", "HamburgerMenu.tsx", "BottomNav.tsx"):
        src = (FRONTEND / "components" / name).read_text(encoding="utf-8")
        assert "buildNavItems" in src, f"{name} が buildNavItems を使っていない"


def test_root_layout_passes_access_to_both_navs() -> None:
    """ルートレイアウトがヘッダとボトムナビの両方へ可視性を渡す。

    片方だけだと、渡されなかった側が既定値（中央・地方 ON）を描いてしまい、
    OFF にしたはずのリンクが残る。
    """
    src = (FRONTEND / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "getMenuContext" in src
    for tag in ("SiteHeader", "BottomNav"):
        line = next(ln for ln in src.splitlines() if f"<{tag} " in ln)
        assert "access={access}" in line, f"<{tag}> に access が渡っていない"


def test_menu_guards_do_not_hardcode_races_as_fallback() -> None:
    """ガードの送り先が `/races` 決め打ちでない。

    中央が OFF の人を `/races` へ送るとガードがまた送り返し、リダイレクトが
    循環する。送り先は `landingPath()` が決める。
    """
    src = (FRONTEND / "lib" / "menu.ts").read_text(encoding="utf-8")
    assert "landingPath(" in src
    assert 'redirect("/races")' not in src
