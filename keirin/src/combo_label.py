"""`picks_history.pred_combo` の解釈と表示整形（2026-08-14 新設）。

## なぜ要るか

`pred_combo` には**2つの書き方**が混在している。

    畳んだ形   5=1-2,3,4                 軸2車 + 相手列（7S系・7C・9C）
    畳んだ形   三単:5-2-3,4,7            1着・2着固定 + 3着列（7T1・7C の三連単切替）
    展開形     三複:2=5=7,2=5=6,… / 三単:5-2-3,5-2-4,…   1点ずつ（7H2）
    展開形     三単:7-3-1,7-3-5,…                       1点ずつ（7H1・9H1）

Discord の確定通知はこれを**そのまま出していた**ため、7H1/7H2 だけが
1点ずつの羅列になっていた（ユーザー指摘 2026-08-14）。

🔴 **さらに的中判定も壊れていた。** 通知側は「畳んだ形」だけを想定した
   自前パースをしており、展開形を渡すと軸と相手を取り違える。実データで
   `三複:2=5=7,…` に対し **`❌ 不的中（軸3/2）`**（2車のはずが3車）と表示していた
   ＝三連複が当たっていても外れとして通知していた。表示の問題ではない。

ここは**解釈の単一正本**。通知もWebも同じ規則で読むようにする。
"""
from __future__ import annotations

import re
from collections import OrderedDict

#: 券種の接頭辞 → 内部種別
_KIND_PREFIX = {"三複": "trio", "三単": "trifecta"}

#: 「a=b=c」「a-b-c」など、3車が並んだ1点
_POINT = re.compile(r"^\d+[-=]\d+[-=]\d+$")

#: `bet_detail.lines[].bet_type` → 内部種別。`src/sold_performance._ORDERED` と同じ値。
_BET_TYPE_KIND = {"3連複": "trio", "3連単": "trifecta"}


def _numbers(token: str) -> list[int]:
    return [int(x) for x in re.split(r"[-=]", token) if x.isdigit()]


def parse_pred_combo(text: str | None) -> list[tuple[str, list[tuple[int, ...]]]]:
    """`pred_combo` を [(券種, [買い目, …]), …] へ分解する。

    買い目は三連複なら**車番昇順のタプル**、三連単なら**着順のタプル**。
    解釈できない断片は無視する（通知を落とさない）。

    >>> parse_pred_combo("5=1-2,3")
    [('trio', [(1, 2, 5), (1, 3, 5)])]
    >>> parse_pred_combo("三単:5-2-3,4")
    [('trifecta', [(5, 2, 3), (5, 2, 4)])]
    """
    if not text:
        return []
    out: list[tuple[str, list[tuple[int, ...]]]] = []
    for seg in str(text).split("/"):
        seg = seg.strip()
        if not seg:
            continue
        kind = None
        if ":" in seg:
            prefix, _, rest = seg.partition(":")
            kind = _KIND_PREFIX.get(prefix.strip())
            if kind is not None:
                seg = rest.strip()
        # 補助情報（"(axis_sum=1.5)" 等）は落とす
        seg = re.sub(r"\([^)]*\)", "", seg).strip()
        if not seg:
            continue
        tokens = [t.strip() for t in seg.split(",") if t.strip()]
        if not tokens:
            continue

        if all(_POINT.match(t) for t in tokens):
            # 展開形（1点ずつ）。券種が未指定なら区切り文字から決める。
            if kind is None:
                kind = "trio" if "=" in tokens[0] else "trifecta"
            combos = []
            for t in tokens:
                nums = _numbers(t)
                if len(nums) == 3:
                    combos.append(tuple(sorted(nums)) if kind == "trio" else tuple(nums))
        else:
            # 畳んだ形。先頭トークンに軸、以降が相手。
            head = _numbers(tokens[0])
            if len(head) < 3:
                continue
            if kind is None:
                kind = "trio" if "=" in tokens[0] else "trifecta"
            a1, a2 = head[0], head[1]
            legs = [head[2]] + [int(t) for t in tokens[1:] if t.isdigit()]
            combos = [tuple(sorted((a1, a2, x))) if kind == "trio" else (a1, a2, x)
                      for x in legs]
        if combos:
            out.append((kind, combos))
    return out


def _fmt_trio(combos: list[tuple[int, ...]]) -> str:
    """全点に共通する2車があれば `a=b=c,d,e` へ畳む。無ければ列挙のまま。"""
    counts: dict[int, int] = {}
    for c in combos:
        for car in set(c):
            counts[car] = counts.get(car, 0) + 1
    common = sorted(car for car, n in counts.items() if n == len(combos))
    if len(common) != 2:
        return ",".join("=".join(map(str, c)) for c in combos)
    a1, a2 = common
    thirds = [next((x for x in c if x != a1 and x != a2), None) for c in combos]
    if any(t is None for t in thirds):
        return ",".join("=".join(map(str, c)) for c in combos)
    return f"{a1}={a2}=" + ",".join(str(t) for t in thirds)


