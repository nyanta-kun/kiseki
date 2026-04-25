"""JRA-VAN NEXT DM指数インポーター

1403/{date}{course}{race_no}.dat ファイルを読み取り、
タイム型DM・対戦型DM指数を Backend API に POST する。

使用方法:
    python jvnext_dm_importer.py --date 20260425
    python jvnext_dm_importer.py --date 20260425 --course 03  # 特定コースのみ
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import zlib
from pathlib import Path

import requests

LOG_FILE = r"C:\kiseki\windows-agent\jvnext_dm_importer.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

CACHE_ROOT = Path(r"C:\Users\ysuzuki\AppData\Local\JRA-VAN\NEXT\cache")
PERSISTENT_STORE = Path(r"C:\kiseki\data\dm_1403")  # バッチ保存先（JVNextCore再起動後も残る）
RECORD_LEN = 25  # 1頭あたりのDMレコード長
LINE1_HEADER_LEN = 23  # Line1先頭のヘッダー部分長（DM種別+更新時刻等）

# JRA-VAN NEXTのコースコード→JVLinkコースコードマッピング
# JRA-VAN NEXTは2桁数字（01=札幌, 05=東京）でJVLinkと同一
# ただし0埋め2桁を確認するために明示マッピング
COURSE_CODE_MAP: dict[str, str] = {
    "01": "01",  # 札幌
    "02": "02",  # 函館
    "03": "03",  # 福島
    "04": "04",  # 新潟
    "05": "05",  # 東京
    "06": "06",  # 中山
    "07": "07",  # 中京
    "08": "08",  # 京都
    "09": "09",  # 阪神
    "10": "10",  # 小倉
}


def load_1403_file(path: Path) -> dict[int, dict[str, float | int | None]]:
    """1403 DM指数ファイルを読み込み、馬番→DM値のdictを返す。

    Returns:
        {horse_number: {"jvan_time_dm": 43.1, "jvan_battle_dm": 14}}
    """
    try:
        raw = path.read_bytes()
        dec = zlib.decompress(raw)
        text = dec.decode("cp932", errors="replace")
        lines = text.split("\r\n")
    except Exception as e:
        logger.error(f"Failed to decode {path}: {e}")
        return {}

    if len(lines) < 2:
        logger.warning(f"Unexpected line count in {path}: {len(lines)}")
        return {}

    # Line0: ファイルヘッダー（コース情報）
    # Line1: タイム型DM + 対戦型DM（両方が同一レコードに含まれる）
    line1 = lines[1]

    result: dict[int, dict[str, float | int | None]] = {}
    horse_number = 1
    pos = LINE1_HEADER_LEN

    while pos + RECORD_LEN <= len(line1):
        chunk = line1[pos : pos + RECORD_LEN]
        if not chunk.strip():
            break
        try:
            time_dm_raw = chunk[0:4].strip()
            battle_dm_raw = chunk[8:10].strip()
            jvan_time_dm: float | None = int(time_dm_raw) / 10.0 if time_dm_raw else None
            jvan_battle_dm: int | None = int(battle_dm_raw) if battle_dm_raw else None
        except (ValueError, IndexError):
            jvan_time_dm = None
            jvan_battle_dm = None

        if jvan_time_dm is not None or jvan_battle_dm is not None:
            result[horse_number] = {
                "jvan_time_dm": jvan_time_dm,
                "jvan_battle_dm": jvan_battle_dm,
            }
        horse_number += 1
        pos += RECORD_LEN

    return result


def parse_1402_header(path: Path) -> tuple[str, str, str, str] | None:
    """1402エントリーファイルのヘッダーから (course, kai, day, race_no) を返す。

    ヘッダー例: '0320260425010501015'
      pos 0-1: course (03)
      pos 2-9: date (20260425)
      pos 10-11: kai (01)
      pos 12-13: day (05)
      pos 14-15: race_no (01)
      pos 16-18: n_horses (015)
    """
    try:
        raw = path.read_bytes()
        dec = zlib.decompress(raw)
        text = dec.decode("cp932", errors="replace")
        lines = text.split("\r\n")
    except Exception as e:
        logger.error(f"Failed to decode {path}: {e}")
        return None

    if not lines:
        return None

    hdr = lines[0]
    if len(hdr) < 16:
        return None

    course = hdr[0:2]
    kai = hdr[10:12]
    day = hdr[12:14]
    race_no = hdr[14:16]
    return course, kai, day, race_no


def build_jravan_race_id(date: str, course: str, kai: str, day: str, race_no: str) -> str:
    """JVLinkのレースID（16文字）を構築する。

    形式: year(4) + month_day(4) + course(2) + kai(2) + day(2) + race_num(2)
    例: "2026042503010501"
    """
    return f"{date}{course}{kai}{day}{race_no}"


def fetch_race_id_map(date: str, backend_url: str) -> dict[tuple[str, str], str]:
    """Backend API から指定日のコース+レース番号→jravan_race_id マップを取得する。

    Returns:
        {("05", "01"): "2026042605020201", ...}
    """
    import urllib.request
    url = f"{backend_url}/api/races?date={date}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            import json
            data = json.loads(resp.read())
        races = data.get("races", data) if isinstance(data, dict) else data
        result: dict[tuple[str, str], str] = {}
        for r in races:
            rid = r.get("jravan_race_id", "")
            if len(rid) == 16:
                cc = rid[8:10]
                rno = rid[14:16]
                result[(cc, rno)] = rid
        logger.info(f"API から {len(result)} レースのrace_idを取得")
        return result
    except Exception as e:
        logger.warning(f"API race_id 取得失敗 ({url}): {e}")
        return {}


def collect_dm_records(
    date: str,
    course_filter: str | None = None,
    backend_url: str = "https://api.galloplab.com",
) -> list[dict]:
    """指定日の全1403ファイルからDMレコードを収集する。

    live cache と persistent store の両方を検索する。
    jravan_race_id は Backend API から取得（1402ファイル不要）。

    Returns:
        [{"jravan_race_id": "...", "horse_number": 1, "jvan_time_dm": 43.1, "jvan_battle_dm": 14}]
    """
    # 検索対象: live cache + persistent store（重複はstemで除去）
    seen_stems: set[str] = set()
    dm_files: list[Path] = []
    for search_dir in [CACHE_ROOT / "1403", PERSISTENT_STORE]:
        for p in sorted(search_dir.glob(f"1403{date}*.dat")):
            if p.stem not in seen_stems:
                seen_stems.add(p.stem)
                dm_files.append(p)

    if not dm_files:
        logger.warning(f"No 1403 files found for date={date}")
        return []

    # API から race_id マップを取得（1402ファイル不要）
    race_id_map = fetch_race_id_map(date, backend_url)

    records: list[dict] = []

    for dm_path in dm_files:
        fname = dm_path.stem  # e.g. "1403202604250301"

        if len(fname) != 16:
            logger.warning(f"Unexpected filename length: {fname}")
            continue

        course_code = fname[12:14]  # e.g. "03"
        race_no_str = fname[14:16]  # e.g. "01"

        if course_filter and course_code != course_filter:
            continue

        jravan_race_id = race_id_map.get((course_code, race_no_str))
        if not jravan_race_id:
            # APIにない場合は1402ファイルにフォールバック
            entry_path = CACHE_ROOT / "1402" / f"1402{date}{course_code}{race_no_str}00.dat"
            if entry_path.exists():
                header = parse_1402_header(entry_path)
                if header:
                    _, kai, day, _ = header
                    jravan_race_id = build_jravan_race_id(date, course_code, kai, day, race_no_str)
            if not jravan_race_id:
                logger.warning(f"race_id 解決不可: CC={course_code} R{race_no_str} — スキップ")
                continue

        dm_map = load_1403_file(dm_path)
        if not dm_map:
            logger.warning(f"No DM data in {dm_path}")
            continue

        for horse_number, dm_values in dm_map.items():
            records.append({
                "jravan_race_id": jravan_race_id,
                "horse_number": horse_number,
                "jvan_time_dm": dm_values.get("jvan_time_dm"),
                "jvan_battle_dm": dm_values.get("jvan_battle_dm"),
            })

        logger.info(
            f"  {dm_path.name}: race_id={jravan_race_id}, {len(dm_map)}頭"
        )

    return records


def post_dm_records(records: list[dict], backend_url: str, api_key: str) -> dict:
    """DM指数レコードをBackend APIにPOSTする。"""
    url = f"{backend_url}/api/import/jvan_dm"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = {"records": records}
    # proxies={} でシステムプロキシ(mitmproxy)をバイパス
    resp = requests.post(url, json=payload, headers=headers, timeout=30,
                         proxies={"http": None, "https": None})
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="JRA-VAN NEXT DM指数インポーター")
    parser.add_argument("--date", required=True, help="対象日 YYYYMMDD")
    parser.add_argument("--course", help="コースコード (e.g. 03=福島). 省略時は全コース")
    parser.add_argument("--dry-run", action="store_true", help="POSTせずに内容を表示のみ")
    args = parser.parse_args()

    # 環境変数から設定を読む
    from pathlib import Path as P
    from dotenv import load_dotenv

    env_path = P(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    backend_url = os.environ.get("BACKEND_URL", "https://api.galloplab.com")
    api_key = os.environ.get("CHANGE_NOTIFY_API_KEY", "")

    logger.info(f"=== JRA-VAN NEXT DM インポート: date={args.date}, course={args.course or '全コース'} ===")
    logger.info(f"Backend: {backend_url}")

    records = collect_dm_records(args.date, args.course, backend_url=backend_url)
    if not records:
        logger.info("DM records なし（終了）")
        return

    logger.info(f"合計 {len(records)} 件のDMレコードを取得")

    if args.dry_run:
        for r in records:
            logger.info(f"  [DRY-RUN] {r}")
        return

    try:
        result = post_dm_records(records, backend_url, api_key)
        logger.info(f"POST完了: {result}")
    except Exception as e:
        logger.error(f"POST失敗: {e}")


if __name__ == "__main__":
    main()
