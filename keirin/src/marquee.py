"""看板レース（売上が集まりやすいレース）の検出（2026-08-09 新設）。

## なぜ要るのか

2026-08-08 のレース単位分析で、**当日売上 5,060pt の 84% が「外れたレース」に
集中**していた。売れたのは全て看板レース（決勝・特選クラス）で、逆に高配当を
返した準決勝・予選は買い手0。「売れた時に当たらず、当たり出した時に売れていない」。

ユーザー決定（2026-08-09）: **看板レースとその前後には必ず推奨を出す**。
目的関数は売上加重の的中率（ROI悪化は許容）。

## 判定の正本は kiseki 側（2026-08-11 一本化）

    backend/src/services/keirin_marquee.py

看板判定は **自動入稿（このリポジトリ）と Web一覧の★表示**の2箇所で要る。
keirin が別リポジトリだった間はキーワードを**両方に写して**いたが、
kiseki へ統合されたのでファイルから直接読み込む形にした。
**ここでキーワードを定義してはいけない**（写した瞬間に
「★は付くのに入稿されない」またはその逆を作れる）。
`tests/test_marquee.py::test_keywords_are_not_redefined_here` が機械的に禁じている。

判定そのものの注意点（準決勝の部分一致・レース番号で判定しない等）は
正本の docstring を見ること。

## このモジュール固有の責務

    前後 : 看板レースの前後への展開（同一開催）— `marquee_race_nos()`

Web は★を付けるだけなので「前後」は要らず、入稿側だけが使う。
⚠️ **2026-08-21 から前後には広げない**（`NEIGHBOR_SPAN = 0`）。看板本体だけを
   埋める。理由と戻し方はその定数のコメント。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# keirin/src/marquee.py → parents[2] が kiseki のリポジトリルート。
_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_marquee.py")

_MODULE_NAME = "kiseki_keirin_marquee"


def _load_canonical() -> ModuleType:
    """kiseki 側の正本をファイルから読み込む。

    ⚠️ `sys.path` に `backend/` を足す方式は使えない。keirin にも `src`
       パッケージがあり **名前が衝突する**ため。正本は標準ライブラリ以外を
       import しないので、ファイル指定の読み込みで安全に共有できる。

    ⚠️ 見つからないときは**黙って自前定義へ落ちない**。フォールバックは
       二重管理を静かに復活させ、ずれても誰も気づけない。
    """
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"看板レース判定の正本が見つかりません: {_CANONICAL}\n"
            "keirin は kiseki リポジトリ内（<kiseki>/keirin）で動かす前提です。"
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CANONICAL)
    if spec is None or spec.loader is None:            # pragma: no cover - 実質起きない
        raise ImportError(f"正本を読み込めません: {_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_canonical = _load_canonical()

MARQUEE_KEYWORDS = _canonical.MARQUEE_KEYWORDS
MARQUEE_EXCLUDE = _canonical.MARQUEE_EXCLUDE
BIG_EVENT_KEYWORDS = _canonical.BIG_EVENT_KEYWORDS

# 正本の関数をそのまま束縛する（ラップし直すと分岐が生まれるため）。
# kiseki 側の名前は `is_marquee_race`、keirin 側の呼び出し名は `is_marquee_type`。
is_marquee_type = _canonical.is_marquee_race
is_big_event_type = _canonical.is_big_event_race
# 🔴 穴埋めの対象判定はこれを使う（看板 or 大会の予選）。
#    `is_marquee_type` を直接見ると大会の予選が漏れる。
is_fill_target = _canonical.is_fill_target


# 看板レースの前後に何レースぶん広げるか（0 = 広げない）。
#
# 🔴 **2026-08-21 に 1 → 0 へ変更**（ユーザー判断）。2026-08-09 の
#    「看板レースとその前後には必ず推奨を出す」方針のうち、**前後だけを畳む**。
#    看板本体には従来どおり必ず出す。
#
# 理由は件数。入稿は「ランクのゲートを通ったもの + 看板穴埋め」の和で、
# **穴埋めはゲートを通らない**（売れる開催に商品を絶やさないための経路）。
# 実測（2026-08-14〜21）でランク 27.7件/日 に対し穴埋めが 17.4件/日 あり、
# 合計 45件/日 は目標の 30〜40 に収まらない。2026-08-21 の内訳では
# 穴埋め17件のうち **6件が前後の展開**（一般・選抜・予選）だった。
#
# ⚠️ 前後は**看板そのものではない**。売上が集まるのは決勝・特選クラス本体で、
#    隣接レースはその「ついで」に出していたもの。削るならここが先という判断。
#
# 戻すときはこの値を 1 に戻すだけでよい（`tests/test_marquee.py` が
# 展開の有無を固定しているので、値と検査が食い違ったままにはならない）。
NEIGHBOR_SPAN = 0


def marquee_race_nos(races: list[dict]) -> set[int]:
    """同一開催のレース一覧から、**穴埋め対象**のレース番号を返す。

    races: [{"race_no": int, "race_type": str|None, "cup_grade": int|None}, …]
      （同一開催ぶん）

    🔴 **グレードがキーワードより優先**（2026-08-14・ユーザー判断）。
       開催グレードが GIII 以上なら**その開催の全レース**を対象にする。
       グレードは開催の属性なので、`races` のどれか1つでも値を持っていれば
       それを開催のグレードとして使う（レースごとに違う値にはならない）。

    🔴 グレードが取れないとき（NULL＝2026-08-14 より前のレース等）は、
       **看板 または 大会の予選**（`is_fill_target`）を対象にする。

    ⚠️ 前後への展開は `NEIGHBOR_SPAN`（既定 0 ＝ 展開しない・2026-08-21）。
       広げる場合も**存在しないレース番号は返さない**（欠番・最終Rの次）。
       グレードで全レースが対象になる場合は展開自体が不要（既に全部入っている）。
    """
    present = {int(r["race_no"]) for r in races if r.get("race_no") is not None}
    grades = [int(r["cup_grade"]) for r in races if r.get("cup_grade") is not None]
    cup_grade = max(grades) if grades else None
    if cup_grade is not None and is_fill_target(None, cup_grade):
        return present
    marquee = {int(r["race_no"]) for r in races
               if r.get("race_no") is not None and is_fill_target(r.get("race_type"))}
    out = set(marquee)
    for n in marquee:
        for d in range(1, NEIGHBOR_SPAN + 1):
            out |= {n - d, n + d}
    return out & present
