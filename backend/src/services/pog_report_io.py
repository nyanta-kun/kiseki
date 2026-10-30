"""POG 週報の入出力（DB・netkeiba・PDF 化・Discord）。

紙面の組み立ては `pog_report.py`（純関数）。ここは外とやりとりする部分だけ。

## DB は同期セッションで読む

`pog_standings` / `pog_recent_races` は API 用に async だが、SQL 自体は
そのまま使える。ここでは**同じ SQL を同期セッションで流す**。
別の SQL を書くと、画面の順位表と週報の順位表がずれる。

## netkeiba は門番を通す

`ip_restriction.require_not_restricted()` を最初と 10 頭ごとに呼び、
`RateLimiter` で間隔を空ける（実測 約8秒/リクエスト）。
**制限中は netkeiba の欄を空にして紙面は出す**（DB の情報だけでも用は足りる）。

## PDF は Mac mini の Chrome で作る

backend コンテナにはブラウザも日本語フォントも無いため、このジョブは
**Mac mini の launchd で動かす**（`scripts/notify_pog_report.sh`）。
🔴 Chrome は**使い捨てのプロファイル**（`--user-data-dir`）で起動する。
Mac mini の Chrome は Claude in Chrome の作業用にも使われており、
同じプロファイルで起動すると既存のウィンドウに吸収されて PDF が出ない。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..scrapers.netkeiba import ip_restriction
from ..scrapers.netkeiba.decode import decode_page
from ..scrapers.netkeiba.rate_limiter import RateLimiter
from . import pog_notify, pog_recent_races, pog_standings
from .pog_report import (
    CENTER_NAME,
    BbsPost,
    EntryRow,
    HorseInfo,
    OwnerStanding,
    ResultRow,
    Workout,
    owner_label,
    parse_bbs_payload,
    wood_place,
)

logger = logging.getLogger(__name__)

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
DISCORD_TIMEOUT = 120
DISCORD_USERNAME = "赤兎週報"

# ───────────────────────── DB ─────────────────────────


def load_groups(session: Session) -> list[dict]:
    """送り先のグループ。`pog_notify` と同じ「指名がある最新年度」だけ。"""
    return [dict(r) for r in session.execute(pog_notify._GROUPS_SQL).mappings()]


def load_horses(session: Session, group_id: int) -> list[HorseInfo]:
    """指名馬（`pog_standings.fetch_horses` と同じ SQL）。"""
    sql = pog_standings._HORSES_SQL.format(agg=pog_standings._HORSE_AGG.format(asof_filter=""), user_filter="")
    out = []
    for r in session.execute(text(sql), {"group_id": group_id}).mappings():
        if not r["netkeiba_horse_id"]:
            continue
        out.append(
            HorseInfo(
                horse_id=str(r["netkeiba_horse_id"]),
                name=r["horse_name"] or "",
                owner=r["owner_name"] or "",
                sex=r["sex"] or "",
                sire=r["sire"] or "",
                broodmare=r["broodmare"] or "",
                stable=r["stable"] or "",
                record=f"{r['win']}-{r['place']}-{r['show']}-{r['out']}",
                prize=int(r["prize"] or 0),
            )
        )
    return out


def load_standings(session: Session, group_id: int) -> list[OwnerStanding]:
    """馬主の順位（`pog_standings.fetch_owners` と同じ SQL）。"""
    rows = session.execute(text(pog_standings._owners_sql(asof=False)), {"group_id": group_id}).mappings()
    return [
        OwnerStanding(
            rank=r["rank"],
            owner=r["name"] or "",
            win=r["win"],
            place=r["place"],
            show=r["show"],
            out=r["out"],
            prize=r["prize"],
            top_horse=r["top_horse"] or "",
        )
        for r in rows
    ]


_LAST_RUN_SQL = text(
    """
    WITH runs AS (
        SELECT kh.jravan_code AS horse_id, kr.date, kr.course_name, kr.race_name,
               kr.surface, kr.distance, rr.finish_position
        FROM keiba.horses kh
        JOIN keiba.race_results rr ON rr.horse_id = kh.id
        JOIN keiba.races kr ON kr.id = rr.race_id
        WHERE kh.jravan_code = ANY(:ids)
        UNION ALL
        SELECT ch.umaconn_code, r.date, r.course_name, r.race_name,
               r.surface, r.distance, rr.finish_position
        FROM chihou.horses ch
        JOIN chihou.race_results rr ON rr.horse_id = ch.id
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE ch.umaconn_code = ANY(:ids)
    )
    SELECT DISTINCT ON (horse_id) *
    FROM runs
    WHERE finish_position IS NOT NULL
    ORDER BY horse_id, date DESC
    """
)


def load_last_runs(session: Session, horse_ids: list[str]) -> dict[str, tuple[str, date | None]]:
    """馬ごとの直近の出走（`10/4 東京 2歳新馬 芝1800m 1着`, その日付）。"""
    out: dict[str, tuple[str, date | None]] = {}
    if not horse_ids:
        return out
    for r in session.execute(_LAST_RUN_SQL, {"ids": horse_ids}).mappings():
        d = str(r["date"])
        when = f"{int(d[4:6])}/{int(d[6:8])}" if len(d) == 8 else d
        cond = f"{r['surface'] or ''}{r['distance'] or ''}m" if r["distance"] else ""
        label = " ".join(
            x for x in (when, r["course_name"] or "", r["race_name"] or "", cond, f"{r['finish_position']}着") if x
        )
        run_day = datetime.strptime(d, "%Y%m%d").date() if len(d) == 8 else None
        out[str(r["horse_id"])] = (label, run_day)
    return out


def load_recent(session: Session, group_id: int) -> tuple[list[EntryRow], list[ResultRow]]:
    """今週の出走（結果待ち）と先週の結果（`pog_recent_races` と同じ SQL）。"""
    today = date.today()
    entries: list[EntryRow] = []
    results: list[ResultRow] = []
    for r in session.execute(pog_recent_races._SQL, {"group_id": group_id}).mappings():
        day = r["date"]
        owner = owner_label(r["owners"])
        cond = f"{r['ground'] or ''}{r['distance'] or ''}m" if r["distance"] else ""
        place = f"{r['course_name'] or ''}{r['race_no'] or ''}R"
        if r["result"] is None and day >= today:
            entries.append(
                EntryRow(
                    day=day,
                    horse_id=str(r["netkeiba_horse_id"]),
                    horse=r["horse_name"] or "",
                    owner=owner,
                    place=place,
                    race=r["race_name"] or place,
                    cond=cond,
                    time=_hhmm(r["post_time"]),
                    jockey=r["jockey"] or "",
                    # 出馬表の行は枠順確定まで horse_no=0、特別登録の行は NULL
                    src="fix" if r["horse_no"] is not None else "sp",
                )
            )
        elif r["result"] is not None:
            res = str(r["result"]).strip()
            results.append(
                ResultRow(
                    day=day,
                    horse=r["horse_name"] or "",
                    owner=owner,
                    place=place,
                    cond=cond,
                    jockey=r["jockey"] or "",
                    ninki=r["ninki"],
                    chaku=int(res) if res.isdigit() else None,
                    prize=r["prize"],
                )
            )
    return entries, results


_WORKOUT_SQL = text(
    """
    SELECT 'slope' AS kind, blood_reg_no, training_date, center, NULL AS course,
           NULL::numeric AS t6, NULL::numeric AS t5, time_4f AS t4, time_3f AS t3,
           time_2f AS t2, lap_200_0 AS t1
    FROM keiba.slope_training
    WHERE blood_reg_no = ANY(:ids) AND training_date BETWEEN :start AND :end
    UNION ALL
    SELECT 'wood', blood_reg_no, training_date, center, wood_course,
           time_6f, time_5f, time_4f, time_3f, time_2f, lap_200_0
    FROM keiba.wood_training
    WHERE blood_reg_no = ANY(:ids) AND training_date BETWEEN :start AND :end
    """
)


def load_workouts(session: Session, horse_ids: list[str], start: date, end: date) -> dict[str, list[Workout]]:
    """坂路とウッドの追い切り（`blood_reg_no` = 指名の netkeiba_horse_id）。

    坂路は 4F から、ウッドは取れている最長（6F → 5F → 4F）から並べる。
    最後は 1F（`lap_200_0`）。
    """
    out: dict[str, list[Workout]] = {}
    if not horse_ids:
        return out
    rows = session.execute(
        _WORKOUT_SQL,
        {"ids": horse_ids, "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d")},
    ).mappings()
    for r in rows:
        day = datetime.strptime(r["training_date"], "%Y%m%d").date()
        center = str(r["center"] or "")
        if r["kind"] == "slope":
            place, head = f"{CENTER_NAME.get(center, '')}坂路", ["t4", "t3", "t2"]
        else:
            place = wood_place(center, str(r["course"] or ""))
            head = next((h for h in (["t6", "t5", "t4", "t3"], ["t5", "t4", "t3"]) if r[h[0]]), ["t4", "t3"])
        times = tuple(float(r[k]) for k in [*head, "t1"] if r[k] is not None)
        if len(times) < 2:
            continue
        dist = {"t6": "6F", "t5": "5F", "t4": "4F"}[head[0]] if r[head[0]] is not None else ""
        t4 = float(r["t4"]) if r["t4"] is not None else None
        out.setdefault(str(r["blood_reg_no"]), []).append(
            Workout(day=day, place=place, distance=dist, times=times, time_4f=t4)
        )
    return out


_PROJECTED_SQL = text(
    """
    SELECT pe.race_date, pe.netkeiba_horse_id, pe.horse_name, rm.name AS course_name,
           pe.race_number, pe.race_name, pe.expected_jockey_name,
           array_agg(DISTINCT COALESCE(m.nickname, u.name)) AS owners
    FROM keiba.projected_entries pe
    JOIN keiba.racecourse_map rm ON rm.jra_code = pe.course_code
    JOIN keiba.pog_picks p ON p.netkeiba_horse_id = pe.netkeiba_horse_id
                          AND p.group_id = :group_id AND p.visible AND p.pick_order > 0
    JOIN keiba.users u ON u.id = p.user_id
    LEFT JOIN keiba.pog_group_members m ON m.group_id = p.group_id AND m.user_id = p.user_id
    WHERE pe.race_date BETWEEN :start AND :end
    GROUP BY 1, 2, 3, 4, 5, 6, 7
    """
)


def load_projected(session: Session, group_id: int, start: date, end: date) -> list[EntryRow]:
    """netkeiba の出走想定（水曜夜に Mac mini が取り込む）。"""
    out = []
    for r in session.execute(
        _PROJECTED_SQL,
        {
            "group_id": group_id,
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
        },
    ).mappings():
        d = datetime.strptime(r["race_date"], "%Y%m%d").date()
        place = f"{r['course_name']}{r['race_number']}R"
        out.append(
            EntryRow(
                day=d,
                horse_id=str(r["netkeiba_horse_id"]),
                horse=r["horse_name"],
                owner=owner_label(r["owners"]),
                place=place,
                race=r["race_name"] or place,
                cond="",
                time="",
                jockey=r["expected_jockey_name"] or "",
                src="nk",
            )
        )
    return out


def _hhmm(v: object) -> str:
    s = str(v or "").replace(":", "")
    return f"{s[:2]}:{s[2:4]}" if len(s) >= 4 and s[:4].isdigit() else ""


_RECORDED_SQL = text(
    """
    SELECT 1 FROM keiba.pog_notifications
    WHERE group_id = :group_id AND notification_type = 'report' AND race_date = :day
    LIMIT 1
    """
)
_RECORD_SQL = text(
    """
    INSERT INTO keiba.pog_notifications (group_id, notification_type, race_date, content)
    VALUES (:group_id, 'report', :day, :content)
    ON CONFLICT DO NOTHING
    """
)


def already_sent(session: Session, group_id: int, day: date) -> bool:
    """その日の号を送ったか。"""
    return session.execute(_RECORDED_SQL, {"group_id": group_id, "day": day}).first() is not None


def record_sent(session: Session, group_id: int, day: date, content: str) -> None:
    """送ったことを残す。🔴 必ず送信の**後**に呼ぶ（`pog_notify` と同じ順序）。"""
    session.execute(_RECORD_SQL, {"group_id": group_id, "day": day, "content": content})
    session.commit()


# ───────────────────────── netkeiba ─────────────────────────


@dataclass
class NetkeibaFetcher:
    """近況と掲示板を取る。門番とレート制限を必ず通す。"""

    session: Session
    limiter: RateLimiter
    http: requests.Session
    requests_made: int = 0
    gate_every: int = 20

    STATUS_URL = "https://db.netkeiba.com/social/api_db_horse_info_simple.html"
    BBS_URL = "https://bbs.netkeiba.com/"

    def _get(self, url: str, params: dict) -> requests.Response:
        if self.requests_made % self.gate_every == 0:
            ip_restriction.require_not_restricted(self.session, context="POG 週報")
        self.limiter.wait()
        self.requests_made += 1
        r = self.http.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r

    def status_text(self, horse_id: str) -> str:
        """「次走・近況情報」の本文。"""
        r = self._get(self.STATUS_URL, {"input": "UTF-8", "output": "json", "id": horse_id})
        body = decode_page(r).strip()
        try:
            value = json.loads(body)
            if isinstance(value, str):
                body = value
        except ValueError:
            pass
        t = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
        for junk in ("次走・近況情報", "投稿する", "皆様からの情報提供をお待ちしております。", "もっと見る"):
            t = t.replace(junk, "")
        return " ".join(t.split())

    def bbs(self, horse_id: str, limit: int = 40) -> list[BbsPost]:
        """掲示板の投稿（新しい順）。"""
        r = self._get(
            self.BBS_URL,
            {
                "pid": "api_get_comment_list_simple",
                "input": "UTF-8",
                "output": "json",
                "sort": "1",
                "key": horse_id,
                "max_length": 300,
                "limit": limit,
                "category_cd": "horse",
            },
        )
        try:
            payload = json.loads(decode_page(r))
        except ValueError:
            logger.warning("掲示板の応答が JSON ではありません: %s", horse_id)
            return []
        return parse_bbs_payload(payload)


def make_http() -> requests.Session:
    """netkeiba 用の HTTP セッション。"""
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    )
    return s


# ───────────────────────── PDF ─────────────────────────


def find_chrome() -> str:
    """Chrome の実行ファイル。見つからなければ例外。"""
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    raise FileNotFoundError("Chrome が見つかりません（PDF を作れません）")


def render_pdf(html: str, out_path: Path, *, timeout: int = 120) -> Path:
    """HTML を Chrome のヘッドレス印刷で PDF にする。"""
    chrome = find_chrome()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pog_report_") as tmp:
        src = Path(tmp) / "report.html"
        src.write_text(html, encoding="utf-8")
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={tmp}/profile",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out_path}",
            src.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"PDF を作れませんでした (rc={proc.returncode}): {proc.stderr[-500:]}")
    return out_path


# ───────────────────────── Discord ─────────────────────────


def send_discord_file(webhook_url: str, content: str, pdf: Path, filename: str) -> bool:
    """PDF を添付して投稿する。成功したら True。

    `pog_notify.send_discord` は 204 だけを成功にしているが、添付つきの投稿は
    **200 が返る**（2026-09-16 の手動送信で実測）。両方を成功とする。
    """
    payload = {"username": DISCORD_USERNAME, "content": content}
    try:
        files: dict[str, tuple[str | None, bytes, str]] = {
            "payload_json": (None, json.dumps(payload, ensure_ascii=False).encode(), "application/json"),
            "files[0]": (filename, pdf.read_bytes(), "application/pdf"),
        }
        res = requests.post(webhook_url, files=files, timeout=DISCORD_TIMEOUT)
    except requests.RequestException as e:
        logger.error("Discord 送信でエラー: %s", e)
        return False
    if res.status_code in (200, 204):
        return True
    logger.error("Discord 送信失敗: %s %s", res.status_code, res.text[:200])
    return False
