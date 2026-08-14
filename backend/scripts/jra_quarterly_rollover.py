"""中央(JRA) 指数の四半期ローリング更新。

`TEST_START` を「当四半期の初日」にしたことで、本番モデルの学習終端
（= TEST_START の前日）が四半期ごとに自動で前進する。それに合わせてモデルを
作り直すための四半期バッチ。地方の `chihou_monthly_rollover.py` と同型だが、
**中央は月 約288レースしかなく月次では効果量を判定できない**ため四半期にしている
（根拠は `src/jra_protocol.py` の docstring）。

## 3フェーズ（この順序に意味がある）

1. `evaluate` — **前四半期**を一度きり評価し台帳へ記録する。
   DB の指数値は前回サイクルで作ったモデル（前々四半期までで学習）の出力なので、
   **この時点では honest**。順序を崩して先に再学習すると評価が in-sample になる。
2. `retrain`  — 前四半期までを含めて再学習する。旧モデルは
   `data/backup/jra_model_YYYYMMDD/` へ退避する。
3. `backfill` — **デプロイ後に**実行する。v27 を全期間再計算し DB を新モデルへ揃える。

⚠️ `backfill` をデプロイ前に走らせてはいけない。DB は新モデル・本番の live 算出は
旧モデルという新旧混在になる。

⚠️ **evaluate は DB の `calculated_indices` を読む。** そこに入っているのは
「その日に live で算出された値」と「バックフィルで上書きされた値」の混成なので
（`docs/jra_rebuild_2026_08.md` 5.2）、**前四半期のバックフィルを走らせたあとに
evaluate してはいけない**。順序を守っている限りは問題にならない。

## 自動化範囲

四半期バッチ（LaunchAgent `com.kiseki.jra-quarterly-rollover`）が回すのは
`evaluate` と `retrain` まで。**コミット・デプロイ・backfill は人が実行する**
（モデル差し替えとデプロイは外向きの操作なので自動では踏み込まない）。
レポート末尾に次に打つコマンドを出力する。

使い方:
    cd backend
    .venv/bin/python scripts/jra_quarterly_rollover.py                  # evaluate+retrain
    .venv/bin/python scripts/jra_quarterly_rollover.py --phase evaluate
    .venv/bin/python scripts/jra_quarterly_rollover.py --phase backfill # デプロイ後
    .venv/bin/python scripts/jra_quarterly_rollover.py --quarter 2026Q2 # 対象を指定
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
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

import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402  # pandas.read_sql は DBAPI2 接続を警告するので使わない

from src import jra_protocol  # noqa: E402
from src.indices.composite import COMPOSITE_VERSION, OUT_PROB_CUTOFF  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_rollover")

MODELS_DIR = _root / "models"
BACKUP_DIR = _root / "data" / "backup"
REPORT_DIR = _root / "docs" / "quarterly_rollover"
MODEL_FILES = [
    "jra_reg_rank_lgb.txt",
    "jra_reg_rank_metrics.json",
    "jra_out_rate_lgb.txt",
    "jra_out_rate_metrics.json",
]
JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

EVAL_SQL = """
SELECT r.date, ci.race_id, ci.horse_id, ci.composite_index, ci.out_probability,
       rr.finish_position, rr.abnormality_code
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
LEFT JOIN keiba.race_results rr
       ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = %(ver)s
  AND r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN %(courses)s
"""

# 同四半期の過年度実績。中央は季節性が強く（夏の小倉・冬の中山等で開催地が偏る）、
# 前四半期を直前の四半期と比べると「劣化した」と誤読する。必ず同四半期で並べる。
YOY_SQL = """
SELECT substr(r.date, 1, 4) AS yr, count(*) AS races,
       avg(CASE WHEN u.fp = 1 THEN 1.0 ELSE 0 END) AS top1_win,
       avg(CASE WHEN u.fp <= 3 THEN 1.0 ELSE 0 END) AS top1_place
