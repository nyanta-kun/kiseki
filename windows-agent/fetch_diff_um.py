"""DIFF dataspec から UM レコード（競走馬マスタ）を取得し、
種牡馬を含む祖先データを /api/import/bloodlines に送信する。

HN レコード（繁殖馬マスタ）は繁殖牝馬のみを持つため、
種牡馬名は UM の3代血統情報（pos 205, breeding_code+馬名×14頭）から取得する。

実行後に repost_blod_from_cache.py を実行すると pedigrees.sire が解決される。
"""
import logging
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, r"C:\Python312-32\Lib\site-packages")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("fetch_diff_um.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

env_path = Path(r"C:\kiseki\.env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

BACKEND_URL = os.environ.get("BACKEND_URL", "https://api.galloplab.com")
API_KEY = os.environ.get("CHANGE_NOTIFY_API_KEY", "")
JRAVAN_SID = os.environ.get("JRAVAN_SID", "kiseki")
BATCH_SIZE = 500

logger.info(f"Backend: {BACKEND_URL}")


def normalize_jvread(raw: str) -> str:
    """COM BSTR (Unicode) を 1バイト=1文字 の Latin-1 形式に正規化する。"""
    try:
        return raw.encode("cp932").decode("latin-1")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def post_batch(records: list[dict], batch_num: int) -> bool:
    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/import/bloodlines",
            json={"records": records},
            headers={"X-API-Key": API_KEY},
            timeout=120,
        )
        if resp.status_code == 200:
            stats = resp.json().get("stats", {})
            logger.info(
                f"  batch {batch_num}: OK "
                f"um_ancestors={stats.get('um_ancestors', 0)} "
                f"hn_parsed={stats.get('hn_parsed', 0)}"
            )
            return True
        else:
            logger.error(f"  batch {batch_num}: ERROR {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"  batch {batch_num}: EXCEPTION {e}")
        return False


def main() -> None:
    logger.info("=== DIFF UM取得開始 (option=3, 全期間) ===")

    try:
        import win32com.client
        jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
    except Exception as e:
        logger.error(f"JV-Link初期化失敗: {e}")
        return

    rc = jv.JVInit(JRAVAN_SID)
    logger.info(f"JVInit rc={rc}")
    if rc < 0:
        logger.error(f"JVInit failed: rc={rc}")
        return

    from_time = "19860101000000"
    logger.info(f"JVOpen: DIFF from={from_time} option=4 (ダイアログ無し)")
    result = jv.JVOpen("DIFF", from_time, 4, 0, 0, "")
    if isinstance(result, tuple):
        rc, count, dl = result[0], result[1], result[2]
        logger.info(f"JVOpen rc={rc} count={count} dl={dl}")
        if rc < 0:
            logger.error(f"JVOpen failed: rc={rc}")
            jv.JVClose()
            return
    else:
        logger.error(f"JVOpen unexpected result: {result}")
        return

    current_file = ""
    file_records: list[dict] = []
    batch: list[dict] = []
    batch_num = 0
    total_um = 0
    total_records = 0
    errors = 0

    def flush_batch() -> None:
        nonlocal batch_num, errors
        if not batch:
            return
        batch_num += 1
        ok = post_batch(batch, batch_num)
        if not ok:
            errors += 1
        batch.clear()

    while True:
        try:
            r = jv.JVRead()
        except Exception as e:
            logger.error(f"JVRead error: {e}")
            break

        if r is None:
            break
        if isinstance(r, tuple) and len(r) >= 2:
            rc2, data = r[0], r[1] if len(r) > 1 else ""
        else:
            rc2, data = r, ""

        if rc2 == 0:  # EOF
            flush_batch()
            logger.info(f"JVRead EOF: UM={total_um} / 全{total_records}件")
            break
        elif rc2 == -1:  # ファイル切り替わり
            fname_result = jv.JVFileName()
            current_file = fname_result[0] if isinstance(fname_result, tuple) else str(fname_result)
            logger.info(f"ファイル切り替わり: {current_file}")
            continue
        elif rc2 == -3:  # DL中
            time.sleep(1)
            continue
        elif rc2 < -3:
            logger.error(f"JVRead error rc={rc2}")
            break
        else:
            buff = data
            if isinstance(buff, bytes):
                buff = buff.decode("latin-1")
            else:
                buff = normalize_jvread(buff)

            rec_id = buff[:2].strip() if len(buff) >= 2 else ""
            if rec_id == "UM":
                batch.append({"rec_id": "UM", "data": buff})
                total_um += 1
                if len(batch) >= BATCH_SIZE:
                    flush_batch()
            total_records += 1

            if total_records % 5000 == 0:
                logger.info(f"  {total_records}件読み込み済 (UM: {total_um}件)")

    jv.JVClose()
    logger.info(f"=== 完了: UM {total_um}件取得, {batch_num}バッチ送信, {errors}エラー ===")


if __name__ == "__main__":
    main()
