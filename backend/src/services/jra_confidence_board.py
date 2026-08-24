"""中央競馬「推奨」タブの単勝信頼度ボード。

各レースの**全出走馬**を、単勝信頼度（= `calculated_indices.win_probability`）の
高い順に並べ、オッズと「オッズ×単勝信頼度」を添えて見せるための組み立て。

DB にも FastAPI にも依存しない純関数なので、API からもバッチからも同じ結果になる。

用語:
    単勝信頼度      `win_probability`。is_win 較正ヘッド（`models/v26_iswin_calib.txt`）
                    を通した勝率予測で、softmax 生値ではない
    オッズ×単勝信頼度  単勝オッズ × 単勝信頼度。いわゆる単勝期待値（EV）で、
                    1.0 が損益分岐。**表示は小数第1位まで**
    信頼度          こちらは**レース単位**の指標（`calculate_race_confidence` の
                    score / rank）。馬ごとの単勝信頼度とは別物なので混同しないこと
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoardHorse:
    """ボードに並べる1頭。

    Attributes:
        horse_number: 馬番。未確定なら None。
        horse_name: 馬名。
        win_odds: 最新の単勝オッズ。未取得なら None。
        win_probability: 単勝信頼度（0〜1）。指数が無ければ None。
        finish_position: 着順。レース確定前は None。
    """

    horse_number: int | None
    horse_name: str | None
    win_odds: float | None
    win_probability: float | None
    finish_position: int | None = None

    @property
    def odds_x_confidence(self) -> float | None:
        """オッズ×単勝信頼度（小数第1位）。オッズか信頼度が欠ければ None。

        🔴 **丸めはここで済ませる。** 画面側で丸めると、同じ値が
        API レスポンスと表示で食い違う経路ができる。
        """
        if self.win_odds is None or self.win_probability is None:
            return None
        return round(self.win_odds * self.win_probability, 1)


def sort_by_confidence(horses: list[BoardHorse]) -> list[BoardHorse]:
    """単勝信頼度の降順に並べる。

    🔴 **指数が無い馬を落とさない。** 落とすと 12頭立てなのに 11 行しか出ず、
    しかも画面からは欠けていることが分からない。並べようが無いので末尾へ回し、
    順位を付けずに出す（API では `confidence_rank_in_race` が None になる）。

    同値・未算出どうしは馬番昇順で安定させる（実行ごとに並びが変わらないように）。
    """
    return sorted(
        horses,
        key=lambda h: (
            h.win_probability is None,                       # 未算出は末尾へ
            -(h.win_probability or 0.0),                     # 信頼度の降順
            h.horse_number if h.horse_number is not None else 999,
        ),
    )


def rank_in_race(ordered: list[BoardHorse]) -> list[int | None]:
    """並べ替え済みリストに対する順位を返す（未算出の馬は None）。

    同着扱いはしない。単勝信頼度は小数4桁まで持つので実質的に同値は起きず、
    起きても馬番で決着させたほうが表示が安定する。
    """
    ranks: list[int | None] = []
    n = 0
    for h in ordered:
        if h.win_probability is None:
            ranks.append(None)
            continue
        n += 1
        ranks.append(n)
    return ranks
