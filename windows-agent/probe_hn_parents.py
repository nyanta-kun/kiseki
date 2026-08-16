"""HN（繁殖馬マスタ）の親コード欄が実際に埋まっているかを実測する。

## なぜ要るか

5代血統（インブリード判定に必要）を **netkeiba をスクレイピングせずに** 作れるかを決める。

`parse_hn` は父馬繁殖登録番号（pos 230-239）と母馬繁殖登録番号（pos 240-249）を
抽出しているが、`keiba.breeding_horses` は `breeding_code / name / name_en` の
3列しか持たず**保存していない**。

もしこの2欄が埋まっていれば、繁殖登録番号で**何代でも再帰**できる:

    競走馬 → SK/UM の3代14頭 → 各祖先を HN で引く → その親 → …

繁殖馬マスタは種牡馬・繁殖牝馬の両方を 99% カバーしている
（実測 2026-08-16: 父 2,853/2,871・母 39,881/39,925）ので、
**埋まってさえいれば netkeiba は不要**になる。

逆に空欄が多ければ、5代は netkeiba から取るしかない。
**その判断のためだけのスクリプト。DB には一切書かない。**

## 実行

    ssh windows-vm 'powershell -NoProfile -Command "Set-Content -Path \\"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\\" -Value \\"probe_hn_parents.py\\" -Encoding ASCII"'
    ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

出力: `C:\\kiseki\\windows-agent\\probe_hn_parents.log`

⚠️ 火曜 08:00-15:00 は JRA-VAN のメンテナンス窓（`jvlink_maintenance.py`）。
   その時間帯は JVOpen がダイアログを出して固まりうるので実行しない。

🔴 **開催日の日中に実行しないこと**（2026-08-16 に踏んだ）。
   realtime と並走させたところ `JVOpen(BLDN, option=4)` が **22分戻らず**、
   JV-Link 枠を占有し続けたので taskkill で回収した。CLAUDE.md は複数インスタンスの
   同時実行を「可能」としているが、BLDN の累積マスタ（571ファイル）は重く別物。
   **realtime が止まる 22:30 JST 以降に回すこと。**

⚠️ このスクリプトは `BlockingCallGuard` / `jvlink_dialog_guard` を通していない。
   モーダルが出ると自力復帰できないので、長時間戻らなければ taskkill で回収する。
"""

import logging
import os
import sys
from pathlib import Path

import win32com.client

_here = Path(__file__).resolve()
log_path = _here.parent / "probe_hn_parents.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(log_path), encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger("probe_hn_parents")

JRAVAN_SID = os.getenv("JRAVAN_SID", "kiseki")

# 何レコード見たら十分か。HN は BLDN の先頭付近にまとまって出るので数千で足りる。
MAX_HN = 5000


def _s(data: str, start: int, end: int) -> str:
    """1-indexed のバイト位置で切り出す（jvlink_parser と同じ規則）。"""
    return data[start - 1:end].strip()


def main() -> None:
    jv = win32com.client.Dispatch("JVDTLab.JVLink")
    rc = jv.JVInit(JRAVAN_SID)
    log.info(f"JVInit rc={rc}")
    if rc != 0:
        sys.exit(1)

    # BLDN の累積マスタを引く。from_time をサービス開始前にすると
    # 累積マスタだけが返る（CLAUDE.md「bldn-full モード 仕様」）。
    result = jv.JVOpen("BLDN", "20000101000000", 4, 0, 0, "")
    rc = result[0] if isinstance(result, tuple) else result
    n_files = result[1] if isinstance(result, tuple) else "?"
    log.info(f"JVOpen(BLDN, option=4) rc={rc} files={n_files}")
    if rc < 0:
        log.error("JVOpen 失敗。メンテナンス窓か接続不良の可能性")
        jv.JVClose()
        sys.exit(1)

    n_hn = 0
    n_sire = 0
    n_dam = 0
    n_both = 0
    samples: list[str] = []

    while n_hn < MAX_HN:
        try:
            r = jv.JVRead("", 256000, "")
        except Exception as e:
            log.error(f"JVRead exception: {e}")
            break
        ret = r[0]
        if ret == 0:
            log.info("EOF")
            break
        if ret < -3:
            log.error(f"JVRead error ret={ret}")
            break
        if ret in (-1, -3):
            continue

        data = r[1] if len(r) > 1 else ""
        if not data.startswith("HN"):
            continue
        if len(data) < 250:
            continue

        n_hn += 1
        breeding_code = _s(data, 12, 21)
        sire_code = _s(data, 230, 239)
        dam_code = _s(data, 240, 249)
        if sire_code:
            n_sire += 1
        if dam_code:
            n_dam += 1
        if sire_code and dam_code:
            n_both += 1
        if len(samples) < 5:
            samples.append(
                f"    breeding={breeding_code} sire={sire_code!r} dam={dam_code!r} len={len(data)}"
            )

    jv.JVClose()

    log.info("=" * 60)
    log.info(f"  HN レコード          : {n_hn:,}")
    if n_hn:
        log.info(f"  父コードあり          : {n_sire:,} ({n_sire / n_hn * 100:.1f}%)")
        log.info(f"  母コードあり          : {n_dam:,} ({n_dam / n_hn * 100:.1f}%)")
        log.info(f"  両方あり（再帰可能）  : {n_both:,} ({n_both / n_hn * 100:.1f}%)")
    log.info("  サンプル:")
    for s in samples:
        log.info(s)
    log.info("=" * 60)
    log.info("  両方ありが高ければ netkeiba 不要。低ければ 5代はスクレイピングが要る。")


if __name__ == "__main__":
    main()
