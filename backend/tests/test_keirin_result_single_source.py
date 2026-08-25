"""一覧・確認画面・Discord が**同じ採点結果**を出すことの構造テスト（2026-08-25）。

## 背景（実際に起きた表示の食い違い）

2026-08-25 防府8R・7S は、Discord が「🎯 的中 15,200円」と報告している最中に
推奨一覧が「✗」、入稿確認が「… 未確定」を出していた。原因は3つ:

1. **一覧が `picks_history.hit` を出していた**。あれはランクの**候補**の成績で、
   売った商品ではない（防府8R は看板の穴埋めで軸を組み替えて入稿しており、
   候補は軸 7・2、売ったのは軸 1・7）。実測 2026-08-07〜25 の売った295商品のうち
   **53件（18%）**で的中の表示が食い違っていた
2. **確認画面が「発走+90分 / status=3」で採点対象を絞っていた**。どちらも
   「着順と配当が DB に入った」ことを意味せず、確定が早いレースをただ待たせていた
3. **採点の実装が3つに分かれていた**（同着・端数・券種の決め方・配当が引けない
   ときの扱いが全部違った）

ここは DB を用意せず、**その3つが元に戻っていないこと**だけを構造で見る。
挙動そのものは `test_keirin_submitted_pick_result.py` が固定している。
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from src.api import keirin_router


def test_picks_は入稿があるならそれを採点する():
    """🔴 `submission_only`（picks_history に行が無い入稿）だけを採点対象に
    戻してはいけない。それが 53件の食い違いの正体だった。"""
    src = inspect.getsource(keirin_router.get_picks)
    m = re.search(r"sub_result = \(([\s\S]*?)else None\)", src)
    assert m, "sub_result の組み立てを見つけられなかった（書き方を変えたなら本テストも追随）"
    expr = m.group(1)
    assert "submitted_bet" in expr, "入稿の原本から採点していない"
    assert "submission_only" not in expr, \
        "入稿があるレースを『picks_history に行が無いとき』だけ採点対象にしている"
    assert "submission_cancelled" in expr, "取消した入稿を商品として採点している"


def test_picks_の的中と投資は入稿の採点から出す():
    src = inspect.getsource(keirin_router.get_picks)
    assert "sub_result.hit and sub_result.settled" in src
    assert "sub_result.bet if sub_result else 0" in src
    # 「まだ分からない」を画面へ渡す。これが無いと確定前が「✗」になる。
    assert '"settled":' in src
    # 当たり目はサーバーが返す（フロントで着順から組み立てると同着を落とす）。
    assert '"winning_combos": won' in src


def _picks_response_exprs() -> dict[str, str]:
    """`get_picks` が組み立てるレスポンス dict を {キー: 値の式} で返す。

    文字列 grep だと `paper_hit` のような**別キー**まで巻き込んでしまうので、
    キーごとに値の式を取り出して見る。
    """
    tree = ast.parse(inspect.getsource(keirin_router.get_picks))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "race_key"
                    for k in arg.keys):
                return {k.value: ast.unparse(v)
                        for k, v in zip(arg.keys, arg.values)
                        if isinstance(k, ast.Constant)}
    raise AssertionError("レスポンスの組み立てを見つけられませんでした")


def test_売っていない行はpicks_historyの成績へフォールバックしない():
    """🔴 **見送ったレースを「購入・的中」として出さない**（2026-08-25）。

    2026-08-25 松阪7R(7S) は平均払戻ゲート（想定平均 19,226円 <= 20,000円）で
    入稿していないのに、一覧と Discord が `picks_history` 由来で
    「購入・的中 42,400円」を出していた。8月は毎日 26〜49件がこの状態だった。

    ⚠️ `paper_*`（モデルの候補としての結果）は**別キー**なので対象外。
       混ざるのを防ぐのが目的で、候補の結果を返すこと自体は禁じていない。
    """
    exprs = _picks_response_exprs()
    for key in ("hit", "payout", "bet_amount"):
        expr = exprs[key]
        assert "sub_result" in expr, f'"{key}" が売った商品の採点から出ていない'
        assert "r[" not in expr, (
            f'"{key}" が picks_history へフォールバックしている（{expr}）。'
            " 候補の名目値は売上にも収支にも対応しない。"
        )
    # 売ったかどうかを画面へ渡す。フロントの購入判定はこれだけを見る。
    assert exprs["sold"] == "sold"
    src = inspect.getsource(keirin_router.get_picks)
    assert "sold = bool(submitted_bet) and not submission_cancelled" in src


def test_モデルの候補としての結果は別キーで返る():
    """🟢 入稿・Discord は売った商品に揃えたが、**Web ではモデルを追えること**。

    見送ったレースが当たっていたかどうかが分からないと、ゲートの是非を
    後から検証できない（2026-08-25 ユーザー要望）。
    🔴 ただし売った成績と**同じキーに入れない**。混ぜた瞬間に元の食い違いへ戻る。
    """
    exprs = _picks_response_exprs()
    for key in ("paper_hit", "paper_payout", "paper_bet"):
        assert key in exprs, f"{key} を返していない"
        assert "sub_result" not in exprs[key], \
            f"{key} が売った商品の採点から出ている（候補の成績ではない）"
        assert "r[" in exprs[key], f"{key} が picks_history から出ていない"


def test_picks_は見送った理由を返す():
    """理由が無いと、売らなかったことは分かっても「なぜ」が画面から消える。"""
    src = inspect.getsource(keirin_router.get_picks)
    assert '"skip_reason"' in src and '"skip_reason_text"' in src
    assert "submission_skips" in src, "見送りの記録テーブルを引いていない"
    assert '"cancel_reason"' in src


def test_stats_の既定は売った商品():
    """🔴 一覧・Discord・統計で既定の母集団が違うと、同じ日の数字が3種類できる。"""
    sig = inspect.signature(keirin_router.get_stats)
    assert sig.parameters["source"].default == "sold", \
        "統計の既定が「売った商品」でなくなっている"
    src = inspect.getsource(keirin_router.get_stats)
    assert "_fetch_settled_submissions" in src
    assert "keirin.picks_history" not in src, \
        "統計本体が picks_history を直接数えている（候補は _fetch_paper_picks 経由）"


def test_stats_はモデルの母集団も選べる():
    """🟢 ゲートの是非を後から追うために、候補側も見られること（2026-08-25）。

    🔴 ただし**足したり混ぜたりしない**。金額の意味が違う（候補の賭け金は
       「1万円賭けたことにしたら」という名目値）ので、切り替えであって合算ではない。
    """
    src = inspect.getsource(keirin_router.get_stats)
    assert '_fetch_paper_picks' in src
    assert 'source == "paper"' in src
    # 画面がどちらを見ているか出せるように返す（ラベルが無いと必ず誤読される）
    assert '"source":' in src
    paper = inspect.getsource(keirin_router._fetch_paper_picks)
    # 候補側も「見送り」と未確定は数えない（買っていない・結果が無い）
    assert "miwokuri" in paper
    assert "wr.status = 3" in paper


def test_確定成績は発走からの経過時間で足切りしない():
    """🔴 `status=3` / 「発走+90分」で絞ると、確定が早いレースが 90分間ただ
    「未確定」になる。採点できるかは `settle()` の `settled` が決める。"""
    src = inspect.getsource(keirin_router._fetch_settled_submissions)
    assert "5400" not in src, "発走+90分の足切りが復活している"
    assert "wr.status = 3" not in src, "status による足切りが復活している"
    assert "if not res.settled" in src, "未採点の行を集計へ流している"


def test_採点は正本ひとつだけ():
    """🔴 router 側に採点規則を書き戻さない（3実装に割れた原因）。"""
    src = Path(keirin_router.__file__).read_text()
    assert "from ..services.keirin_settlement import" in src
    # 当たり目の突き合わせは正本の中だけで行う。
    assert "win_trio" not in src and "win_trifecta" not in src


def test_discord_も同じ正本を使う():
    """keirin 側（別 venv・`src` パッケージ名が衝突するので import できない）は
    ファイル指定で正本を読み込む。看板判定と同じ形。"""
    keirin_src = Path(__file__).resolve().parents[2] / "keirin" / "src" / "sold_performance.py"
    if not keirin_src.exists():                       # pragma: no cover
        return                                        # keirin を含まない配備では検査しない
    body = keirin_src.read_text()
    assert "keirin_settlement.py" in body, "Discord 側が正本を参照していない"
    assert "def settle(" not in body, "keirin 側に採点の実装を持ってはいけない"


def test_discord_は着順を車番へ畳まずに採点する():
    """🔴 `order3[:3]` のように車番だけへ畳むと同着で当たり目を取り違える。"""
    notify = (Path(__file__).resolve().parents[2] / "keirin" / "scripts"
              / "notify_race_result_wt.py")
    if not notify.exists():                           # pragma: no cover
        return
    body = notify.read_text()
    assert "settle_submission(detail, finishers, payouts)" in body
    assert "settle_submission(detail, order3" not in body
