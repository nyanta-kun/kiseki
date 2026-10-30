"""POG 週報「赤兎週報」の紙面づくり（純関数のみ）。

2026-09-16 に Claude Desktop から手動で 1 回だけ送った「赤兎日報」（Windows 用の
`pog_daily` セット・zip のまま未設置）を kiseki へ移したもの。通信と PDF 化は
`pog_report_io.py`、実行は `scripts/notify_pog_report.py`。

## 元のセットから変えたこと

| | 元（pog_daily） | ここ |
|---|---|---|
| 指名馬・馬主・順位 | sekito-stable の API | `keiba.pog_*`（`pog_standings` と同じ SQL） |
| 今週の出走 | JV-Link を直接読む | `pog_recent_races` ＋ `keiba.projected_entries` |
| 先週の結果 | sekito の recent-races | `pog_recent_races`（結果が入った行） |
| 馬名・調教師・成績 | netkeiba の馬ページ | `keiba.pog_horses` と POG の集計 |
| 近況・掲示板 | netkeiba | netkeiba（**ここだけ外へ取りに行く**） |
| 掲示板の応答 | HTML（`.comment_txt_area_01`） | 🔴 **JSON の一覧**（2026-10-30 実測で形式が変わっていた） |

sekito の API に頼らないので、sekito を完全停止しても止まらない。

## 掲示板は言い換えない

分類はキーワードで行い、本文は**原文のまま抜粋**する（元のセットの方針を踏襲）。
掲示板は一般ユーザーの投稿で、要約すると未確認の話が断定口調になるため。
紙面では必ず「掲示板・未確認」と添える。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

ESC = html.escape
WEEKDAYS = "月火水木金土日"

# ───────────────────────── 掲示板 ─────────────────────────

# 上から順に評価し、最初に当たったものを採る（重要な順）
BBS_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "故障",
        re.compile(r"骨折|骨片|剥離|関節鏡|手術|屈腱|靭帯|挫石|フレグモーネ|裂蹄|" r"跛行|登録抹消|競走除外"),
    ),
    ("出走", re.compile(r"特別登録|出馬表|出走(を)?(予定|想定|確定|表)|今週(出|使)")),
    ("鞍上", re.compile(r"鞍上|騎乗|乗り替わ|継続騎乗|騎手(が|は|で)")),
    ("次走", re.compile(r"次走|次(は|走は)|ローテ|使う予定|向かう|参戦|帰厩(予定)?")),
    (
        "調教",
        re.compile(
            r"追い?切り|最終追|坂路|ウッド|美浦[ＷWDＤ]|栗東[CＣWＷ坂]|"
            r"馬なり|一杯|強め|併せ|直線追|\d{2}\.\d-\d{2}\.\d"
        ),
    ),
    ("デビュー", re.compile(r"新馬|デビュー|初戦|入厩")),
]
# 🔴 元のセットは「回避」も故障に入れていたが、2026-10-30 の試走で 2 件とも
#    誤判定だった（「〜なら回避でも構わない」「（別の馬の）回避は痛い」）。
#    故障は Discord の本文に ⚠ で出るので、取りこぼしより誤報の方が害が大きい。
BBS_PRIORITY = {"故障": 0, "出走": 1, "次走": 2, "鞍上": 3, "調教": 4, "デビュー": 5, "": 9}
_REPLY_RE = re.compile(r"^>>\d+\s*")
_BBS_TIME_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})")


@dataclass(frozen=True)
class BbsPost:
    """掲示板の投稿 1 件。"""

    user: str
    text: str
    posted_at: datetime | None


def classify_bbs(text: str) -> str:
    """投稿を分類する。どれにも当たらなければ空文字。"""
    for label, rx in BBS_RULES:
        if rx.search(text):
            return label
    return ""


def clean_bbs(text: str, limit: int = 160) -> str:
    """返信記号を落とし、空白を詰め、長ければ切る。言い換えはしない。"""
    t = " ".join(_REPLY_RE.sub("", text).split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def parse_bbs_payload(payload: object) -> list[BbsPost]:
    """掲示板 API の応答（JSON）から投稿を取り出す。

    🔴 2026-10-30 実測の形:
        {"status":"OK","data":{"count":"1629","list":[
            {"nickname":..,"comment":..,"datetime":"2026/10/29 7:56",
             "is_hidden_comment":"0", ...}, ...]}}
    元のセットは HTML を前提にしていたため、このままでは 0 件になっていた。
    非表示の投稿（`is_hidden_comment != "0"`）は載せない。
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    items = data.get("list") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    posts: list[BbsPost] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("is_hidden_comment", "0")) != "0":
            continue
        body = " ".join(str(it.get("comment") or "").split())
        if not body:
            continue
        posts.append(
            BbsPost(
                user=str(it.get("nickname") or ""),
                text=body,
                posted_at=parse_bbs_time(str(it.get("datetime") or "")),
            )
        )
    return posts


def parse_bbs_time(s: str) -> datetime | None:
    """`2026/10/29 7:56` を datetime に。読めなければ None。"""
    m = _BBS_TIME_RE.search(s)
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def recent_posts(posts: list[BbsPost], now: datetime, days: int) -> list[BbsPost]:
    """直近 `days` 日の投稿だけ残す。日時が読めない投稿は落とす。"""
    since = now - timedelta(days=days)
    return [p for p in posts if p.posted_at is not None and p.posted_at >= since]


