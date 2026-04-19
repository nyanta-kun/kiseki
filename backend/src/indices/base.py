"""指数算出Agent 基底クラス"""

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession


class IndexCalculator(ABC):
    """全指数Agentの基底クラス。各Agentはこれを継承して実装する。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    @abstractmethod
    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の指数を算出する。"""
        ...

    @abstractmethod
    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        """レース全馬の指数を一括算出する。{horse_id: index_value}

        データ不足・計算不能な馬は None を返す。
        composite.py 側でレース内平均値に置換される。
        """
        ...

    async def recalculate(self, race_id: int, version: int) -> dict[int, float | None]:
        """再算出（デフォルトはcalculate_batchと同じ）。必要に応じてオーバーライド。"""
        return await self.calculate_batch(race_id)
