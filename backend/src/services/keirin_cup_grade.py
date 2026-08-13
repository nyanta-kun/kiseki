"""開催グレード（GP / GI / GII / GIII / FI / FII）の判定（2026-08-14 新設）。

## なぜ要るのか

GI/GII/GIII の開催は注目度も売上も桁違いに高い。実測（2026-08-11 松山・
オールスター競輪）で **1レースあたりの有償ptが他会場の 5.0倍**だった。
ところが従来は開催グレードを一切持っておらず（`wt_races.grade` は
**級班**の A級/S級/L級）、GI 開催で商品が出ていないことに気づけなかった
（2026-08-13 松山 6R〜11R が丸ごと無推奨）。

## コードの由来と実測

winticket の `FETCH_KEIRIN_CUP_RACES` / `FETCH_KEIRIN_RACE` の `cup.grade`。
**既に取得しているページの state に入っている**ので追加リクエストは要らない。

実測15開催で対応を確定（2026-08-13〜14）:

    6 GP    ＫＥＩＲＩＮグランプリ２０２５（平塚 2025-12-28）
    5 GI    オールスター競輪 / 読売新聞社杯全日本選抜競輪
    4 GII   サマーナイトフェスティバル（高知 2026-07-17）
    3 GIII  鳳凰賞典レース / 開設７６周年記念金亀杯 / いわき金杯 ほか6件
    2 FI    ＫＥＩＲＩＮライジングスターズ / 喫茶スプーン杯
    1 FII   ウィンチケット杯 / オッズパーク杯 / チャリロト杯 / DMM競輪杯

**数値が大きいほど格上**の単調な体系。

⚠️ `cup.labels` は判定に使わない。GP が `[3,4]`・GI/GII が `[1,4]`・
   GIII が `[4]`・FI/FII が `[2,3]`/`[5,3]`/`[3]`/`[]` と、`3` が GP にも
   FII にも出るため grade と違って単調でない。

⚠️ **標準ライブラリ以外を import しないこと。** keirin は自分の venv から
   このファイルを直接読む（`keirin_marquee.py` と同じ制約）。
"""

from __future__ import annotations

# grade コード → 表示ラベル。実測で確定した対応（上記docstring）。
GRADE_LABELS: dict[int, str] = {
    6: "GP",
    5: "GI",
    4: "GII",
    3: "GIII",
    2: "FI",
    1: "FII",
}

# 「大会」として扱う下限。GIII 以上（記念競輪以上）は売上・注目が別格なので
# FI/FII と分けて扱う（2026-08-14 ユーザー判断）。
BIG_EVENT_MIN_GRADE = 3


def grade_label(grade: int | None) -> str | None:
    """grade コードを表示ラベルへ。未知の値は None。

    🔴 **未知の値を勝手に丸めない。** 新しいグレード体系が入ったときに
       黙って FII 扱いになると、その開催だけ商品が消える。呼び出し側が
       None を見て「不明」と扱えるようにする。
    """
    if grade is None:
        return None
    try:
        return GRADE_LABELS.get(int(grade))
    except (TypeError, ValueError):
        return None


def is_big_event_grade(grade: int | None) -> bool:
    """GIII 以上（記念競輪・GII・GI・GP）か。

    🔴 **未知・欠損は False**（＝通常開催として扱う）。安全側に倒すのは、
       誤って「大会」と判定して穴埋めを大量に出すより、出さないほうが
       戻せるため。ただし**未知の値が来たことは呼び出し側でログに残すこと**。
    """
    if grade is None:
        return False
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return False
    # 未知でも「既知の最大より大きい」なら格上とみなす（新設グレード対策）。
    if g not in GRADE_LABELS and g < max(GRADE_LABELS):
        return False
    return g >= BIG_EVENT_MIN_GRADE


def is_known_grade(grade: int | None) -> bool:
    """対応表にある値か。False ならログに残して対応表を見直す合図。"""
    if grade is None:
        return False
    try:
        return int(grade) in GRADE_LABELS
    except (TypeError, ValueError):
        return False
