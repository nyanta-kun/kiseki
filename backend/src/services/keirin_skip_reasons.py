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
#: 日次上限（`keirin_type_lab_gate.DAILY_CAP`）に達したので、その日はもう出さない。
#: 🔴 **これは「悪いから落とした」ではない**。上限に当たっただけで、同じ商品が
#:    別の日なら出る。バッジの文面もそう読めるようにしてある
DAILY_CAP = "daily_cap"

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
    DAILY_CAP: "日次上限",
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
    DAILY_CAP: "その日の上限件数に達したため見送りました（商品が悪いわけではありません）",
}

#: すべてのコード（検査用）
ALL_CODES: frozenset[str] = frozenset(LABELS)

# ---------------------------------------------------------------------------
# 取消理由のうち「保留」を意味するもの（2026-08-26・ユーザー判断）
# ---------------------------------------------------------------------------
# 🔴 **入力が揃っていないことを理由にした取消は「却下」ではなく「保留」**。
#    並び予想・AI印が未公開のあいだは指数も予測オッズも当てにできないので
#    その回は落とすが、**入力が届いたら判定し直すべき**であって、
#    その日ずっと売らないと決めたわけではない。
#
#    `netkeirin_submit_wt._already_submitted()` は `status='deleted'` を
#    一律「その日は処理済み」として扱う（2026-08-13・人が中身を見て落とした
#    ものが勝手に戻らないようにするため）。この文言の取消**だけ**をそこから
#    外すことで、「なぜ消したか」で再判定の可否が決まるようにする。
#    ⚠️ **「誰が消したか」で分けてはいけない。** 人が押した取消でも、理由が
#       「入力待ち」なら意味は "not now" であって "not ever" ではない。
#
# 🔴 **看板穴埋め（`submit_marquee_wt.py`）は従来どおり全ての取消でブロックする**
#    （2026-08-26・ユーザー判断）。再判定でどのランクも取らなかった看板レースは
#    取消のままにする。両者の重複判定は
#    `tests/test_cancel_force_and_marquee_dedup.py` が突き合わせている。
#
# ⚠️ **この文字列は `frontend/src/app/keirin/cancelReasons.ts` と1文字も違えないこと。**
#    画面が送る文言をここで照合するので、ずれると再判定が黙って起きなくなる
#    （失敗の向きは安全側＝従来どおりブロック）。
#    `keirin/tests/test_cancel_pending_inputs.py` が両者の一致を見ている。
CANCEL_PENDING_INPUTS = "入力待ちのため取消（後の波で再判定）"


def cancel_is_pending_inputs(reason: str | None) -> bool:
    """その取消理由が「入力待ち＝後の波で再判定してよい」ものか。

    >>> cancel_is_pending_inputs("入力待ちのため取消（後の波で再判定）")
    True
    >>> cancel_is_pending_inputs("手動取消")
    False
    >>> cancel_is_pending_inputs(None)
    False
    """
    return reason == CANCEL_PENDING_INPUTS


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
