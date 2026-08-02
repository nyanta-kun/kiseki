"""地方 検証プロトコル（学習/検証/テスト期間）のユニットテスト

2026-08-03 に `TEST_START` を固定値 "20260701" から**当月1日の月次ローリング**へ
変更した。固定だと本番モデルの学習終端（= TEST_START の前日）も固定され、
月を追うごとにモデルが古くなるため。
"""

from __future__ import annotations

import datetime
import importlib
import os

from src import chihou_protocol as proto


class TestDefaultTestStart:
    def test_当月1日になる(self):
        assert proto._default_test_start(datetime.date(2026, 8, 3)) == "20260801"
        assert proto._default_test_start(datetime.date(2026, 8, 31)) == "20260801"

    def test_月をまたぐと前進する(self):
        a = proto._default_test_start(datetime.date(2026, 8, 31))
        b = proto._default_test_start(datetime.date(2026, 9, 1))
        assert a < b
        assert b == "20260901"

    def test_年をまたいでも壊れない(self):
        assert proto._default_test_start(datetime.date(2027, 1, 1)) == "20270101"


class TestPeriodConsistency:
    def test_VAL_ENDはTEST_STARTの前日(self):
        ts = datetime.datetime.strptime(proto.TEST_START, "%Y%m%d").date()
        ve = datetime.datetime.strptime(proto.VAL_END, "%Y%m%d").date()
        assert ve == ts - datetime.timedelta(days=1)

    def test_期間が時系列順に並ぶ(self):
        assert proto.TRAIN_END < proto.VAL_START <= proto.VAL_END < proto.TEST_START

    def test_TRAIN_ENDは固定(self):
        """honest 再学習の境界。ローリングさせると過去の検証と比較できなくなる。"""
        assert proto.TRAIN_END == "20250630"


class TestEnvOverride:
    def test_環境変数で固定できる(self, monkeypatch):
        """過去の分析を当時の境界で再現するための逃げ道。"""
        monkeypatch.setenv("CHIHOU_TEST_START", "20260701")
        reloaded = importlib.reload(proto)
        try:
            assert reloaded.TEST_START == "20260701"
            assert reloaded.VAL_END == "20260630"
        finally:
            monkeypatch.delenv("CHIHOU_TEST_START", raising=False)
            importlib.reload(proto)

    def test_未設定なら当月1日に戻る(self):
        os.environ.pop("CHIHOU_TEST_START", None)
        reloaded = importlib.reload(proto)
        assert reloaded.TEST_START == datetime.date.today().replace(day=1).strftime("%Y%m%d")


class TestTrainDataEndFollowsProtocol:
    def test_学習終端はTEST_STARTの前日(self):
        """本番モデルが TEST 期間を学習に含まないことの担保。

        2026-08-03 以前は "20260706" がハードコードされ、TEST_START(20260701) を
        6日超過して TEST の 257レースを学習していた。
        """
        from scripts.train_chihou_market_lgb import TRAIN_DATA_END

        ts = datetime.datetime.strptime(proto.TEST_START, "%Y%m%d").date()
        te = datetime.datetime.strptime(TRAIN_DATA_END, "%Y%m%d").date()
        assert te == ts - datetime.timedelta(days=1)