def _fmt_trifecta(combos: list[tuple[int, ...]]) -> str:
    """1着・2着が同じ点をまとめて `a-b-c,d,e` にする。

    ⚠️ **着順に意味があるので共通2車が入れ替わる形は畳まない**。
       1着・2着ごとにグループ化し、グループ単位で畳む
       （7H1 の 8点は 2グループ＝2つのフォーメーションに畳める）。
    """
    groups: "OrderedDict[tuple[int, int], list[int]]" = OrderedDict()
    for c in combos:
        groups.setdefault((c[0], c[1]), []).append(c[2])
    return " ".join(f"{a1}-{a2}-" + ",".join(map(str, ts))
                    for (a1, a2), ts in groups.items())


def format_pred_combo(text: str | None, *, labels: bool = True) -> str:
    """`pred_combo` を**まとめた表示文字列**にする（解釈できなければ原文）。

    labels=False で `三複:` / `三単:` の接頭辞を落とす。
    🔴 **区切り文字が券種を表している**（三連複 `=` / 三連単 `-`）ので、
       表記が統一されていれば接頭辞は冗長（2026-08-21 ユーザー方針）。
       ⚠️ 落としてよいのは畳み方が上の規則に従っているときだけ。
          原文をそのまま返す経路（解釈不能）では券種が判らないので、
          `labels=False` でも原文を触らない。

    >>> format_pred_combo("三複:2=5=7,2=5=6 / 三単:5-2-3,5-2-4")
    '三複:2=5=7,6 / 三単:5-2-3,4'
    >>> format_pred_combo("三単:5-2-3,5-2-4", labels=False)
    '5-2-3,4'
    >>> format_pred_combo("5=1-2,3,4")
    '1=5=2,3,4'
    """
    parsed = parse_pred_combo(text)
    if not parsed:
        return (text or "").strip()
    names = {"trio": "三複", "trifecta": "三単"}
    has_prefix = labels and ":" in str(text)
    parts = []
    for kind, combos in parsed:
        body = _fmt_trio(combos) if kind == "trio" else _fmt_trifecta(combos)
        parts.append(f"{names[kind]}:{body}" if has_prefix else body)
    return " / ".join(parts)


def format_bet_lines(lines: object) -> str:
    """`netkeirin_submissions.bet_detail` の `lines` を pred_combo と同じ表記へ畳む。

    🔴 **売った買い目の正本は bet_detail**。`picks_history.pred_combo` は候補で、
       看板の穴埋めで売ったレースには**そもそも候補行が無い**（買い目が空欄で
       通知されていた）。両者が食い違う場合も、実際に買ったのはこちら。

    >>> format_bet_lines([{"bet_type": "3連複", "combo": "1=3=5"},
    ...                   {"bet_type": "3連複", "combo": "2=3=5"}])
    '3=5=1,2'
    """
    if not isinstance(lines, (list, tuple)):
        return ""
    buckets: "OrderedDict[str, list[tuple[int, ...]]]" = OrderedDict()
    for line in lines:
        if not isinstance(line, dict):
            continue
        kind = _BET_TYPE_KIND.get(str(line.get("bet_type") or "").strip())
        if kind is None:
            continue                       # 未知の券種は畳めない（黙って捨てる）
        nums = _numbers(str(line.get("combo") or ""))
        if len(nums) != 3:
            continue
        combo = tuple(sorted(nums)) if kind == "trio" else tuple(nums)
        got = buckets.setdefault(kind, [])
        if combo not in got:
            got.append(combo)
    parts = [_fmt_trio(c) if k == "trio" else _fmt_trifecta(c)
             for k, c in buckets.items()]
    return " / ".join(parts)


def is_hit(text: str | None, order3: tuple[int, ...]) -> bool | None:
    """確定した上位3車（着順）に対して買い目が当たっているか。

    order3: (1着, 2着, 3着)。3車揃っていなければ None（判定不能）。

    🔴 三連単は**着順まで一致**して初めて的中。三連複は順不同。
       2券種（7H2）は**どちらかが当たれば的中**として扱う。
    """
    if len(order3) < 3:
        return None
    parsed = parse_pred_combo(text)
    if not parsed:
        return None
    top3 = tuple(sorted(order3[:3]))
    exact = tuple(order3[:3])
    for kind, combos in parsed:
        if kind == "trio" and any(c == top3 for c in combos):
            return True
        if kind == "trifecta" and any(c == exact for c in combos):
            return True
    return False


def axis_cars(text: str | None) -> list[int]:
    """買い目の「軸」とみなせる車（全点に共通して現れる車）を返す。

    ⚠️ BOX 買い（7H2 の三連複）には共通車が無いので**空になりうる**。
       「軸n/2」のような表示はこの結果が2車のときだけ出すこと
       （以前は展開形を軸として読み、`軸3/2` という不可能な表示が出ていた）。
    """
    parsed = parse_pred_combo(text)
    if not parsed:
        return []
    combos = [c for _kind, cs in parsed for c in cs]
    if not combos:
        return []
    counts: dict[int, int] = {}
    for c in combos:
        for car in set(c):
            counts[car] = counts.get(car, 0) + 1
    return sorted(car for car, n in counts.items() if n == len(combos))