@dataclass(frozen=True)
class BbsSummary:
    """1 頭ぶんの掲示板の要点。"""

    count: int
    label: str
    when: str
    text: str


def summarize_bbs(posts: list[BbsPost]) -> BbsSummary | None:
    """件数と、いちばん重要な投稿 1 件の原文抜粋を返す。

    同じ重要度なら新しい投稿を採る（API は新しい順で返すが、念のため並べ直す）。
    """
    if not posts:
        return None
    ordered = sorted(posts, key=lambda p: p.posted_at or datetime.min, reverse=True)
    top = min(ordered, key=lambda p: BBS_PRIORITY[classify_bbs(p.text)])
    when = f"{top.posted_at.month}/{top.posted_at.day}" if top.posted_at else ""
    return BbsSummary(count=len(posts), label=classify_bbs(top.text), when=when, text=clean_bbs(top.text))


# ───────────────────────── 近況（netkeiba 次走・近況情報） ─────────────────────────

_REST_RE = re.compile(r"休養のため放牧.*?(\d{2}/\d{2}/\d{2})")
_BACK_RE = re.compile(r"放牧から帰厩.*?(\d{2}/\d{2}/\d{2})")
_ENTRY_RE = re.compile(r"(\d{4}/\d{1,2}/\d{1,2}\([日月火水木金土]\))\s*([^\s]+?)\s*上記レースに(特別登録|出走想定)")
_PLAN_RE = re.compile(r"(\d{4}/\d{1,2}/\d{1,2}\([日月火水木金土]\))\s*([^\s※]+)")


@dataclass(frozen=True)
class HorseStatus:
    """近況テキストから読み取った状態。"""

    state: str = ""  # 帰厩 / 放牧 / 出走 / ""
    note: str = ""  # 放牧から帰厩(10/1) など
    entry: str = ""  # 特別登録・出走想定（公式）
    plan: str = ""  # netkeiba 独自の想定
    since: date | None = None  # 放牧・帰厩の日付


def parse_status(text: str) -> HorseStatus:
    """netkeiba の「次走・近況情報」の本文から状態を取り出す。"""
    state = note = entry = plan = ""
    since: date | None = None
    m = _BACK_RE.search(text)
    if m:
        state, note, since = "帰厩", f"放牧から帰厩({_mmdd(m.group(1))})", _yymmdd(m.group(1))
    else:
        m = _REST_RE.search(text)
        if m:
            state, note, since = "放牧", f"放牧中({_mmdd(m.group(1))}〜)", _yymmdd(m.group(1))
    m = _ENTRY_RE.search(text)
    if m:
        entry = f"{m.group(1)} {m.group(2)}（{m.group(3)}）"
        state = state or "出走"
    elif "netkeiba独自の想定" in text:
        m = _PLAN_RE.search(text)
        if m:
            plan = f"{m.group(1)} {m.group(2)}"
    return HorseStatus(state=state, note=note, entry=entry, plan=plan, since=since)


def reconcile_status(status: HorseStatus, last_run: date | None) -> HorseStatus:
    """放牧・帰厩の日付より後に走っていたら、その情報は古いので落とす。

    netkeiba の近況は帰厩が書き込まれないことがある。2026-10-30 の試走で、
    10/24 にアルテミスSを勝った馬が「放牧中(9/25〜)」のまま出ていた。
    """
    if status.since and last_run and last_run > status.since and status.state in ("放牧", "帰厩"):
        return HorseStatus(entry=status.entry, plan=status.plan, state="出走" if status.entry else "")
    return status


def _mmdd(yymmdd: str) -> str:
    _, m, d = yymmdd.split("/")
    return f"{int(m)}/{int(d)}"


def _yymmdd(s: str) -> date | None:
    """`26/09/25` → date。"""
    try:
        y, m, d = (int(x) for x in s.split("/"))
        return date(2000 + y, m, d)
    except ValueError:
        return None


# ───────────────────────── 紙面の組み立て ─────────────────────────


def md_label(d: date) -> str:
    """`10/31(土)`。"""
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


@dataclass
class HorseInfo:
    """指名馬 1 頭ぶん（DB ＋ netkeiba）。"""

    horse_id: str
    name: str
    owner: str
    sex: str = ""
    sire: str = ""
    broodmare: str = ""
    stable: str = ""
    record: str = ""  # 1-0-0-1
    prize: int = 0  # POG 賞金（万円）
    last: str = ""  # 直近の出走
    last_run: date | None = None
    status: HorseStatus = field(default_factory=HorseStatus)
    bbs: BbsSummary | None = None
    entered: bool = False  # 今週の出走予定に載っている


# ───────────────────────── 調教 ─────────────────────────

