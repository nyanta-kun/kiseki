"""OCR 結果 → 平八の印テーブル → DB 照合で馬番確定（純粋ロジック）。

`ocr.swift` が吐く「x, y, w, h, テキスト」の行を受け取り、表の列（x 帯）と
行（y クラスタ）に分解して ◎/○/▲/☆ の馬名を取り出し、DB の出走表と
馬名で照合してレースと馬番を確定させる。

呼び出し側は `match_marks.py`。詳しくは同ディレクトリの README.md を参照。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

# 表の列位置（画像幅に対する比）。◎/○/▲/☆ の馬名がこの x 帯に入る。
# 馬番も同じ帯の左端に出るが、OCR の数字は信用しないので捨てる（README 参照）。
MARK_BANDS = [("◎", 0.10, 0.30), ("○", 0.315, 0.49), ("▲", 0.525, 0.70), ("☆", 0.735, 0.93)]

# 見出し・凡例の帯。ここより上は表本体ではない。
BODY_TOP = 0.175
# 同じ行とみなす y の差
ROW_TOLERANCE = 0.011
# 馬名として扱う最小文字数（これ未満は馬番・記号とみなして捨てる）
MIN_NAME_LEN = 3
# 1行として採用する最小の印数（3印以上読めていれば行として扱う）
MIN_MARKS_PER_ROW = 3
# レース割り当てを採用する最低一致度（読めた印の平均）
MIN_RACE_SCORE = 0.72


def norm(s: str) -> str:
    """馬名の比較用に正規化する（カタカナだけ残す）。"""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[^ァ-ヶー]", "", s)


def load_ocr(path: str) -> list[dict[str, Any]]:
    """`ocr.swift` の出力（TSV）を読む。y はボックス中心に直す。"""
    toks: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) != 5:
                continue
            x, y, _w, h, text = float(p[0]), float(p[1]), float(p[2]), float(p[3]), p[4]
            toks.append({"x": x, "y": y + h / 2, "t": text.strip()})
    return toks


def rows_from(toks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """表の1行 = {印: 馬名} に畳む。"""
    body = [t for t in toks if t["y"] > BODY_TOP]
    body.sort(key=lambda t: t["y"])

    rows: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for t in body:
        if cur and abs(t["y"] - cur[-1]["y"]) > ROW_TOLERANCE:
            rows.append(cur)
            cur = []
        cur.append(t)
    if cur:
        rows.append(cur)

    out: list[dict[str, str]] = []
    for row in rows:
        rec: dict[str, str] = {}
        for t in row:
            name = norm(t["t"])
            if len(name) < MIN_NAME_LEN:
                continue
            for mark, lo, hi in MARK_BANDS:
                if lo <= t["x"] < hi:
                    # 同じ帯に複数読めたら長いほう（分割された断片を拾わない）
                    if mark not in rec or len(name) > len(rec[mark]):
                        rec[mark] = name
                    break
        if len(rec) >= MIN_MARKS_PER_ROW:
            out.append(rec)
    return out


def match(rows: list[dict[str, str]], races: dict) -> list[dict[str, Any]]:
    """各行を馬名でレースに割り当て、馬番を DB 側から確定させる。

    Args:
        rows: `rows_from()` の出力。
        races: {(競馬場, R): [(馬番, 馬名), ...]}

    Returns:
        [{"course", "race", "score", "marks": {印: {"no", "name", "sim"}}}, ...]
    """
    result: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()

    for rec in rows:
        best: tuple[float, tuple[str, int], dict[str, tuple[int, str, float]]] | None = None
        for race_key, entries in races.items():
            names = [norm(n) for _, n in entries]
            score = 0.0
            picks: dict[str, tuple[int, str, float]] = {}
            for mark, ocr_name in rec.items():
                sim, i = max(
                    (difflib.SequenceMatcher(None, ocr_name, n).ratio(), i)
                    for i, n in enumerate(names)
                )
                score += sim
                picks[mark] = (entries[i][0], entries[i][1], round(sim, 2))
            score /= len(rec)
            if best is None or score > best[0]:
                best = (score, race_key, picks)

        if best is not None and best[0] >= MIN_RACE_SCORE and best[1] not in used:
            used.add(best[1])
            result.append({
                "course": best[1][0],
                "race": best[1][1],
                "score": round(best[0], 3),
                "marks": {
                    mark: {"no": v[0], "name": v[1], "sim": v[2]}
                    for mark, v in best[2].items()
                },
            })
    return result
