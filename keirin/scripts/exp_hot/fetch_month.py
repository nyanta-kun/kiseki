"""好調予想家10人の 2026-08 全商品を一覧APIから収集する（詳細ページは取らない）。

一覧の li に 購入金額・払戻・収支 が入っているので、これだけで
「高額的中の裏でいくら払っているか」（＝真の的中率とROI）が出せる。
"""
from __future__ import annotations
import datetime as dt, json, re, sys
from pathlib import Path
from fetch_goods import fetch_list

YIDS = [506, 428, 424, 465, 354, 482, 401, 585, 614, 546]


def days(a: str, b: str):
    d = dt.date(int(a[:4]), int(a[4:6]), int(a[6:]))
    e = dt.date(int(b[:4]), int(b[4:6]), int(b[6:]))
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)


def parse_list(yid: int, date: str, raw: str) -> list[dict]:
    out = []
    for li in re.findall(r'<li[^>]*class="[^"]*Selectable[^"]*"[^>]*>(.*?)</li>', raw, re.S):
        g = re.search(r'umai_prof_prop_(b\d+_%d)' % yid, li)
        jyo = re.search(r'<span class="Jyo">(.*?)</span>', li)
        num = re.search(r'<span class="Num">(\d+)R</span>', li)
        nam = re.search(r'<span class="Name">(.*?)</span>', li)
        if not (g and jyo and num):
            continue
        buy = re.search(r'<th>購入金額</th>\s*<td>([\d,]+)円</td>', li)
        pub = re.search(r'<th>公開日時</th>\s*<td>(.*?)</td>', li)
        cmt = re.search(r'<div class="Comment">\s*<p class="Txt">(.*?)</p>', li, re.S)
        bal = re.findall(r'<em>([\-\+\d,]+)</em>円', li)
        out.append({
            "yid": yid, "date": date, "gid": g.group(1), "venue": jyo.group(1),
            "race_no": int(num.group(1)), "race_name": (nam.group(1) if nam else ""),
            "bet": int(buy.group(1).replace(",", "")) if buy else None,
            "published_at": pub.group(1).strip() if pub else None,
            "comment": re.sub(r"<[^>]+>", "", cmt.group(1)).strip() if cmt else None,
            "payout": int(bal[0].replace(",", "").replace("+", "")) if len(bal) >= 1 else None,
            "profit": int(bal[1].replace(",", "").replace("+", "")) if len(bal) >= 2 else None,
        })
    return out


def main() -> None:
    a, b = sys.argv[1], sys.argv[2]
    rows = []
    for yid in YIDS:
        n0 = len(rows)
        for d in days(a, b):
            try:
                rows += parse_list(yid, d, json.loads(fetch_list(yid, d)))
            except Exception as exc:                       # noqa: BLE001
                print(f"!! {yid} {d}: {exc}", flush=True)
        print(f"{yid}: {len(rows)-n0}件", flush=True)
    Path("month.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print("total", len(rows))


if __name__ == "__main__":
    main()