# トレセン区分（JV-Data の HC/WC）。0=美浦 / 1=栗東（2026-10-30 に厩舎の所属で確認:
# 田中博康厩舎=0、友道康夫厩舎=1）
CENTER_NAME = {"0": "美浦", "1": "栗東"}
# ウッドのコース。実データは 美浦=3(D=W コース) / 栗東=2(C=CW) だけ
WOOD_NAME = {("0", "3"): "美浦W", ("1", "2"): "栗東CW"}
WORKOUT_DAYS = 14
WORKOUT_MAX = 3


@dataclass(frozen=True)
class Workout:
    """追い切り 1 本（`keiba.slope_training` / `keiba.wood_training`）。"""

    day: date
    place: str  # 美浦坂路 / 栗東CW など
    distance: str  # 6F / 5F / 4F（先頭のラップの距離）
    times: tuple[float, ...]  # 先頭から 3F までの通過 → 最後の 1F
    time_4f: float | None

    def label(self) -> str:
        """`10/28 美浦W 6F 84.8-67.7-52.7-38.6-11.8`。"""
        body = "-".join(f"{t:.1f}" for t in self.times)
        return f"{self.day.month}/{self.day.day} {self.place} {self.distance} {body}"


def wood_place(center: str, course: str) -> str:
    """ウッドの場所名。対応表に無いコースは `美浦ウッド` のように書く。"""
    return WOOD_NAME.get((center, course), f"{CENTER_NAME.get(center, '')}ウッド")


def pick_workouts(workouts: list[Workout], race_day: date) -> tuple[Workout, ...]:
    """レース前 `WORKOUT_DAYS` 日の追い切りを新しい順に最大 `WORKOUT_MAX` 本。

    レース当日以降の時計は載せない（翌週の号で前週の時計を見せないため）。
    """
    since = race_day - timedelta(days=WORKOUT_DAYS)
    picked = [w for w in workouts if since <= w.day < race_day]
    picked.sort(key=lambda w: w.day, reverse=True)
    return tuple(picked[:WORKOUT_MAX])


def fastest(workouts: tuple[Workout, ...]) -> Workout | None:
    """4F が最も速い 1 本（坂路とウッドは 4F で比べる）。"""
    timed = [w for w in workouts if w.time_4f]
    return min(timed, key=lambda w: w.time_4f or 0) if timed else None


@dataclass(frozen=True)
class EntryRow:
    """今週の出走予定 1 行。"""

    day: date
    horse_id: str
    horse: str
    owner: str
    place: str
    race: str
    cond: str
    time: str
    jockey: str
    src: str  # fix=出馬表 / nk=netkeiba想定 / sp=特別登録
    workouts: tuple[Workout, ...] = ()


@dataclass(frozen=True)
class ResultRow:
    """先週の結果 1 行。"""

    day: date
    horse: str
    owner: str
    place: str
    cond: str
    jockey: str
    ninki: int | None
    chaku: int | None
    prize: int | None


@dataclass(frozen=True)
class OwnerStanding:
    """馬主の順位。"""

    rank: int
    owner: str
    win: int
    place: int
    show: int
    out: int
    prize: int
    top_horse: str


@dataclass
class Report:
    """紙面 1 号ぶん。`build_html` と `build_discord_content` の入力。"""

    title: str
    subtitle: str
    issued_at: datetime
    horses: list[HorseInfo]
    this_week: list[EntryRow]
    last_week: list[ResultRow]
    standings: list[OwnerStanding]
    bbs_days: int

    @property
    def date_label(self) -> str:
        d = self.issued_at.date()
        return f"{d.year}年{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}）"

    @property
    def headline(self) -> str:
        if not self.this_week:
            return "今週の出走予定はなし"
        lead = self.this_week[0]
        rest = len({e.horse_id for e in self.this_week}) - 1
        tail = f" ほか{rest}頭" if rest > 0 else ""
        return f"{lead.horse} が {lead.race} に出走{tail}"

    def owners_in_rank_order(self) -> list[tuple[OwnerStanding | None, str, list[HorseInfo]]]:
        """(順位, 馬主名, その馬主の指名馬) を順位順に。順位表に居ない馬主は末尾。"""
        by_owner: dict[str, list[HorseInfo]] = {}
        for h in self.horses:
            by_owner.setdefault(h.owner, []).append(h)
        out: list[tuple[OwnerStanding | None, str, list[HorseInfo]]] = []
        seen: set[str] = set()
        for s in self.standings:
            out.append((s, s.owner, by_owner.get(s.owner, [])))
            seen.add(s.owner)
        for owner, hs in by_owner.items():
            if owner not in seen:
                out.append((None, owner, hs))
        return out

    def injuries(self) -> list[HorseInfo]:
        """掲示板で「故障」に分類された馬。"""
        return [h for h in self.horses if h.bbs and h.bbs.label == "故障"]


def owner_label(owners: object) -> str:
    """`pog_recent_races` の `owners`（配列）を表示用に。"""
    if isinstance(owners, list | tuple):
        return "・".join(str(o) for o in owners if o)
    return str(owners or "")


