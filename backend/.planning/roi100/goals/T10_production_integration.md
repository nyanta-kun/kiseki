# T10: 本番統合（API・UI・自動推奨）【Wave 2・T09 依存】

## 目的
T09 で生き残った戦略のみを本番化する。kiseki の既存パターンに従う:
- 推奨は都度算出（JRA sweet_spot 方式・DB保存しない）: `backend/src/api/recommendations.py` に券種別カテゴリ追加
- `backend/src/services/recommender.py` にレース集約ロジック
- フロント: `frontend/src/components/IndicesTable.tsx` / 推奨パネルにバッジ・買い目表示
- realtime オッズ更新（T01）に連動した当日スキャン（T07 overlay）の定時実行

## 必須要件
- 戦略ごとに 30日実勢の hit_rate / ROI を recommendations API の summaries に出す（地方カテゴリと同形式）
- 月次モニタリング: `monitor_*` 系スクリプトの慣習に従い、fresh ROI が CI 下限を割ったら戦略を自動停止できるフラグを用意
- デプロイは既存 ghcr.io / VPS 手順（memory: deploy_infra）

## スコープ外
IPAT 自動投票（MS7/MS8 で別途）
