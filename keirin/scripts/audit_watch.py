#!/usr/bin/env python3
"""監査（2026-09-20）後の前向き観測 — 商品を変えずに毎日1枚だけ見る。

## なぜ別の道具が要るのか

- 夜間レビュー（`nightly_review_type_lab.py`）は**その日1日**を見る道具で、売上を見ない。
- 朝の Discord（`backend/src/services/keirin_sales_report.py`）は**前日の売上**を見るが成績を見ない。

監査（`keirin/docs/AUDIT_2026_09_20.md`）で最優先KPIが**売上**に決まり、
商品はそのまま据え置いて並行で観測する期間に入った。見たいのは
**①構成が動いていないこと ②売上 ③監査以降の累積成績**の3つで、
どれも「今日1日」では答えが出ない。だから日ごとの表ではなく**累積**を毎日出す。

## 🔴 この道具の仕事は「判定しないこと」

監査 §4.2 の教訓:

    実売の小標本（高額枠は的中 3〜5% なので n=400 でも CI 幅は ±35pt）で
    商品の良し悪しを判定しない。判定は honest な板で行い、実売は
    「板と整合するか」の確認に使う。

したがってここは、**監査が出した期待帯を毎日そのまま横に置く**だけにする。

- 期待帯の中にいる限り「想定どおり」。日々の上下は読まない
- **CI が期待帯から完全に外れたときだけ** 🔴 を出す
- 件数が足りない層は**「判定不能」と書く**。黙って数字だけ出すと、
  読んだ人が勝手に判定する（監査で統括が一度やった誤り）
- 🔴 **高額枠は実売では永久に判定しない**（的中 3〜5%）。参考値として出すだけ

## 情報源と正本（ここに規則を書き直さない）

| 何 | 正本 | ここでの扱い |
|---|---|---|
| 売った商品の母集団 | `scripts/sold_performance_report._fetch` | import する（#590 の status 絞りを写経しない） |
| 採点 | `backend/src/services/keirin_settlement.py` | `src.sold_performance` 経由で委譲 |
| 売上の円換算 | `backend/src/services/keirin_sales_report.py` | ファイル読み込みで束縛 |
| 商品構成 | `src/type_lab.py` / `src/confident_pick.py` / `backend/src/services/keirin_type_lab_gate.py` | 実物の値を読む |

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/audit_watch.py                 # 監査日から今日まで
    PYTHONPATH=. .venv/bin/python scripts/audit_watch.py --out watch.md
    PYTHONPATH=. .venv/bin/python scripts/audit_watch.py --write-baseline  # 構成の基準を撮り直す

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.sold_performance_report import _fetch  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.entrants import valid_cars_by_race  # noqa: E402
from src.sold_performance import SoldRace, build_sold_races, summarize  # noqa: E402

#: 監査の基準日。ここから前向きに積む（これ以前は監査の対象で、観測ではない）。
AUDIT_DAY = "2026-09-20"

#: 構成の基準。`--write-baseline` で撮り直す。
BASELINE = REPO / "docs" / "audit_2026_09_20" / "watch_baseline.json"

#: 10万円以上の払戻（＝「看板」）。売上の翌日効果のイベント（監査 §5）。
SIGNBOARD_PAYOUT = 100_000


# ── 正本の束縛 ──────────────────────────────────────────────────
def _load(path: Path, name: str) -> ModuleType:
    """kiseki backend 側のモジュールを**ファイル指定で**読み込む。

    keirin は自分の venv（FastAPI も SQLAlchemy も無い）で動くので、
    束縛してよいのは**標準ライブラリだけで書かれた正本**に限る。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:      # pragma: no cover
        raise RuntimeError(f"正本を読み込めない: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_SALES = _load(REPO.parent / "backend" / "src" / "services" / "keirin_sales_report.py",
               "kiseki_keirin_sales_report")
_GATE = _load(REPO.parent / "backend" / "src" / "services" / "keirin_type_lab_gate.py",
              "kiseki_keirin_type_lab_gate")


# ── §1 商品構成 ─────────────────────────────────────────────────
_MISSING = "<未定義>"


