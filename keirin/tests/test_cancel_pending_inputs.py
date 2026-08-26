"""「入力待ち取消」は後の波で再判定される（2026-08-26・ユーザー判断）。

## なぜ必要か

`_already_submitted()` は `status='deleted'` を一律「その日は処理済み」として扱う
（2026-08-13）。人が中身を見て落とした商品が昼・夕の波で勝手に戻らないようにする
ためで、この規則自体は正しい。

しかし **並び予想・AI印が未公開なのは商品の良し悪しではなく、データが届いて
いないだけ**。意味は "not now" であって "not ever" ではない。実害（2026-08-26 熊本）:
朝7:10に入稿された看板穴埋め3件を「AI印未公開」で取り消したあと、16時前に印と
ラインが届いたのに**昼も夕も一度も再判定されなかった**。手で `--force` を打って
確かめると 5R は 7S が受理（しかも二軸が 4/1 → 2/4 に変わっていた）、6R は
平均払戻ゲートで見送り、4R はどのランクも該当せず、と**3件とも結論が違った**。

さらに、再判定でまた見送るときに取消理由が「AI印未公開」のままだと、
**取消の記録が実態を説明しない**（実際は平均払戻が安くて落ちている）。

## 何を固定するか

1. 語彙の正本（`backend/src/services/keirin_skip_reasons.py`）と画面
   （`frontend/src/app/keirin/cancelReasons.ts`）の文言が**1文字も違わない**こと
2. 再判定でまた見送ったら、取消理由が**その回の理由へ張り替わる**こと
3. 入稿し直せたら取消の痕跡（`cancel_reason` / `deleted_at`）が**消える**こと
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import scripts.netkeirin_submit_wt as m
from src.submission_skips import CANCEL_PENDING_INPUTS, cancel_is_pending_inputs

ROOT = Path(__file__).resolve().parent.parent
CANCEL_REASONS_TS = (ROOT.parent / "frontend" / "src" / "app" / "keirin"
                     / "cancelReasons.ts")


def test_画面と正本の文言が一致している():
    """🔴 画面が送る文言を Python が照合する。ずれると再判定が黙って起きなくなる。

    ⚠️ 失敗の向きは安全側（従来どおりブロック）なので、**実行時には気付けない**。
       ここで機械的に突き合わせるしかない。
    """
    assert CANCEL_REASONS_TS.exists(), f"画面側の定義が見つかりません: {CANCEL_REASONS_TS}"
    src = CANCEL_REASONS_TS.read_text(encoding="utf-8")
    found = re.search(r'pendingInputs:\s*"([^"]+)"', src)
    assert found, "cancelReasons.ts に pendingInputs がありません"
    assert found.group(1) == CANCEL_PENDING_INPUTS, (
        f"画面『{found.group(1)}』と正本『{CANCEL_PENDING_INPUTS}』が違います。"
        "再判定が起きなくなります（正本は backend/src/services/keirin_skip_reasons.py）")


def test_他の取消理由は再判定されない():
    """人が中身を見て落とした取消は従来どおり永久ブロック（2026-08-13 の規則）。"""
    for reason in ("手動取消", "強制取消", "場単位で取消", "全件取消",
                   "平均払戻が安い", None, ""):
        assert not cancel_is_pending_inputs(reason), reason
    assert cancel_is_pending_inputs(CANCEL_PENDING_INPUTS)


def test_見送り時に入力待ち取消のラベルを張り替える():
    """🔴 再判定でまた見送ったら、取消理由は**その回の理由**になること。

    古いラベルが残ると、取消の記録が実態を説明しない
    （実際は平均払戻で落ちているのに画面は「AI印未公開」のまま）。
    """
    calls: list[tuple[str, tuple]] = []

    class _Conn:
        def execute(self, sql, params=()):
            calls.append((" ".join(sql.split()), tuple(params)))

    m._relabel_pending_cancel(
        _Conn(), "20260826_87_06#7S", "7S",
        m.SKIP_GATE_MEAN_PAYOUT, "平均払戻 11,903円 <= 20,000円")

    assert len(calls) == 1
    sql, params = calls[0]
    assert sql.startswith("UPDATE netkeirin_submissions SET cancel_reason = ?")
    # 🔴 **入力待ち取消の行だけ**を狙うこと。status と理由の両方で絞る。
    assert "status = ?" in sql and "cancel_reason = ?" in sql
    new_reason, race_key, rank_key, status, old_reason = params
    assert new_reason == "再判定: 平均払戻 11,903円 <= 20,000円"
    # 🔴 ランク接尾辞を落とすこと（`netkeirin_submissions.race_key` は接尾辞なし）
    assert race_key == "20260826_87_06"
    assert rank_key == "7S"
    assert status == m.STATUS_DELETED
    assert old_reason == CANCEL_PENDING_INPUTS


def test_見送りの記録と張り替えが同じ場所で呼ばれる():
    """🔴 `_skip()` を通れば必ず張り替わること。

    別経路で `record_skip` だけ呼ぶ実装ができると、その理由のときだけ
    古いラベルが残る（`print` と記録を分けてはいけないのと同じ構造）。
    """
    src = inspect.getsource(m._skip)
    assert "record_skip(" in src
    assert "_relabel_pending_cancel(" in src, (
        "_skip が張り替えを呼んでいません。再判定で見送ったときに"
        "「入力待ち」のラベルが残ります")


def test_入稿し直したら取消の痕跡が消える():
    """🔴 `INSERT OR REPLACE` は**列を並べた分しか上書きしない**（PG の ON CONFLICT）。

    `cancel_reason` を並べ忘れると、取り消した行へ入稿し直しても理由が残り、
    生きている商品に取消の痕跡がぶら下がる。実際 2026-08-26 熊本5R で発生した。
    """
    src = inspect.getsource(m._record_submission)
    cols = [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "deleted_at" in n.value]
    assert cols, "record_submission の列並びを見つけられません"
    joined = " ".join(cols)
    for col in ("deleted_at", "cancel_reason"):
        assert col in joined, f"{col} を上書きしていません（取消の痕跡が残ります）"


def test_夕方波が7T1と7T3の候補も作り直す():
    """🔴 **再判定するなら買い目も作り直すこと**（2026-08-26）。

    7T1 / 7T3 は「朝1回で当日全開催ぶん」を作る設計で、夕方の再生成
    （`evening_picks_wt.sh`）に乗っていなかった。他ランクは `wave-picks-wt` が
    `_night` を作るのでこの穴が無い。

    実害（2026-08-26 熊本7R・7T1）: 朝は印ゼロ・ライン未取得で 1-2-4 の1点
    （当時の予測 17.5倍）が選ばれ、morning/noon は入力欠落ガードで見送り、
    18:00 に印が揃ってガードを通過した。ところが**買い目は朝のまま**で、
    現在の入力で引き直すと同じ目は 11.2倍＝想定払戻 111,654円。
    7T1 の目標（15万円）に届かない商品が出ていた。作り直すと軸ごと変わり
    1-7 の4点（61〜188倍）になった。

    ⚠️ 出力名に `_night` が要る。`_load_candidates` は evening の波でこの名前を
       先に探し、無ければ朝の生成物へ落ちる（＝綴りを間違えると**黙って**
       この節が防ぎたい状態に戻る）。
    """
    sh = (ROOT / "scripts" / "evening_picks_wt.sh").read_text(encoding="utf-8")
    for rank in ("7t1", "7t3"):
        assert f"scripts/build_{rank}_candidates.py" in sh, (
            f"夕方波が {rank.upper()} の候補を作り直していません")
        assert f"_night_s{rank}_candidates.json" in sh, (
            f"{rank.upper()} の出力名に _night がありません"
            "（朝の候補が使われ続けます）")
