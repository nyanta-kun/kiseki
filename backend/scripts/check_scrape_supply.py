#!/usr/bin/env python3
"""指数の**上流**（sekito スクレイプ供給）の死活監視。

背景（2026-09-05 の障害調査で発覚）:
    指数そのものの死活は `check_feature_health.py`（週次・Mac の LaunchAgent
    `com.kiseki.feature-health`）が見ている。しかし 2026-09-05 に、その網から
    こぼれる形で **2 か所が同時に、数か月にわたって静かに切れていた**。

      1. `sync-jra-from-jvlink` の `ON CONFLICT DO NOTHING` で
         `sekito.races.start_time` が 00:00 に固定され、`netkeiba-paddock` が
         「発走20分以内」に永久に一致しなくなっていた。3 分ごとに
         「対象レースなし」と success を出し続けるので、ジョブの成否からは
         一切見えない。`p_rank` は 2026-08-22/23 の 2 日を除き 7 月以降ずっと 0 件。
      2. `scheduler.js` の既定タイムアウト 10 分が `netkeiba-index`（所要 約36分）を
         毎日 kill していた。処理順が中央→地方のため **土日は地方が全滅**。
         中央も 57 レース中 16 レースまでしか進んでいなかった。

    なぜ既存の監視で捕まらなかったか:
      - `check_feature_health.py` は `paddock_index` の sd=0 を正しく検出していたが、
        **`KNOWN_ISSUES` に登録されていたため `[要対応] なし` を返していた**。
        上流が「恒久的に死んでいる」前提で抑制した判断が、上流を直せる状態に
        なった後も残り続けた。
      - 同スクリプトは月次 sd を見るので**遅行指標**でもある。供給が切れてから
        指標が動くまで最大 1 か月かかる。
      - `netkeiba-index` の失敗は `sekito.script_requests` に `failed` として
        毎日記録されていた。**DB に答えが書いてあるのに誰も見ていなかった。**

設計方針:
    「指数が変になったか」ではなく「**入力が届いたか**」を当日中に、実行の
    事実ベースで見る。5 つのチェックはいずれも今回の 2 件を名指しで捕まえる。

    🔴 VPS の backend コンテナで動かせるよう、DB アクセスは **SQLAlchemy の
    `SyncSessionLocal`** を使う（psycopg2 は入っていない）。
    `feature_health_weekly.sh` が Mac 側に置かれているのは psycopg2 依存が
    理由なので、こちらはその制約を受けず **VPS の cron で日次実行できる**。

🔴 実行時刻と「対象日」の設計（ここを間違えると誤検知する）:
    **07:00 JST に実行する前提**で、チェックごとに見る日が違う。

      - 発走時刻チェック → **当日**（= 対象日の翌日）
      - 網羅率・ジョブ失敗   → **前日**（対象日そのもの。既に確定している）

    つまり `--date` の既定は **昨日**。当日の発走時刻を見るのは
    `nxt = target + 1` で表現している。

    なぜ夜ではなく朝か: sekito の `sync-jra-from-jvlink` は毎日 06:00 に走る。
    日曜ぶんの発走時刻は「土曜 11:30 に JRA が公開 → 土曜 12:00 に kiseki の
    daily_trigger が keiba.races へ取り込み → **日曜 06:00 の同期**で
    sekito.races に入る」という順で埋まる。土曜の夜に翌日ぶんを見ると
    まだ 00:00 なので、**毎晩かならず誤検知する**。
    06:00 の同期後・08:30 の netkeiba-index 前・初レース 09:50 前、という
    条件を満たす 07:00 が唯一の正しい窓。

チェックと閾値の根拠:
    1. 当日 JRA の発走時刻 — `start_time` が 00:00 または NULL のレースが 1 件でも
       あれば WARN。**その日のパドックが空振りすることを、始まる前に知らせる**のが狙い。
    2. スクレイプジョブの失敗 — 当日の `script_requests.status='failed'` が 1 件でも
       あれば WARN。今回の netkeiba-index はこれだけで検知できた。
    3. netkeiba のレース網羅率 — 当日の開催レース数に対し、`sekito.netkeiba` に
       1 行でもあるレースの割合。**中央・地方を必ず分けて見る**（今回の障害は
       「中央 0.28 / 地方 0.00」という、合算では薄まって見えない形で出た）。
       タイム指数は**新馬戦を分母から除く**。過去走から作られる指標なので初出走馬
       しかいない新馬戦には原理的に存在しない（実測: 直近60日の中央で 62R 中 0R）。
       除外しないと正常な日でも中央が 83% 前後に留まり、閾値 80% すれすれになる。
    4. 吉馬のレース網羅率 — 同上。正常時は実測 1.0。
       ⚠️ 吉馬は**新馬戦でも取れている**（2026-09-05 実測 36/36R）ので、
       3 の新馬除外をここへ広げないこと。
    5. 穴ぐさの当日ピック — 中央開催日にピックが 0 件なら WARN。穴ぐさは
       全レースにピックが付くわけではないので、比率ではなく有無で見る。

    網羅率の既定閾値 0.8 は「数レースの取りこぼしは許すが、体系的な欠落は
    捕まえる」水準。障害時の実測値（中央 0.28 / 地方 0.00）とは大きく離れており、
    正常時の実測値（吉馬 1.0）とも十分な余裕がある。

    ⚠️ パドックの閾値だけは既定 0.5 と緩くしてある。**2026-09-05 時点で健全な
    パドック取得の実績が存在しない**（7月以降ずっと 0 件だった）ため、正常時の
    網羅率が分からない。1 週間ぶん健全なデータが溜まったら実測して締め直すこと。

使い方:
    # VPS cron（07:00 JST）— ラッパ経由
    0 7 * * * /home/ysuzuki/GitHub/kiseki/scripts/scrape_supply_check.sh \
      >> /home/ysuzuki/GitHub/kiseki/logs/scrape_supply_check.log 2>&1

    # 手動（コンテナ内・DB は SyncSessionLocal が .env から解決する）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/check_scrape_supply.py

    # Discord へ通知する（異常があるときだけ送る）
    ... scripts/check_scrape_supply.py --notify

    # 過去日を検証する（--date は「網羅率を見る日」。発走時刻はその翌日を見る）
    ... scripts/check_scrape_supply.py --date 2026-09-05

終了コード: 全チェック正常なら 0、1 件でも WARN があれば 1。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import text  # noqa: E402

from src.db.session import SyncSessionLocal  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")

# 開催レース数に対する取得レース数の比。これを下回ったら WARN。
COVERAGE_MIN = 0.8
# パドックだけは健全時の実績が無いため暫定値（要再較正・docstring 参照）
PADDOCK_COVERAGE_MIN = 0.5


class Report:
    """WARN を溜めて、まとめて出力・通知するための入れ物。"""

    def __init__(self) -> None:
        self.warns: list[str] = []
        self.lines: list[str] = []

    def ok(self, msg: str) -> None:
        self.lines.append(f"  OK   {msg}")

    def warn(self, msg: str) -> None:
        self.warns.append(msg)
        self.lines.append(f"  WARN {msg}")

    def info(self, msg: str) -> None:
        self.lines.append(f"       {msg}")


def _race_counts(s, target: date) -> tuple[int, int]:
    """当日の開催レース数を (中央, 地方) で返す。"""
    row = s.execute(
        text(
            "select count(*) filter (where left(course_code,1)='J'),"
            "       count(*) filter (where left(course_code,1)<>'J')"
            "  from sekito.races where date = :d"
        ),
        {"d": target},
    ).one()
    return int(row[0]), int(row[1])


def check_next_day_post_time(s, target: date, rep: Report) -> None:
    """① 当日 JRA の発走時刻が入っているか（sync-jra-from-jvlink の回帰検知）。

    07:00 実行では target が前日なので、`target + 1` = 当日を見ることになる。
    """
    nxt = target + timedelta(days=1)
    row = s.execute(
        text(
            "select count(*),"
            "       count(*) filter (where start_time is null"
            "                          or cast(start_time as time) = time '00:00')"
            "  from sekito.races"
            " where date = :d and left(course_code,1) = 'J'"
        ),
        {"d": nxt},
    ).one()
    total, missing = int(row[0]), int(row[1])

    if total == 0:
        rep.info(f"当日({nxt}) の中央開催なし — 発走時刻チェックはスキップ")
        return
    if missing:
        rep.warn(
            f"当日({nxt}) の中央 {total}R のうち {missing}R の発走時刻が 00:00/NULL。"
            " netkeiba-paddock が対象レースを見つけられず空振りする。"
            " sync-jra-from-jvlink の ON CONFLICT を確認すること"
        )
    else:
        rep.ok(f"当日({nxt}) の中央 {total}R すべてに発走時刻が入っている")


def check_job_failures(s, target: date, rep: Report) -> None:
    """② 当日のスクレイプジョブが失敗していないか。"""
    rows = s.execute(
        text(
            "select ss.script_name, count(*)"
            "  from sekito.script_requests sr"
            "  left join sekito.scripts_schedules ss on ss.id = sr.schedule_id"
            " where cast(sr.created_at as date) = :d and sr.status = 'failed'"
            " group by 1 order by 2 desc"
        ),
        {"d": target},
    ).all()

    if not rows:
        rep.ok("当日のスクレイプジョブに failed なし")
        return
    for name, n in rows:
        rep.warn(f"ジョブ失敗: {name} が {n} 回 failed（タイムアウト kill もここに出る）")


def _coverage(
    s, table: str, target: date, column: str | None = None, *, skip_maiden: bool = False
) -> tuple[int, int, int, int]:
    """開催レースを分母に、(中央 分母, 中央 取得, 地方 分母, 地方 取得) を返す。

    分母は `sekito.races`（＝実際に開催されたレース）で、分子はそのうち対象
    テーブルに 1 行でもデータがあるレース。**分子と分母に同じ除外を掛ける**ため、
    データ側ではなくレース側から数える。

    skip_maiden: 新馬戦を分母から外す。タイム指数は過去走から作られるので、
        初出走馬しかいない新馬戦には**原理的に存在しない**。
        実測（2026-09-05 時点・直近60日の中央）:
            新馬     62R 中   0R 取得 (0%)   ← 構造的にゼロ
            未勝利  230R 中 108R 取得 (47%)  ← タイムアウトによる欠損
            その他  356R 中 123R 取得 (35%)  ← 同上
        除外しないと、正常な日でも中央が 83% 前後に留まり閾値 80% すれすれになる。
        ⚠️ 吉馬は新馬戦でも取れている（2026-09-05 実測 36/36R）ので、この除外を
        他のチェックへ広げないこと。パドックも当日の目視評価なので新馬にも存在する。
    """
    cond = f" and n.{column} is not null" if column else ""
    # 🔴 coalesce は必須。`race_name not like ...` は race_name が NULL のとき
    #    NULL を返すので、その行は分母からも分子からも**黙って消える**。
    #    地方は実測でレースの約半分が race_name NULL（2026-09-05: 21R 中 13R）。
    #    coalesce 無しだと 9/4 の地方が 19/36R → 8/19R に化けた。
    #    この監視自体が「静かに数が減る」型で壊れては本末転倒なので、ここは触らない。
    maiden = " and coalesce(r.race_name, '') not like '%新馬%'" if skip_maiden else ""
    row = s.execute(
        text(
            "with x as ("
            "  select r.course_code, exists ("
            f"      select 1 from sekito.{table} n"
            "       where n.date = r.date and n.course_code = r.course_code"
            f"         and n.race_no = r.race_no{cond}"
            "  ) as got"
            f"   from sekito.races r where r.date = :d{maiden}"
            ")"
            "select count(*) filter (where left(course_code,1) = 'J'),"
            "       count(*) filter (where left(course_code,1) = 'J' and got),"
            "       count(*) filter (where left(course_code,1) <> 'J'),"
            "       count(*) filter (where left(course_code,1) <> 'J' and got)"
            "  from x"
        ),
        {"d": target},
    ).one()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


def _judge(rep: Report, label: str, got: int, held: int, floor: float) -> None:
    if held == 0:
        return
    ratio = got / held
    msg = f"{label}: {got}/{held}R ({ratio:.0%})"
    if ratio < floor:
        rep.warn(f"{msg} — 閾値 {floor:.0%} 未満")
    else:
        rep.ok(msg)


def check_netkeiba_coverage(s, target: date, rep: Report) -> None:
    """③ netkeiba の網羅率。中央・地方を必ず分けて見る。"""
    # タイム指数（新馬戦は分母から除く）
    jra_held, jra_got, nar_held, nar_got = _coverage(
        s, "netkeiba", target, "idx_max", skip_maiden=True
    )
    _judge(rep, "netkeiba タイム指数[中央・新馬除く]", jra_got, jra_held, COVERAGE_MIN)
    _judge(rep, "netkeiba タイム指数[地方・新馬除く]", nar_got, nar_held, COVERAGE_MIN)

    # パドック（中央のみ。scripts_schedules id 64 が JRA 限定）
    jra_held, jra_got, _, _ = _coverage(s, "netkeiba", target, "p_rank")
    _judge(rep, "netkeiba パドック[中央]", jra_got, jra_held, PADDOCK_COVERAGE_MIN)


def check_kichiuma_coverage(s, target: date, rep: Report) -> None:
    """④ 吉馬の網羅率。新馬戦でも取れているので除外しない。"""
    jra_held, jra_got, nar_held, nar_got = _coverage(s, "kichiuma", target)
    _judge(rep, "吉馬[中央]", jra_got, jra_held, COVERAGE_MIN)
    _judge(rep, "吉馬[地方]", nar_got, nar_held, COVERAGE_MIN)


def check_anagusa_presence(s, target: date, rep: Report) -> None:
    """⑤ 中央開催日に穴ぐさのピックが入っているか（比率ではなく有無で見る）。"""
    jra_held, _ = _race_counts(s, target)
    if jra_held == 0:
        return
    n = s.execute(
        text("select count(*) from sekito.anagusa where date = :d"), {"d": target}
    ).scalar_one()
    if n == 0:
        rep.warn(f"穴ぐさ: 中央 {jra_held}R の開催日なのにピックが 0 件")
    else:
        rep.ok(f"穴ぐさ: {n} 件")


def main() -> int:
    p = argparse.ArgumentParser(description="sekito スクレイプ供給の死活監視")
    p.add_argument(
        "--date",
        help="網羅率・ジョブ失敗を見る日 YYYY-MM-DD（既定: 昨日 JST）。"
        "発走時刻はその翌日を見る — docstring の「実行時刻と対象日」を参照",
    )
    p.add_argument(
        "--notify",
        action="store_true",
        help="WARN があるとき Discord に通知する",
    )
    args = p.parse_args()

    # 既定は「昨日」。07:00 実行で、網羅率は確定済みの前日を、発走時刻は
    # 06:00 の同期が済んだ当日（= target + 1）を見るための既定値。
    target = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(JST).date() - timedelta(days=1)
    )

    rep = Report()
    with SyncSessionLocal() as s:
        jra_held, nar_held = _race_counts(s, target)
        rep.info(f"対象日 {target} — 開催 中央 {jra_held}R / 地方 {nar_held}R")
        check_next_day_post_time(s, target, rep)
        check_job_failures(s, target, rep)
        check_netkeiba_coverage(s, target, rep)
        check_kichiuma_coverage(s, target, rep)
        check_anagusa_presence(s, target, rep)

    header = f"スクレイプ供給チェック {target}"
    print("=" * 72)
    print(header)
    print("=" * 72)
    for line in rep.lines:
        print(line)
    print()
    print(f"WARN: {len(rep.warns)} 件")

    if rep.warns and args.notify:
        from src.utils import discord

        body = "\n".join(f"- {w}" for w in rep.warns)
        discord.send(f"⚠️ **{header}** — 上流の供給に異常\n{body}")

    return 1 if rep.warns else 0


if __name__ == "__main__":
    sys.exit(main())