def merge_entries(confirmed: list[EntryRow], projected: list[EntryRow]) -> list[EntryRow]:
    """出馬表・特別登録と出走想定をまとめる。

    同じ馬・同じ日は出馬表・特別登録を優先し、想定は捨てる。木曜以降に
    確定出馬表が入ると、水曜の想定と二重に載るのを防ぐ。

    ただし**特別登録には鞍上が無い**。水曜の号では重賞が特別登録と想定の
    両方に載るので、想定の鞍上を特別登録の行へ移してから捨てる
    （捨てると、いちばん注目されるレースだけ鞍上が「未定」になる）。
    """
    proj = {(p.horse_id, p.day): p for p in projected}
    kept = [
        replace(e, jockey=proj[(e.horse_id, e.day)].jockey) if not e.jockey and (e.horse_id, e.day) in proj else e
        for e in confirmed
    ]
    have = {(e.horse_id, e.day) for e in confirmed}
    merged = kept + [p for p in projected if (p.horse_id, p.day) not in have]
    return sorted(merged, key=lambda e: (e.day, e.time or "99:99", e.place, e.horse))


# ───────────────────────── HTML ─────────────────────────

SRC_LABEL = {
    "fix": ("出馬表", "s-fix"),
    "nk": ("netkeiba想定", "s-nk"),
    "sp": ("特別登録", "s-sp"),
    "bbs": ("掲示板", "s-bbs"),
}
STATE_BADGE = {"出走": "b-run", "放牧": "b-rest", "帰厩": "b-back"}
PICK_COLOR = {
    "故障": "c8102e",
    "出走": "14110f",
    "次走": "8a6d1f",
    "鞍上": "1e7a46",
    "調教": "2b5fa8",
    "デビュー": "6b3fa0",
}