def _val(mod: Any, name: str) -> Any:
    """定数を JSON に載る形で取り出す。

    🔴 **名前が消えていたら例外にせず `<未定義>` を返す。** 定数の改名も
    「構成が動いた」ことに変わりはないので、差分として見えればよい。
    ここで落とすと夜間チェーンごと止まる。
    """
    got = getattr(mod, name, _MISSING)
    if isinstance(got, (set, frozenset)):
        return sorted(str(x) for x in got)
    if isinstance(got, tuple):
        return [str(x) for x in got]
    if isinstance(got, dict):
        return {str(k): v for k, v in sorted(got.items())}
    if isinstance(got, (str, int, float, bool)) or got is None:
        return got
    return str(got)


def live_config() -> dict[str, Any]:
    """いま本番が使っている「商品構成」を1枚の辞書にする。

    入れるのは**売り物の形を決める値**だけ。学習パラメータや閾値の
    すべてを入れると差分が毎回出て読まれなくなる。
    """
    import src.confident_pick as CP
    import src.type_lab as TL

    cfg: dict[str, Any] = {"__source__": "live"}
    for name in ("BUDGET", "AXIS_SUM_FIRM", "MIN_PAYOUT_MULT",
                 "SIGNBOARD_TYPES", "SIGNBOARD_RACE_TYPES", "SIGNBOARD_TARGET",
                 "SIGNBOARD_MAX_ODDS", "HIGHPAY_TYPES", "HIGHPAY_SLOTS_PER_DAY",
                 "HIGHPAY_BIG_SLOTS", "HIGHPAY_BIG_TARGET", "HIGHPAY_PLAN_KEYS",
                 "UPPER_BANDS", "UPPER_BAND_PLANS", "OSAE_PLANS",
                 "TYPE_F_SELL_BY_RACE_TYPE", "TYPE_F_SELL_DEFAULT", "TYPE_F_SELL_LINE",
                 "TIER_SELL_ENABLED", "MIN_MEAN_PAYOUT"):
        cfg[f"type_lab.{name}"] = _val(TL, name)
    cfg["type_lab.PLANS"] = sorted(getattr(TL, "PLANS", {}))
    for name in ("CONFIDENT_BEFORE_HOUR", "CONFIDENT_MIN_SYNTH_ODDS",
                 "CONFIDENT_PRIORITY_KEYWORD", "TIER_CONFIDENT_PLANS",
                 "TYPE_LAB_CONFIDENT_PLANS"):
        cfg[f"confident.{name}"] = _val(CP, name)
    for name in ("AXIS_GATE_MIN", "AXIS_GATE_EXEMPT_PLANS", "AXIS_GATE_DROP_RATIO",
                 "DAILY_CAP_RACE_FRACTION", "DAILY_CAP_EXEMPT_KEYWORDS",
                 "DAILY_CAP_EXEMPT_PLANS"):
        cfg[f"gate.{name}"] = _val(_GATE, name)
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT rank_key FROM netkeirin_settings WHERE enabled ORDER BY rank_key")]
    cfg["db.netkeirin_settings.enabled"] = [r["rank_key"] for r in rows]
    return cfg


def diff_config(base: Mapping[str, Any], live: Mapping[str, Any]) -> list[tuple[str, Any, Any]]:
    keys = sorted(set(base) | set(live))
    out = []
    for k in keys:
        if k.startswith("__"):
            continue
        b, l = base.get(k, _MISSING), live.get(k, _MISSING)
        if b != l:
            out.append((k, b, l))
    return out


def section_config(base_path: Path) -> tuple[list[str], bool]:
    live = live_config()
    if not base_path.exists():
        return ([f"⚠️ 構成の基準がまだ無い（{base_path.relative_to(REPO)}）。"
                 f"`--write-baseline` で撮ってから観測を始めること。"], False)
    base = json.loads(base_path.read_text(encoding="utf-8"))
    got = diff_config(base.get("config", {}), live)
    head = f"基準 {base.get('taken_at', '?')}（git {base.get('git', '?')[:8]}）"
    if not got:
        return ([f"✅ 監査時点から変更なし　{head}"], False)
    lines = [f"🔴 **構成が動いている**（{len(got)} 件）　{head}", "",
             "| 項目 | 基準 | いま |", "|---|---|---|"]
    for k, b, l in got:
        lines.append(f"| `{k}` | `{json.dumps(b, ensure_ascii=False)}` "
                     f"| `{json.dumps(l, ensure_ascii=False)}` |")
    lines += ["",
              "⚠️ 意図した変更なら基準を撮り直す（`--write-baseline`）。",
              "撮り直すと**その日から別の観測**になる。累積を続けたいなら戻すこと。"]
    return (lines, True)


