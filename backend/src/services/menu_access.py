"""ユーザーごとの表示メニュー（POG / 中央 / 地方 / 競輪）の判定。

**判定の唯一の正本。** DB にも FastAPI にも依存しない純関数だけを置く。
フロント側の写しは `frontend/src/lib/menuAccess.ts`（同じ規則をそのまま
実装している）。**片方だけ変えないこと。**
`backend/tests/test_menu_access.py` が両者の対応を固定している。

## 二段構えになっている理由

| 段 | 関数 | 何をするか |
|---|---|---|
| 保存する値 | `normalize_menu_flags` | 管理者が付けた ON/OFF を規則に合わせて整える |
| 見せる値 | `resolve_menu_access` | 保存値にロール等の事情を足して**実際の可視性**を出す |

分けてあるのは、**管理画面のチェックボックスの状態**（＝保存値）と
**その人に実際に見えるもの**（＝可視性）が一致しないためである。
admin は自分のフラグが OFF でも全部見える（下記）ので、両者を1つの値に
まとめると管理画面が「OFF なのに見えている」を表現できなくなる。

## 規則

1. 🔴 **POG が ON なら中央と地方も必ず ON**（ユーザー指示・2026-09-10）。
   POG は中央・地方の出走と賞金の上に乗っている機能で、順位表から
   `/races/{id}` `/chihou/races/{id}` へ直接リンクしている。POG だけ
   見せると、そのリンクの先が全部 404 になる。
   **保存時にも読み出し時にも掛ける**（正規化を保存側だけに置くと、
   DB を直接更新された行が規則を破ったまま配信される）。
2. **admin はフラグを無視して全部見える**（ユーザー指示・2026-09-10）。
   管理者が自分のフラグを切って締め出されるのを防ぐため。
3. **競輪は当面 admin 限定**（ユーザー指示・2026-09-10）。フラグは保存
   するが可視性には効かせない。`/keirin` 配下は netkeirin（ウマい車券）
   への入稿トリガーと入稿設定の編集を含み、外部サービスへ影響するため。
   開放するときは `KEIRIN_REQUIRES_ADMIN` を False にする（それだけで
   フラグが効くようになる。他は触らなくてよい）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

#: 競輪を admin 限定に据え置くか。False にするとフラグ（`menu_keirin`）が効く。
#: ⚠️ False にする前に、`/keirin/settings` と入稿トリガーを admin 限定へ
#: 分離すること（現状は同じルート配下にある）。
KEIRIN_REQUIRES_ADMIN = True

#: メニューのキー。DB 列名は `menu_` を前置したもの。
MENU_KEYS: tuple[str, ...] = ("pog", "jra", "chihou", "keirin")


@dataclass(frozen=True)
class MenuFlags:
    """4 メニューの ON/OFF。保存値にも可視性にも同じ型を使う。"""

    pog: bool = False
    jra: bool = True
    chihou: bool = True
    keirin: bool = False

    def to_dict(self) -> dict[str, bool]:
        """`{"pog": ..., "jra": ...}` を返す。API レスポンス用。"""
        return asdict(self)


def normalize_menu_flags(flags: MenuFlags) -> MenuFlags:
    """保存する値を規則に合わせて整える。

    POG が ON なら中央・地方を ON へ引き上げる（規則 1）。
    それ以外は与えられた値をそのまま通す。
    """
    if flags.pog:
        return MenuFlags(pog=True, jra=True, chihou=True, keirin=flags.keirin)
    return flags


def resolve_menu_access(role: str, flags: MenuFlags) -> MenuFlags:
    """その人に**実際に見えるもの**を返す。

    Args:
        role: `keiba.users.role`（`admin` / `member`）。
        flags: DB に入っている保存値。

    Returns:
        ナビの表示とルートガードの両方に使う可視性。
    """
    if role == "admin":
        return MenuFlags(pog=True, jra=True, chihou=True, keirin=True)

    normalized = normalize_menu_flags(flags)
    if KEIRIN_REQUIRES_ADMIN:
        return MenuFlags(
            pog=normalized.pog,
            jra=normalized.jra,
            chihou=normalized.chihou,
            keirin=False,
        )
    return normalized
