"""ROI100 馬券戦略パッケージ。

確率エンジン (finish_order) / オッズ近似 (odds_model) / レース選定 (race_selector)
/ 決済 (backtest) / 資金配分 (allocation, ticket_builder)
/ 重勝式（WIN5・地方重勝式）の足の幅配分 (multi_race_formation) を提供する。
各モジュールは独立 import する（このファイルでの re-export は行わない）。
"""
