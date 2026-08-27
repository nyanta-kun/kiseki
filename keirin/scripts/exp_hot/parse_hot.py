"""予想詳細ページの買い目表を「1点＝1行」まで展開して読む。

netkeiba の買い目表は3形式が混在する:
  通常          … 1行=1点（金額が点ごとに違う＝手動の傾斜配分）
  フォーメーション/流し … 1行=N点（`N通り` + `各X円`）
  ボックス      … 同上
`tr class="HitBaken"` が的中行で、`払い戻し：X円 x Y倍` から的中倍率が取れる。
"""
from __future__ import annotations
import re
from pathlib import Path

NUM = re.compile(r'<span class="Waku\d+ Shaban_Num">(\d+)</span>')
BT = re.compile(r"<th>(3連単|3連複|2車単|2車複|ワイド|2枠単|2枠複)")
SYS = re.compile(r'<span class="BakenSystemTxt">(.*?)</span>')


def parse_bets(path: Path) -> dict:
    t = path.read_text(encoding="utf-8")
    out: dict = {"rows": [], "total_bet": None, "payout": None, "hit": None}
    m = re.search(r'<table class="YosoKaimeTable01">(.*?)</table>', t, re.S)
    if not m:
        return out
    body = m.group(1)
    for tr in re.split(r"<tr[ >]", body)[1:]:
        bt = BT.search(tr)
        if not bt:
            continue
        sysm = SYS.search(tr)
        mode = sysm.group(1) if sysm else ""
        cols = [[int(x) for x in NUM.findall(dl)]
                for dl in re.findall(r'<dl class="fc">(.*?)</dl>', tr, re.S)]
        n = re.search(r"<strong>(\d+)通り</strong>", tr)
        each = re.search(r"<strong>各([\d,]+)円</strong>", tr)
        one = re.search(r'<span class="BuyPatern"><strong></strong>\s*<strong>([\d,]+)円</strong>',
                        tr, re.S)
        if n and each:                                  # フォーメーション/流し/ボックス
            npts, unit = int(n.group(1)), int(each.group(1).replace(",", ""))
        elif one:                                       # 通常（1行=1点）
            npts, unit = 1, int(one.group(1).replace(",", ""))
        else:
            npts, unit = None, None
        hit = tr.startswith('class="HitBaken"') or 'class="HitBaken"' in tr[:40]
        ho = re.search(r"払い戻し</b>\s*([\d\-]*)\s*[：:]([\d,]+)円x([\d.]+)倍="
                       r"<strong>([\d,]+)円", tr)
        out["rows"].append({
            "bet_type": bt.group(1), "mode": mode, "cols": cols,
            "combo": NUM.findall(tr) if not cols else None,
            "n_points": npts, "unit": unit, "hit": bool(ho) or hit,
            "hit_combo": (ho.group(1) or None) if ho else None,
            "hit_stake": int(ho.group(2).replace(",", "")) if ho else None,
            "hit_odds": float(ho.group(3)) if ho else None,
            "hit_payout": int(ho.group(4).replace(",", "")) if ho else None,
        })
    f = re.search(r"<tfoot>.*?<td>([\d,]+)円</td>", body, re.S)
    out["total_bet"] = int(f.group(1).replace(",", "")) if f else None
    r = re.search(r'払い戻し金額</th>\s*<td>([\d,]+)円', t)
    out["payout"] = int(r.group(1).replace(",", "")) if r else None
    hits = [x for x in out["rows"] if x["hit_odds"]]
    out["hit"] = hits[0] if hits else None
    out["n_points_total"] = sum(x["n_points"] or 0 for x in out["rows"])
    units = [x["unit"] for x in out["rows"] if x["unit"]]
    out["unit_min"], out["unit_max"] = (min(units), max(units)) if units else (None, None)
    out["bet_types"] = sorted({x["bet_type"] for x in out["rows"]})
    return out
