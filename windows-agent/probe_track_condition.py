"""発走前に馬場状態を取得できる速報系データを実機で特定するプローブ（読み取り専用）。

背景:
  `keiba.races.condition` は成績確定後の RA（0B12）でしか埋まらないため、
  レース発走前の指数算出時には馬場状態が分からない。
  JRA は開催日の朝に馬場状態を発表しているので、速報系のどれかで取れるはず。

調べること:
  1. `0B14`（速報開催情報・一括）に **WE レコード（天候馬場状態）** が来るか
     → 来るならバイト位置を実データから同定する（仕様書にフィールド表が無いため）
  2. `0B15`（速報レース情報）の RA が発走前に馬場状態を持つか
     → RA のデータ区分と 889/890 バイト目（芝/ダート馬場状態コード）を確認

出力は標準出力ではなくファイルに書く（pythonw / RunAdhoc 経由で起動されるため）。

使い方（Windows・RunAdhoc 経由）:
    probe_track_condition.py [YYYYMMDD]
出力: C:\\kiseki\\windows-agent\\probe_track_condition.txt
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

import pythoncom
import win32com.client

OUT_PATH = Path(__file__).resolve().parent / "probe_track_condition.txt"

# 参考: RA レコードの馬場状態（1-indexed）
RA_TURF_COND_POS = 889
RA_DIRT_COND_POS = 890
# コード表2010 馬場状態コード
COND_MAP = {"0": "未設定", "1": "良", "2": "稍重", "3": "重", "4": "不良"}
# コード表2011 天候コード
WEATHER_MAP = {
    "0": "未設定", "1": "晴", "2": "曇", "3": "雨", "4": "小雨",
    "5": "雪", "6": "小雪",
}


def _normalize(buff) -> str:
    """JVRead の戻り値をそのまま Latin-1 文字列にする（1文字=1バイト）。"""
    if isinstance(buff, bytes):
        return buff.decode("latin-1", errors="replace")
    return str(buff)


def dump(out, dataspec: str, key: str, want: set[str], max_dump: int = 6) -> None:
    """JVRTOpen して取得できたレコード種別を集計し、対象レコードを詳細ダンプする。"""
    pythoncom.CoInitialize()
    jv = win32com.client.Dispatch("JVDTLab.JVLink")
    rc = jv.JVInit("UNKNOWN")
    out.write(f"\n===== JVRTOpen({dataspec}, {key}) =====\n")
    out.write(f"JVInit rc={rc}\n")
    rc = jv.JVRTOpen(dataspec, key)
    out.write(f"JVRTOpen rc={rc}\n")
    if rc < 0:
        out.write("  → データなし / エラー\n")
        try:
            jv.JVClose()
        except Exception:  # noqa: BLE001
            pass
        return

    counts: dict[str, int] = {}
    dumped: dict[str, int] = {}
    while True:
        r = jv.JVRead("", 256000, "")
        code = r[0]
        if code == 0:
            break
        if code == -1:
            continue
        if code < -1:
            out.write(f"  JVRead error rc={code}\n")
            break
        buff = _normalize(r[1])
        rec_id = buff[:2]
        counts[rec_id] = counts.get(rec_id, 0) + 1
        if rec_id in want and dumped.get(rec_id, 0) < max_dump:
            dumped[rec_id] = dumped.get(rec_id, 0) + 1
            out.write(f"\n--- {rec_id} #{dumped[rec_id]} (len={len(buff)}) ---\n")
            out.write(f"  raw : {buff[:80]!r}\n")
            if rec_id == "RA":
                kubun = buff[2:3]
                turf = buff[RA_TURF_COND_POS - 1:RA_TURF_COND_POS]
                dirt = buff[RA_DIRT_COND_POS - 1:RA_DIRT_COND_POS]
                out.write(
                    f"  データ区分={kubun} 開催={buff[11:19]} 場={buff[19:21]} R={buff[25:27]}"
                    f" 芝馬場[889]={turf}({COND_MAP.get(turf, '?')})"
                    f" ダ馬場[890]={dirt}({COND_MAP.get(dirt, '?')})\n"
                )
            elif rec_id == "WE":
                # フィールド表が無いので 1 バイトずつ位置付きで出す（同定用）
                out.write("  pos:value（1-indexed / 非空白のみ）\n    ")
                for i, ch in enumerate(buff[:42], start=1):
                    if ch not in (" ", "\r", "\n"):
                        out.write(f"{i}:{ch!r} ")
                out.write("\n")
    try:
        jv.JVClose()
    except Exception:  # noqa: BLE001
        pass
    out.write(f"\nレコード種別ごとの件数: {counts}\n")


def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    with OUT_PATH.open("w", encoding="utf-8") as out:
        out.write(f"probe_track_condition {datetime.now().isoformat()} date={date}\n")
        for dataspec, want in (("0B14", {"WE"}), ("0B16", {"WE"}), ("0B15", {"RA"})):
            try:
                dump(out, dataspec, date, want)
            except Exception:  # noqa: BLE001
                out.write(f"\n!!! {dataspec} で例外\n{traceback.format_exc()}\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
