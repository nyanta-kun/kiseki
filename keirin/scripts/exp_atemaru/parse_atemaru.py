"""アーカイブ済みのアテマル予想ページを JSONL へ解析する。"""
from __future__ import annotations
import html, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "atemaru"
DET = ROOT / "detail"
MARKS = {"Icon_Honmei": "◎", "Icon_Taikou": "○", "Icon_Kurosan": "▲",
         "Icon_Osae": "△", "Icon_Renka": "×", "Icon_Hoshi": "☆"}
NUM = re.compile(r'<span class="Waku\d+ Shaban_Num">(\d+)</span>')


def _text(s: str) -> str:
    s = re.sub(r"<svg.*?</svg>", "", s, flags=re.S)
    s = re.sub(r"<.*?>", "\n", s, flags=re.S)
    return html.unescape(s)


def parse(p: Path) -> dict | None:
    t = p.read_text(encoding="utf-8")
    if "この予想は未購入です" in t:
        return None
    out: dict = {"gid": p.stem}
    m = re.search(r"race_id=(\d{12})", t)
    if not m:
        return None
    rid = m.group(1)
    out["race_id"] = rid
    out["date"] = rid[:8]
    out["venue_code"] = rid[8:10]
    out["race_no"] = int(rid[10:12])
    m = re.search(r"(\d{4}/\d{2}/\d{2}) (\S+) (\d+)R (\S+) (\S+) (\d{2}):(\d{2}) (\d)車", _text(t))
    if m:
        out["venue"] = m.group(2)
        out["cls"] = m.group(4)
        out["race_type"] = m.group(5)
        out["start_at"] = f"{m.group(6)}:{m.group(7)}"
        out["n_entries"] = int(m.group(8))

    # ---- 予想印 ----
    mk = {}
    pts = {}
    tbl = re.search(r'<table class="YosoShirushiTable01">(.*?)</table>', t, re.S)
    if tbl:
        for tr in tbl.group(1).split("<tr>")[1:]:
            icon = re.search(r"Icon_Shirushi (\w+)", tr)
            num = re.search(r'<span class="Num Waku\d+">(\d+)</span>', tr)
            pt = re.search(r'class="RaceCardCell01">(?:<!--.*?-->)?([\d.]+)', tr, re.S)
            if icon and num:
                mk[MARKS.get(icon.group(1), icon.group(1))] = int(num.group(1))
                if pt:
                    pts[int(num.group(1))] = float(pt.group(1))
    out["marks"] = mk
    out["race_point"] = pts

    # ---- 並び予想 ----
    lines = []
    sec = re.search(r'<section class="DeployYoso">(.*?)</section>', t, re.S)
    if sec:
        cur: list[int] = []
        for cell in re.findall(r'<div class="Shaban_InBox">(.*?)</div>', sec.group(1), re.S):
            if "WakuSeparat" in cell:
                if cur:
                    lines.append(cur)
                cur = []
                continue
            m2 = re.search(r'Shaban_Num">(\d+)</span>', cell)
            if m2:
                cur.append(int(m2.group(1)))
        if cur:
            lines.append(cur)
    out["lines"] = lines

    # ---- 買い目 ----
    legs = []
    kt = re.search(r'<table class="YosoKaimeTable01">(.*?)</table>', t, re.S)
    if kt:
        for tr in re.split(r"<tr[ >]", kt.group(1))[1:]:
            if "YosoKaimeTable" in tr or "<th>券種" in tr:
                continue
            bt = re.search(r"<th>(3連単|3連複|2車単|2車複|ワイド|2枠単|2枠複)", tr)
            if not bt:
                continue
            cols = []
            for dl in re.findall(r"<dl class=\"fc\">(.*?)</dl>", tr, re.S):
                cols.append([int(x) for x in NUM.findall(dl)])
            n = re.search(r"<strong>(\d+)通り</strong>", tr)
            amt = re.search(r"<strong>各([\d,]+)円</strong>", tr)
            legs.append({"bet_type": bt.group(1), "cols": cols,
                         "n_points": int(n.group(1)) if n else None,
                         "unit": int(amt.group(1).replace(",", "")) if amt else None})
    out["legs"] = legs
    m = re.search(r"<tfoot>.*?<td>([\d,]+)円</td>", t, re.S)
    out["total_bet"] = int(m.group(1).replace(",", "")) if m else None

    # ---- 払戻・収支 ----
    rf = re.search(r'<table class="YosoRefundTable01">(.*?)</table>', t, re.S)
    if rf:
        vals = re.findall(r"([-+]?[\d,]+)円", _text(rf.group(1)))
        if len(vals) >= 2:
            out["payout"] = int(vals[0].replace(",", "").replace("+", ""))
            out["profit"] = int(vals[1].replace(",", "").replace("+", ""))
    hit = re.search(r"払い戻し</b>([\d\-]+)：([\d,]+)円x([\d.]+)倍", t)
    if hit:
        out["hit_combo"] = hit.group(1)
        out["hit_odds"] = float(hit.group(3))

    # ---- レース結果 ----
    res = []
    rt = re.search(r'<table class="RaceResultTable"[^>]*>(.*?)</table>', t, re.S)
    if rt:
        for tr in re.findall(r'<tr class="List">(.*?)</tr>', rt.group(1), re.S):
            rk = re.search(r'class="ResultRank">(\S+?)</td>', tr)
            nm = re.search(r'class="HorseNum Wakuban Waku\d+">(\d+)</td>', tr)
            if rk and nm:
                res.append((rk.group(1), int(nm.group(1))))
    out["result"] = res

    # ---- 見解 ----
    cm = re.search(r"競輪予想見解：.*?(?=<)", _text(t), re.S)
    body = _text(t)
    i = body.find("競輪予想見解：")
    if i >= 0:
        j = body.find("お気に入り予想家", i)
        seg = body[i: j if j > 0 else i + 20000]
        seg = "\n".join(l.strip() for l in seg.split("\n") if l.strip())
        out["comment"] = seg
        out["ai_index"] = {}
        for m2 in re.finditer(r"(\d+)番車：(\S+?)選手.*?指数は([\d.]+)", seg, re.S):
            out["ai_index"][int(m2.group(1))] = float(m2.group(3))
    return out


def main() -> None:
    outp = ROOT / "parsed.jsonl"
    n = 0
    with outp.open("w", encoding="utf-8") as f:
        for p in sorted(DET.glob("*.html")):
            try:
                d = parse(p)
            except Exception as exc:  # noqa: BLE001
                print("!!", p.name, exc)
                continue
            if d:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
                n += 1
    print("parsed", n, "->", outp)


if __name__ == "__main__":
    main()
