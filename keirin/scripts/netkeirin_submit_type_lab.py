#!/usr/bin/env python3
"""型ラボのプランを netkeirin「ウマい車券」へ下書き入稿する（2026-08-28 新設）。

    python3 scripts/netkeirin_submit_type_lab.py YYYY-MM-DD morning [--dry-run]
    python3 scripts/netkeirin_submit_type_lab.py YYYY-MM-DD noon      # 残りを拾う
    python3 scripts/netkeirin_submit_type_lab.py YYYY-MM-DD evening

## なぜ既存の `netkeirin_submit_wt.py` に足さず別スクリプトにしたか

型ラボは**既存ランクの全面置き換え**であって、ランクを1本足す話ではない。
`RANK_CONFIGS` へ相乗りすると、候補JSON・ゲート・優先順位・傾斜配分といった
既存ランク専用の経路を通ることになり、**既存の入稿を壊すリスクを負う**。
移行の初日にそれは引き合わない。

そこでこのスクリプトは:

- **既存ランクの入稿には一切触らない**（`netkeirin_submit_wt.py` は無改造）
- 既存ランクは `netkeirin_settings.enabled = false` で止める
- ロールバックは **SQL 1本**（型ラボ false / 既存 true）。デプロイが要らない

共通の部品（設定の読み込み・締切判定・出走表HTML・見送り記録）は
`netkeirin_submit_wt` から **import して使い回す**。写すと二重管理になる。

## 商品の定義

1レースの型（A〜F）が売るプランをちょうど1つ決める（`src.type_lab.sell_plans_for`）。
**型は排他なので 1レース1商品が構造的に守られる**——優先順位の設計が要らない。

| 型 | プラン | 券種 |
|---|---|---|
| A 鉄板 | `A_hit` | 三連単 |
| B 堅い・中 | `B_hit` | 三連単 |
| C 崩れ筋 | `C_hit` | 三連単 |
| D 混戦・軸あり | `D_hit` | 三連複 |
| E 混戦・中 | `E_hit` | 三連単 |
| F 大混戦 | `F_pay` | 三連単 |

`rank_key` は **プラン名そのもの**（`A_hit` … `F_pay`）。`type_lab_picks.plan_key`
と同じ値なので、入稿と検証台が結合キーなしで突き合わせられる。

## 🔴 賭け金を作り直さない

買い目も配分も `type_lab_picks.legs` に入っている値を**そのまま**送る。
ここで配分し直すと「20か月測ったもの」と「売ったもの」が別物になる
（`CLAUDE.md` の検証の作法 §2 がまさにこの型の事故）。

## 波の扱い

朝は当日の全レースを対象にする（型ラボは予測オッズだけで組むので板を待つ理由が無い）。
昼・夕は**まだ入稿していないレースだけ**を拾う。その際

🔴 **買い目を組み直してから入稿する。** 朝に並び予想・AI印が未公開だったレースは
   `entry_health.missing_market_inputs` で見送るが、`type_lab_picks` の行自体は
   **欠測のまま作られている**（印なし＝最弱・ライン無し＝全員同ラインと読まれる）。
   組み直さずに入稿すると、その壊れた買い目を売ることになる。
🔴 組み直しは **`--race-key` で名指し**する。日全体を組み直すと、既に売った
   レースの行まで UPSERT で書き換わり、売ったものと記録が食い違う。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection                      # noqa: E402
from src.netkeirin_client import (                           # noqa: E402
    ACT_TYPE_DEFAULT,
    ACT_TYPE_LONGSHOT,
    BET_KIND_TRIFECTA_FORMATION,
    BET_KIND_TRIO_BOX,
    BetLeg,
    NetkeirinClient,
)
from src.notify.discord import send                          # noqa: E402
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS   # noqa: E402
from src.submission_skips import (                           # noqa: E402
    CANDIDATE_INVALID as SKIP_CANDIDATE_INVALID,
    CLOSED as SKIP_CLOSED,
    GATE_MEAN_PAYOUT as SKIP_GATE_MEAN_PAYOUT,
    GATE_POINT_ODDS as SKIP_GATE_POINT_ODDS,
    MISSING_LINEUP as SKIP_MISSING_LINEUP,
    SUBMIT_FAILED as SKIP_SUBMIT_FAILED,
)
from src.marquee import is_fill_target                       # noqa: E402
from src.type_lab import SELLABLE_PLAN_KEYS, sell_plans_for  # noqa: E402
from src.type_lab_submission import build_submission         # noqa: E402

# 🔴 **共通部品は既存スクリプトから import する**（写さない）。
#    ここに写した瞬間、締切の秒数・見送りの記録形式・出走表の列構成が
#    2箇所に分かれる。このリポジトリが繰り返し事故を起こした型。
from scripts.netkeirin_submit_wt import (                    # noqa: E402
    ORIGIN_RANK,
    REVIEW_URL,
    _already_submitted,
    _approval_required,
    auto_publish_submitted,
    _build_entry_table,
    _is_enabled,
    _load_closed_races,
    _load_settings,
    _missing_market_inputs,
    _record_submission,
    _skip,
    build_bet_detail,
)

#: 勝負アイコン。**型F（`F_pay`）だけ「穴狙い」**（2026-08-28・ユーザー決定）。
#:
#: 🟢 「自信あり」と違い **穴狙いは1日に何件でも付けられる**ので、選定は要らず
#:    プランで決め打ちできる。
#: 🔴 `F_pay` は「1着=◎固定・2着2車・3着流し」の4点で、表示的中 8.5%・
#:    払戻中央 56,580円・10万円超が 3.6日に1回。**この体系で唯一の一撃枠**
#:    （`SUMMARY.md` §4 を破るのは F_pay だけ）。アイコンと商品の性格が一致する。
#: ⚠️ `C_hit`（予測20倍以上）・`E_hit`（30倍以上）は帯こそ高いが表示的中は
#:    23.2% / 18.2% で、当たる回数を売る商品なので既定のままにしてある。
#:    変えるならこの表だけを直す。
#:
#: 🔴 **承認制では入稿時の act_type は使われない。** 承認経路
#:    （`netkeirin_submit_wt.approve_and_submit`）が送る値を決めるので、
#:    `build_bet_detail(..., act_type=...)` で**商品と一緒に持ち回る**。
#:    ⚠️ 「自信あり」に選ばれたレースはそちらが優先される（1日1件の明示的な
#:       選定なので、複数可の穴狙いが譲る）。実際には `F_pay` の Σp は低く
#:       自信ありに選ばれることはほぼ無い。
ACT_TYPE_BY_PLAN: dict[str, str] = {
    "A_hit": ACT_TYPE_DEFAULT,
    "A_trio": ACT_TYPE_DEFAULT,
    # 🔴 `A_ana` は**穴狙いそのもの**（指数1位を1点も買わない）。ここだけは
    #    商品の性格と一致するのでアイコンを付ける。F_hit を既定のままにした
    #    2026-08-30 の判断（「適用範囲を広げると切り分けが難しくなる」）とは
    #    別の話で、こちらは買い目からして穴狙いにしか読めない。
    "A_ana": ACT_TYPE_LONGSHOT,
    "B_hit": ACT_TYPE_DEFAULT,
    "C_hit": ACT_TYPE_DEFAULT,
    "D_hit": ACT_TYPE_DEFAULT,
    "E_hit": ACT_TYPE_DEFAULT,
    "F_pay": ACT_TYPE_LONGSHOT,
    # 🔴 9車の型F（決勝以外）で売る。**穴狙いアイコンは付けない**
    #    （2026-08-30 ユーザー判断「穴狙いのアイコンは現状のまま様子見」）。
    #    F_pay と同じ型だがアイコンの適用範囲を広げると効果の切り分けが
    #    さらに難しくなる（今も商品との交絡が切れていない）。
    "F_hit": ACT_TYPE_DEFAULT,
}

#: 軸信頼ゲートの正本。**backend 側のファイルを読み込んで束縛する**
#: （`src/marquee.py` が `keirin_marquee.py` を読むのと同じ形）。
#: 🔴 写経しないこと。閾値がプランごとに8つあり、片方だけ更新されると
#:    「画面のゲート」と「入稿のゲート」が静かに食い違う。
def _load_axis_gate():
    import importlib.util
    path = REPO.parent / "backend/src/services/keirin_type_lab_gate.py"
    if not path.exists():                      # pragma: no cover - 配備漏れの検知
        raise SystemExit(f"[type_lab_submit] 軸信頼ゲートの正本が見つかりません: {path}")
    spec = importlib.util.spec_from_file_location("keirin_type_lab_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_GATE = _load_axis_gate()


# ───────────────────────────── 読み出し ─────────────────────────────

def _load_rows(day: str) -> list[dict]:
    """当日の型ラボの行（`mode` が live / live9）。売るプランだけに絞って返す。"""
    with get_connection() as conn:
        # `cup_grade` は `type_lab_picks` に無いので `wt_races` から引く
        # （看板判定 `is_fill_target` がグレードを先に見るため）。
        rows = conn.execute(
            "SELECT t.race_key, t.race_date, t.venue_name, t.race_no, t.race_type,"
            "       t.n_entries, t.type_label, t.axis_sum, t.pw_ent, t.axis1, t.axis2,"
            "       t.p3_order,"
            "       t.mode, t.plan_key, t.bet_type, t.n_legs, t.budget, t.legs,"
            "       t.pred_mean_payout, t.pred_min_payout, t.rule_version,"
            "       t.generated_at, r.cup_grade"
            "  FROM type_lab_picks t"
            "  LEFT JOIN wt_races r ON r.race_key = t.race_key"
            " WHERE t.race_date = ? AND t.mode IN (?, ?)",
            (day, "live", "live9"),
        ).fetchall()
    # 🔴 **1レース1型。** 組み直しで型が変わると古い型の行が残ることがあり
    #    （2026-08-29 に4レースで実際に起きた）、行ごとに `type_label` を見る
    #    下の判定は古い行も「その型なら売ってよい」と通してしまう。
    #    生成側（`build_type_lab_picks._drop_stale_plans`）で消しているが、
    #    **読む側でも最新の型だけに絞る**（消し漏れがそのまま二重入稿になるため）。
    current: dict[tuple[str, str], tuple] = {}
    for r in rows:
        d = dict(r)
        key = (str(d["race_key"]), str(d["mode"]))
        gen = d.get("generated_at")
        if key not in current or (gen is not None and current[key][0] is not None
                                  and gen > current[key][0]):
            current[key] = (gen, str(d["type_label"]))

    # 🔴 **型A の売り分けにはレース単位の情報が要る**（2026-08-31）。
    #    `A_trio` を選ぶ条件は「三連複2点が入稿ゲートを通ること」なので、
    #    同じレースの `A_trio` 行の `pred_mean_payout` を先に集めておく。
    #    ⚠️ **1点でも予測 2.0倍未満なら通らない**ので、平均だけ見て決めない
    #       （`_gate_reason` と同じ2条件を当てる）。
    trio_ok: dict[str, bool] = {}
    for r in rows:
        d = dict(r)
        if d["plan_key"] != "A_trio":
            continue
        legs = json.loads(d["legs"]) if isinstance(d["legs"], str) else (d["legs"] or [])
        trio_ok[str(d["race_key"])] = _gate_reason(dict(d, legs=legs)) is None

    out = []
    for r in rows:
        d = dict(r)
        if d["plan_key"] not in SELLABLE_PLAN_KEYS:
            continue
        if str(d["type_label"]) != current[(str(d["race_key"]), str(d["mode"]))][1]:
            continue        # 組み直し前の古い型の行
        # 🔴 **行があること自体を根拠にしない。** 生成側は8プラン組むので
        #    `SELL_PLANS` の絞りだけでは 9車の型F の種別条件が効かない。
        #    売る／売らないの判定は必ず `sell_plans_for` を通す（唯一の正本）。
        allowed = {p.key for p in sell_plans_for(
            str(d["type_label"]), int(d["n_entries"] or 7), d.get("race_type"),
            pw_ent=(float(d["pw_ent"]) if d.get("pw_ent") is not None else None),
            trio_ok=trio_ok.get(str(d["race_key"])))}
        if d["plan_key"] not in allowed:
            continue
        d["legs"] = json.loads(d["legs"]) if isinstance(d["legs"], str) else (d["legs"] or [])
        out.append(d)
    return out


def races_taken_by_other_ranks(already: set[tuple[str, str]]) -> set[str]:
    """**型ラボ以外のランク**が既に取っているレース。

    🔴 **netkeirin は1レース1商品。** ループの `(race_key, plan) in already` は
       同じランクの二重入稿しか止めないので、既存ランク・看板穴埋めが取った
       レースへ型ラボが出すと **netkeirin 上で既存の商品を上書きする**。
       全面置換後は既存が全部 OFF なので普段は空だが、移行期・ロールバック中・
       看板穴埋めが動いた日に効く。

    🔴 **`_already_submitted()` はランクを絞らず全ランクの行を返す**（取消も
       「その日は処理済み」として含む）。だからここで別クエリを投げる必要はなく、
       投げると二重管理になる。
       ⚠️ 初版は専用クエリを書いたうえで `already` の race_key を差し引いており、
          `already` が全ランクを含むせいで**常に空集合**になっていた
          （＝ガードが一度も効かない。2026-08-28 の dry-run で発覚）。
    """
    return {rk for rk, rank in already if rank not in SELLABLE_PLAN_KEYS}


def _combo_cars(combo: str) -> list[int]:
    return [int(x) for x in combo.replace("=", "-").split("-") if x.strip().isdigit()]


def _legs_of(row: dict) -> tuple[list[BetLeg], dict]:
    """`type_lab_picks.legs` → (入稿する買い目行, {買い目: 予測オッズ})。

    🔴 **1点=1行**（`netkeirin_submit_wt._dutch_point_legs` と同じ形）。型C の12点・
       型E の14点は 1着列×2着列×3着列 のフォーメーションに畳めない
       （畳むと買っていない目が混入する）。
    """
    legs: list[BetLeg] = []
    odds: dict = {}
    trio = str(row.get("bet_type")) == "trio"
    for lg in row["legs"]:
        cars = _combo_cars(str(lg["combo"]))
        stake = int(lg["stake"])
        if len(set(cars)) != 3 or stake <= 0:
            continue
        if trio:
            legs.append(BetLeg(BET_KIND_TRIO_BOX, [sorted(cars)], stake))
            odds[frozenset(cars)] = float(lg.get("pred_odds") or 0)
        else:
            legs.append(BetLeg(BET_KIND_TRIFECTA_FORMATION,
                               [[cars[0]], [cars[1]], [cars[2]]], stake))
            odds[tuple(cars)] = float(lg.get("pred_odds") or 0)
    return legs, odds


# ───────────────────────────── ゲート ─────────────────────────────

def _gate_reason(row: dict) -> tuple[str, str] | None:
    """入稿ゲートに掛かるなら (理由コード, 説明) を返す。通るなら None。

    🔴 **判定できないものは通す**（`stake_allocation` の各ゲートと同じ思想）。
       分からないことを理由に商品を落とさない。
    """
    mean_pay = row.get("pred_mean_payout")
    if mean_pay is not None and float(mean_pay) <= MIN_MEAN_PAYOUT:
        return (SKIP_GATE_MEAN_PAYOUT,
                f"買い目の平均想定払戻 {float(mean_pay):,.0f}円 <= {MIN_MEAN_PAYOUT:,}円")
    odds = [float(lg.get("pred_odds") or 0) for lg in row["legs"]]
    odds = [o for o in odds if o > 0]
    if odds and min(odds) < MIN_POINT_ODDS:
        return (SKIP_GATE_POINT_ODDS,
                f"予測 {min(odds):.1f} 倍の目があります（下限 {MIN_POINT_ODDS} 倍）")
    return None


def _is_marquee(row: dict) -> bool:
    """看板レース（＝穴埋め対象）か。判定は `keirin_marquee` の正本に束縛。"""
    return bool(is_fill_target(row.get("race_type"), row.get("cup_grade")))


def _make_skip(dry_run: bool):
    """見送りの記録関数。**dry-run では何も書かない**。

    🔴 `_skip()` は `submission_skips` への INSERT と取消理由の張り替えを行う。
       dry-run は「何も変えずに中身を見る」ものなので、ここも通してはいけない。
       ログだけは出す（何が落ちたか見えないと dry-run の意味が無い）。
    """
    if not dry_run:
        return _skip

    def _dry(race_key, rank_key, session, code, detail, venue_name=None,
             race_no=None, *, tag="スキップ", quiet=False):
        if not quiet:
            where = f"{venue_name}{race_no}R" if venue_name is not None else race_key
            print(f"[type_lab_submit] (dry-run) {tag} {where} ({rank_key}): {detail}",
                  flush=True)
    return _dry


def _passes_axis_gate(row: dict) -> bool:
    """軸信頼ゲートを通るか。**看板レースは素通しする**（2026-08-28・ユーザー判断）。

    🔴 「看板レースには必ず推奨を出す」（2026-08-09 のユーザー方針）を優先する。
       軸信頼ゲートは看板の **26%**（3.78件/日・確認窓 2026 実測 899/3,460件）を
       落とすので、素で当てると売上の 84% が集まる層で商品が消える。
    ⚠️ 代償は、落ちる側の層（表示的中 18.74%・ROI 68.7%）が看板に混ざること。
       商品構成として測った 22.0% / 78.7% は**看板を含めた値ではない**。
    ⚠️ **素通しするのは軸信頼ゲートだけ。** 平均払戻 20,000円 と 1点 2.0倍 は
       看板にも掛ける。あちらはユーザーが明示的に決めた入稿方針で、
       看板へ広げるかは別途の判断が要る（`stake_allocation` の注記）。
       実測の影響も 3,460件中 1件 と無視できる大きさ。
    """
    if _is_marquee(row):
        return True
    return bool(_GATE.passes_axis_gate(
        str(row["plan_key"]),
        float(row["axis_sum"]) if row.get("axis_sum") is not None else None,
        int(row["n_entries"]) if row.get("n_entries") is not None else None))


# ───────────────────────────── 組み直し ─────────────────────────────

def rebuild(day: str, race_keys: list[str], n_entries: int) -> None:
    """名指しのレースだけ買い目を組み直す（昼・夕の波用）。

    🔴 **日全体を組み直さない。** `type_lab_picks` の一意キーは
       (race_key, plan_key, mode) なので、日全体を回すと**既に売ったレースの
       買い目まで UPSERT で書き換わる**。売ったものと記録が食い違うと、
       採点も店頭の説明も後から復元できない。
    """
    if not race_keys:
        return
    cmd = [sys.executable, "scripts/build_type_lab_picks.py", "--mode", "live",
           "--date", day, "--n-entries", str(n_entries)]
    for k in race_keys:
        cmd += ["--race-key", k]
    print(f"[type_lab_submit] {n_entries}車 {len(race_keys)}R を組み直します", flush=True)
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if r.returncode != 0:
        # 🔴 組み直しの失敗で入稿を止めない。組み直せなかったレースは
        #    古い行のままなので、`missing_market_inputs` が改めて弾く。
        print(f"[type_lab_submit] ⚠️ 組み直しに失敗（継続）: {r.stderr.strip()[-400:]}",
              flush=True)


# ───────────────────────────── 入稿 ─────────────────────────────

def _print_detail(row: dict, sub: dict, detail: str) -> None:
    """dry-run で入稿データを1件まるごと見せる（目視確認用）。

    🔴 **`type_lab_picks` の値と突き合わせられる形で出す。** 点数・賭け金・
       予測オッズ・想定払戻を並べておかないと「測ったものを売っているか」を
       目で確かめられない。
    """
    lines = json.loads(detail).get("lines", [])
    total = sum(int(x["stake"] or 0) for x in lines)
    marks = " ".join(f"{c}{m}" for c, m in sorted(sub["marks"].items()))
    print(f"\n{'─' * 78}")
    print(f"{row['venue_name']}{row['race_no']}R {row.get('race_type') or ''} "
          f"[{row['n_entries']}車 型{row['type_label']} / {row['plan_key']}] "
          f"軸信頼 {float(row['axis_sum'] or 0):.3f}")
    print(f"  タイトル : {sub['title']}")
    print(f"  印       : {marks}")
    print(f"  買い目   : {len(lines)}点 / 合計 {total:,}円 "
          f"（記録 n_legs={row['n_legs']} 予算={row['budget']:,}円）")
    for x in lines:
        o = x.get("odds")
        pay = int(x["stake"]) * float(o) if o else 0
        print(f"      {x['combo']:>9}  {int(x['stake']):>6,}円  予測 {o if o else '—':>7}倍"
              f"  想定払戻 {pay:>9,.0f}円")
    print(f"  想定払戻 : 平均 {float(row['pred_mean_payout'] or 0):,.0f}円 / "
          f"最低 {float(row['pred_min_payout'] or 0):,.0f}円")
    body = sub["comment"].split("【参考データ】")[0].rstrip()
    print("  コメント :\n" + "\n".join("    " + l for l in body.splitlines()))


def submit_row(row: dict, session: str, client: NetkeirinClient | None,
               dry_run: bool, show_detail: bool = False,
               skip=None) -> tuple[bool, str]:
    """1行を入稿する。戻り値 (入稿したか, メッセージ)。"""
    race_key = str(row["race_key"])
    plan = str(row["plan_key"])
    venue = str(row["venue_name"] or "?")
    race_no = int(row["race_no"])
    n_cars = int(row["n_entries"] or 7)
    skip = skip or _make_skip(dry_run)
    legs, pred_odds = _legs_of(row)
    if not legs:
        skip(race_key, plan, session, SKIP_CANDIDATE_INVALID,
              "買い目が空です", venue, race_no)
        return False, "買い目が空"

    sub = build_submission(row, None)
    marks = sub["marks"]
    entry_html = _build_entry_table(race_key, marks)
    sub = build_submission(row, entry_html)

    act_type = ACT_TYPE_BY_PLAN.get(plan, ACT_TYPE_DEFAULT)
    # 🔴 **アイコンを買い目と一緒に保存する。** 承認制では入稿時ではなく
    #    承認時に netkeirin へ送るので、ここで渡した `act_type` は使われない。
    #    `approve_and_submit` が `bet_detail` からこれを読む。
    # 🔴 **車数を必ず入れる。** 承認経路は印の数から車数を導くフォールバックを
    #    持つが、型ラボは**買っていない車に印を付けない**（`marks_for`）ので
    #    それでは足りず、「7/9車のみ対応」で承認が丸ごと失敗する。
    detail = build_bet_detail(legs, source="type_lab", marks=marks,
                              predicted_odds=pred_odds, act_type=act_type,
                              n_cars=n_cars)
    if dry_run:
        if show_detail:
            _print_detail(row, sub, detail)
        return True, sub["title"]

    ok, msg = client.submit_pick_multi(
        race_date=date.fromisoformat(str(row["race_date"])),
        venue_name=venue, race_no=race_no, n_cars=n_cars,
        legs=legs, marks=marks, title=sub["title"], comment=sub["comment"],
        act_type=act_type,
    )
    if not ok:
        skip(race_key, plan, session, SKIP_SUBMIT_FAILED, msg, venue, race_no)
        return False, msg
    _record_submission(
        race_key, plan, session, venue, race_no, None,
        int(row["axis1"]), int(row["axis2"]), msg, bet_detail=detail,
        title=sub["title"], comment=sub["comment"], origin=ORIGIN_RANK)
    return True, sub["title"]


def run(day: str, session: str, dry_run: bool, only_key: str | None,
        do_rebuild: bool, show: int = 0) -> None:
    settings = _load_settings()
    propose_only = _approval_required()
    closed = _load_closed_races(day)

    rows = _load_rows(day)
    if only_key:
        rows = [r for r in rows if str(r["race_key"]) == only_key]
    if not rows:
        print(f"[type_lab_submit] {day} {session}: 対象の行がありません")
        return

    already = _already_submitted(sorted({str(r["race_key"]) for r in rows}))
    # 🔴 **別ランクが取ったレースには出さない**（netkeirin は1レース1商品）。
    taken = races_taken_by_other_ranks(already)
    # 🔴 **型ラボ自身が取ったレースにも出さない。** `(race_key, plan) in already` は
    #    同じプランの二重入稿しか止めないので、**別プランなら通ってしまう**。
    #    組み直しで型が変わったレースがまさにこれで、2026-08-29 の昼に4レースが
    #    「型Cの商品」と「組み直し前の型Fの商品」を同時に出した。
    #    ⚠️ 取消済みも `already` に含まれる＝取り消したレースは出し直さない
    #      （看板穴埋めと同じ方針）。
    taken_by_type_lab = {rk for rk, rank in already if rank in SELLABLE_PLAN_KEYS}

    # ── 昼・夕は「まだ入稿していないレース」を組み直してから読み直す ──
    # 🔴 **dry-run では組み直さない。** 組み直しは `type_lab_picks` への
    #    書き込みなので、「何も変えずに中身を見る」という dry-run の約束を破る。
    #    2026-08-28 の初回検証で実際に本番の行を書き換えてしまった。
    if do_rebuild and session != "morning" and not dry_run:
        todo = {int(r["n_entries"] or 7): [] for r in rows}
        for r in rows:
            rk = str(r["race_key"])
            if (rk, str(r["plan_key"])) in already or rk in closed:
                continue
            todo[int(r["n_entries"] or 7)].append(rk)
        for n_cars, keys in todo.items():
            rebuild(day, sorted(set(keys)), n_cars)
        rows = _load_rows(day)
        if only_key:
            rows = [r for r in rows if str(r["race_key"]) == only_key]
    elif do_rebuild and session != "morning" and dry_run:
        n_todo = len({str(r["race_key"]) for r in rows
                      if (str(r["race_key"]), str(r["plan_key"])) not in already
                      and str(r["race_key"]) not in closed})
        print(f"[type_lab_submit] (dry-run のため組み直しは行いません: 対象 {n_todo}R)",
              flush=True)

    n_ok = 0
    skip = _make_skip(dry_run)
    skipped: dict[str, int] = {}
    titles: list[str] = []
    #: Discord の内訳用（`(会場, プラン)`）。**レース名は入れない**——通知は
    #: 件数と内訳だけにする（一覧は上の print で cron.log に残る）。
    submitted: list[tuple[str, str]] = []
    client = None if dry_run else NetkeirinClient(propose_only=propose_only)

    def bump(code: str) -> None:
        skipped[code] = skipped.get(code, 0) + 1

    for row in sorted(rows, key=lambda r: (str(r["venue_name"] or ""), int(r["race_no"]))):
        race_key = str(row["race_key"])
        plan = str(row["plan_key"])
        venue = str(row["venue_name"] or "?")
        race_no = int(row["race_no"])

        if not _is_enabled(settings, plan):
            bump("disabled")
            continue
        if (race_key, plan) in already:
            bump("already")
            continue
        if race_key in taken:
            # 別ランク（既存商品・看板穴埋め）が既に取っている。入稿失敗ではない。
            bump("taken_by_other_rank")
            continue
        if race_key in taken_by_type_lab:
            # 型ラボの別プランが既に取っている（1レース1商品）。
            bump("taken_by_type_lab")
            continue
        if race_key in closed:
            # ⚠️ ログは静かに（波ごとに終わった開催が毎回並ぶと読めない）。
            #    記録は1件ずつ残す＝画面で「締切超過」として出る。
            skip(race_key, plan, session, SKIP_CLOSED,
                  "発走15分前を過ぎていました", quiet=True)
            bump("closed")
            continue
        # 🔴 並び予想・AI印が未公開のレースは出さない（2026-08-26 の熊本7Rの型）。
        #    欠測は欠測として扱われず、印なし＝最弱・ライン無し＝全員同ラインと
        #    読まれた買い目が例外もログも無しに出来上がる。
        lineup = _missing_market_inputs(race_key)
        if lineup:
            skip(race_key, plan, session, SKIP_MISSING_LINEUP,
                  f"{lineup} → この回は見送り（後の波で再判定）", venue, race_no)
            bump("lineup")
            continue
        if not _passes_axis_gate(row):
            # 軸信頼ゲートは「商品の定義」であってゲート落ちではない。
            # 記録すると毎日10件前後が見送り一覧を埋めて信号が死ぬので数だけ数える。
            bump("axis_gate")
            continue
        reason = _gate_reason(row)
        if reason:
            skip(race_key, plan, session, reason[0], reason[1], venue, race_no)
            bump(reason[0])
            continue

        ok, msg = submit_row(row, session, client, dry_run,
                             show_detail=dry_run and n_ok < show, skip=skip)
        if ok:
            # 🔴 **この回の中でも1レース1商品**（`already` は開始時の断面なので、
            #    同じ実行の中で2つ目のプランが通るのを止められない）。
            taken_by_type_lab.add(race_key)
            n_ok += 1
            titles.append(f"{venue}{race_no}R({plan}) {msg}")
            submitted.append((str(venue), str(plan)))
        else:
            bump("failed")

    tag = "[dry-run] " if dry_run else ""
    print(f"[type_lab_submit] {tag}{day} {session}: 入稿 {n_ok}件  "
          f"見送り {dict(sorted(skipped.items()))}", flush=True)
    for t in titles:
        print(f"  + {t}")

    # 🔴 自動公開は**通知より先**（文面が「下書き」から「公開済み」に変わる）。
    #    承認制のときは netkeirin へ送ったものが無いので必ず空になる。
    published = auto_publish_submitted(dry_run)
    n_published = sum(1 for r in published if r.get("ok"))
    n_publish_ng = len(published) - n_published

    if n_ok and not dry_run:
        # 🔴 チャンネルキーは `src/notify/discord.py::_WEBHOOK_ENV_KEYS` にあるものだけ。
        #    2026-08-28〜29 は存在しない "keirin" を渡していて **毎回 ValueError で
        #    落ちていた**（入稿そのものは終わっているのに `type_lab_daily.sh` が
        #    「入稿に失敗」と記録し、Discord には1通も出ていなかった）。
        #    有効なキーであることは `tests/test_discord_channels.py` が機械的に固定する。
        # 🔴 **通知の失敗で入稿を失敗扱いにしない。** ここへ来た時点で netkeirin
        #    への送信は終わっている。例外を上げると呼び出し側が再実行を考える。
        try:
            # 🔴 **レースを1件ずつ並べない**（2026-08-30 ユーザー指摘）。50件超が
            #    スマホで数画面ぶん流れて、肝心の件数が埋もれる。出すのは件数だけ。
            #    レース名の一覧は cron.log に残っている（上の print）。
            #
            # 🔴 **本文はモードで変える**（2026-08-30 ユーザー指定）:
            #      公開まで済んでいる → 何を売ったかが確定しているので**ランク別の件数**
            #      下書き／入稿案のまま → まだ人の操作が要るので**確認ページのリンク**
            #    「公開したのにリンクを出す」と何もすることが無いのにページを開かせ、
            #    「下書きなのに内訳だけ出す」と承認を促す導線が消える。
            if n_published:
                c = Counter(p for _, p in submitted)
                body = "ランク別 " + " ・ ".join(
                    f"{k} {v}" for k, v in
                    sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))
            else:
                body = f"入稿確認 → {REVIEW_URL}"
            if n_publish_ng:
                body += f"\n⚠️ 公開失敗 {n_publish_ng}件（下書きのまま）"
            send(f"📮 **NetKeirin入稿 {n_ok}件**（{day} / {session}）\n{body}",
                 channel="netkeirin")
        except Exception as e:      # noqa: BLE001 — 通知は付随情報
            print(f"[type_lab_submit] Discord通知失敗（入稿は完了している）: {e!r}",
                  flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=date.today().isoformat())
    ap.add_argument("session", nargs="?", default="morning",
                    choices=("morning", "noon", "evening"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--race-key", help="このレースだけ（手動入稿用）")
    ap.add_argument("--show", type=int, default=0,
                    help="dry-run で先頭 N 件の入稿データを丸ごと表示する")
    ap.add_argument("--no-rebuild", action="store_true",
                    help="昼・夕でも買い目を組み直さない（調査用）")
    a = ap.parse_args()
    print(f"[type_lab_submit] {datetime.now():%F %T} {a.date} {a.session}"
          f"{' (dry-run)' if a.dry_run else ''}", flush=True)
    run(a.date, a.session, a.dry_run, a.race_key, not a.no_rebuild, a.show)


if __name__ == "__main__":
    main()
