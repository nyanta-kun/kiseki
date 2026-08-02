"""地方 指数の月次ローリング更新

`TEST_START` を「当月1日」にしたことで、学習終端（= TEST_START の前日）が
毎月自動で前進する。それに合わせてモデルを作り直すための月次バッチ。

## なぜ月次なのか

`TEST_START` を固定すると本番モデルの学習終端も固定され、**月を追うごとに
モデルが古くなる**。逆に無条件で最新まで学習すると一度きり評価用の TEST が
汚れる。「当月は未使用のまま残し、先月までは学習に使う」を毎月繰り返すことで
両立させる。

## 3フェーズ（この順序に意味がある）

1. `evaluate` — **先月**を一度きり評価し台帳に記録する。
   DB の指数値は前回サイクルの backfill（先々月までで学習したモデル）が書いた
   ものなので、**先月を評価する時点では honest** である。この順序を崩して
   先に再学習すると評価が in-sample になる。
2. `retrain`  — 先月までを含めて再学習（`train_chihou_market_lgb.py --refit-only`）。
   旧モデルは `data/backup/model_YYYYMMDD/` へ退避する。
3. `backfill` — **デプロイ後に**実行する。v13 を全期間再計算し DB を新モデルに揃える。

⚠️ `backfill` を**デプロイ前に**走らせてはいけない。DB は新モデル・本番の live 算出は
旧モデルという新旧混在になる（memory: feedback_full_period_migration）。

## 既定の自動化範囲

月次バッチ（LaunchAgent `com.kiseki.chihou-monthly-rollover`）が回すのは
`evaluate` と `retrain` まで。**コミット・デプロイ・backfill は人が実行する**
（モデル差し替えとデプロイは外向きの操作なので自動では踏み込まない）。
レポート末尾に次に打つコマンドを出力する。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_monthly_rollover.py                 # evaluate+retrain
    .venv/bin/python scripts/chihou_monthly_rollover.py --phase evaluate
    .venv/bin/python scripts/chihou_monthly_rollover.py --phase backfill  # デプロイ後
    .venv/bin/python scripts/chihou_monthly_rollover.py --month 202607    # 対象月を指定
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from scripts.chihou_cutoff_venue_review import (  # noqa: E402
    CUT_GAP_HARD,
    CUT_GAP_SOFT,
    CUT_RANK_MIN,
    load_db,
    mark_cut,
    summarize,
)
from scripts.chihou_rank_quality_review import connect  # noqa: E402
from src.chihou_protocol import TEST_START, record_test_usage  # noqa: E402
from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_rollover")

MODELS_DIR = _root / "models"
BACKUP_DIR = _root / "data" / "backup"
REPORT_DIR = _root / "docs" / "monthly_rollover"
MODEL_FILES = [
    "chihou_prod_lgb.v12_44feat.txt",
    "chihou_prod_lgb_win.v12_44feat.txt",
    "chihou_prod_lgb.v12_44feat_metrics.json",
]
BACKFILL_START = "20240101"


def prev_month(today: datetime.date) -> str:
    first = today.replace(day=1)
    return (first - datetime.timedelta(days=1)).strftime("%Y%m")


def month_bounds(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[4:6])
    return f"{ym}01", f"{ym}{calendar.monthrange(y, m)[1]:02d}"


def run(cmd: list[str]) -> None:
    logger.info("実行: %s", " ".join(cmd))
    subprocess.run(cmd, cwd=_root, check=True)


YOY_SQL = """
SELECT substr(r.date, 1, 6) AS ym, count(*) AS races,
       avg(CASE WHEN u.fp = 1 THEN 1.0 ELSE 0 END) AS top1_win,
       avg(CASE WHEN u.fp <= 3 THEN 1.0 ELSE 0 END) AS top1_place
