"""出走表の選手コメントと「調子が良い」の判定（services/keirin_rider_comments.py）を固定する。"""

from src.services.keirin_rider_comments import comments_for_entry, post_window_start

PRE = {
    "kind": "pre",
    "race_key": "20261001_63_01",
    "cup_id": "C",
    "race_date": "2026-10-01",
    "body": "仕上がりは良い",
    "src_updated_at": 1000,
    "condition": 1,
}
PREV = {
    "kind": "post",
    "race_key": "20261001_63_05",
    "cup_id": "C",
    "race_date": "2026-10-01",
    "body": "脚が重かった",
    "src_updated_at": 2000,
    "condition": -1,
}
OWN = {
    "kind": "post",
    "race_key": "20261002_63_03",
    "cup_id": "C",
    "race_date": "2026-10-02",
    "body": "良い感じで抜けた",
    "src_updated_at": 9000,
    "condition": 2,
}


def test_latest_comment_before_start_decides_color():
    shown, good = comments_for_entry([PRE, PREV, OWN], race_key="20261002_63_03", cup_id="C", race_start=5000)
    # 直近（発走前）は前走後の「脚が重かった」→ 青にしない
    assert good is False
    assert [c["label"] for c in shown] == ["前検日", "前走後", "このレース後"]


def test_own_post_race_comment_never_makes_it_blue():
    """このレースのレース後コメントは発走後に出るので、青の判定に使わない（後知恵になる）。"""
    _, good = comments_for_entry([OWN], race_key="20261002_63_03", cup_id="C", race_start=5000)
    assert good is False


def test_pre_meet_good_condition_is_blue():
    _, good = comments_for_entry([PRE], race_key="20261001_63_01", cup_id="C", race_start=5000)
    assert good is True


def test_comment_published_after_start_is_ignored():
    """前走後のコメントでも、公開がこのレースの発走より後なら判定に使わない。"""
    late = {**PREV, "src_updated_at": 6000}
    shown, good = comments_for_entry([PRE, late], race_key="20261002_63_03", cup_id="C", race_start=5000)
    assert good is True  # 判定は前検日の「良い」
    assert [c["label"] for c in shown] == ["前検日"]


def test_pre_meet_comment_of_other_cup_is_not_used():
    other = {**PRE, "cup_id": "OTHER"}
    shown, good = comments_for_entry([other], race_key="20261002_63_03", cup_id="C", race_start=5000)
    assert shown == [] and good is False


def test_untagged_comment_is_not_blue():
    _, good = comments_for_entry([{**PRE, "condition": None}], race_key="r", cup_id="C", race_start=5000)
    assert good is False


def test_post_window_start():
    assert post_window_start(["2026-10-02", "2026-10-01"]) == "2026-09-01"


def test_order_is_by_event_not_by_page_update_time():
    """前検日ページの updatedAt が後から更新されても、開催前日の談話を「最新」にしない（2026-10-01 修正）。"""
    pre_late = {**PRE, "src_updated_at": 3000}  # 前走後（2000）より後に更新された
    _, good = comments_for_entry([pre_late, PREV], race_key="20261002_63_03", cup_id="C", race_start=5000)
    assert good is False  # 最新は前走後の「脚が重かった」（-1）


def test_neutral_latest_comment_is_not_blue():
    """直近のコメントが調子に触れていなければ青にしない（古い「良い」を持ち越さない）。"""
    neutral = {**PREV, "condition": 0, "body": "作戦通りでした"}
    _, good = comments_for_entry([PRE, neutral], race_key="20261002_63_03", cup_id="C", race_start=5000)
    assert good is False


def test_good_comment_three_or_more_days_old_is_not_blue():
    """レース日の3日前以上の「調子が良い」は青にしない（2026-10-01 ユーザー指摘）。"""
    old = {**PRE, "race_date": "2026-09-28"}  # 20261001 の3日前
    _, good = comments_for_entry([old], race_key="20261001_63_03", cup_id="C", race_start=None)
    assert good is False
    recent = {**PRE, "race_date": "2026-09-29"}  # 2日前はまだ使う
    _, good = comments_for_entry([recent], race_key="20261001_63_03", cup_id="C", race_start=None)
    assert good is True


def test_post_comment_carries_finish_order():
    prev = {**PREV, "finish_order": 2}
    shown, _ = comments_for_entry([PRE, prev], race_key="20261002_63_03", cup_id="C", race_start=5000)
    assert [(c["label"], c["finish_order"]) for c in shown] == [("前検日", None), ("前走後", 2)]
