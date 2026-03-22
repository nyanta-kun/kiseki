"""指数算出Agent 基底クラス"""

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session


class IndexCalculator(ABC):
    """全指数Agentの基底クラス。各Agentはこれを継承して実装する。"""

    def __init__(self, db: Session):
        self.db = db

    @abstractmethod
    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の指数を算出する。"""
        ...

    @abstractmethod
    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の指数を一括算出する。{horse_id: index_value}"""
        ...

    def recalculate(self, race_id: int, version: int) -> dict[int, float]:
        """再算出（デフォルトはcalculate_batchと同じ）。必要に応じてオーバーライド。"""
        return self.calculate_batch(race_id)