FROM (
  SELECT DISTINCT ci.race_id,
         first_value(rr.finish_position) OVER (
           PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS fp
  FROM keiba.calculated_indices ci
  JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  JOIN keiba.races r2 ON r2.id = ci.race_id
  WHERE ci.version = %(ver)s
    AND rr.finish_position IS NOT NULL
    AND COALESCE(rr.abnormality_code, 0) = 0
    AND r2.course IN %(courses)s
    AND ((substr(r2.date, 5, 2)::int - 1) / 3) + 1 = %(q)s
) u
JOIN keiba.races r ON r.id = u.race_id
GROUP BY 1 ORDER BY 1
"""


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _query(conn, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def prev_quarter(today: datetime.date) -> str:
    """今日が属する四半期の1つ前を "YYYYQn" で返す。"""
    q = (today.month - 1) // 3 + 1
    return f"{today.year}Q{q - 1}" if q > 1 else f"{today.year - 1}Q4"


def quarter_bounds(label: str) -> tuple[str, str]:
    """"YYYYQn" → ("YYYYMMDD", "YYYYMMDD")。"""
    year, q = int(label[:4]), int(label[5])
    start = datetime.date(year, (q - 1) * 3 + 1, 1)
    end_month_first = (
        datetime.date(year + 1, 1, 1) if q == 4 else datetime.date(year, q * 3 + 1, 1)
    )
    end = end_month_first - datetime.timedelta(days=1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def run(cmd: list[str]) -> None:
    logger.info("実行: %s", " ".join(cmd))
    subprocess.run(cmd, cwd=_root, check=True)


def _python() -> str:
    """backend/.venv があればそれを使う（LaunchAgent から起動されるため）。"""
    venv = _root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def phase_evaluate(label: str) -> dict:
    """前四半期を一度きり評価する（DB の指数値＝デプロイ済みモデルの出力）。"""
    start, end = quarter_bounds(label)
    logger.info(f"[evaluate] {label} ({start}〜{end}) を一度きり評価")
    conn = connect()
    try:
        df = _query(conn, EVAL_SQL,
                    {"ver": COMPOSITE_VERSION, "start": start, "end": end,
                     "courses": JRA_COURSES})
        yoy = _query(conn, YOY_SQL,
                     {"ver": COMPOSITE_VERSION, "q": int(label[5]),
                      "courses": JRA_COURSES}).to_dict("records")
    finally:
        conn.close()

    if df.empty:
        raise SystemExit(
            f"{label} の version={COMPOSITE_VERSION} の行がありません"
            "（バックフィル未実施か、対象期間にレースが無い）"
        )

    fin = df[df["finish_position"].notna() & (df["finish_position"] > 0)]
    fin = fin[fin["abnormality_code"].fillna(0).isin([0])]
    top1 = fin.loc[fin.groupby("race_id")["composite_index"].idxmax()]

    cut = df[df["out_probability"].notna()]
    cut_mask = cut["out_probability"] >= OUT_PROB_CUTOFF
    cut_fin = cut[cut["finish_position"].notna() & (cut["finish_position"] > 0)]
    cut_fin_mask = cut_fin["out_probability"] >= OUT_PROB_CUTOFF
    n_win = int((cut_fin["finish_position"] == 1).sum())

    overall = {
        "n_rows": int(len(df)),
        "n_races": int(df["race_id"].nunique()),
        "top1_win": float((top1["finish_position"] == 1).mean()),
        "top1_place": float((top1["finish_position"] <= 3).mean()),
        # 足切り（Web グレーアウト）の較正が保たれているか
        "cut_rate": float(cut_mask.mean()) if len(cut) else None,
        "cut_actual_out_rate": (
            float((cut_fin.loc[cut_fin_mask, "finish_position"] >= 6).mean())
            if cut_fin_mask.any() else None
        ),
        "cut_missed_win": (
            float((cut_fin.loc[cut_fin_mask, "finish_position"] == 1).sum() / n_win)
            if n_win else None
        ),
    }

    jra_protocol.record_test_usage(
        f"四半期ローリング更新の一度きり評価（{label}）",
        "jra_quarterly_rollover.py",
        f"指数1位 勝率={overall['top1_win']:.4f} 複勝率={overall['top1_place']:.4f} / "
        f"足切り率={overall['cut_rate']} 1着取りこぼし={overall['cut_missed_win']}",
    )
    return {"quarter": label, "start": start, "end": end,
            "overall": overall, "same_quarter_history": yoy}


def phase_retrain() -> None:
    """旧モデルを退避してから再学習する。"""
    stamp = datetime.date.today().strftime("%Y%m%d")
    dest = BACKUP_DIR / f"jra_model_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        src = MODELS_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
    logger.info(f"[retrain] 旧モデルを退避: {dest}")

    py = _python()
    # 着外率ヘッド → 順位回帰ヘッドの順。後者は前者の featurize / load_df を使う
    run([py, "scripts/train_jra_out_rate.py"])
    run([py, "scripts/train_jra_reg_rank.py"])


def phase_backfill() -> None:
    """デプロイ後に v27 を全期間再計算し、着外率も揃える。"""
    py = _python()
    run([py, "scripts/inference_v27.py"])
    run([py, "scripts/backfill_jra_out_probability.py"])


def write_report(label: str, evaluated: dict | None, retrained: bool) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{label}.md"
    lines = [
        f"# JRA 四半期ローリング更新 {label}",
        "",
        f"実行日: {datetime.date.today().isoformat()}",
        f"プロトコル: `{jra_protocol.describe()}`",
        f"composite version: {COMPOSITE_VERSION}",
        "",
    ]
    if evaluated:
        o = evaluated["overall"]
        lines += [
            f"## 一度きり評価（{evaluated['start']}〜{evaluated['end']}）",
            "",
            "| 指標 | 値 |",
            "|---|---|",
            f"| レース数 | {o['n_races']:,} |",
            f"| 指数1位 勝率 | {o['top1_win']:.4f} |",
            f"| 指数1位 複勝率 | {o['top1_place']:.4f} |",
            f"| 足切り率（p_out ≥ {OUT_PROB_CUTOFF}） | {o['cut_rate']} |",
            f"| 足切り馬の実着外率 | {o['cut_actual_out_rate']} |",
            f"| 足切りによる1着取りこぼし | {o['cut_missed_win']} |",
            "",
            "> ⚠️ **モデルの refit 境界が `TEST_START` の前日になる前に作られた行を",
            "> 評価した場合、この数値は in-sample である。**",
            "> `models/jra_reg_rank_metrics.json` の `refit_period` が評価期間に",
            "> かかっていないことを確認すること（`docs/jra_rebuild_2026_08.md` 13章）。",
            "",
            "### 同四半期の過年度比較",
            "",
            "**中央は季節性が強く開催地が四半期ごとに偏る。**",
            "直前の四半期と比べて劣化と読まないこと。",
            "",
            "| 年 | レース数 | 指数1位 勝率 | 複勝率 |",
            "|---|---|---|---|",
        ]
        for r in evaluated["same_quarter_history"]:
            lines.append(
                f"| {r['yr']} | {int(r['races']):,} | {float(r['top1_win']):.4f} "
                f"| {float(r['top1_place']):.4f} |"
            )
        lines.append("")
    if retrained:
        lines += ["## 再学習", "", "`train_jra_out_rate.py` / `train_jra_reg_rank.py` を実行済み。",
                  f"旧モデルは `data/backup/jra_model_{datetime.date.today():%Y%m%d}/` に退避。", ""]
    lines += [
        "## 次に人が行うこと",
        "",
        "```bash",
        "# 1. モデル差分を確認してコミット",
        "git -C .. add backend/models && git -C .. commit -m 'chore(jra): 四半期ローリングでモデル更新'",
        "",
        "# 2. デプロイ（main への PR → CI の Blue-Green デプロイ）",
        "",
        "# 3. デプロイ後に全期間バックフィル（順序を逆にすると新旧混在になる）",
        f"{'.venv/bin/python' if (_root / '.venv').exists() else 'python'} "
        "scripts/jra_quarterly_rollover.py --phase backfill",
        "```",
        "",
        "⚠️ **バックフィルだけでは当日・翌日のレースは埋まらない**"
        "（地方 v14 で実際に踏んだ）。版を上げた場合は本番の算出も叩くこと:",
        "",
        "```bash",
        "ssh sekito \"API_KEY=\\$(grep '^CHANGE_NOTIFY_API_KEY=' ~/GitHub/kiseki/.env | cut -d= -f2-)",
        "  curl -s -X POST 'http://127.0.0.1:8003/api/import/calculate?date=YYYYMMDD' "
        "-H \\\"X-API-Key: \\$API_KEY\\\"\"",
        "```",
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=["all", "evaluate", "retrain", "backfill"], default="all")
    p.add_argument("--quarter", help="対象四半期 YYYYQn（省略時は前四半期）")
    args = p.parse_args()

    label = args.quarter or prev_quarter(datetime.date.today())
    logger.info(f"対象: {label} / {jra_protocol.describe()}")

    if args.phase == "backfill":
        phase_backfill()
        return

    evaluated = None
    if args.phase in ("all", "evaluate"):
        evaluated = phase_evaluate(label)
        logger.info(json.dumps(evaluated["overall"], ensure_ascii=False, indent=2))

    retrained = False
    if args.phase in ("all", "retrain"):
        phase_retrain()
        retrained = True

    report = write_report(label, evaluated, retrained)
    logger.info(f"レポート: {report}")


if __name__ == "__main__":
    main()