# 元のセットの紙面をほぼそのまま使う（Chrome の印刷で A4 縦）
CSS = """
@page { size: A4 portrait; margin: 10mm 9mm 9mm 9mm; }
* { box-sizing:border-box; }
body { margin:0; color:#14110f; background:#fffdf6; font-size:15px; line-height:1.34;
  font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic","Noto Sans CJK JP","Meiryo",sans-serif;
  -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.serif { font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif; }
.page { page-break-after:always; } .page:last-child { page-break-after:auto; }
.muted{color:#8d8375} .sep{color:#c0b6a4;margin:0 6px} .hn{font-weight:700}
.ow{color:#7a6f60;font-size:.88em} .r1{color:#c8102e} .r2{color:#1e7a46} .r9{color:#5a5045}
.mh{border-bottom:4px double #14110f;padding-bottom:8px;margin-bottom:12px}
.mh-top{display:flex;align-items:flex-end;gap:12px}
.mh-t{font-weight:900;font-size:40px;letter-spacing:.06em;line-height:.95}
.mh-s{flex:1;text-align:right;font-size:13px;color:#6b5f52;line-height:1.5}
.mh-s .d{font-size:19px;color:#14110f;font-weight:700}
.mh-rule{height:2px;background:#14110f;margin:7px 0 8px}
.mh-kick{color:#c8102e;font-weight:700;font-size:14px;letter-spacing:.1em}
.mh-head{font-weight:900;font-size:24px;line-height:1.3;margin-top:3px}
.sh{background:#14110f;color:#fffdf6;font-weight:700;font-size:17px;letter-spacing:.16em;
  padding:5px 12px;margin:16px 0 6px;display:flex;justify-content:space-between;align-items:baseline}
.sh .rt{font-weight:400;font-size:12px;letter-spacing:.02em;opacity:.82}
.sh.red{background:#c8102e}
.cd{display:flex;border:1.5px solid #14110f;margin-bottom:5px;break-inside:avoid}
.cd-l{width:104px;background:#14110f;color:#fffdf6;padding:5px 4px;text-align:center;flex-shrink:0}
.cd-d{font-weight:700;font-size:15px} .cd-t{font-size:13px;margin-top:2px;color:#f0c040}
.cd-r{flex:1;padding:4px 10px;min-width:0} .cd-race{font-size:14px}
.cd-race b{font-size:16px} .cd-cond{color:#6b5f52;font-size:13px;margin-left:6px}
.cd-horse{font-weight:900;font-size:20px;color:#c8102e;line-height:1.2;margin:1px 0}
.cd-wk{font-size:11.5px;color:#3d372f;margin-top:2px;display:flex;flex-wrap:wrap;gap:1px 10px;
  font-variant-numeric:tabular-nums}
.wk-l{font-weight:700;color:#2b5fa8} .wk{white-space:nowrap} .wk-b{font-weight:700;color:#14110f}
.cd-meta{font-size:12.5px;color:#3d372f}
.src{display:inline-block;font-size:10.5px;font-weight:700;padding:0 5px;margin-left:7px;
  vertical-align:1.5px;border:1px solid}
.s-fix{color:#14110f;border-color:#14110f;background:#f0c040}
.s-nk{color:#2b5fa8;border-color:#2b5fa8;background:#eaf1fb}
.s-sp{color:#1e7a46;border-color:#1e7a46;background:#e6f3ea}
.s-bbs{color:#6b5316;border-color:#c3ae74;background:#f3ead4}
.rf{border-left:4px solid #c3ae74;background:#faf5e8;padding:5px 11px;margin-bottom:6px}
.rf-2{font-size:12.5px;color:#5a5045;margin-top:1px}
.lw{border-left:4px solid #c8102e;background:#f8f2e6;padding:4px 11px;margin-bottom:4px;break-inside:avoid}
.lw-1{display:flex;align-items:baseline} .lw-h{font-weight:700;font-size:16px}
.lw-ch{margin-left:auto;font-weight:900;font-size:17px} .lw-pt{margin-left:10px;font-weight:700;color:#1e7a46}
.lw-2{font-size:13px;color:#5a5045}
table{width:100%;border-collapse:collapse}
.rkt td{padding:5px 8px;border-bottom:1px solid #ddd3c1;font-size:15px}
.rkt .rk{width:26px;text-align:center;font-weight:900;font-size:18px}
.rkt .ow2{font-weight:700;font-size:16px;color:#14110f}
.rkt .pt{text-align:right;font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}
.rkt .rec{text-align:center;color:#5a5045;font-size:13px;white-space:nowrap}
.rkt .tp{color:#5a5045;font-size:13px;text-align:left;padding-left:18px}
.rkt tr.lead td{background:#f6ecd8} .rkt tr.lead .ow2{color:#c8102e}
.rk-foot{display:flex;justify-content:space-between;padding:6px 8px;background:#14110f;color:#fffdf6;font-size:14px}
.tp-row{display:flex;padding:7px 11px;border-bottom:1px dotted #c9bfae;font-size:13.5px;line-height:1.5}
.tp-row:last-child{border-bottom:none}
.tp-l{width:76px;flex-shrink:0} .tp-c{flex:1;padding-left:11px;min-width:0}
.tp-lb{display:block;text-align:center;font-size:12px;font-weight:700;padding:1px 4px;color:#fff}
.chip{display:block;text-align:center;font-size:10.5px;color:#6b5f52;margin-top:2px}
.lb-rest{background:#2b5fa8} .lb-back{background:#1e7a46} .lb-alert{background:#c8102e} .lb-note{background:#8a6d1f}
.og{display:flex;padding:2px 0;border-bottom:1px dotted #e0d7c6} .og:last-child{border-bottom:none}
.og-o{display:inline-block;width:58px;flex-shrink:0;font-weight:700;background:#efe9dc;
  text-align:center;font-size:12px;margin-right:9px;height:18px}
.og-n{margin-left:auto;padding-left:8px;color:#8d8375;font-size:11.5px;white-space:nowrap}
.og-b{flex:1;min-width:0} .og-t{display:block;font-size:12.5px;color:#5a5045}
.ig-f{font-size:11.5px;color:#8d8375;border-top:1px dotted #c9bfae;padding-top:3px;margin-top:2px}
.rt-r{padding:6px 0 7px;border-bottom:1px solid #e0d7c6;break-inside:avoid}
.rt-1{display:flex;align-items:baseline}
.rt-d{display:inline-block;min-width:78px;font-weight:700;font-size:13px;background:#efe9dc;
  padding:1px 6px;margin-right:9px;text-align:center}
.rt-h{font-weight:700;font-size:17px} .rt-2{font-size:14px;margin:1px 0 0 87px}
.rt-3{font-size:12.5px;color:#5a5045;margin-left:87px}
.pk{display:flex;border-bottom:1px solid #e0d7c6;padding:6px 0;break-inside:avoid}
.pk-c{width:62px;flex-shrink:0}
.pk-c span{display:block;text-align:center;font-size:11.5px;font-weight:700;color:#fff;padding:2px 0}
.pk-b{flex:1;padding-left:11px;min-width:0} .pk-h{font-weight:700;font-size:17px}
.pk-t{font-size:13px;color:#2f2a24;margin-top:1px}
.disc{background:#f1ead9;border-left:4px solid #8d8375;padding:6px 11px;font-size:12px;
  color:#5a5045;margin-bottom:9px;line-height:1.5}
.os{margin-bottom:11px}
.os-h{background:#14110f;color:#fffdf6;padding:3px 11px;display:flex;align-items:baseline;gap:8px}
.os-rank{font-weight:900;font-size:20px;color:#f0c040}
.os-nm{font-weight:700;font-size:21px;letter-spacing:.1em}
.os-pt{margin-left:auto;font-weight:700;font-size:18px} .os-pt .u{font-size:12px}
.os-rec{font-size:12px;opacity:.85} .os-n{font-size:12px;opacity:.7}
.hr{padding:3px 11px 4px;border-bottom:1px solid #e0d7c6;break-inside:avoid}
.hr:nth-child(even){background:#faf6ec}
.hr-1{display:flex;align-items:baseline} .hr-nm{font-weight:700;font-size:17px}
.hr-rec{margin-left:auto;font-size:13px;color:#5a5045;white-space:nowrap}
.hr-pz{margin-left:8px;color:#8d8375} .hr-2{font-size:12px;color:#5a5045;margin-top:1px}
.sex-m{color:#2b5fa8;font-weight:700;margin-right:5px} .sex-f{color:#c8102e;font-weight:700;margin-right:5px}
.x{color:#b5ab99} .ln{font-size:12.2px;margin-top:1px} .ln.last{color:#2f2a24}
.ln.nxt{color:#c8102e;font-weight:700} .ln.kin{color:#2b5fa8} .ln.bbs{color:#5a4a2f}
.bl{display:inline-block;background:#e8dfc8;color:#6b5316;font-size:10.5px;font-weight:700;
  padding:0 5px;margin-right:6px}
.bdg{display:inline-block;font-size:11px;padding:1px 5px;margin-left:7px;color:#fff}
.b-run{background:#c8102e} .b-rest{background:#2b5fa8} .b-back{background:#1e7a46}
.foot{margin-top:10px;border-top:1.5px solid #14110f;padding-top:5px;font-size:11px;
  color:#6b5f52;display:flex;justify-content:space-between}
"""


