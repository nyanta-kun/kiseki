# T08: WEB 追加データ取得（netkeiba 直前情報ほか）【Wave 2】

## 目的
直交情報源の拡充。過去検証で「外部指数は入力特徴/信頼度フィルタとして有効（馬券シグナル単体では無効）」と確定しているため、
T04 荒れ分類器と T07 オーバーレイの**入力特徴**として WEB データを追加する。

## 候補（優先順）
1. netkeiba 直前オッズ・オッズ変化（late money。地方では効かなかったが JRA エキゾチックでは未検証）
2. パドック評価（paddock_index は既存。鮮度・カバレッジの監査から）
3. 調教データの指数化（memory: training_data_integration の続き。坂路/ウッド fetch 実装）
4. 想定人気 vs 実人気の乖離

## 注意
- netkeiba スクレイプは sekito 側の v_races 移行問題（memory: netkeiba_scrape_stopped_2026_05）の修正と整合させる
- 取得頻度・利用規約・既存 LaunchAgent 構成（projected_entries）の慣習に従う

## 完了判定
各データ源について「T04/T07 への特徴追加 A/B（5seed・3分割）で改善が std を超えるか」で採否判定。
