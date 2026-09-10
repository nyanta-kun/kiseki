"""表示メニュー（POG / 中央 / 地方 / 競輪）の判定を固定する。

🔴 **規則そのものを潰さないための検査。** どれも「壊しても例外が出ない」型で、
気づけるのは利用者が「見えるはずのものが見えない」と言ってきたときになる。

固定するのは 4 つ:

1. POG が ON なら中央・地方も ON（保存側でも読み出し側でも）
2. admin はフラグを無視して全部見える
3. 競輪は当面 admin 限定（`KEIRIN_REQUIRES_ADMIN`）
4. フロントの写し（`frontend/src/lib/menuAccess.ts`）が同じ規則を持つ
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.services.menu_access import (
    KEIRIN_REQUIRES_ADMIN,
    MENU_KEYS,
    MenuFlags,
    normalize_menu_flags,
    resolve_menu_access,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. POG ⇒ 中央 + 地方
# ---------------------------------------------------------------------------
def test_pog_on_forces_jra_and_chihou() -> None:
    """POG だけ ON にしても中央・地方が立つ。

    立たないと POG の順位表・記録室から張っている `/races/{id}` /
    `/chihou/races/{id}` が全部ガードに弾かれる。
    """
    got = normalize_menu_flags(MenuFlags(pog=True, jra=False, chihou=False))
    assert (got.pog, got.jra, got.chihou) == (True, True, True)


def test_pog_off_does_not_touch_others() -> None:
    """POG が OFF なら中央・地方は与えられたまま（勝手に立てない）。"""
    got = normalize_menu_flags(MenuFlags(pog=False, jra=False, chihou=False))
    assert (got.jra, got.chihou) == (False, False)


def test_pog_does_not_force_keirin() -> None:
    """POG は競輪を巻き込まない（規則は中央・地方だけ）。"""
    assert normalize_menu_flags(MenuFlags(pog=True, keirin=False)).keirin is False


def test_resolve_applies_the_rule_too() -> None:
    """読み出し側でも規則が掛かる。

    DB を直接更新された行が「POG だけ ON」で残っていても配信では直る。
    """
    access = resolve_menu_access(
        "member", MenuFlags(pog=True, jra=False, chihou=False)
    )
    assert (access.pog, access.jra, access.chihou) == (True, True, True)


# ---------------------------------------------------------------------------
# 2. admin は全部見える
# ---------------------------------------------------------------------------
def test_admin_sees_everything_regardless_of_flags() -> None:
    """admin は 4 つとも OFF でも全部見える（自分で自分を締め出さないため）。"""
    access = resolve_menu_access(
        "admin", MenuFlags(pog=False, jra=False, chihou=False, keirin=False)
    )
    assert all(getattr(access, k) for k in MENU_KEYS)


def test_member_with_all_off_sees_nothing() -> None:
    """一般ユーザーは全部 OFF なら本当に全部見えない（admin だけが例外）。"""
    access = resolve_menu_access(
        "member", MenuFlags(pog=False, jra=False, chihou=False, keirin=False)
    )
    assert not any(getattr(access, k) for k in MENU_KEYS)


# ---------------------------------------------------------------------------
# 3. 競輪は admin 限定
# ---------------------------------------------------------------------------
def test_keirin_stays_admin_only_for_members() -> None:
    """フラグが立っていても一般ユーザーには競輪を出さない。

    `/keirin` 配下は netkeirin への入稿トリガーと入稿設定の編集を含む。
    開放するときは `KEIRIN_REQUIRES_ADMIN` を False にする。
    """
    if not KEIRIN_REQUIRES_ADMIN:
        pytest.skip("競輪を開放済み（KEIRIN_REQUIRES_ADMIN=False）")
    assert resolve_menu_access("member", MenuFlags(keirin=True)).keirin is False


def test_keirin_flag_is_still_stored() -> None:
    """可視性に効かなくても保存値としては残る（管理画面のチェック状態）。"""
    assert normalize_menu_flags(MenuFlags(keirin=True)).keirin is True


# ---------------------------------------------------------------------------
# 4. フロントの写しが同じ規則を持つ
# ---------------------------------------------------------------------------
def _front_source() -> str:
    path = REPO_ROOT / "frontend" / "src" / "lib" / "menuAccess.ts"
    assert path.exists(), f"{path} が無い（フロントの写しを消していないか）"
    return path.read_text(encoding="utf-8")


def test_frontend_mirror_has_the_pog_rule() -> None:
    """フロント側の正規化も POG ⇒ 中央 + 地方 を持っている。

    ⚠️ 画面のナビはフロントの写しで描かれる。バックエンドだけ直すと
    「API は正しいのにナビだけ規則を破る」になる。
    """
    src = _front_source()
    assert "normalizeMenuFlags" in src
    # POG が真のときに jra / chihou を true にしている行があること。
    assert re.search(r"pog[\s\S]{0,200}jra:\s*true[\s\S]{0,80}chihou:\s*true", src), (
        "frontend/src/lib/menuAccess.ts に POG⇒中央+地方 の分岐が見当たらない"
    )


def test_frontend_mirror_keeps_the_keirin_switch() -> None:
    """競輪の開放スイッチが両側に在る（片方だけ倒せないようにする）。"""
    src = _front_source()
    assert "KEIRIN_REQUIRES_ADMIN" in src
    front_value = re.search(
        r"KEIRIN_REQUIRES_ADMIN\s*=\s*(true|false)", src
    )
    assert front_value is not None, "KEIRIN_REQUIRES_ADMIN の値が読めない"
    assert (front_value.group(1) == "true") is KEIRIN_REQUIRES_ADMIN, (
        "競輪の開放スイッチがバックエンドとフロントで食い違っている"
    )