def _src(s: str) -> str:
    if s not in SRC_LABEL:
        return ""
    t, c = SRC_LABEL[s]
    return f'<span class="src {c}">{t}</span>'


def _foot(title: str, n: int, note: str) -> str:
    return f'<div class="foot"><span>{ESC(note)}</span><span>{ESC(title)} — {n}</span></div>'


# 改ページしない区切り（1面）の注記
NOTE_CSS = ".note{font-size:11px;color:#6b5f52;margin:6px 0 4px}"


def _chaku_cls(chaku: int | None) -> str:
    if chaku == 1:
        return "r1"
    if chaku is not None and chaku <= 3:
        return "r2"
    return "r9"


def _entry_card(e: EntryRow) -> str:
    return (
        f'<div class="cd"><div class="cd-l"><div class="cd-d">{ESC(md_label(e.day))}</div>'
        f'<div class="cd-t">{ESC(e.time or "—")}</div></div><div class="cd-r">'
        f'<div class="cd-race">{ESC(e.place)}　<b>{ESC(e.race)}</b>'
        f'<span class="cd-cond">{ESC(e.cond)}</span>{_src(e.src)}</div>'
        f'<div class="cd-horse">{ESC(e.horse)}<small class="ow">［{ESC(e.owner)}］</small></div>'
        f'<div class="cd-meta">鞍上 <b>{ESC(e.jockey or "未定")}</b></div>'
        f"{_workouts(e.workouts)}</div></div>"
    )


def _workouts(ws: tuple[Workout, ...]) -> str:
    """追い切りの行。4F が最速の 1 本を太字にする。"""
    if not ws:
        return '<div class="cd-wk"><span class="wk-l">調教</span><span class="muted">直近の時計なし</span></div>'
    best = fastest(ws)
    rows = "".join(f'<span class="wk{" wk-b" if w is best else ""}">{ESC(w.label())}</span>' for w in ws)
    return f'<div class="cd-wk"><span class="wk-l">調教</span>{rows}</div>'


def _result_row(r: ResultRow) -> str:
    chaku = f"{r.chaku}着" if r.chaku else "—"
    prize = f'<span class="lw-pt">+{r.prize:,}万</span>' if r.prize else ""
    ninki = f"　{r.ninki}人気" if r.ninki else ""
    return (
        f'<div class="lw"><div class="lw-1"><span class="lw-h">{ESC(r.horse)}</span>'
        f'<span class="ow">［{ESC(r.owner)}］</span>'
        f'<span class="lw-ch {_chaku_cls(r.chaku)}">{ESC(chaku)}</span>{prize}</div>'
        f'<div class="lw-2">{ESC(md_label(r.day))}　{ESC(r.place)}　{ESC(r.cond)}'
        f'<span class="sep">｜</span>{ESC(r.jockey)}{ESC(ninki)}</div></div>'
    )


def _owner_group(owner: str, body: str, tail: str = "") -> str:
    return f'<div class="og"><span class="og-o">{ESC(owner)}</span><div class="og-b">{body}</div>{tail}</div>'


def _topics(rep: Report) -> str:
    """帰厩・放牧・故障（掲示板）を馬主ごとに。"""
    rows = ""
    for state, label, cls in (("帰厩", "帰厩", "lb-back"), ("放牧", "放牧中", "lb-rest")):
        groups = []
        total = 0
        for _, owner, hs in rep.owners_in_rank_order():
            # 今週走る馬は近況欄に出さない（出走予定の欄に居る）
            picked = [h for h in hs if h.status.state == state and not h.entered]
            if not picked:
                continue
            total += len(picked)
            body = "、".join(
                f'<span class="hn">{ESC(h.name)}</span><span class="muted">（{ESC(h.status.note)}）</span>'
                for h in picked
            )
            groups.append(_owner_group(owner, body, f'<span class="og-n">{len(picked)}</span>'))
        if groups:
            rows += (
                f'<div class="tp-row"><div class="tp-l"><span class="tp-lb {cls}">{label}</span>'
                f'<span class="chip">{total}頭</span></div>'
                f'<div class="tp-c">{"".join(groups)}</div></div>'
            )
    inj = rep.injuries()
    inj_body = (
        "".join(
            _owner_group(
                h.owner,
                f'<span class="hn">{ESC(h.name)}</span><span class="og-t">{ESC(h.bbs.text if h.bbs else "")}</span>',
            )
            for h in inj
        )
        or '<span class="muted">報告はありません。</span>'
    )
    rows += (
        '<div class="tp-row"><div class="tp-l"><span class="tp-lb lb-alert">故障の話題</span>'
        '<span class="chip">掲示板</span></div><div class="tp-c">'
        + inj_body
        + '<div class="ig-f">※ 掲示板で故障に触れた投稿です。<b>別の馬の話も含みます</b>。いずれも未確認情報です。</div></div></div>'
    )
    return rows