# ── 集計の道具 ──────────────────────────────────────────────────
def _boot_ci(races: Sequence[SoldRace], n_boot: int, seed: int
             ) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """**日でクラスタした**ブートストラップ CI（表示的中, ROI）。

    🔴 レース単位で抜くと CI が狭く出る。同じ日の商品は同じ板・同じ選別を
    共有していて独立ではない（監査 §4.1 も日クラスタで出している）。
    """
    if not races:
        return (None, None)
    by_day: dict[str, list[SoldRace]] = {}
    for r in races:
        by_day.setdefault(r.race_date, []).append(r)
    days = list(by_day.values())
    if len(days) < 2:
        return (None, None)
    rnd = random.Random(seed)
    hits, rois = [], []
    for _ in range(n_boot):
        pick = [days[rnd.randrange(len(days))] for _ in range(len(days))]
        flat = [r for d in pick for r in d]
        s = summarize(flat)
        if s.n_races:
            hits.append(s.net_hit_rate or 0.0)
        if s.bet:
            rois.append(s.roi or 0.0)

    def q(xs: list[float]) -> tuple[float, float] | None:
        if len(xs) < 100:
            return None
        xs = sorted(xs)
        return (xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1])

    return (q(hits), q(rois))


#: 実売では永久に判定しない層に付ける印（的中 3〜5% の商品）。
NEVER_JUDGE = -1


def _verdict(ci: tuple[float, float] | None, band: Sequence[float] | None,
             n: int, min_n: int) -> str:
    """期待帯と CI を突き合わせる。**既定は「判定不能」**。"""
    if min_n == NEVER_JUDGE:
        return "参考（実売では判定しない）"
    if n < min_n:
        return f"判定不能（n<{min_n}）"
    if ci is None or band is None:
        return "判定不能"
    lo, hi = ci
    blo, bhi = band[0], band[1]
    if hi < blo:
        return "🔴 期待帯を下回る"
    if lo > bhi:
        return "🟢 期待帯を上回る"
    return "想定どおり"