FROM (
  SELECT DISTINCT ci.race_id,
         first_value(rr.finish_position) OVER (
           PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS fp
  FROM chihou.calculated_indices ci
  JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  JOIN chihou.races r2 ON r2.id = ci.race_id
  WHERE ci.version = %(ver)s
    AND rr.finish_position IS NOT NULL
    AND COALESCE(rr.abnormality_code, 0) = 0
    AND substr(r2.date, 5, 2) = %(mm)s
) u
JOIN chihou.races r ON r.id = u.race_id
GROUP BY 1 ORDER BY 1
"""


def fetch_same_month_history(conn, ym: str) -> list[dict]:
    """同じ月の過去実績を返す。

    指数1位の勝率は**季節性が強い**（実測: 7月は 2024 0.413 / 2025 0.439 /
    2026 0.422 なのに 1月は 0.456〜0.497）。前月だけを冬場の窓と比べると
    「劣化した」と誤読するので、必ず同月で並べて見る。
    """
    cur = conn.cursor()
    cur.execute(YOY_SQL, {"ver": CHIHOU_COMPOSITE_VERSION, "mm": ym[4:6]})
    rows = [{"ym": a, "races": int(b), "top1_win": float(c), "top1_place": float(d)}
            for a, b, c, d in cur.fetchall()]
    cur.close()
    return rows


def phase_evaluate(ym: str) -> dict:
    """先月を一度きり評価する（DB の指数値＝デプロイ済みモデルの出力を使う）。"""
    start, end = month_bounds(ym)
    logger.info(f"[evaluate] {start}〜{end} を一度きり評価")
    conn = connect()
    try:
        df = load_db(conn, start, end)
        yoy = fetch_same_month_history(conn, ym)
    finally:
        conn.close()
    if df.empty:
        raise SystemExit(f"{ym} の指数行がありません（backfill 未実施かレースなし）")

    d = mark_cut(df, "composite_index")
    overall = summarize(d)
    by_venue = []
    for venue, sub in d.groupby("course_name"):
        s = summarize(sub)
        if s:
            s["course_name"] = venue
            by_venue.append(s)

    # 指数1位馬の的中（ランキング品質の最小限の確認）
    fin = d[d["finish_position"].notna() & (d["finish_position"] > 0)]
    top1 = fin.loc[fin.groupby("race_id")["composite_index"].idxmax()]
    overall["top1_win"] = float((top1["finish_position"] == 1).mean())
    overall["top1_place"] = float((top1["finish_position"] <= 3).mean())

    record_test_usage(
        f"月次ローリング更新の一度きり評価（{ym}）",
        "chihou_monthly_rollover.py",
        f"指数1位 勝率={overall['top1_win']:.4f} 複勝率={overall['top1_place']:.4f} / "
        f"除外率={overall['cut_rate']:.3f} 着外率={overall['cut_out_rate']:.3f}",
    )
    return {"month": ym, "start": start, "end": end,
            "overall": overall, "by_venue": by_venue, "same_month_history": yoy}


def phase_retrain() -> None:
    """旧モデルを退避してから再学習する。"""
    stamp = datetime.date.today().strftime("%Y%m%d")
    dest = BACKUP_DIR / f"model_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        src = MODELS_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
    logger.info(f"[retrain] 旧モデルを退避: {dest}")
    run([sys.executable, "scripts/train_chihou_market_lgb.py", "--refit-only"])


def phase_backfill() -> None:
    end = datetime.date.today().strftime("%Y%m%d")
    logger.info(f"[backfill] v{CHIHOU_COMPOSITE_VERSION} を {BACKFILL_START}〜{end} で再計算")
    run([sys.executable, "scripts/inference_chihou_v13.py",
         "--start", BACKFILL_START, "--end", end,
         "--batch-size", "3000", "--sleep", "0.3"])


def write_report(res: dict, retrained: bool) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ym = res["month"]
    path = REPORT_DIR / f"{ym}.md"
    o = res["overall"]
    lines = [
        f"# 地方 指数 月次ローリング更新レポート {ym}",
        "",
        f"生成: {datetime.datetime.now():%Y-%m-%d %H:%M}  /  "
        f"評価対象: {res['start']}〜{res['end']}  /  TEST_START: {TEST_START}",
        "",
        "## 一度きり評価（デプロイ済みモデルの出力＝DBの指数値）",
        "",
        f"- レース数 {o['races']:,} / 頭数 {o['horses']:,}",
        f"- 指数1位 勝率 **{o['top1_win']:.1%}** / 複勝率 **{o['top1_place']:.1%}**",
        f"- 足切り（gap>={CUT_GAP_HARD:g} or (gap>={CUT_GAP_SOFT:g} and 順位>={CUT_RANK_MIN})）: "
        f"除外率 **{o['cut_rate']:.1%}** / 除外馬の着外率 **{o['cut_out_rate']:.1%}** / "
        f"1着取りこぼし **{o['winner_cut_rate']:.1%}** / 3着内取りこぼし **{o['placer_cut_rate']:.1%}**",
        "",
        "## 競馬場別",
        "",
        "| 競馬場 | R数 | 除外率 | 除外馬の着外率 | 1着取りこぼし | 3着内取りこぼし |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(res["by_venue"], key=lambda x: -x["cut_out_rate"]):
        lines.append(
            f"| {r['course_name']} | {r['races']:,} | {r['cut_rate']:.1%} | "
            f"{r['cut_out_rate']:.1%} | {r['winner_cut_rate']:.1%} | {r['placer_cut_rate']:.1%} |"
        )
    yoy = res.get("same_month_history") or []
    if len(yoy) > 1:
        lines += [
            "",
            "## 同月比較（季節性の確認）",
            "",
            "指数1位の勝率は季節性が強い（夏は低く冬は高い）。前月を冬場の窓と比べると",
            "劣化と誤読するため、**必ず同じ月同士で比べること**。",
            "",
            "| 年月 | R数 | 1位勝率 | 1位複勝率 |",
            "|---|---:|---:|---:|",
        ]
        for r in yoy:
            mark = " ←今回" if r["ym"] == res["month"] else ""
            lines.append(f"| {r['ym']}{mark} | {r['races']:,} | "
                         f"{r['top1_win']:.1%} | {r['top1_place']:.1%} |")
        lines.append("")
        lines.append("※ 今回の月以外は in-sample（全期間学習モデルの遡及適用）。"
                     "今回の月だけが out-of-sample なので、"
                     "**同水準なら劣化なし**と読む。")

    lines += ["", "## 次にやること", ""]
    if retrained:
        lines += [
            "モデルは再学習済み（`backend/models/chihou_prod_lgb.v12_44feat*.txt` が更新されている）。",
            "**デプロイまでは DB を触らないこと**（新旧混在になる）。",
            "",
            "```bash",
            "# 1. コミットしてデプロイ（CI が本番へ反映する）",
            "git add backend/models/chihou_prod_lgb.v12_44feat*.txt \\",
            "        backend/models/chihou_prod_lgb.v12_44feat_metrics.json \\",
            f"        backend/docs/monthly_rollover/{ym}.md backend/scripts/CHIHOU_TEST_USAGE_LEDGER.md",
            "",
            "# 2. デプロイ完了後に DB を新モデルへ揃える",
            "cd backend && .venv/bin/python scripts/chihou_monthly_rollover.py --phase backfill",
            "```",
        ]
    else:
        lines.append("（evaluate のみ実行。再学習は `--phase retrain`）")
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=["evaluate", "retrain", "backfill", "all"],
                   default="all",
                   help="all は evaluate+retrain まで（backfill はデプロイ後に別途）")
    p.add_argument("--month", default=None, help="評価対象月 YYYYMM（既定: 先月）")
    args = p.parse_args()

    ym = args.month or prev_month(datetime.date.today())

    if args.phase == "backfill":
        phase_backfill()
        return

    res = None
    if args.phase in ("evaluate", "all"):
        res = phase_evaluate(ym)
        print(json.dumps(res["overall"], ensure_ascii=False, indent=2, default=float))

    retrained = False
    if args.phase in ("retrain", "all"):
        phase_retrain()
        retrained = True

    if res is not None:
        path = write_report(res, retrained)
        logger.info(f"レポート: {path}")
        print(f"\nレポート: {path}")
    if retrained:
        print("\n⚠️ backfill はデプロイ後に実行すること"
              "（先に走らせると DB=新モデル / live=旧モデルの混在になる）")


if __name__ == "__main__":
    main()
