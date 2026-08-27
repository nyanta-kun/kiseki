"""好調予想家（/yoso/hot/）の「直近の高額的中」を実測で分解する。

各的中について 券種 / 点数 / 1点あたり購入額 / 的中オッズ を詳細ページから取り、
「万車券を当てた」のか「点数を絞って1点の賭け金を大きくした」のかを切り分ける。
"""
from __future__ import annotations
import html, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "exp_gensen"))
from parse_gensen import parse  # noqa: E402
from fetch_goods import fetch_list, fetch_detail  # noqa: E402

RAW = Path(__file__).resolve().parent / "raw"


def targets() -> list[dict]:
    s = RAW.joinpath("hot.html").read_text(encoding="utf-8")
    out = []
    for b in re.split(r'(?=/yoso/profile/\?id=\d+)', s)[1:]:
        yid = int(re.search(r'/yoso/profile/\?id=(\d+)', b).group(1))
        txt = html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', b[:5000])))
        name = txt.split('">')[0] if '">' in txt else ""
        name = re.sub(r'^[^ ]*rf=hot_report ', '', txt).split(" 公開中")[0].strip()
        seg = txt.split("直近の高額的中", 1)
        if len(seg) < 2:
            continue
        for m in re.finditer(
            r"(\d{4})年(\d{1,2})月(\d{1,2})日\( . \) (\S+) (\d+)R (\S+) ([^ ]*) 払戻金： ([\d,]+) 円",
                seg[1][:1200]):
            out.append({"yid": yid, "name": name,
                        "date": f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
                        "venue": m.group(4), "race_no": int(m.group(5)),
                        "cls": m.group(6), "race_type": m.group(7),
                        "payout_claimed": int(m.group(8).replace(",", ""))})
    return out


def find_gid(yid: int, date: str, venue: str, race_no: int) -> str | None:
    raw = json.loads(fetch_list(yid, date))
    for li in re.findall(r'<li[^>]*class="[^"]*Selectable[^"]*"[^>]*>(.*?)</li>', raw, re.S):
        g = re.search(r'umai_prof_prop_(b\d+_%d)' % yid, li)
        jyo = re.search(r'<span class="Jyo">(.*?)</span>', li)
        num = re.search(r'<span class="Num">(\d+)R</span>', li)
        if g and jyo and num and jyo.group(1) == venue and int(num.group(1)) == race_no:
            return g.group(1)
    return None


def main() -> None:
    rows = []
    for t in targets():
        gid = find_gid(t["yid"], t["date"], t["venue"], t["race_no"])
        t["gid"] = gid
        if gid:
            p = RAW / "detail" / f"{gid}.html"
            fetch_detail(gid)
            d = parse(p)
            if d:
                t.update({k: d.get(k) for k in
                          ("legs", "total_bet", "payout", "profit", "hit_combo",
                           "hit_odds", "n_entries", "race_type", "cls", "result")})
        rows.append(t)
        print(".", end="", flush=True)
    print()
    Path("hot_hits.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print(len(rows), "件 -> hot_hits.jsonl")


if __name__ == "__main__":
    main()
