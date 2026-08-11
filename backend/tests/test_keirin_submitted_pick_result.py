"""手動・穴埋め入稿レースの買い目・的中・払戻の組み立て検査（2026-08-11）。

## 背景

ランクのゲートを通っていない入稿（手動・看板の穴埋め）は `picks_history` に
行が立たないため採点バッチも走らない。実際に商品として売っているのに Web の
一覧にも成績にも現れなかった（実測 135レース・売上シェアの約7割）。

そこで **入稿の原本（`netkeirin_submissions.bet_detail`）と確定結果**から
直に組み立てる。ここが壊れても例外は出ず、「不的中」と表示されるだけなので
気づけない。だから検査で固定する。

⚠️ **最大の罠は券種ごとの区切り文字**。三連複は `1=2=4`、三連単は `1-2-4`。
   取り違えると **当たっているのに不的中**になる。
"""
from __future__ import annotations

from src.api.keirin_router import _finish_top3_frames, _submitted_pick_result


def _bet(*lines):
    return {"total": sum(x[1] for x in lines),
            "lines": [{"bet_type": "3連複", "combo": c, "stake": s, "odds": None}
                      for c, s in lines]}


def _entries(order: dict[int, int]):
    """{車番: 着順} から wt_entries 相当の行を作る。"""
    return [{"frame_no": f, "finish_order": order.get(f)} for f in range(1, 8)]


# ---------------------------------------------------------------------------
# 着順の取り出し
# ---------------------------------------------------------------------------

def test_確定していれば着順どおりに返す():
    assert _finish_top3_frames(_entries({5: 1, 2: 2, 7: 3})) == [5, 2, 7]


def test_未確定ならNoneを返す():
    assert _finish_top3_frames(_entries({})) is None


def test_3着まで揃っていなければNone():
    """1〜2着しか入っていない中途半端な状態で「不的中」と出さないため。"""
    assert _finish_top3_frames(_entries({5: 1, 2: 2})) is None


# ---------------------------------------------------------------------------
# 買い目・投資
# ---------------------------------------------------------------------------

def test_買い目と投資額は入稿の原本から取る():
    """⚠️ 再構成しない。傾斜配分は入稿時点の想定オッズで決まり後から再現できない。"""
    out = _submitted_pick_result(_bet(("1=2=4", 4900), ("1=2=3", 3300)), None, 0, 0)
    assert out["pred_combo"] == "1=2=4 1=2=3"
    assert out["n_combos"] == 2
    assert out["bet_amount"] == 8200


def test_入稿記録が無ければ空で返す():
    out = _submitted_pick_result(None, [1, 2, 4], 1000, 0)
    assert out == {"pred_combo": None, "n_combos": None, "bet_amount": 0,
                   "hit": False, "payout": 0}


# ---------------------------------------------------------------------------
# 的中・払戻
# ---------------------------------------------------------------------------

def test_三連複の的中は車番を昇順に並べて突き合わせる():
    """🔴 結果が 4→1→2 でも買い目 `1=2=4` は的中。着順で並べたまま比べると外れる。"""
    out = _submitted_pick_result(_bet(("1=2=4", 5000)), [4, 1, 2], trio_pay=560, trifecta_pay=0)
    assert out["hit"] is True
    assert out["payout"] == 560 * 5000 // 100  # 100円あたり560円


def test_三連単は着順まで一致して初めて的中():
    bet = {"total": 1000,
           "lines": [{"bet_type": "3連単", "combo": "1-2-4", "stake": 1000, "odds": None}]}
    assert _submitted_pick_result(bet, [1, 2, 4], 0, 3200)["hit"] is True
    # 同じ3車でも着順が違えば不的中
    assert _submitted_pick_result(bet, [4, 2, 1], 0, 3200)["hit"] is False


def test_区切り文字を取り違えない():
    """三連複の買い目を三連単の区切りで持っていると突き合わせに失敗する。
    実データは 3連複=`1=2=4` / 3連単=`1-2-4`。"""
    wrong = {"total": 1000,
             "lines": [{"bet_type": "3連複", "combo": "1-2-4", "stake": 1000, "odds": None}]}
    # 三連複として買ったのに区切りが `-` だと三連単扱いになり、着順が違えば外れる
    assert _submitted_pick_result(wrong, [4, 1, 2], 5600, 0)["hit"] is False


def test_複数点が当たったら合算する():
    """フォーメーションで同じ結果に2点掛かることがある。"""
    bet = {"total": 2000, "lines": [
        {"bet_type": "3連複", "combo": "1=2=4", "stake": 1000, "odds": None},
        {"bet_type": "3連単", "combo": "1-2-4", "stake": 1000, "odds": None},
    ]}
    out = _submitted_pick_result(bet, [1, 2, 4], trio_pay=560, trifecta_pay=3200)
    assert out["payout"] == 5600 + 32000
    assert out["hit"] is True


def test_外れは払戻ゼロ():
    out = _submitted_pick_result(_bet(("1=2=4", 5000)), [3, 5, 6], 560, 0)
    assert out["hit"] is False
    assert out["payout"] == 0


def test_未確定レースは的中判定しない():
    """発走前に「不的中」と出さない。"""
    out = _submitted_pick_result(_bet(("1=2=4", 5000)), None, 0, 0)
    assert out["hit"] is False
    assert out["payout"] == 0
    # 買い目と投資額は発走前から出す
    assert out["bet_amount"] == 5000


def test_払戻オッズが取れていなければ的中にしない():
    """`trio_pay=0` は「まだオッズが引けていない」状態。
    ここで hit=True にすると**払戻0円の的中**という嘘の行が出る。"""
    out = _submitted_pick_result(_bet(("1=2=4", 5000)), [1, 2, 4], trio_pay=0, trifecta_pay=0)
    assert out["hit"] is False
    assert out["payout"] == 0
