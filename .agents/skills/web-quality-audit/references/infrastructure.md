# インフラ/DevOps チェック項目

## 検索クエリ例
- `web application infrastructure best practices {year}`
- `DevOps checklist production readiness`
- `{cloud-provider} web hosting best practices`
- `container orchestration best practices {year}`
- `monitoring and observability web application`
- `disaster recovery web application`

## チェック項目

### 1. デプロイ構成
- **デプロイ自動化**: CI/CDパイプラインの構築
- **デプロイ戦略**: Blue/Green、Canary、Rolling Update等
- **ロールバック**: 迅速なロールバック手順の整備
- **環境分離**: 開発/ステージング/本番環境の適切な分離
- **Infrastructure as Code**: Terraform、CloudFormation等の使用
- **設定管理**: 環境ごとの設定の管理方法

### 2. コンテナ/オーケストレーション
- **Dockerfile**: マルチステージビルド、軽量ベースイメージ
- **docker-compose**: 開発環境の再現性
- **Kubernetes**（使用している場合）: リソース制限、ヘルスチェック、HPA
- **イメージレジストリ**: プライベートレジストリ、イメージスキャン

### 3. 可用性とスケーラビリティ
- **冗長化**: 単一障害点（SPOF）の排除
- **ロードバランシング**: 適切なLB設定、ヘルスチェック
- **オートスケーリング**: 負荷に応じた自動スケーリング
- **データベースのスケーリング**: リードレプリカ、シャーディング
- **SLA/SLO**: サービスレベルの定義と計測

### 4. 監視とオブザーバビリティ
- **メトリクス**: CPU、メモリ、ディスク、ネットワークの監視
- **ログ管理**: 集中ログ管理（ELK、CloudWatch Logs等）
- **トレーシング**: 分散トレーシング（OpenTelemetry等）
- **アラート**: 適切な閾値とエスカレーション
- **ダッシュボード**: 運用状況の可視化
- **外形監視**: エンドポイント死活監視

### 5. 障害復旧（DR）
- **バックアップ**: データベース、ファイルの定期バックアップ
- **復旧手順**: 障害時の復旧手順書（Runbook）
- **RTO/RPO**: 復旧時間目標/復旧時点目標の定義
- **障害訓練**: 定期的な障害復旧訓練の実施
- **インシデント対応**: エスカレーションフロー、連絡体制

### 6. CI/CDパイプライン
- **ビルド**: 再現可能なビルドプロセス
- **テスト**: パイプラインでの自動テスト実行
- **セキュリティスキャン**: SAST/DAST の組み込み
- **アーティファクト管理**: ビルド成果物の管理と保持
- **デプロイ承認**: 本番デプロイの承認フロー

### 7. ネットワーク構成
- **DNS**: 適切なDNS設定、TTL
- **SSL/TLS**: 証明書の管理、自動更新
- **CDN**: 静的アセットのCDN配信
- **WAF**: Webアプリケーションファイアウォールの設定
- **DDoS対策**: DDoS攻撃への対策
