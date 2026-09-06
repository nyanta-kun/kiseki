"""吉馬（kichiuma.net / kichiuma-chiho.net）の SP 能力値取得。

sekito `bin/scrape/kichiuma` からの移設。吉馬はログイン不要で、SP 能力値表は
初期 HTML に SSR 済みなので requests + `pandas.read_html` だけで取れる。

移設で変えたこと:
    - 対象レースの供給元を `sekito.races` から `keiba.races` / `chihou.races` へ
      （`scrapers/targets.py`。2026-09-06 実測で両者は完全一致）。
    - 場コード → 吉馬 ID の解決を `sekito.racecourse` の JOIN から
      `utils/racecourse.py` へ。
    - DB アクセスを psycopg2 から SQLAlchemy セッションへ。移設前は
      `FetchStatusManager` を呼ぶたびに接続を張り直しており、1 レースあたり
      最大 4 接続を開いていた。ここでは 1 実行 1 セッション。

移設で変えていないこと:
    - URL の組み立て、SP 表の同定と列マッピング、値の正規化
    - レース中止の検出と記録、`should_fetch` によるスキップ
    - リクエスト間の 0.5〜2.0 秒ジッタ
    - 書き込み先 `sekito.kichiuma` と `sekito.data_fetch_status`

⚠️ 文字コードは UTF-8 決め打ちのまま。netkeiba と違い吉馬は 2 ホストとも UTF-8 で、
   移設時点の実測でも化けていない（[[netkeiba-scraper-facts]] の EUC-JP 問題は
   db.netkeiba.com 固有）。**取得先を足すときはホストごとに確かめること。**
"""

from __future__ import annotations

import logging
import random
import re
import time
from io import StringIO

import pandas as pd
import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..utils.racecourse import BY_CODE
from .fetch_status import FetchStatusManager, detect_race_cancellation
from .targets import TargetRace

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
JRA_BASE_URL = "https://kichiuma.net/php/search.php"
NAR_BASE_URL = "https://kichiuma-chiho.net/php/search.php"

DATA_TYPE = "kichiuma"

# SP 能力値表は固定列構成。位置と表記ゆれの両方を候補に持つ（先頭が位置指定）。
#   [0]馬 [1]評価 [2]SP能力値 [3]馬名 [4]先行力
#   [5]SP信頼(記号) [6]SP信頼.1(数値) [7]SP調整(記号) [8]SP調整.1(数値)
#   [9]SP最大(記号) [10]SP最大.1(数値) [11]末脚力(記号) [12]末脚力.1(数値)
COLUMN_MAPPINGS: dict[str, list] = {
    "horse_no": [0, "馬", "馬番"],
    "sp_score": [2, "SP 能力値", "SP能力値"],
    "senko": [4, "先行力"],
    "sp_trust": [6, "SP信頼.1"],
    "sp_adjust": [8, "SP調整.1"],
    "sp_max": [10, "SP最大.1"],
    "sueashi": [12, "末脚力.1"],
}

# 値に混ざる印。数値化の前に落とす。
_MARKS = ("▲", "…", "－", "—", "×", "◯", "△", "※", "＊", "*", "◎")
_EMPTY = ("", "-", "--", "---", "－")


def _clean(value) -> str | None:
    """印と空白を落として数値文字列の候補を返す。数値になりえなければ None。"""
    if value is None or pd.isna(value):
        return None
    s = str(value).strip()
    for mark in _MARKS:
        s = s.replace(mark, "")
    s = s.strip()
    return None if s in _EMPTY else s


def safe_float(value) -> float | None:
    """文字列を安全に float へ。取れなければ None。"""
    s = _clean(value)
    if s is None:
        return None
    if not s.replace(".", "").replace("-", "").replace("+", "").isdigit():
        m = re.search(r"[-+]?\d*\.?\d+", s)
        s = m.group() if m else None
        if s is None:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def safe_int(value) -> int | None:
    """文字列を安全に int へ。取れなければ None。"""
    s = _clean(value)
    if s is None:
        return None
    if not s.replace("-", "").replace("+", "").isdigit():
        m = re.search(r"[-+]?\d+", s)
        s = m.group() if m else None
        if s is None:
            return None
    try:
        return int(s)
    except ValueError:
        return None


def build_url(target: TargetRace, kichiuma_id: str) -> str:
    """吉馬の SP 能力値ページ URL。中央と地方でホストが違う。"""
    base = JRA_BASE_URL if target.is_jra else NAR_BASE_URL
    d = target.date
    return (
        f"{base}?race_id={d:%Y%m%d}{target.race_no:02d}{int(kichiuma_id):02d}"
        f"&date={d.year}%2F{d.month}%2F{d.day}"
        f"&no={target.race_no}&id={int(kichiuma_id):02d}&p=fp"
    )


def _resolve_column(candidates: list, df: pd.DataFrame, name: str):
    """列名候補（文字列の部分一致 → 位置）から実際の列を決める。"""
    for col in candidates:
        if isinstance(col, str):
            if col in df.columns:
                return col
            for actual in df.columns:
                if col in str(actual):
                    return actual
        elif isinstance(col, int) and col < len(df.columns):
            return col
    logger.warning("%s カラムが見つかりません。候補=%s, 利用可能=%s",
                   name, candidates, list(df.columns)[:8])
    return None


def pick_sp_table(html: str) -> pd.DataFrame | None:
    """全テーブルのうち SP 能力値表を選ぶ。

    実 HTML は `SP<br>能力値` なので、`pd.read_html(..., match=r"SP\\s*能力値")`
    はパーサ次第で `SP\\n能力値` になりヒットしない。全表を読んでから
    列名で同定する方が堅い（移設前の判断をそのまま引き継ぐ）。
    """
    try:
        dfs = pd.read_html(StringIO(html))
    except ValueError:
        return None
    for df in dfs:
        cols = " ".join(str(c) for c in df.columns)
        if "SP" in cols and "能力値" in cols:
            return df
    return None


