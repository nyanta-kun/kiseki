"""メカウチダ履歴ページ → 各馬の行 / 買い目の行（CSV）。"""

import csv, html, os, re, sys

D = sys.argv[1]
num = re.compile(r"<span class='num'[^>]*>(\d+)</span>")
row_re = re.compile(r"<tr><td>(<span class='num'.*?)</tr>", re.S)
pct = re.compile(r"(\d+)%<small class=muted><br>実(\d+)</small>")
horses, picks = [], []
for f in sorted(os.listdir(D + "/hist")):
    t = open(D + "/hist/" + f).read()
    if len(t) < 1000:
        continue
    d = f[:8]
    cards = t.split("<div class='rh'>")[1:]
    for c in cards:
        rv = re.search(r"<span class='rv'>([^<]+)<b>(\d+)R</b>", c)
        if not rv:
            continue
        venue, rn = rv.group(1), int(rv.group(2))
        lv = re.search(r"荒れやすさ ([低中高])", c[:600])
        lv = lv.group(1) if lv else ""
        # 買い目（判断済み）: 表より前の horse ブロック
        head = c.split("<details", 1)[0].split("<div class='fcw'>", 1)[0]
        for m in re.finditer(
            r"<div class='horse( is-pv)?'>(.*?)<span class='res ([a-z]+)'>(.*?)</span></div>", head, re.S
        ):
            if m.group(1) or m.group(3) == "pv":
                continue
            body = m.group(2)
            hn = int(num.search(body).group(1))
            ev = re.search(r"期待値([0-9.]+)", body)
            dpop = re.search(r"判断時 (\d+)番人気", body)
            picks.append(
                dict(
                    date=d,
                    venue=venue,
                    rn=rn,
                    hn=hn,
                    res=m.group(3),
                    ev=ev.group(1) if ev else "",
                    dpop=dpop.group(1) if dpop else "",
                    level=lv,
                )
            )
        tbl = c.find("<table class='fct")
        if tbl < 0:
            continue
        for r in row_re.finditer(c[tbl : c.find("</table>", tbl)]):
            row = r.group(1)
            hn = int(num.search(row).group(1))
            tds = re.findall(r"<td[^>]*>(.*?)</td>", "<td>" + row, re.S)
            ps = pct.findall(row)
            if len(ps) != 3:
                continue
            pop = re.sub(r"<[^>]+>", "", tds[2]).strip()
            pos = re.match(r"([^<]*)", tds[3]).group(1).strip()
            fin = re.sub(r"<[^>]+>", "", tds[-1]).strip()
            horses.append(
                dict(
                    date=d,
                    venue=venue,
                    rn=rn,
                    hn=hn,
                    pop=pop,
                    pos=pos,
                    p1=ps[0][0],
                    r1=ps[0][1],
                    p2=ps[1][0],
                    r2=ps[1][1],
                    p3=ps[2][0],
                    r3=ps[2][1],
                    fin=fin,
                    level=lv,
                )
            )
for name, rows in (("horses", horses), ("picks", picks)):
    with open(f"{D}/{name}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
print(len(horses), "horses", len({(h["date"], h["venue"], h["rn"]) for h in horses}), "races", len(picks), "picks")
from collections import Counter

print(Counter(p["res"] for p in picks))
print(Counter(h["date"] for h in horses))
