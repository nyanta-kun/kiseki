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
    assert '"hit": (sub_result.hit and sub_result.settled if sub_result' in src
    assert '"bet_amount": (sub_result.bet if sub_result' in src
    # 「まだ分からない」を画面へ渡す。これが無いと確定前が「✗」になる。
    assert '"settled":' in src
    # 当たり目はサーバーが返す（フロントで着順から組み立てると同着を落とす）。
    assert '"winning_combos": won' in src


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