def parse_sp_table(df: pd.DataFrame, target: TargetRace) -> list[dict]:
    """SP 能力値表を 1 頭 1 行のレコードへ。

    馬番も SP 能力値も取れない行（ヘッダ再掲・注記など）は捨てる。
    """
    cols = {field: _resolve_column(cands, df, field) for field, cands in COLUMN_MAPPINGS.items()}

    records: list[dict] = []
    for idx in range(len(df)):
        try:
            values: dict[str, object] = {}
            for field, col_ref in cols.items():
                if col_ref is None:
                    values[field] = None
                    continue
                raw = df.iloc[idx, col_ref] if isinstance(col_ref, int) else df.iloc[idx][col_ref]
                values[field] = safe_int(raw) if field == "horse_no" else safe_float(raw)

            if values["horse_no"] is None and values["sp_score"] is None:
                continue

            records.append({
                "date": target.date,
                "course_code": target.course_code,
                "race_no": target.race_no,
                **values,
            })
        except Exception as e:  # 1 行の崩れで表全体を落とさない
            logger.warning("行 %d の解析でエラー: %s", idx, e)
            continue
    return records


_UPSERT = text(
    """
    INSERT INTO sekito.kichiuma
        (date, course_code, race_no, horse_no, sp_score, senko, sp_trust, sp_adjust, sp_max, sueashi)
    VALUES (:date, :course_code, :race_no, :horse_no, :sp_score, :senko,
            :sp_trust, :sp_adjust, :sp_max, :sueashi)
    ON CONFLICT (date, course_code, race_no, horse_no)
    DO UPDATE SET sp_score = EXCLUDED.sp_score,
                  senko = EXCLUDED.senko,
                  sp_trust = EXCLUDED.sp_trust,
                  sp_adjust = EXCLUDED.sp_adjust,
                  sp_max = EXCLUDED.sp_max,
                  sueashi = EXCLUDED.sueashi
    """
)


def upsert(session: Session, records: list[dict]) -> int:
    """`sekito.kichiuma` へ UPSERT する。horse_no が無い行は主キーを満たさないので落とす。"""
    rows = [r for r in records if r.get("horse_no") is not None]
    if not rows:
        return 0
    session.execute(_UPSERT, rows)
    session.commit()
    return len(rows)


def _make_http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.5",
    })
    return s


def scrape(
    session: Session,
    targets: list[TargetRace],
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[int, int]:
    """対象レースぶんの吉馬データを取得する。

    Args:
        session: 同期 DB セッション。
        targets: `targets.target_races()` の結果。
        dry_run: DB へ書かない（取得と解析だけ行う）。
        force: `should_fetch` を無視して取り直す。

    Returns:
        (成功件数, エラー件数)。
    """
    if not targets:
        logger.info("対象レースがありません")
        return 0, 0

    status = FetchStatusManager(session)
    http = _make_http_session()
    success = errors = 0

    for target in targets:
        rc = BY_CODE[target.course_code]
        label = f"{target.group_label} {rc.name} {target.race_no}R"

        if not dry_run and not force:
            current = status.get_status(target.date, target.course_code, target.race_no, DATA_TYPE)
            if current and current[0] == "race_cancelled":
                logger.info("レース中止済みのためスキップ: %s", label)
                continue
            if not status.should_fetch(target.date, target.course_code, target.race_no, DATA_TYPE):
                logger.debug("%s 吉馬: 取得済み", label)
                continue

        url = build_url(target, rc.kichiuma_id)
        try:
            resp = http.get(url, timeout=30)
            resp.raise_for_status()
            html = resp.content.decode("utf-8", errors="replace")

            if detect_race_cancellation(html):
                logger.warning("レース中止検出: %s", label)
                if not dry_run:
                    status.mark_race_cancelled(target.date, target.course_code, target.race_no)
                continue

            df = pick_sp_table(html)
            if df is None or df.empty:
                logger.warning("吉馬テーブルが空: %s", label)
                if not dry_run:
                    status.mark_not_available(
                        target.date, target.course_code, target.race_no, DATA_TYPE,
                        reason="テーブルが空",
                    )
                errors += 1
                continue

            records = parse_sp_table(df, target)
            if not records:
                logger.warning("吉馬データなし: %s", label)
                if not dry_run:
                    status.mark_not_available(
                        target.date, target.course_code, target.race_no, DATA_TYPE,
                        reason="Kichiuma データなし",
                    )
                errors += 1
                continue

            if dry_run:
                logger.info("[dry-run] %s: %d 頭 (先頭 horse_no=%s, sp_score=%s)",
                            label, len(records), records[0]["horse_no"], records[0]["sp_score"])
            else:
                written = upsert(session, records)
                status.mark_fetched(target.date, target.course_code, target.race_no, DATA_TYPE)
                logger.info("吉馬 取得成功: %s - %d 頭", label, written)
            success += 1

        except Exception as e:
            logger.error("%s の吉馬取得でエラー: %s", label, e)
            if not dry_run:
                try:
                    session.rollback()
                    status.mark_failed(
                        target.date, target.course_code, target.race_no, DATA_TYPE, str(e)
                    )
                except Exception:
                    logger.exception("mark_failed の記録に失敗")
            errors += 1

        # サーバ負荷を抑える（移設前と同じ 0.5〜2.0 秒のジッタ）
        time.sleep(random.uniform(0.5, 2.0))

    logger.info("吉馬 取得完了 - 成功: %d, エラー: %d", success, errors)
    return success, errors