def _rotation(rep: Report) -> str:
    """翌週以降の予定（公式の登録・netkeiba 想定・掲示板の次走/鞍上の話）。"""
    out = []
    for h in rep.horses:
        if h.entered:
            continue
        if h.status.entry:
            out.append((0, h, "sp", h.status.entry, ""))
        elif h.status.plan:
            out.append((1, h, "nk", h.status.plan, ""))
        elif h.bbs and h.bbs.label in ("出走", "次走", "鞍上"):
            out.append((2, h, "bbs", "", h.bbs.text))
    out.sort(key=lambda x: (x[0], x[3]))
    rows = []
    for _, h, src, race, text in out[:14]:
        # race は「2026/11/23(月) 東京スポーツ杯２歳Ｓ（特別登録）」の形
        when, _, name = race.partition(" ")
        when = when[5:] if when[:4].isdigit() else when  # 年を落とす
        if not when and h.bbs:
            when = h.bbs.when
        rows.append(
            f'<div class="rt-r"><div class="rt-1"><span class="rt-d">{ESC(when)}</span>'
            f'<span class="rt-h">{ESC(h.name)}</span><span class="ow">［{ESC(h.owner)}］</span>'
            f'{_src(src)}</div><div class="rt-2"><b>{ESC(name)}</b></div>'
            f'<div class="rt-3">{ESC(text)}</div></div>'
        )
    return "".join(rows)


def _pickups(rep: Report, per_label: int = 4) -> str:
    seen: dict[str, int] = {}
    out = []
    for label in ("故障", "出走", "鞍上", "次走", "調教", "デビュー"):
        for h in rep.horses:
            if not h.bbs or h.bbs.label != label or seen.get(label, 0) >= per_label:
                continue
            seen[label] = seen.get(label, 0) + 1
            out.append(
                f'<div class="pk"><div class="pk-c"><span style="background:#{PICK_COLOR[label]}">'
                f'{ESC(label)}</span></div><div class="pk-b">'
                f'<div class="pk-h">{ESC(h.name)}<span class="ow">［{ESC(h.owner)}］ {ESC(h.bbs.when)}</span></div>'
                f'<div class="pk-t">{ESC(h.bbs.text)}</div></div></div>'
            )
    return "".join(out)


def _horse_row(h: HorseInfo) -> str:
    sexcls = "sex-m" if h.sex in ("牡", "セ") else "sex-f"
    notes = ""
    if h.status.entry:
        notes += f'<div class="ln nxt">▶ {ESC(h.status.entry)}</div>'
    if h.status.plan:
        notes += f'<div class="ln nxt">▶ {ESC(h.status.plan)}（netkeiba想定）</div>'
    if h.status.note:
        notes += f'<div class="ln kin">● {ESC(h.status.note)}</div>'
    if h.bbs:
        notes += f'<div class="ln bbs"><span class="bl">掲示板 {h.bbs.count}</span>{ESC(h.bbs.text)}</div>'
    state = "出走" if h.entered else h.status.state
    bdg = f'<span class="bdg {STATE_BADGE[state]}">{state}</span>' if state in STATE_BADGE else ""
    prize = f"{h.prize:,}万" if h.prize else ""
    return (
        f'<div class="hr"><div class="hr-1"><span class="hr-nm">{ESC(h.name)}</span>{bdg}'
        f'<span class="hr-rec">{ESC(h.record)}<span class="hr-pz">{ESC(prize)}</span></span></div>'
        f'<div class="hr-2"><span class="{sexcls}">{ESC(h.sex)}</span>'
        f'{ESC(h.sire)} <span class="x">×</span> {ESC(h.broodmare)}'
        f'<span class="sep">｜</span>{ESC(h.stable)}</div>'
        f'<div class="ln last">{ESC(h.last or "未出走")}</div>{notes}</div>'
    )


