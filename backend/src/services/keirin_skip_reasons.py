"""入稿を見送った／取り消した理由の語彙（**唯一の正本**）。

`keirin/scripts/netkeirin_submit_wt.py` が各ゲートで書き、Web の一覧が
バッジとして読む。書く側と読む側で語彙がずれると
**「理由不明」の見送りが静かに増える**ので、コードとラベルはここだけに置く。

🔴 **標準ライブラリ以外を import しないこと。** keirin は自分の venv
   （FastAPI も SQLAlchemy も無い）からこのファイルを直接読み込む
   （`keirin/src/submission_skips.py`）。依存を足すと
   **Web は無事なまま入稿だけが落ちる**（`keirin_marquee.py` と同じ制約）。

⚠️ **コードの文字列は変えない。** DB に保存済みの値なので、
   変えると過去分のバッジが「理由不明」に落ちる。ラベルは変えてよい。
"""

from __future__ import annotations

#: 平均払戻ゲート（`stake_allocation.MIN_MEAN_PAYOUT`）
GATE_MEAN_PAYOUT = "gate_mean_payout"
#: 1点でも安すぎる目がある（`stake_allocation.MIN_POINT_ODDS`）
GATE_POINT_ODDS = "gate_point_odds"
#: 想定払戻の下限（`stake_allocation.MIN_EXPECTED_PAYOUT_BY_RANK`）
GATE_EXPECTED_FLOOR = "gate_expected_floor"
#: 1レース1商品。別ランクが先に押さえた
RANK_CONFLICT = "rank_conflict"
#: 発走15分前を過ぎている
CLOSED = "closed"
#: 三連単の板／予測オッズが作れず、その開催の波まで見送った
DEFER_WAVE = "defer_wave"
#: 候補情報が不正（軸が取れない・買い目が組めない）
CANDIDATE_INVALID = "candidate_invalid"
#: netkeirin への送信そのものが失敗した
SUBMIT_FAILED = "submit_failed"
#: WT印・並びが未取得（`keirin/src/entry_health.py`）。指数も予測オッズも
#: 学習データにほぼ無い入力で動くことになるので、その回は見送って次の波へ回す
MISSING_LINEUP = "missing_lineup"

#: バッジに出す短いラベル。**8文字以内**（一覧の行に収める）
LABELS: dict[str, str] = {
    GATE_MEAN_PAYOUT: "平均払戻",
    GATE_POINT_ODDS: "安い目",
    GATE_EXPECTED_FLOOR: "払戻下限",
    RANK_CONFLICT: "他ランク",
    CLOSED: "締切超過",
    DEFER_WAVE: "次の波へ",
    CANDIDATE_INVALID: "候補不正",
    SUBMIT_FAILED: "入稿失敗",
    MISSING_LINEUP: "並び未取得",
}

#: バッジの `title`（マウスオーバー）に出す説明。`reason_text` が無いときの代わり
DESCRIPTIONS: dict[str, str] = {
    GATE_MEAN_PAYOUT: "買い目の想定平均払戻が下限に届かないため入稿しませんでした",
    GATE_POINT_ODDS: "元返しに近い安い目が含まれるため入稿しませんでした",
    GATE_EXPECTED_FLOOR: "想定払戻の下限に届かないため入稿しませんでした",
    RANK_CONFLICT: "同じレースを別のランクが先に入稿したため見送りました",
    CLOSED: "発走間際で入稿の締切を過ぎていました",
    DEFER_WAVE: "予測オッズを作れないため、その開催の回まで見送りました",
    CANDIDATE_INVALID: "候補の情報が不正で買い目を組めませんでした",
    SUBMIT_FAILED: "netkeirin への入稿が失敗しました",
    MISSING_LINEUP: "並び予想・AI印が未公開で、指数もオッズも当てにできないため見送りました",
}

#: すべてのコード（検査用）
ALL_CODES: frozenset[str] = frozenset(LABELS)


def label(code: str | None) -> str:
    """バッジの短いラベル。未知のコードでも**空にしない**。

    >>> label("gate_mean_payout")
    '平均払戻'
    >>> label("nope")
    '見送り'
    >>> label(None)
    '見送り'
    """
    if not code:
        return "見送り"
    return LABELS.get(code, "見送り")


def describe(code: str | None, detail: str | None = None) -> str:
    """`title` に出す説明。`detail`（記録時の実測値つき文言）を優先する。

    >>> describe("gate_mean_payout", "平均払戻 19,226円 <= 20,000円")
    '平均払戻 19,226円 <= 20,000円'
    >>> describe("closed")
    '発走間際で入稿の締切を過ぎていました'
    >>> describe(None)
    '入稿しなかった理由は記録されていません'
    """
    if detail:
        return detail
    if not code:
        return "入稿しなかった理由は記録されていません"
    return DESCRIPTIONS.get(code, "入稿しませんでした")
