"""JV-Link サーバー状態のガード — メンテナンス窓の事前回避と JVOpen 戻り値の分類。

Why（2026-08-04 の障害）:
    JRA-VAN のサーバーメンテナンス中に JVOpen を呼ぶと rc=-504 が返るだけでなく、
    JVDTLab / JVNextCore が **モーダルダイアログ** を出してデスクトップセッションを
    掴む。エージェントは pythonw.exe から起動しているためダイアログを閉じる者がおらず、
    COM 呼び出しがそのまま長時間ブロックする。
    実測: 2026-08-04 13:41 の JVOpen は 1193 秒（約20分）待たされた末に rc=-504 を返し、
    jvlink_historical の time_limit=7200 秒の処理枠をそれだけで食い潰した。

    したがって「rc を見てから諦める」のでは足りない。**既知のメンテナンス窓では
    JVOpen / JVRTOpen を最初から呼ばない**のが唯一の確実な回避策になる。

窓の指定（環境変数 JVLINK_MAINTENANCE_WINDOWS、カンマ区切り）:
    TUE 08:00-15:00          毎週火曜（既定）
    1ST-TUE 08:00-15:00      毎月第一火曜（JRA-VAN 公式 FAQ の記載）
    2026-09-10 09:00-12:00   特定日（臨時メンテナンスの一時追加用）

既定値を「毎週火曜 8:00-15:00」にしている理由:
    公式 FAQ の記載は「毎月第一火曜 8:00〜15:00」だが、実運用ではそれ以外の火曜にも
    ダイアログが観測されている。JRA は火曜に開催しないため、火曜日中の蓄積系バッチを
    止めても失うのは同日のバックフィル枠だけで実害が小さい。安全側に倒す。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta

__all__ = [
    "DEFAULT_MAINTENANCE_WINDOWS",
    "MaintenanceWindow",
    "MaintenanceWindowActive",
    "active_window",
    "classify_rc",
    "describe_rc",
    "load_windows",
    "parse_windows",
    "RC_FATAL",
    "RC_MAINTENANCE",
    "RC_NO_DATA",
    "RC_OK",
    "RC_TRANSIENT",
]

DEFAULT_MAINTENANCE_WINDOWS = "TUE 08:00-15:00"
ENV_VAR = "JVLINK_MAINTENANCE_WINDOWS"

_WEEKDAYS = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}
_ORDINALS = {"1ST": 1, "2ND": 2, "3RD": 3, "4TH": 4, "5TH": 5}

_RE_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_RE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


# ---------------------------------------------------------------------------
# メンテナンス窓
# ---------------------------------------------------------------------------

class MaintenanceWindowActive(RuntimeError):
    """メンテナンス窓中に JV-Link サーバーへアクセスしようとしたときに送出する。

    呼び出し側はこれを「異常終了」ではなく「今回は見送り」として扱い、
    次回のスケジュール実行に委ねること。
    """


@dataclass(frozen=True)
class MaintenanceWindow:
    """1 件のメンテナンス窓。

    Attributes:
        kind: "weekly" | "monthly" | "date"
        weekday: 曜日 (0=月 .. 6=日)。kind が weekly / monthly のとき有効。
        nth: 第何週か (1-5)。kind が monthly のとき有効。
        on_date: 対象日。kind が "date" のとき有効。
        start: 開始時刻。
        end: 終了時刻（この時刻ちょうどは窓の外）。
        raw: 元の指定文字列（ログ用）。
    """

    kind: str
    start: dtime
    end: dtime
    raw: str
    weekday: int | None = None
    nth: int | None = None
    on_date: date | None = None

    def matches_date(self, d: date) -> bool:
        """指定日がこの窓の対象日かどうかを返す。"""
        if self.kind == "date":
            return d == self.on_date
        if self.weekday is None or d.weekday() != self.weekday:
            return False
        if self.kind == "weekly":
            return True
        # monthly: 同じ曜日が月内で何回目か
        return (d.day - 1) // 7 + 1 == self.nth

    def contains(self, dt: datetime) -> bool:
        """指定日時がこの窓の中かどうかを返す。"""
        return self.matches_date(dt.date()) and self.start <= dt.time() < self.end

    def end_at(self, dt: datetime) -> datetime:
        """dt が属する窓の終了日時を返す。"""
        return datetime.combine(dt.date(), self.end)


def _parse_time_range(token: str, raw: str) -> tuple[dtime, dtime]:
    """"HH:MM-HH:MM" を (start, end) に変換する。"""
    m = _RE_TIME_RANGE.match(token)
    if not m:
        raise ValueError(f"時刻範囲の書式が不正です: {token!r} (指定={raw!r})")
    sh, sm, eh, em = (int(x) for x in m.groups())
    start, end = dtime(sh, sm), dtime(eh, em)
    if start >= end:
        raise ValueError(
            f"開始 >= 終了 の窓は日跨ぎ扱いになるため未対応です: {token!r} (指定={raw!r})"
        )
    return start, end


def parse_windows(spec: str) -> list[MaintenanceWindow]:
    """窓の指定文字列を解析する。不正なエントリは ValueError を送出する。

    Args:
        spec: カンマ区切りの窓指定（例: "TUE 08:00-15:00,2026-09-10 09:00-12:00"）

    Returns:
        MaintenanceWindow のリスト。spec が空なら空リスト。
    """
    windows: list[MaintenanceWindow] = []
    for entry in spec.split(","):
        raw = entry.strip()
        if not raw:
            continue
        parts = raw.split()
        if len(parts) != 2:
            raise ValueError(f"窓の書式が不正です: {raw!r} (期待: '<日指定> HH:MM-HH:MM')")
        day_token, time_token = parts[0].upper(), parts[1]
        start, end = _parse_time_range(time_token, raw)

        if m := _RE_DATE.match(parts[0]):
            y, mo, d = (int(x) for x in m.groups())
            windows.append(
                MaintenanceWindow("date", start, end, raw, on_date=date(y, mo, d))
            )
        elif day_token in _WEEKDAYS:
            windows.append(
                MaintenanceWindow("weekly", start, end, raw, weekday=_WEEKDAYS[day_token])
            )
        elif "-" in day_token:
            ordinal, _, wd = day_token.partition("-")
            if ordinal not in _ORDINALS or wd not in _WEEKDAYS:
                raise ValueError(f"日指定が不正です: {parts[0]!r} (指定={raw!r})")
            windows.append(
                MaintenanceWindow(
                    "monthly", start, end, raw,
                    weekday=_WEEKDAYS[wd], nth=_ORDINALS[ordinal],
                )
            )
        else:
            raise ValueError(f"日指定が不正です: {parts[0]!r} (指定={raw!r})")
    return windows


def load_windows(logger=None) -> list[MaintenanceWindow]:
    """環境変数から窓を読み込む。未設定なら既定値を使う。

    書式エラーは **握り潰して既定値にフォールバックしない**。誤設定のまま
    「窓なし」で動くとダイアログ地獄に戻るため、既定値へ倒したうえで警告を出す。
    """
    spec = os.getenv(ENV_VAR, "").strip() or DEFAULT_MAINTENANCE_WINDOWS
    try:
        return parse_windows(spec)
    except ValueError as e:
        if logger:
            logger.warning(
                f"[maintenance] {ENV_VAR} の書式が不正です ({e})。"
                f" 既定値 {DEFAULT_MAINTENANCE_WINDOWS!r} を使用します。"
            )
        return parse_windows(DEFAULT_MAINTENANCE_WINDOWS)


def active_window(
    now: datetime | None = None,
    windows: list[MaintenanceWindow] | None = None,
    logger=None,
) -> MaintenanceWindow | None:
    """now が属するメンテナンス窓を返す。窓の外なら None。"""
    now = now or datetime.now()
    if windows is None:
        windows = load_windows(logger)
    for w in windows:
        if w.contains(now):
            return w
    return None


def skip_reason(
    label: str,
    now: datetime | None = None,
    windows: list[MaintenanceWindow] | None = None,
    logger=None,
) -> str | None:
    """メンテナンス窓中なら「スキップ理由」を返す。窓の外なら None。

    呼び出し側はこれが None でないときに JVOpen / JVRTOpen を**呼ばない**。

    Args:
        label: ログに出す呼び出し元の名前（"JVOpen(RACE)" など）
    """
    now = now or datetime.now()
    w = active_window(now, windows, logger)
    if w is None:
        return None
    remain = int((w.end_at(now) - now).total_seconds())
    return (
        f"{label}: JRA-VAN メンテナンス窓のためスキップします"
        f" (窓={w.raw}, 残り約{remain // 60}分)"
    )


def next_window_start(
    now: datetime | None = None,
    windows: list[MaintenanceWindow] | None = None,
    logger=None,
    horizon_days: int = 40,
) -> datetime | None:
    """次にメンテナンス窓が始まる日時を返す。horizon_days 以内に無ければ None。"""
    now = now or datetime.now()
    if windows is None:
        windows = load_windows(logger)
    for offset in range(horizon_days + 1):
        d = now.date() + timedelta(days=offset)
        starts = [
            datetime.combine(d, w.start) for w in windows if w.matches_date(d)
        ]
        starts = [s for s in starts if s > now]
        if starts:
            return min(starts)
    return None


# ---------------------------------------------------------------------------
# JVOpen / JVRTOpen 戻り値の分類
# ---------------------------------------------------------------------------

RC_OK = "ok"
RC_NO_DATA = "no_data"
RC_MAINTENANCE = "maintenance"
RC_TRANSIENT = "transient"
RC_FATAL = "fatal"

#: 出典: JRA-VAN Data Lab. ヘルプセンター / JV-Link 仕様書
RC_MEANINGS: dict[int, str] = {
    -1: "該当データなし",
    -111: "dataspec パラメータが不正",
    -112: "fromtime パラメータが不正",
    -113: "option パラメータが不正",
    -114: "key パラメータが不正",
    -115: "option が dataspec に対して不正",
    -116: "fromtime が指定範囲外",
    -201: "JVInit が行われていない",
    -202: "前回の JVOpen が終了していない",
    -203: "JVClose が行われていない",
    -211: "レジストリ／設定ファイル異常",
    -301: "認証エラー（利用キー未設定・不正）",
    -302: "利用キーの有効期限切れ",
    -303: "利用キーがサービス対象外／ファイル存在確認エラー",
    -401: "JV-Link 内部エラー",
    -402: "ダウンロードしたファイルが不正",
    -403: "ダウンロードしたファイルの解凍失敗",
    -411: "サーバーエラー",
    -412: "サーバーエラー",
    -413: "通信確立不可（ネットワーク／セキュリティソフト）",
    -421: "サーバーエラー",
    -431: "JRA-VAN サービス停止中",
    -501: "該当ファイルなし",
    -502: "ダウンロード失敗",
    -503: "ファイルアクセス失敗",
    -504: "サーバーメンテナンス中",
}

#: 待てば直る系。ERROR ではなく WARNING で扱い、次回実行に委ねる。
_MAINTENANCE_RCS = frozenset({-504, -431})
_TRANSIENT_RCS = frozenset({-402, -403, -411, -412, -413, -421, -502, -503})


def classify_rc(rc: int) -> str:
    """JVOpen / JVRTOpen の戻り値を扱いやすい区分に分類する。

    Returns:
        RC_OK / RC_NO_DATA / RC_MAINTENANCE / RC_TRANSIENT / RC_FATAL のいずれか。
    """
    if rc >= 0:
        return RC_OK
    if rc == -1:
        return RC_NO_DATA
    if rc in _MAINTENANCE_RCS:
        return RC_MAINTENANCE
    if rc in _TRANSIENT_RCS:
        return RC_TRANSIENT
    return RC_FATAL


def describe_rc(rc: int) -> str:
    """戻り値を「rc=-504 (サーバーメンテナンス中)」の形に整形する。"""
    meaning = RC_MEANINGS.get(rc)
    return f"rc={rc} ({meaning})" if meaning else f"rc={rc}"


def log_open_failure(logger, label: str, rc: int) -> str:
    """JVOpen 失敗を分類に応じたログレベルで記録し、分類を返す。

    Why: 従来は rc<0 を一律 logger.error していたため、待てば直る -504 と
    復旧作業が要る -303 がログ上で区別できなかった。

    Args:
        logger: 呼び出し元の logger
        label: "JVOpen(RACE)" など
        rc: 戻り値

    Returns:
        classify_rc(rc) の結果
    """
    kind = classify_rc(rc)
    desc = describe_rc(rc)
    if kind == RC_NO_DATA:
        logger.info(f"{label}: {desc} — 取得対象なし")
    elif kind == RC_MAINTENANCE:
        logger.warning(f"{label}: {desc} — 次回実行に委ねます（復旧待ち）")
    elif kind == RC_TRANSIENT:
        logger.warning(f"{label}: {desc} — 一時的な障害。次回実行に委ねます")
    else:
        logger.error(f"{label}: {desc}")
    return kind