def _pct(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "—"


def _ci(ci: tuple[float, float] | None) -> str:
    return f"[{ci[0]:.0%}, {ci[1]:.0%}]" if ci else "—"


# ── §2 売上 ────────────────────────────────────────────────────
def _sales(start: str, end: str) -> tuple[list[dict], dict[str, dict]]:
    """日別の売上と、レース別（商品の種別へ紐づけるため）を引く。

    ⚠️ netkeirin の取得は**翌朝 9:40**（`scripts/scrape_netkeirin_sales.sh`）。
    直近の日は必ず空になる。空を 0 と書くと「売れなかった日」に見えるので
    「未取得」と書き分けること。
    """
    ymd = lambda s: s.replace("-", "")  # noqa: E731
    with get_connection() as c:
        daily = [dict(r) for r in c.execute(
            "SELECT sale_date, n_sold, sold_paid_points, n_predictions, "
            "       n_hits_incl_garami, n_hits_excl_garami "
            "FROM netkeirin_sales_daily WHERE sale_date BETWEEN ? AND ? "
            "ORDER BY sale_date", (ymd(start), ymd(end)))]
        race = {}
        for r in c.execute(
                "SELECT race_key, race_date, sold_paid_points, n_sold "
                "FROM netkeirin_sales_race WHERE race_date BETWEEN ? AND ?",
                (ymd(start), ymd(end))):
            d = dict(r)
            race[d["race_key"]] = d
    return daily, race


def _cancelled_keys(start: str, end: str) -> set[str]:
    """中止になったレース。

    🔴 **中止は「採点の取りこぼし」ではない**。着順が永久に入らないので
    未採点のまま残り続ける。分けずに数えると健全性の赤が消えなくなり、
    本物の取りこぼし（#590 が拾う分）が埋もれる。
    """
    with get_connection() as c:
        return {dict(r)["race_key"] for r in c.execute(
            "SELECT race_key FROM wt_races "
            "WHERE cancel = 1 AND race_date BETWEEN ? AND ?", (start, end))}


def _race_types(keys: Iterable[str]) -> dict[str, str]:
    """レース種別（決勝・準決勝…）。「自信あり」をどこへ置いたかを見るため。"""
    keys = sorted(set(keys))
    if not keys:
        return {}
    out: dict[str, str] = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            q = ("SELECT race_key, race_type FROM wt_races WHERE race_key IN (%s)"
                 % ",".join("?" * len(chunk)))
            for r in c.execute(q, chunk):
                d = dict(r)
                out[d["race_key"]] = str(d.get("race_type") or "—")
    return out


def _confident_keys(start: str, end: str) -> set[str]:
    """「自信あり」を付けて出したレース。

    `sold_performance_report._fetch` は成績に要る列しか引かないので、
    ここだけ別に引く（向こうを書き換えると採点側の母集団の話とまざる）。
    """
    with get_connection() as c:
        return {dict(r)["race_key"] for r in c.execute(
            "SELECT ns.race_key FROM netkeirin_submissions ns "
            "JOIN wt_races wr ON wr.race_key = ns.race_key "
            "WHERE ns.is_confident AND ns.deleted_at IS NULL "
            "  AND ns.status IN ('submitted', 'published') "
            "  AND wr.race_date BETWEEN ? AND ?", (start, end))}


def section_sales(start: str, end: str, subs: list[Mapping[str, Any]],
                  races: Sequence[SoldRace], conf: set[str]) -> list[str]:
    daily, per_race = _sales(start, end)
    if not daily:
        return ["（売上はまだ1日も取得できていない。netkeirin の取得は翌朝 9:40）"]
    meta = {s["race_key"]: s for s in subs}
    payout = {r.race_key: r.payout for r in races}

    def day_rows(day: str) -> list[dict]:
        return [r for r in per_race.values() if r["race_date"] == day]

    def big_of(day: str) -> int:
        return sum(1 for r in day_rows(day)
                   if payout.get(r["race_key"], 0) >= SIGNBOARD_PAYOUT)

    def yen(pt: float) -> int:
        return _SALES.net_revenue_yen(_SALES.revenue_yen(pt))

    def iso(day: str) -> str:
        return f"{day[:4]}-{day[4:6]}-{day[6:]}"

    # ── 週次（長く観測するほどここだけ見れば足りる）────────────────
    weeks: dict[str, list[dict]] = {}
    for d in daily:
        y, w, _ = date.fromisoformat(iso(d["sale_date"])).isocalendar()
        weeks.setdefault(f"{y}-W{w:02d}", []).append(d)
    lines = ["**週次**", "",
             "| 週 | 日数 | 商品 | 有償pt | 手取り/日 | 表示的中 | 10万+ |",
             "|---|--:|--:|--:|--:|--:|--:|"]
    for label, rows in weeks.items():
        pt = sum(int(r.get("sold_paid_points") or 0) for r in rows)
        n = sum(int(r.get("n_predictions") or 0) for r in rows)
        hit = sum(int(r.get("n_hits_excl_garami") or 0) for r in rows)
        big = sum(big_of(r["sale_date"]) for r in rows)
        lines.append(f"| {label} | {len(rows)} | {n} | {pt:,} "
                     f"| {yen(pt / len(rows)):,}円 "
                     f"| {(hit / n if n else 0):.1%} | {big} |")

    # ── 日次は直近14日だけ（累積が伸びても読める長さに保つ）──────────
    recent = daily[-14:]
    lines += ["", f"**直近{len(recent)}日**", "",
              "| 日 | 商品 | 有償pt | 手取り | 高額枠pt | 自信ありpt | 10万+ |",
              "|---|--:|--:|--:|--:|--:|--:|"]
    for d in recent:
        day = d["sale_date"]
        pt = int(d.get("sold_paid_points") or 0)
        hi = cf = 0
        for r in day_rows(day):
            rk = r["race_key"]
            p = int(r.get("sold_paid_points") or 0)
            if (meta.get(rk, {}).get("origin") or "") == "highpay_fill":
                hi += p
            if rk in conf:
                cf += p
        lines.append(f"| {iso(day)} | {int(d.get('n_predictions') or 0)} | {pt:,} "
                     f"| {yen(pt):,}円 | {hi:,} | {cf:,} | {big_of(day)} |")

    # ── 売れ行き（期間計）───────────────────────────────────────
    pt_all = sum(int(d.get("sold_paid_points") or 0) for d in daily)
    n_all = sum(int(d.get("n_predictions") or 0) for d in daily)
    sold_all = sum(int(d.get("n_sold") or 0) for d in daily)
    incl = sum(int(d.get("n_hits_incl_garami") or 0) for d in daily)
    excl = sum(int(d.get("n_hits_excl_garami") or 0) for d in daily)
    rows_all = list(per_race.values())
    zero = sum(1 for r in rows_all if not int(r.get("n_sold") or 0))
    paid = sorted(p for p in (payout.get(r.race_key, 0) for r in races if r.net_hit) if p)
    lines += ["",
              f"**累計 {len(daily)} 日** 手取り **{yen(pt_all / len(daily)):,}円/日**"
              f"（監査の型ラボ期 6,458円/日）・10万+ "
              f"{sum(big_of(d['sale_date']) for d in daily)} 件",
              "",
              f"- 売れ行き: {sold_all / n_all:.2f} 個/商品"
              f"（無売上 {zero / len(rows_all):.1%}・レース別 {len(rows_all)} 件）"
              if rows_all and n_all else "- 売れ行き: —",
              f"- ガミ率 {((incl - excl) / incl if incl else 0):.1%}"
              f"（当たった {incl} 件のうち払戻が賭け金に届かなかった割合）",
              f"- 表示的中の中央払戻 "
              f"{(paid[len(paid) // 2] if paid else 0):,}円（{len(paid)} 件）"]

    # ── 自信ありの置き場所（監査 §5 で最も効率の良い単一ダイヤル）──────
    types = _race_types(conf)
    if types:
        tally: dict[str, int] = {}
        for t in types.values():
            tally[t] = tally.get(t, 0) + 1
        top = " / ".join(f"{k} {v}" for k, v in
                         sorted(tally.items(), key=lambda kv: -kv[1])[:6])
        lines.append(f"- 自信あり {len(types)} 件の置き場所: {top}")

    lines += ["",
              "⚠️ 最終日が欠けているのは未取得（翌朝 9:40 に入る）。0 ではない。",
              "⚠️ 売上に効くのは**高額のラベル・自信あり・決勝・10万+的中の翌日**"
              "（監査 §5）。**的中率・本数・公開の早さには反応しない**ので、"
              "売上が動いた日に成績の説明を当てないこと。"]
    return lines


# ── §3 前向き成績 ───────────────────────────────────────────────
#: 層ごとの「監査が出した期待帯」と、実売で判定してよい最小件数。
#: 🔴 高額枠は `min_n` を実質無限にしてある（的中 3〜5% は実売では判定できない）。
LAYERS: tuple[tuple[str, str, tuple[float, float] | None, tuple[float, float] | None, int], ...] = (
    ("全体", "all", (0.624, 0.859), None, 300),
    ("本線（高額・看板枠を除く）", "base", (0.697, 0.909), (0.25, 0.36), 300),
    ("高額枠（origin=highpay_fill）", "highpay", (0.50, 1.155), None, NEVER_JUDGE),
    ("看板枠（F_sign / F_pay）", "signboard", (0.614, 0.872), None, NEVER_JUDGE),
    # 🔴 逃げ先頭ライン（2026-09-24〜・型ラボが売らないレースへ・準決勝系と型Eを除く・上限なし）。的中は 1レース
    #    3〜4% なので実売では判定しない。帯は過去2年の③（1万円均等・最大3本を除く 93〜99%
    #    ↔ 含む 111〜116%）の幅を広めに取った参考値。判定は `docs/type_lab/line_lead_2026_09_24.md` §7。
    ("逃げ先頭ライン（L_lead）", "line_lead", (0.50, 1.50), None, NEVER_JUDGE),
)


def _layer(races: Sequence[SoldRace], key: str) -> list[SoldRace]:
    if key == "all":
        return list(races)
    if key == "highpay":
        return [r for r in races if (r.origin or "") == "highpay_fill"]
    if key == "signboard":
        return [r for r in races if r.rank_key in ("F_sign", "F_pay")]
    if key == "line_lead":
        return [r for r in races if r.rank_key == "L_lead"]
    # 🔴 本線から逃げ先頭ラインを外す（的中 3〜4% の一撃商品が混ざると本線の的中帯が壊れる）。
    return [r for r in races
            if (r.origin or "") != "highpay_fill"
            and r.rank_key not in ("F_sign", "F_pay", "L_lead")]


#: 今回の見直しで入った商品。**変更日より前は別の商品**なので、そこから積む。
REVISED: tuple[tuple[str, str, str], ...] = (
    ("型E 点数＝計画払戻の床 (#586)", "2026-09-17", "E_hit"),
    ("高額枠 5本 (#588)", "2026-09-19", "__highpay__"),
    ("自信あり 第3世代 (#589)", "2026-09-19", "__confident__"),
    ("自信あり 的中型に限定 (#596)", "2026-09-21", "__confident__"),
    ("逃げ先頭ライン 穴狙い（準決勝系・型E 除く・上限なし）", "2026-09-25", "L_lead"),
)


def section_perf(races: Sequence[SoldRace], conf: set[str],
                 pending: Sequence[Mapping[str, Any]], stale: Sequence[Mapping[str, Any]],
                 n_boot: int, seed: int) -> list[str]:
    lines = ["| 層 | n | 表示的中 | ROI | 95%CI | 監査の期待 | 判定 |",
             "|---|--:|--:|--:|---|---|---|"]
    for label, key, roi_band, hit_band, min_n in LAYERS:
        sub = _layer(races, key)
        s = summarize(sub)
        _, roi_ci = _boot_ci(sub, n_boot, seed)
        band = f"ROI {roi_band[0]:.0%}〜{roi_band[1]:.0%}" if roi_band else "—"
        lines.append(f"| {label} | {s.n_races} | {_pct(s.net_hit_rate)} "
                     f"| {_pct(s.roi)} | {_ci(roi_ci)} | {band} "
                     f"| {_verdict(roi_ci, roi_band, s.n_races, min_n)} |")

    lines += ["", "**今回の見直し商品**（変更日から積む）", "",
              "| 商品 | 観測開始 | n | 表示的中 | ROI | 判定 |", "|---|---|--:|--:|--:|---|"]
    for label, since, key in REVISED:
        if key == "__highpay__":
            sub = [r for r in races if (r.origin or "") == "highpay_fill" and r.race_date >= since]
        elif key == "__confident__":
            sub = [r for r in races if r.race_key in conf and r.race_date >= since]
        else:
            sub = [r for r in races if r.rank_key == key and r.race_date >= since]
        s = summarize(sub)
        lines.append(f"| {label} | {since} | {s.n_races} | {_pct(s.net_hit_rate)} "
                     f"| {_pct(s.roi)} | 判定不能（観測中） |")

    lines += ["",
              "🔴 **高額枠・看板枠・見直し商品は実売では判定しない。** 的中 3〜5% の商品は "
              "n=400 でも CI 幅が ±35pt ある（監査 §4.2）。ここは板と食い違っていないかを"
              "見るだけで、良し悪しは honest な板（`evidence/P1_highpay/`）で決める。",
              "🔴 ROI は**日でクラスタ**したブートストラップ。レース単位で抜くと CI が狭く出る。"]
    if pending:
        lines.append(f"⚠️ まだ採点できていない入稿 {len(pending)} 件"
                     f"（うち取りこぼし {len(stale)} 件）は集計に入れていない。"
                     f"**外れとして 0 円で足すと当たっている商品が回収率を下げる**。")
    return lines


# ── §4 健全性 ──────────────────────────────────────────────────
def section_health(start: str, end: str, pending: Sequence[Mapping[str, Any]],
                   stale: Sequence[Mapping[str, Any]], n_cancelled: int) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    with get_connection() as c:
        prop = [dict(r) for r in c.execute(
            "SELECT COUNT(*) AS n FROM netkeirin_submissions ns "
            "JOIN wt_races wr ON wr.race_key = ns.race_key "
            "WHERE ns.deleted_at IS NULL AND ns.status = 'proposed' "
            "  AND wr.race_date BETWEEN ? AND ?", (start, end))][0]["n"]
        skips = {dict(r)["reason_code"]: dict(r)["n"] for r in c.execute(
            "SELECT reason_code, COUNT(*) AS n FROM submission_skips "
            "WHERE race_date BETWEEN ? AND ? GROUP BY reason_code", (d0, d1))}
        pend = [dict(r) for r in c.execute(
            "SELECT COUNT(*) AS n FROM type_lab_picks "
            "WHERE race_date BETWEEN ? AND ? AND hit IS NULL", (start, end))][0]["n"]

    miss = int(skips.get("missing_lineup", 0))
    stale_mark = "✅" if not stale else "🔴"
    lines = [
        f"- 未採点の入稿: {len(pending)} 件（当日の発走前 {len(pending) - len(stale) - n_cancelled} 件"
        f"／中止 {n_cancelled} 件を含む）。"
        f"うち**取りこぼし {stale_mark} {len(stale)} 件**"
        f" … ここが積み上がるなら `settle_type_lab_picks.py --pending-only`（#590）を見る",
        f"- 型ラボの未採点行: **{pend} 件**（当日分は発走前なので 0 にはならない）",
        f"- `proposed`（未送信の案）: {prop} 件 … 実績の母集団からは #590 で除外済み",
        f"- 欠測ガードで止めた商品: {miss} 件"
        f"（並び・印が未公開。**朝の波では普通に出る**——公開前に組むため。"
        f"後の波で組み直されるので、ここが 0 でないこと自体は異常ではない）",
        f"- 軸信頼ゲート落ち: {int(skips.get('axis_gate', 0))} 件 ／ "
        f"入稿ゲート落ち: {int(skips.get('gate_mean_payout', 0))} 件 ／ "
        f"日次上限: {int(skips.get('daily_cap', 0))} 件",
        "",
        "⚠️ 決勝に商品が出ないのは**事故ではない**（2026-08-31 に「看板レースには必ず出す」を"
        "ユーザーが取り消し済み）。無商品の決勝はほぼ軸信頼ゲート落ち。",
    ]
    return lines


# ── 本体 ───────────────────────────────────────────────────────
def build(start: str, end: str, n_boot: int, seed: int) -> tuple[str, bool]:
    subs, finishes, payouts = _fetch(start, end)
    # 🔴 欠車を含む leg は返還（2026-09-20 監査 item2）。渡さないと Web の実売集計と
    #    投資額が食い違い、§3 の ROI だけ古い定義のままになる。
    races, _ = build_sold_races(
        subs, finishes, payouts,
        valid_cars_by_race(s["race_key"] for s in subs))
    # 🔴 「採点できていない」は**外れではない**。当日の発走前と、前日以前の
    #    取りこぼし（#590 で拾い直す対象）は別物なので分けて数える。
    settled = {r.race_key for r in races}
    today = date.today().isoformat()
    cancelled = _cancelled_keys(start, end)
    pending = [s for s in subs if str(s.get("race_key")) not in settled]
    stale = [s for s in pending if str(s.get("race_date") or "") < today
             and str(s.get("race_key")) not in cancelled]
    n_cancelled = sum(1 for s in pending if str(s.get("race_key")) in cancelled)
    conf = _confident_keys(start, end)
    cfg_lines, drifted = section_config(BASELINE)
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1

    out = [f"# 監査後の前向き観測　{start} 〜 {end}（{days}日）", "",
           "監査の正本: `keirin/docs/AUDIT_2026_09_20.md`／最優先KPI = **売上**。",
           "**商品は据え置き**。ここは観測だけで、採否はしない。", "",
           "## §1 商品構成（据え置きの確認）", "", *cfg_lines, "",
           "## §2 売上（最優先KPI）", "",
           *section_sales(start, end, subs, races, conf), "",
           "## §3 前向き成績（監査以降の累積）", "",
           *section_perf(races, conf, pending, stale, n_boot, seed), "",
           "## §4 健全性", "",
           *section_health(start, end, pending, stale, n_cancelled), ""]
    return ("\n".join(out), drifted)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=AUDIT_DAY)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--out", default="", help="Markdown の書き出し先")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--write-baseline", action="store_true",
                    help="いまの構成を基準として撮り直す（観測の起点が変わる）")
    args = ap.parse_args()

    if args.write_baseline:
        import subprocess
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                             capture_output=True, text=True).stdout.strip()
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(
            {"taken_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "git": sha,
             "note": "監査 2026-09-20 時点の商品構成。差分が出たら「据え置き」が破れている。",
             "config": live_config()}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"[audit_watch] 構成の基準を保存: {BASELINE}")
        return 0

    md, drifted = build(args.start, args.end, args.boot, args.seed)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md + "\n", encoding="utf-8")
        print(f"[audit_watch] 保存: {p}{'  🔴 構成が動いている' if drifted else ''}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