def build_html(rep: Report) -> str:
    """紙面の HTML を作る。値はすべてエスケープする（掲示板の原文を含むため）。"""
    t = rep.title
    cards = "".join(_entry_card(e) for e in rep.this_week) or (
        '<div class="rf"><div class="rf-2">今週の出走予定はありません。</div></div>'
    )
    results = "".join(_result_row(r) for r in rep.last_week) or (
        '<div class="rf"><div class="rf-2">先週の出走はありません。</div></div>'
    )
    # 1面は改ページしない（"flow"）。出走予定の件数が週ごとに変わるので、
    # 収まらない分は順位表のページへそのまま流し込む
    pages = [
        f"""<div class="flow">
  <div class="mh"><div class="mh-top">
      <div class="mh-t serif">{ESC(t)}</div>
      <div class="mh-s"><div class="d">{ESC(rep.date_label)}</div>{ESC(rep.subtitle)}／登録{len(rep.horses)}頭<br>
        {rep.issued_at:%H:%M} 発行</div></div>
    <div class="mh-rule"></div>
    <div class="mh-kick">今週の焦点</div>
    <div class="mh-head serif">{ESC(rep.headline)}</div></div>
  <div class="sh">今 週 の 出 走 予 定<span class="rt">出馬表・特別登録・netkeiba想定</span></div>{cards}
  <div class="sh">先 週 の 結 果<span class="rt">直近の確定分</span></div>{results}
  <div class="note">出馬表＝確定／特別登録・netkeiba想定・掲示板＝未確定。調教は太字が直近で4Fの最速</div></div>"""
    ]

    rk = "".join(
        f'<tr class="{"lead" if s.rank == 1 else ""}"><td class="rk">{s.rank}</td>'
        f'<td class="ow2">{ESC(s.owner)}</td><td class="pt">{s.prize:,}万</td>'
        f'<td class="rec">{s.win}-{s.place}-{s.show}-{s.out}</td>'
        f'<td class="tp">{ESC(s.top_horse)}</td></tr>'
        for s in rep.standings
    )
    total = sum(s.prize for s in rep.standings)
    total_rec = "-".join(str(sum(getattr(s, k) for s in rep.standings)) for k in ("win", "place", "show", "out"))
    rotation = _rotation(rep) or '<div class="rt-3">該当する情報はありません。</div>'
    pages.append(f"""<div class="page">
  <div class="sh red">馬 主 別 ラ ン キ ン グ<span class="rt">{ESC(rep.date_label)} 現在</span></div>
  <table class="rkt"><tbody>{rk}</tbody></table>
  <div class="rk-foot"><span>全馬主 合計</span><span>{total:,}万　{ESC(total_rec)}</span></div>
  <div class="sh">近 況 ト ピ ッ ク ス<span class="rt">netkeiba 次走・近況情報 ＋ 掲示板</span></div>{_topics(rep)}
  <div class="sh">翌 週 以 降 の 予 定<span class="rt">特別登録・想定・掲示板／未確定</span></div>{rotation}
  {_foot(t, 1, "出典: galloplab.com ／ netkeiba.com")}</div>""")

    pk = _pickups(rep) or '<div class="pk-t">該当する投稿はありません。</div>'
    n_bbs = sum(1 for h in rep.horses if h.bbs)
    n_posts = sum(h.bbs.count for h in rep.horses if h.bbs)
    pages.append(f"""<div class="page">
  <div class="sh red">掲 示 板 ピ ッ ク ア ッ プ<span class="rt">直近{rep.bbs_days}日／{n_bbs}頭・{n_posts}件</span></div>
  <div class="disc">netkeiba の各馬掲示板の投稿を分類し、<b>原文のまま</b>抜粋しています。
    <b>いずれも一般ユーザーの投稿にもとづく未確認情報</b>で、主催者・厩舎の公式発表ではありません。</div>
  {pk}
  {_foot(t, 2, "掲示板情報はすべて未確認")}</div>""")

    owners = rep.owners_in_rank_order()
    for i, (s, owner, hs) in enumerate(owners):
        head_rank = str(s.rank) if s else "—"
        head_pt = f"{s.prize:,}" if s else "0"
        head_rec = f"{s.win}-{s.place}-{s.show}-{s.out}" if s else ""
        rows = "".join(_horse_row(h) for h in hs)
        pages.append(f"""<div class="page">
  <div class="sh">登 録 馬 一 覧<span class="rt">{i + 1} / {len(owners)}　馬主別・ランキング順</span></div>
  <div class="os"><div class="os-h"><span class="os-rank">{head_rank}</span>
      <span class="os-nm">{ESC(owner)}</span>
      <span class="os-pt">{head_pt}<span class="u">万</span></span>
      <span class="os-rec">{ESC(head_rec)}</span><span class="os-n">{len(hs)}頭</span></div>
    {rows}</div>
  {_foot(t, i + 3, "▶ 次走予定　● 近況　掲示板＝未確認のユーザー投稿")}</div>""")

    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        f"<title>{ESC(t)}</title><style>{CSS}{NOTE_CSS}</style></head>"
        f"<body>{''.join(pages)}</body></html>"
    )


def build_discord_content(rep: Report) -> str:
    """PDF に添える本文（Discord の上限 2000 字の手前で切る）。"""
    lines = [f"📰 **{rep.title}** ─ {rep.date_label}", "本紙（A4縦）を添付します。", ""]
    if rep.this_week:
        lines.append("**今週の出走予定**")
        for e in rep.this_week:
            src = {"nk": "（想定）", "sp": "（特別登録）"}.get(e.src, "")
            lines.append(
                f"・{md_label(e.day)} {e.time} {e.place} {e.race}{src} — "
                f"**{e.horse}**［{e.owner}］ 鞍上 {e.jockey or '未定'}"
            )
    else:
        lines.append("**今週の出走予定** … なし")
    # 🔴 故障の話題は本文に出さない（PDF にだけ原文ごと載せる）。
    #    2026-10-30 の全頭試走で 3 頭中 2 頭が**別の馬の**骨折・屈腱炎の話だった。
    #    キーワードではどの馬の話かを見分けられず、本文に名前を出すと誤報になる。
    text = "\n".join(lines)
    return text if len(text) <= 1900 else text[:1899] + "…"
