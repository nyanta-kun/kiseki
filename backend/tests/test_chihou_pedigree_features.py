"""地方 血統特徴の point-in-time 性を固定するテスト。

守りたいのは 1 点だけ:

🔴 **同じ日の結果を特徴に混ぜないこと。**
地方は同一開催日に同じ種牡馬の産駒が複数走る。同日を含めると
「その日の結果を見てその日を予測する」ことになり、A/B の改善が丸ごと幻になる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.chihou_pedigree_features import (
    PEDIGREE_FEATURES,
    SHRINK_K,
    _cum_by,
    add_pedigree_features,
)


def _hist(rows: list[tuple[str, str, int]]) -> pd.DataFrame:
    """(date, sire, top3) から履歴表を作る。"""
    df = pd.DataFrame(rows, columns=["date", "sire", "top3"])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df


class TestCumBy:
    def test_同じ日の複数走はまとめて1行になる(self) -> None:
        g = _cum_by(_hist([("20260101", "A", 1), ("20260101", "A", 0)]), ["sire"])
        assert len(g) == 1
        assert g.iloc[0]["cum_n"] == 2
        assert g.iloc[0]["cum_s"] == 1

    def test_日付をまたいで累積する(self) -> None:
        g = _cum_by(
            _hist([("20260101", "A", 1), ("20260102", "A", 1), ("20260103", "A", 0)]),
            ["sire"],
        ).sort_values("date")
        assert list(g["cum_n"]) == [1, 2, 3]
        assert list(g["cum_s"]) == [1, 2, 2]

    def test_種牡馬ごとに独立して累積する(self) -> None:
        g = _cum_by(
            _hist([("20260101", "A", 1), ("20260101", "B", 0), ("20260102", "A", 0)]),
            ["sire"],
        )
        assert set(g[g["sire"] == "A"]["cum_n"]) == {1, 2}
        assert set(g[g["sire"] == "B"]["cum_n"]) == {1}


def _pit_from(rows: list[tuple[str, str, int]]) -> dict[str, pd.DataFrame]:
    """テスト用に最小限の pit テーブルを組む。"""
    h = _hist(rows)
    gl = (
        h.groupby("date").agg(n=("top3", "size"), s=("top3", "sum")).reset_index()
        .sort_values("date")
    )
    gl[["cum_n", "cum_s"]] = gl[["n", "s"]].cumsum()
    def _empty(*keys: str) -> pd.DataFrame:
        e = pd.DataFrame(columns=[*keys, "date", "cum_n", "cum_s"])
        return e.astype({"date": "datetime64[ns]"})

    # 母系テーブルは dam 列を持つ履歴から作る（無ければ空）
    hd = h.assign(dam=h["sire"].map(lambda x: f"dam_of_{x}"), horse_id=1)
    return {
        "sire": _cum_by(h, ["sire"]),
        "sire_upset": _cum_by(h, ["sire"]),
        "sire_dist": _empty("sire", "band"),
        "bms": _empty("bms"),
        "dam": _cum_by(hd, ["dam"]),
        "dam_win": _cum_by(hd, ["dam"]),
        "dam_upset": _cum_by(hd, ["dam"]),
        "self": _cum_by(hd, ["horse_id"]),
        "self_win": _cum_by(hd, ["horse_id"]),
        "self_upset": _cum_by(hd, ["horse_id"]),
        "global": gl[["date", "cum_n", "cum_s"]],
        "global_upset": gl[["date", "cum_n", "cum_s"]],
    }


class TestPointInTime:
    """父 A は 1/1 に 2 頭走って 2 頭とも複勝圏（率 1.0）、1/2 に対象馬が走る。"""

    HIST = [("20260101", "A", 1), ("20260101", "A", 1)]

    def _target(self, date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {"horse_id": [1], "date": [date], "distance": [1400]}
        )

    def test_翌日は前日の実績が反映される(self) -> None:
        ped = pd.DataFrame({"horse_id": [1], "sire": ["A"], "bms": [None]})
        out = add_pedigree_features(self._target("20260102"), ped, _pit_from(self.HIST))
        # 縮約前の実績は 2走2複勝圏。prior=1.0（全体も同じ）なので率は 1.0 に張り付く
        assert out["sire_top3_rate_pit"].iloc[0] > 0.99
        assert out["sire_n_runs_log_pit"].iloc[0] == np.log1p(2)

    def test_同日は絶対に混ざらない(self) -> None:
        """1/1 の対象馬は、同じ 1/1 の産駒成績を見てはいけない。"""
        ped = pd.DataFrame({"horse_id": [1], "sire": ["A"], "bms": [None]})
        out = add_pedigree_features(self._target("20260101"), ped, _pit_from(self.HIST))
        assert out["sire_n_runs_log_pit"].iloc[0] == 0.0, "同日の産駒成績が混入している"

    def test_実績が無い父は事前分布へ縮約される(self) -> None:
        ped = pd.DataFrame({"horse_id": [1], "sire": ["Z"], "bms": [None]})
        out = add_pedigree_features(self._target("20260102"), ped, _pit_from(self.HIST))
        # prior=1.0 に k=SHRINK_K で完全に引き戻される
        assert out["sire_top3_rate_pit"].iloc[0] > 0.99
        assert out["sire_n_runs_log_pit"].iloc[0] == 0.0

    def test_父が取れない馬でも欠損せず値が入る(self) -> None:
        """カバー率は91.3%。残りは prior で埋まればよい（偏りは無いと確認済み）。"""
        ped = pd.DataFrame({"horse_id": [], "sire": [], "bms": []}, dtype=object)
        out = add_pedigree_features(self._target("20260102"), ped, _pit_from(self.HIST))
        for col in PEDIGREE_FEATURES:
            assert out[col].notna().all(), f"{col} が欠損している"

    def test_行数と行順は変わらない(self) -> None:
        tgt = pd.DataFrame({
            "horse_id": [1, 2, 3],
            "date": ["20260102", "20260101", "20260103"],
            "distance": [1200, 1600, 1800],
            "marker": ["x", "y", "z"],
        })
        ped = pd.DataFrame({"horse_id": [1, 2, 3], "sire": ["A", "A", "A"],
                            "bms": [None, None, None]})
        out = add_pedigree_features(tgt, ped, _pit_from(self.HIST))
        assert list(out["marker"]) == ["x", "y", "z"]
        assert len(out) == 3
        # 日付順ではなく元の行に正しく対応していること（1/1 の y だけ実績ゼロ）
        assert out.loc[out["marker"] == "y", "sire_n_runs_log_pit"].iloc[0] == 0.0
        assert out.loc[out["marker"] == "x", "sire_n_runs_log_pit"].iloc[0] == np.log1p(2)


class TestShrinkage:
    def test_少数産駒ほど事前分布へ寄る(self) -> None:
        """縮約が効いていないと少数産駒の父のノイズを学習してしまう。"""
        hist = [("20260101", "A", 1)]                    # A は 1 走 1 複勝圏
        hist += [("20260101", f"B{i}", 0) for i in range(200)]  # 全体は 0%
        ped = pd.DataFrame({"horse_id": [1], "sire": ["A"], "bms": [None]})
        tgt = pd.DataFrame({"horse_id": [1], "date": ["20260102"], "distance": [1400]})
        out = add_pedigree_features(tgt, ped, _pit_from(hist))
        rate = out["sire_top3_rate_pit"].iloc[0]
        prior = 1 / 201
        assert rate < 0.1, "1走100%がそのまま出ている（縮約が効いていない）"
        assert rate == (1 + SHRINK_K * prior) / (1 + SHRINK_K)


class TestDuplicateGuard:
    """血統表に horse_id の重複があれば必ず落とすこと。

    現在のキーは血統登録番号（`umaconn_code` ↔ `jravan_code`）なので重複は出ないが、
    キーを変えたときに気付けるようガードは残す。重複したまま結合すると行が増え、
    下流の代入が**黙ってずれる**（名前突合だった頃に walk-forward が
    `Length of values (66615) does not match index (65399)` で落ちて発覚した）。
    """

    def test_血統表に重複があれば弾く(self) -> None:
        import pytest
        ped = pd.DataFrame({
            "horse_id": [1, 1], "sire": ["A", "B"], "bms": [None, None],
        })
        tgt = pd.DataFrame({"horse_id": [1], "date": ["20260102"], "distance": [1400]})
        with pytest.raises(ValueError, match="重複"):
            add_pedigree_features(tgt, ped, _pit_from([("20260101", "A", 1)]))


class TestMaternalSelfExclusion:
    """母系特徴から**自分自身の戦績を必ず除く**こと。

    引き忘れると「馬自身の戦績」を別名でモデルに渡すことになり、
    既存特徴と二重計上になる（そして改善したように見える）。
    """

    def _pit(self) -> dict[str, pd.DataFrame]:
        """母 D の産駒は自分(1)と兄弟(2)の2頭。1/1 に両方1走ずつ。"""
        h = pd.DataFrame({
            "date": pd.to_datetime(["20260101", "20260101"], format="%Y%m%d"),
            "dam": ["D", "D"], "horse_id": [1, 2], "top3": [1, 1],
        })
        gl = h.groupby("date").agg(n=("top3", "size"), s=("top3", "sum")).reset_index()
        gl[["cum_n", "cum_s"]] = gl[["n", "s"]].cumsum()

        def _empty(*keys: str) -> pd.DataFrame:
            return pd.DataFrame(columns=[*keys, "date", "cum_n", "cum_s"]).astype(
                {"date": "datetime64[ns]"}
            )

        return {
            "sire": _empty("sire"), "sire_upset": _empty("sire"),
            "sire_dist": _empty("sire", "band"), "bms": _empty("bms"),
            "dam": _cum_by(h, ["dam"]), "dam_win": _cum_by(h, ["dam"]),
            "dam_upset": _cum_by(h, ["dam"]),
            "self": _cum_by(h, ["horse_id"]), "self_win": _cum_by(h, ["horse_id"]),
            "self_upset": _cum_by(h, ["horse_id"]),
            "global": gl[["date", "cum_n", "cum_s"]],
            "global_upset": gl[["date", "cum_n", "cum_s"]],
        }

    def test_兄弟の走りだけが数えられる(self) -> None:
        ped = pd.DataFrame({"horse_id": [1], "sire": [None], "bms": [None], "dam": ["D"]})
        tgt = pd.DataFrame({"horse_id": [1], "date": ["20260102"], "distance": [1400]})
        out = add_pedigree_features(tgt, ped, self._pit())
        # 母 D の産駒は 2 走あるが、自分の 1 走を引いて兄弟は 1 走
        assert out["dam_sib_n_runs_log_pit"].iloc[0] == np.log1p(1)

    def test_一人っ子は兄弟ゼロになる(self) -> None:
        """自分しか産駒がいない母では、母単位の累計＝自分の累計。"""
        h = pd.DataFrame({
            "date": pd.to_datetime(["20260101"], format="%Y%m%d"),
            "dam": ["D"], "horse_id": [1], "top3": [1],
        })
        gl = h.groupby("date").agg(n=("top3", "size"), s=("top3", "sum")).reset_index()
        gl[["cum_n", "cum_s"]] = gl[["n", "s"]].cumsum()

        def _empty(*keys: str) -> pd.DataFrame:
            return pd.DataFrame(columns=[*keys, "date", "cum_n", "cum_s"]).astype(
                {"date": "datetime64[ns]"}
            )

        pit = {
            "sire": _empty("sire"), "sire_upset": _empty("sire"),
            "sire_dist": _empty("sire", "band"), "bms": _empty("bms"),
            "dam": _cum_by(h, ["dam"]), "dam_win": _cum_by(h, ["dam"]),
            "dam_upset": _cum_by(h, ["dam"]),
            "self": _cum_by(h, ["horse_id"]), "self_win": _cum_by(h, ["horse_id"]),
            "self_upset": _cum_by(h, ["horse_id"]),
            "global": gl[["date", "cum_n", "cum_s"]],
            "global_upset": gl[["date", "cum_n", "cum_s"]],
        }
        ped = pd.DataFrame({"horse_id": [1], "sire": [None], "bms": [None], "dam": ["D"]})
        tgt = pd.DataFrame({"horse_id": [1], "date": ["20260102"], "distance": [1400]})
        out = add_pedigree_features(tgt, ped, pit)
        assert out["dam_sib_n_runs_log_pit"].iloc[0] == 0.0, "自分の戦績が兄弟に混ざっている"
