#!/usr/bin/env python3
"""機会費用 — この形をどこへ入れるか（置換範囲を複数測る）。

範囲:
  all       全レースで現行商品を置き換える
  typeX     指定した型だけ置き換える
  ana       `pw_ent >= ANA_PW_ENT_MIN`（=`A_ana` の選別・型A のみ）
  axisdrop  🟢 **軸信頼ゲートに落ちて今は売っていないレース**（純増）
  gatedrop  🟢 **入稿ゲートに落ちて今は売っていないレース**（純増）
  sign      現行の看板枠 `F_sign`（型F・看板種別）を置き換える
  big       高額枠の `_big` 相当（型B/C/D）を置き換える

🔴 件数を減らす腕には無作為対照を置く（本稿の腕は件数を減らさない＝置換か純増）。
"""
from __future__ import annotations

import sys
from statistics import median

import numpy as np

sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
                   "da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/"
                   "08_mark_second_third")
from importlib import import_module  # noqa: E402

C3 = import_module("03_cmp")
ROWS, NDAYS, WINS = C3.ROWS, C3.NDAYS, C3.WINS
kpi, line, HEAD, boot2, _mk = C3.kpi, C3.line, C3.HEAD, C3.boot2, C3._mk

ANA_PW_ENT_MIN = 1.4076
SIGNBOARD_RT = None          # 下で本番から読む


def _sign_rt():
    global SIGNBOARD_RT
    if SIGNBOARD_RT is None:
        sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
        from src.type_lab import SIGNBOARD_RACE_TYPES
        SIGNBOARD_RT = set(SIGNBOARD_RACE_TYPES)
    return SIGNBOARD_RT


def sold(r) -> bool:
    """いま実際に入稿される（＝在庫になる）か。"""
    b = r["base"]
    return bool(b and b["gate"] and b["axis_ok"])


def scope(r, name: str) -> bool:
    b = r["base"]
    if name == "all":
        return sold(r)
    if name.startswith("type"):
        return sold(r) and r["type"] in name[4:]
    if name == "ana":
        return sold(r) and r["type"] == "A" and r["pw_ent"] >= ANA_PW_ENT_MIN
    if name == "axisdrop":
        return bool(b and b["gate"] and not b["axis_ok"])
    if name == "gatedrop":
        return bool(b and not b["gate"])
    if name == "sign":
        return sold(r) and b["plan"] == "F_sign"
    if name == "big":
        return sold(r) and r["type"] in "BCD"
    return False


def overview():
    for lbl, win in WINS:
        rs = [r for r in ROWS if r["win"] == win]
        nd = NDAYS[win]
        print(f"\n=== {lbl}  n={len(rs):,}R  日数={nd} — 現行ラインナップ ===")
        print(HEAD)
        srt = _sign_rt()
        for tag, f in (
                ("現行 全体(売る分)", sold),
                ("  うち 当てにいく", lambda r: sold(r) and r["base"]["plan"]
                 not in ("A_ana", "F_sign")),
                ("  うち 一撃(A_ana/F_sign)", lambda r: sold(r) and r["base"]["plan"]
                 in ("A_ana", "F_sign")),
                ("軸信頼ゲート落ち(未売)", lambda r: scope(r, "axisdrop")),
                ("入稿ゲート落ち(未売)", lambda r: scope(r, "gatedrop")),
        ):
            recs = [r["base"] for r in rs if r["base"] and f(r)]
            print(line(tag, kpi(recs, nd)))
        print(f"  投資/日: {sum(r['base']['inv'] for r in rs if sold(r))/nd:,.0f}円")
        print(f"  1着が◎○以外の割合: 売る分 "
              f"{np.mean([r['first_out'] for r in rs if sold(r)])*100:.2f}% / "
              f"軸ゲート落ち "
              f"{np.mean([r['first_out'] for r in rs if scope(r,'axisdrop')])*100:.2f}% / "
              f"入稿ゲート落ち "
              f"{np.mean([r['first_out'] for r in rs if scope(r,'gatedrop')])*100:.2f}%")


def cmp_scope(scope_name: str, *arms):
    for lbl, win in WINS:
        rs = [r for r in ROWS if r["win"] == win and scope(r, scope_name)]
        nd = NDAYS[win]
        print(f"\n=== {lbl}  範囲={scope_name}  n={len(rs):,}R ===")
        print(HEAD)
        cur = [r["base"] for r in rs]
        print(line("現行（この範囲）", kpi(cur, nd)))
        for a in arms:
            pairs = [(r["base"], r["arms"][a]) for r in rs if a in r["arms"]]
            if len(pairs) < 30:
                print(f"  {a:24s}  (n={len(pairs)} 不足)")
                continue
            recs = [p[1] for p in pairs]
            print(line(a, kpi(recs, nd)))
            res = boot2(_mk([p[0] for p in pairs]), _mk(recs))
            print("      " + "  ".join(
                f"Δ{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]"
                for k, v in res.items()) + f"  (共通n={len(pairs)})")


def cmp_scope2(scope_name: str, base_arm: str, *arms):
    """範囲を絞ったうえで **腕どうし**を対比較する（基準も腕）。"""
    for lbl, win in WINS:
        rs = [r for r in ROWS if r["win"] == win and scope(r, scope_name)]
        nd = NDAYS[win]
        print(f"\n=== {lbl}  範囲={scope_name}  基準={base_arm}  n={len(rs):,}R ===")
        print(HEAD)
        print(line(base_arm, kpi([r["arms"][base_arm] for r in rs
                                 if base_arm in r["arms"]], nd)))
        for a in arms:
            pairs = [(r["arms"][base_arm], r["arms"][a]) for r in rs
                     if base_arm in r["arms"] and a in r["arms"]]
            if len(pairs) < 30:
                print(f"  {a:24s}  (n={len(pairs)} 不足)")
                continue
            recs = [p[1] for p in pairs]
            print(line(a, kpi(recs, nd)))
            res = boot2(_mk([p[0] for p in pairs]), _mk(recs))
            print("      " + "  ".join(
                f"Δ{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]"
                for k, v in res.items()) + f"  (共通n={len(pairs)})")


def lineup(scope_name: str, *arms):
    """ラインナップ全体（売る分 ∪ 純増分）での増減。"""
    for lbl, win in WINS:
        rs = [r for r in ROWS if r["win"] == win]
        nd = NDAYS[win]
        cur = [r["base"] for r in rs if sold(r)]
        print(f"\n=== {lbl}  ラインナップ全体  範囲={scope_name} ===")
        print(HEAD)
        print(line("現行", kpi(cur, nd)))
        add = scope_name in ("axisdrop", "gatedrop")
        for a in arms:
            new = []
            for r in rs:
                if scope(r, scope_name) and a in r["arms"]:
                    new.append(r["arms"][a])
                elif sold(r):
                    new.append(r["base"])
            s = kpi(new, nd)
            print(line(f"{a}{' (純増)' if add else ' (置換)'}", s))
            inv = sum(x["inv"] for x in new) / nd
            print(f"      投資/日 {inv:,.0f}円（現行 "
                  f"{sum(x['inv'] for x in cur)/nd:,.0f}円）")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "overview":
        overview()
    elif cmd == "scope":
        cmp_scope(sys.argv[2], *sys.argv[3:])
    elif cmd == "scope2":
        cmp_scope2(sys.argv[2], sys.argv[3], *sys.argv[4:])
    elif cmd == "lineup":
        lineup(sys.argv[2], *sys.argv[3:])
