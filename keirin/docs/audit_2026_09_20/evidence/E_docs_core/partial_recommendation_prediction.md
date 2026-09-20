# 監査対象: keirin/docs/RECOMMENDATION.md, keirin/docs/prediction-factors.md

方針: ドキュメントの記述は証拠として扱わない。証拠は本番コード（呼び出し元まで）・
実データ・自分の計算のみ。本タスクは①コード突き合わせに集中（DB/実行は行っていない）。

読了: RECOMMENDATION.md（421行・全文）, prediction-factors.md（536行・全文、2回のReadで分割取得）。

---

## 総括（先に結論）

- **最重要の発見**: `prediction-factors.md` のヘッダ（冒頭サマリー）が本番の実体と大きく乖離している。
  ヘッダは「本番モデル…46特徴量」と書くが、実際の `FEATURE_COLS_WT`（`src/preprocessing/feature_wt.py:1269`）は
  **70特徴量**（`tests/test_feature_prepare.py:57` / `tests/test_rp_trend_wt.py:156` / `tests/test_sb_dyn_wt.py:124`
  が `== 70` を固定しており、モデルの `*.meta.json` も `"feature_count": 70` と実測一致）。
  ファイル内で一番新しい特徴量数の記載（§2-7・2026-08-04付「48→60」）ですら**10特徴量ぶん古い**
  （2026-08-19 のライン先頭系6特徴＝`line_leader_*`/`line_rp_spread`/`line_rp_lead_minus_deputy`、
  2026-09-10 の節内成績4特徴＝`cup_*` が本文のどこにも記載されていない）。
  CLAUDE.md 自身が定めた「ドキュメント更新ルール」（FEATURE_COLS_WT 変更時は本ファイルを必ず更新）に
  違反したまま2回分の追加がすり抜けている。
- **`RECOMMENDATION.md` §3.3 の軸信頼ゲート閾値テーブルが完全に古い**。表に載っている8値のうち
  現行コード（`backend/src/services/keirin_type_lab_gate.py:122-130`）と一致するものが**1つもない**。
  さらに `A_ana` は現在ゲート対象外（`AXIS_GATE_EXEMPT_PLANS`）なのに、表は「A_ana | 1.470」と
  あたかも現役の値であるかのように載せている。
- **`RECOMMENDATION.md` §2.1 のプラン表「配分」列が4プラン分間違っている**。B_hit / C_hit / D_hit / E_hit の
  実装は `alloc="conf"`（床+確信度比例配分）であって、表が書く `dutch`（1/オッズ比例のダッチング）ではない。
  両者は払戻の性質が構造的に異なる別の配分方式であり、看板性（払戻を均すか偏らせるか）の説明にも影響する。
- **`RECOMMENDATION.md` 冒頭の「自信あり」規則説明が1世代古い**。本文 25行目は「旧規則（発走18時前 ∧
  合成3倍以上 ∧ EV最大）」と書くが、これは2026-09-02〜09-18に使われていた規則で、2026-09-19に
  ユーザー指示により「合成2.5倍以上 × Σp最大」へ置き換わっている（`src/confident_pick.py:34-56`）。
  ドキュメントは「最終更新: 2026-09-16」を名乗るタイトルだが、その3日後の変更が未反映。
- 上記いずれも「ドキュメントの記述が誤って積み上がっているかもしれない」という依頼主の懸念と
  正確に一致する型の欠陥である。

---

## 1. 検証可能な主張の一覧表

凡例: 状態＝現行 / 撤回済み / 後の記述で覆された / 不明。本番依拠＝その値が実際に本番の挙動を
決めているか（要確認＝コードに実在するが常時参照される設計かまで未追跡、または生成専用で未販売）。

| 文書:行番号 | 主張の要約 | 根拠として挙げられた窓/スクリプト | 本番依拠 | 状態 |
|---|---|---|---|---|
| RECOMMENDATION.md:18-22 | 2026-09-16 に段(T_firm/T_mid/T_axis/T_upset)の販売を停止し `TIER_SELL_ENABLED=False` | 2026-09-15実績57R vs e018af8 dry-run | はい（`src/type_lab.py:988` 実測 `False`） | 現行 |
| RECOMMENDATION.md:34-58 | 段の3閾値（固め>1.464・広め1.353-1.464・荒れ<=1.353）＋各買い方 | memory keirin-firm-upset-policy-2026-09-14 | はい（`TIER_AXIS_FIRM_MIN=1.464`/`TIER_AXIS_MID_MIN=1.353`, `type_lab.py:990,992`）ただし現在は不販売 | 現行（コードとして存在・販売は停止中） |
| RECOMMENDATION.md:48 | 段の入稿ゲートを「悪い側の払戻見込み」判定に変更・見送り11.96件/日等 | paper 2025 5区分係数 `TIER_REALIZED_FACTOR` | はい（`scripts/exp_type_lab/tier_realized_factor.py` 実在・出力を type_lab.py に埋め込む設計） | 現行（ただし段自体が停止中） |
| RECOMMENDATION.md:70 | 1レース予算は10,000円固定 | — | はい（`BUDGET` 定数, 複数箇所で参照） | 現行 |
| RECOMMENDATION.md:79-86 | `axis_sum >= AXIS_SUM_FIRM(=1.44)` が型A/B/C ↔ D/E/Fの分岐、二軸そろい率66%↔39% | docs/type_lab/ 「型3層」 | はい（`type_lab.py:73,582`） | 現行 |
| RECOMMENDATION.md:88-105 | 荒れ度 `s` の加算式（ライン人数・遅れ率・脚質・開催日目・番手得点） | — | 要確認（未個別追跡・値の割当箇所は`type_lab.py`内、本監査では式全体の1:1突合は未実施） | 不明（未突合） |
| RECOMMENDATION.md:109 | `pw_ent` は型Aの振り分けにのみ使用、閾値1.4076 | — | はい（`ANA_PW_ENT_MIN=1.4076`, `type_lab.py:129,1590,1607`） | 現行 |
| RECOMMENDATION.md:110-112 | `gap`（相手の開き）は計算するが商品には使わない・不採用 | docs/type_lab/gap_gate_2026_08_28.md | ドキュメント実在確認済み | 現行（不採用のまま） |
| RECOMMENDATION.md:123-138 | プラン一覧表（券種・買い方・点数・配分） | — | **一部不一致**（下記コード突き合わせ参照） | 一部誤り |
| RECOMMENDATION.md:140-151 | `sell_plans_for` の分岐順（①看板→②型A→③型F→④その他） | — | 要確認（分岐の存在は type_lab.py に確認、順序の厳密な1:1突合は範囲外） | 概ね整合（サンプル確認のみ） |
| RECOMMENDATION.md:161-172 | 型Fの売り方をF_payからF_hit（＋決勝だけF_pay）へ2026-08-31変更、表の数値 | 実売3日37件 | はい（`type_lab.py`のF_hit/F_pay定義・分岐コメントに同旨あり） | 現行 |
| RECOMMENDATION.md:182-189 | `MIN_MEAN_PAYOUT > 20,000円` = 買い目シェア37.5%未満と同値 | — | はい（`src/stake_allocation.py:286` `MIN_MEAN_PAYOUT=20_000`、`scripts/netkeirin_submit_type_lab.py:441` で参照） | 現行 |
| RECOMMENDATION.md:192-195 | `MIN_POINT_ODDS >= 2.0` | — | はい（`src/stake_allocation.py:453` `MIN_POINT_ODDS=2.0`、submit script 2箇所で参照） | 現行 |
| RECOMMENDATION.md:197-214 | 軸信頼ゲート `AXIS_GATE_MIN` 表（8プラン閾値）・効果29.5件/日・ROI85.0%・「初めて対照20/20」 | 探索窓2025 | **いいえ（値が現行コードと不一致）** | **後の記述で覆された（コード側は2026-09-03/09-11に2回更新済み、本表は最初期の8か月版の数値のまま）** |
| RECOMMENDATION.md:208-210 | 「知らないプランは通す」（閾値なしのプランはゲート素通し） | — | はい（`passes_axis_gate` の `floor is None: return True`, `keirin_type_lab_gate.py:247-250`） | 現行 |
| RECOMMENDATION.md:216-221 | 入力健全性ゲート（並び予想・AI印未公開は出さない） | 2026-08-26熊本7Rの事故 | はい（`_missing_market_inputs` 呼び出し, `netkeirin_submit_type_lab.py`） | 現行 |
| RECOMMENDATION.md:223-235 | 日次上限 = max(1, int(判定対象×0.5))、枠外=9車/決勝/準決勝/グレード3以上 | 2026-09-01実測表 | はい（`DAILY_CAP_RACE_FRACTION=0.5`, `DAILY_CAP_EXEMPT_KEYWORDS=("決勝",)`, `DAILY_CAP_EXEMPT_MIN_GRADE=3`, 9車除外はsubmitスクリプト側`_priority`/`_exempt`） | 現行 |
| RECOMMENDATION.md:229-232 | 残す順=(2×軸信頼順位+実力伯仲順位)/3 | — | はい（`cap_priority` W=2.0, `RP_SD_PRIORITY_AXIS_WEIGHT=2.0`, doctest一致） | 現行 |
| RECOMMENDATION.md:236-276 | 高額枠（型B/C/D・7車・1日5本・15万/40万・3:2配分） | docs/highpay_5slots_2026_09_06.md | はい（`HIGHPAY_TYPES=("B","C","D")`, `HIGHPAY_SLOTS_PER_DAY=5`, `SIGNBOARD_TARGET=150_000`, `HIGHPAY_BIG_TARGET=400_000`, `HIGHPAY_BIG_SLOTS={2,4}`） | 現行（ただし直近に5→10→5と往復した経緯は本文に未記載。下記参照） |
| RECOMMENDATION.md:284-296 | 買い目確率＝位置別合成PL＋同ライン隣接ボーナス λ=2.0, μ=1.5 | 9/9四半期プラス | はい（`RANK_7T3_LINE_ADJ_W=(2.0,1.5)`, `RANK_7T3_BLEND_W=(1.0,0.5,0.0)`, `strategy_wt.py:4540,4567`） | 現行 |
| RECOMMENDATION.md:300-303 | 板(`wt_odds`)は入稿経路で一切読まない。予測オッズのみ | 2026-08-26撤去 | 要確認（本監査では submit script 内の odds 読み出し箇所を悉皆探索していない。ただし CLAUDE.md 側にも同旨の記録あり） | 概ね整合 |
| RECOMMENDATION.md:304-313 | 表示保守倍率テーブル（trio/trifecta×点数） | — | **はい・完全一致**（`backend/src/services/keirin_payout_floor.py:64-67`） | 現行 |
| RECOMMENDATION.md:319-324 | 運用時刻表（07:20/13:05,18:05/5,20,35,50分/09:15） | — | **不明（未検証）**。cron/crontabはgit管理外（VPS側の状態）。リポジトリ内に該当スケジュールの一次証拠なし。`docs/system-architecture.md`は関連スクリプト`check_model_freshness.py`の**提案**cron `0 9 * * *`（=09:00）を「登録は未実施」と明記しており、doc記載の「09:15」と時刻が食い違う | 不明（要現地確認） |
| RECOMMENDATION.md:334-336 | 型が変わると古い型の行が残る問題の説明 | 一意キー(race_key,plan_key,mode) | 要確認（記述のみ、DBスキーマの直接確認は範囲外） | 記述と整合的（他資料と矛盾なし） |
| RECOMMENDATION.md:25,54 | 「自信あり」規則の二重説明（段OFF時=旧規則18時前∧3倍以上∧EV最大 / 段ON時=固め決勝優先Σp） | — | **いいえ（段OFF時の記述が古い）** | **後の記述で覆された**（`src/confident_pick.py`が2026-09-19に2.5倍・Σp最大へ更新済み、下記詳細） |
| RECOMMENDATION.md:342-347 | ROIでは採否を決めない。月次ROIを±2.5ptに収めるのに約15.6年 | — | 数値そのものは検証範囲外（統計計算の再現は行っていない） | 記述のみ（要検証） |
| RECOMMENDATION.md:349-350 | 対照は20 seed必須、3 seedでは足りない（2026-08-27の否定は上振れ） | — | ドキュメント整合性のみ確認（該当ファイル群は存在） | 記述のみ |
| RECOMMENDATION.md:357-359 | 控除率25%の壁は25倍未満まで、型A三連複帯別回収率 | — | コード上の直接対応定数なし（帯別集計はexpスクリプト側、本監査は未実行） | 記述のみ（要検証） |
| RECOMMENDATION.md:363-383 | 「決着済み」表（EV順位・gap・DNF重み付け・波乱検出等の不採用一覧） | 各種 memory | 部分確認（DNF重み付け不採用はCLAUDE.md本文の詳細記述と整合） | 記述のみ（他資料と矛盾なし） |
| prediction-factors.md:4 | 本番モデル lgbm_wt / 46特徴量 | — | **いいえ**（実際は70特徴量） | **誤り（古い）** |
| prediction-factors.md:4 | holdout AUC 0.7793（test-from直近90日で評価後、全データ再学習） | — | 近似（実測 `lgbm_wt.meta.json`: `test_auc_holdout=0.78085`、`trained_at=2026-09-14`）差+0.0016pt | 誤差レベルで陳腐化（週次retrainに伴う自然なドリフト） |
| prediction-factors.md:5 | lgbm_wt_eval holdout AUC 0.7765 | — | 実測0.77845（`lgbm_wt_eval.meta.json`）差+0.0020pt | 誤差レベルで陳腐化 |
| prediction-factors.md:6 | lgbm_wt_win holdout AUC 0.8258 | — | 実測0.82932（`lgbm_wt_win.meta.json`）差+0.0035pt | 誤差レベルで陳腐化 |
| prediction-factors.md:6 | lgbm_wt_win_eval holdout AUC 0.8214 | — | 実測0.82699（`lgbm_wt_win_eval.meta.json`）差+0.0056pt | 誤差レベルで陳腐化（やや大きい） |
| prediction-factors.md:7 | lgbm_wt_top2 holdout AUC 0.8077 | — | 実測0.81083（`lgbm_wt_top2.meta.json`）差+0.0031pt | 誤差レベルで陳腐化 |
| prediction-factors.md:8 | 週次再学習フロー（日曜23:30・AUCゲート top3系>=0.75・win系>=0.78・前回比-0.02超悪化で昇格中止） | `weekly_retrain_wt.sh` | 要確認（スクリプト内容の悉皆確認は範囲外。存在は確認） | 記述のみ |
| prediction-factors.md:12 | ex_spurt_pct/ex_thrust_pct除外（48→46特徴・2026-07-31） | scripts/exp_ab_leaky_ex_features.py | 過去の一事象としては整合（当時46だったのは事実らしいが、現在は70） | 現行の一部（歴史的経緯としては真、現在の総数説明としては誤解を招く） |
| prediction-factors.md:130-135 | FEATURE_COLS_WT 60特徴（2026-08-04時点の説明として） | — | 当時としては真（60→66→70の系列の一段階） | 撤回済み・最新情報でカバーされていない（後続2回の追加が本文非掲載） |
| prediction-factors.md:203 | 2026-08-12 pred_top2_pct追加（表示専用・買い目には未使用） | — | はい（doc内でも「表示専用」旨繰り返し記載、type_lab側では候補にpred_win_pctは使うがtop2表示利用は別経路） | 現行（旧ランク文脈） |
| prediction-factors.md:274-286 | 波乱ゲート top3_sum 四分位カット（旧U/S/A戦略） | `docs/analysis/01〜03` | 旧ランク（RANK_7S系列）の歴史的機構としては実在（`strategy_wt.py`に該当ロジックあり） | 現行コードとしては存在するが**RECOMMENDATION.mdの現行商品体系（型ラボ）とは別系統**。読者が誤って現行商品の説明と誤解しうる |
| prediction-factors.md:358-359 | `RANK_7S_AXIS_SUM_MAX=1.5`、同一分位にする値1.5092 | 2026-08-03 隊列位置A/B検証 | **いいえ（現行コードは1.40）** | **食い違い**（`src/strategy_wt.py:828` 実測 `RANK_7S_AXIS_SUM_MAX = 1.40`。expスクリプトのコメント`scripts/exp_7a_exclusion_confirm.py:191`も「1.40（PR#9で採用済み）」と明記） |
| prediction-factors.md:453-466 | venue_info の bank_length/is_indoor/prefecture/straight_len/cant_deg | — | 未突合（DBスキーマ直接確認は範囲外） | 記述のみ |
| prediction-factors.md:489-535 | 更新履歴テーブル（多数のROI/AUC実測値・全廃/新設の経緯） | 各実験スクリプト | 個別に全件突合は行っていない。サンプル抽出でtier系（09-14〜09-19）とFEATURE_COLS_WT系（08-03,08-04）のみ突合 | 大半は「一度きりの実験記録」であり検証範囲外。ただし更新履歴の最新版数記載（「48→60」等）が現在の70と食い違う点は上記の通り誤り |

---

## 2. コード突き合わせで見つかった具体的な食い違い（file:line付き）

### 2-1. 軸信頼ゲート閾値表（RECOMMENDATION.md §3.3・行197-206）が現行コードと一致しない

`RECOMMENDATION.md:201-206`:
```
| プラン | 下限 | プラン | 下限 |
| `A_hit` / `A_pay` | 1.537 | `C_hit` | 1.480 |
| `A_trio` | 1.499 | `D_hit` | 1.263 |
| `A_ana` | 1.470 | `E_hit` | 1.245 |
| `B_hit` | 1.504 | `F_hit` / `F_pay` / `F_sign` | 1.230 |
```

現行 `backend/src/services/keirin_type_lab_gate.py:122-130`:
```python
AXIS_GATE_MIN: dict[str, float] = {
    "A_hit": 1.596,
    "A_trio": 1.528,
    "B_hit": 1.539,
    "C_hit": 1.500,
    "D_hit": 1.304,
    "E_hit": 1.284,
    "F_hit": 1.246,
    "F_sign": 1.267,
}
```

差分（doc→現行）: A_hit 1.537→1.596 / A_trio 1.499→1.528 / B_hit 1.504→1.539 /
C_hit 1.480→1.500 / D_hit 1.263→1.304 / E_hit 1.245→1.284 / F_hit 1.230→1.246 /
F_sign（docはF_hitと同値扱い1.230）→1.267。**8値すべてが不一致**。

さらに `A_ana` は現行では `AXIS_GATE_EXEMPT_PLANS`（`keirin_type_lab_gate.py:145-169`）に
含まれ、**ゲートを掛けない設計に変わっている**（コメントに理由: 「軸1が飛ぶ側に賭ける商品。
軸信頼で絞るのは狙いと正面から逆（両窓で逆効果）」）。RECOMMENDATION.md は「A_ana | 1.470」を
現役の閾値であるかのように表に載せており、実態（ゲート対象外）と矛盾する。

同ファイルの直後の文（`RECOMMENDATION.md:212-214`）:
> 効果は 29.5件/日・ROI 85.0% [78.2, 92.9]・表示的中 24.5→27.8%。
> **一連の検証で初めて無作為対照20本に 20/20 で勝った腕。**

これは `keirin_type_lab_gate.py` のコメント履歴（同ファイル冒頭docstring）にある**2026-08-28時点の
最初期の実測**（「下位1/5を外す」導入直後・8か月窓）と一致する数値である。その後
2026-09-03（効くプランだけに絞る・9→6プラン）・2026-09-11（`A_ana`以外を全プランp30へ・
件数を−22〜23%）と**少なくとも2回の再較正**が入っており、閾値・対象プラン集合・効果の実測値
（現在は「件/日 40.99→31.69・表示的中25.01→25.27%・ROI82.8→85.5」等、`keirin_type_lab_gate.py:99-106`）
のいずれも変わっている。**RECOMMENDATION.md §3.3は初版のまま更新されていない。**

### 2-2. プラン表の「配分」列（RECOMMENDATION.md §2.1・行123-135）が4プランで誤り

`RECOMMENDATION.md` の表:
```
| `B_hit` | B | 三連単 | 確率上位を Σ(1/予測) <= 1/3 まで | 〜8 | dutch |
| `C_hit` | C | 三連単 | 予測15倍以上から確率上位 ＋ 帯下最人気1点 | 12 | dutch |
| `D_hit` | D | 三連複 | 軸2車＋相手3点 | 3 | dutch |
| `E_hit` | E | 三連単 | 予測30倍以上から確率上位 | 14 | dutch |
```

現行 `src/type_lab.py`:
```
768: "B_hit": Plan("B_hit", "B", "trifecta", "prob_top", 0, max_legs=8,
769:               sigma_max=1 / 3.0, alloc="conf", floor_mult=MIN_PAYOUT_MULT, ...)
821: "C_hit": Plan("C_hit", "C", "trifecta", "prob_top", 0, min_odds=15.0,
822:               max_legs=12, alloc="conf", floor_mult=MIN_PAYOUT_MULT,
823:               underband_min=5.0, tau_adaptive=True, ...)
836: "D_hit": Plan("D_hit", "D", "trio", "axis2_drop_fav", 3, alloc="conf",
837:               floor_mult=MIN_PAYOUT_MULT, ...)
875: "E_hit": Plan("E_hit", "E", "trifecta", "prob_top", 0, min_odds=30.0,
876:               max_legs=14, alloc="conf", floor_mult=MIN_PAYOUT_MULT,
877:               tau_adaptive=True, tau_floor=35_000, tau_max_legs=20, ...)
```

4プランとも `alloc="conf"` であって `dutch` ではない。`allocate()`（`type_lab.py:1868-1927`）の
定義:
```
'dutch' … 賭け金 ∝ 1/予測オッズ（払戻を全点で揃える）
'conf'  … 各点に floor = 予算×floor_mult ÷ 予測オッズ を置き、残りを確率に比例して配る
```
両者は数学的に別の配分規則（"conf" は全点均一払戻にならない）であり、単純な誤記では済まない
実装差。ドキュメントの「配分」列で真に `dutch` なのは `A_trio` / `A_ana` /
`{型}_sign` / `{型}_big`（`type_lab.py:762,765,939,943`）のみ。**A_hit / F_hit / F_pay は表通り
`conf`で正しい**が、`B_hit`/`C_hit`/`D_hit`/`E_hit` の4行が誤り。

さらに `C_hit` と `E_hit` は点数も表の固定値（12・14）と異なる。`C_hit` は 2026-09-11 に
`tau_adaptive=True` へ変更され「固定12点」から「ゲートが許す最大点数（想定平均払戻2万円を
割らない最大）」へ、`E_hit` は 2026-09-17 に `tau_adaptive=True, tau_floor=35_000,
tau_max_legs=20` へ変更され「固定14点」から「想定平均払戻3.5万円を割らない点数（最大20点）」へ
それぞれ変わっている（`type_lab.py:805-819`「2026-09-11:...」/ `:872-874`「2026-09-17:...」の
コメント参照）。E_hit の変更は git log の直近コミット
`2e0b9690 feat(keirin): 型E の点数を14固定から計画払戻の床(3.5万円)で決める` と時系列が一致する
＝**RECOMMENDATION.mdの最終更新（2026-09-16）より後の変更が未反映**。

### 2-3. 「自信あり」規則の説明が2026-09-19の変更前のまま（RECOMMENDATION.md:25,54）

`RECOMMENDATION.md:25`（段OFF時のフォールバック規則）:
> 「自信あり」は段の規則（固め・決勝系優先・Σp）を使わず、旧規則（発走18時前 ∧ 合成3倍以上 ∧ EV最大）

現行 `src/confident_pick.py:34-56`（同ファイルdocstring、2026-09-19付「ユーザー指示」として明記）:
```
候補 … 発走 JST < 18時（CONFIDENT_BEFORE_HOUR）
     ∧ 合成オッズ >= 2.5 倍（CONFIDENT_MIN_SYNTH_ODDS = 2.5、line 135）
順位 … Σp（legs_hit_probability）が最大
```
docstring内に明記: 「これは 2026-09-02 の決定（合成3倍以上 → EV 最大）を**置き換える**」。
つまり RECOMMENDATION.md:25 が「旧規則」として説明している条件（合成3倍・EV最大）は、
2026-09-19 時点でコード側からは**2世代前**（2026-09-02〜09-18に使われていた版）の説明であり、
現行（2026-09-19〜）の「2.5倍・Σp最大」ではない。`CONFIDENT_MIN_SYNTH_ODDS=2.5`は
`confident_pick.py:135`で確認済み。

なお段ON時の説明（`RECOMMENDATION.md:54`「決勝系を優先し、無ければ発走18時前で確率最大」）は
`tier_confident_score`（`confident_pick.py:342-367`、`TIER_CONFIDENT_PLANS=frozenset({"T_firm"})`、
`CONFIDENT_PRIORITY_KEYWORD="決勝"`）と整合しており、こちらは正しい。**誤りは段OFF時の説明のみ**。

### 2-4. `RANK_7S_AXIS_SUM_MAX` の値がprediction-factors.mdの記載と食い違う

`prediction-factors.md:358-359`（2026-08-03 隊列推定位置A/Bの節）:
```
| `RANK_7S_AXIS_SUM_MAX` = 1.5 | 54.9% | 52.9% | 1.5092（+0.6%）|
```
現行 `src/strategy_wt.py:828`:
```python
RANK_7S_AXIS_SUM_MAX = 1.40
```
`scripts/exp_7a_exclusion_confirm.py:191` のコメントに「A = RANK_7S_AXIS_SUM_MAX # 1.40
（PR#9で採用済み）」とあり、1.40が長期にわたる値であることが示唆される。doc記載の1.5は
現行コードの値と一致しない。**この定数（RANK_7S系列）自体は「旧ランクのペーパー継続」
という現在も生きた検証パス**（`RECOMMENDATION.md:395`が言及）で使われているため、
死んだコードのコメントではない。値の食い違いは実害のある誤記である可能性が高い
（あるいはdoc記載時点で別の変更が既に入っていたのに未反映だった可能性）。

### 2-5. `FEATURE_COLS_WT` の特徴量数（prediction-factors.mdヘッダおよび§2-7）

`prediction-factors.md:4`:
> 本番モデル（winticket）: `lgbm_wt` / **46特徴量**

`prediction-factors.md`§2-7見出し（375行目）:
> 2-7. レース種別・ライン実力（2026-08-04 採用・**48→60特徴**）

現行 `src/preprocessing/feature_wt.py:1269-1349`実測（`python3 -c "from src.preprocessing.feature_wt
import FEATURE_COLS_WT; print(len(FEATURE_COLS_WT))"` → **70**）。

固定テスト:
- `tests/test_feature_prepare.py:57` — `assert len(FEATURE_COLS_WT) == 70`
- `tests/test_rp_trend_wt.py:156` — `assert len(FEATURE_COLS_WT) == 70   # 2026-09-10: 節内成績4列を追加（66→70）`
- `tests/test_sb_dyn_wt.py:124` — 同上

テストのコメントから逆算すると系列は **46 → 48（隊列位置・2026-08-03）→ 60（race_type+line_strength・
2026-08-04）→ 66（line_leader系6特徴・2026-08-19）→ 70（cup_*節内成績4特徴・2026-09-10）**。
`prediction-factors.md`は66到達・70到達の**どちらの変更も一切記載していない**
（ヘッダの「46」は§2-1時点の記載を更新し忘れ、本文最新の「60」も2世代遅れ）。

追加された2グループの実装（未記載であることの裏付け）:
- `line_leader_rp` / `line_leader_rp_gap_top` / `line_leader_rp_rank` / `line_leader_is_weakest` /
  `line_rp_spread` / `line_rp_lead_minus_deputy`（`feature_wt.py:1316-1321`、生成関数
  `add_line_leader_features_wt`は`feature_wt.py:431`）。`FEATURE_COLS_WT`定義の直前コメント
  （`feature_wt.py:1298-1315`）にA/B結果の詳細記述があり、実装として明確に本番投入済み。
- `cup_n_so_far` / `cup_top3_rate` / `cup_win_rate` / `cup_mean_order_n`（`feature_wt.py`927行付近に
  定数リスト、1009行付近に計算ロジック。同ファイル1347行のコメント「節内成績（実装 2026-08-20 /
  配線は 2026-09-10）」）。

CLAUDE.md（keirin側）の「ドキュメント更新ルール」表は「`FEATURE_COLS_WT` に特徴量を追加・削除 →
winticket 特徴量一覧テーブル」の更新を明記しており、このルールが2回連続で守られていない。

### 2-6. 高額枠スロット数の往復（5→10→5）がRECOMMENDATION.mdに未記載（軽微・情報の欠落）

`RECOMMENDATION.md:236-276`（§3.6）は「1日5本まで」で説明し、現行の `HIGHPAY_SLOTS_PER_DAY = 5`
（`type_lab.py:1423`）と数値としては一致する。しかし `type_lab.py:1382-1418`のコメント履歴による
と、2026-09-12に5→10へ拡張され、2026-09-19に「表示的中の代償が見合わない」との理由で10→5へ
再度戻された（git log直近コミット `a263f066 fix(keirin): 高額枠を1日10本から5本へ戻す` と一致）。
最終値は一致するため実害は無いが、直近の実地A/B実測（`実入稿・2026-09-12〜09-18・7日・採点済396件`、
`type_lab.py:1408-1416`）はRECOMMENDATION.md本文に反映されておらず、意思決定の経緯が読み取れない。

### 2-7. 補足：expスクリプト自身のdocstringも数日でstaleになっている（方法論上の留意点）

`scripts/exp_type_lab/axis_gate_scope2.py:8-9`（2026-09-09付コメント）:
> `AXIS_GATE_MIN` … **A_hit / D_hit / E_hit / F_hit の4プランだけ**に下限。

これは2026-09-03時点の状態の説明であり、2026-09-11の変更（8プラン：A_hit/A_trio/B_hit/C_hit/
D_hit/E_hit/F_hit/F_sign、A_ana以外全部）で既に古くなっている。**ドキュメントだけでなく
実験スクリプトのdocstringも同じ速度で陳腐化する**ことの実例であり、依頼主の懸念
（過去の調査結果が誤って積み上がる）を補強する。

---

## 3. 文書間・内部の矛盾

1. **RECOMMENDATION.mdとprediction-factors.mdの想定読者の時間軸のズレ**:
   RECOMMENDATION.mdは「型ラボ」（2026-08-28移行、現行の唯一の販売体系）だけを説明するが、
   prediction-factors.mdの大部分（§「現行ランク体系」相当の記述はCLAUDE.md側にあるが、
   prediction-factors.md自体は旧ランク7S/7A/7B/7C/7H1/9C/9H1等の特徴量的根拠を延々と記録している）
   は**型ラボ移行前の商品体系の話**。両ファイルの「現行」が指す商品体系が異なるため、
   prediction-factors.mdだけを読むと今も7S/7C等が販売中の主力であるかのように誤読しうる
   （実際は`RECOMMENDATION.md:395`が言うとおり「旧ランクのペーパー継続」＝非販売の検証系列）。
   ドキュメント間の「今何が売られているか」の参照が一方向（RECOMMENDATION.md→prediction-factors.md
   への「特徴量の仕様」リンクのみ）で、逆方向（prediction-factors.mdの各ランク記述が現在は非販売
   である旨の注記）がない。

2. **prediction-factors.md内部の特徴量数の自己矛盾**（既出・2-5節）:
   ヘッダ「46特徴量」 vs 本文§2-7見出し「48→60特徴」 vs 実コード「70」。
   同一ファイル内で少なくとも3つの異なる数字が並存し、どれも現在の正解ではない。

3. **prediction-factors.md:16の自己訂正**（「旧記述は『2-2節』と誤記していたため本更新で訂正」）
   は内部矛盾ではなく訂正済み事項だが、**同種の追従漏れが2026-08-19以降にも再発している**こと
   （2-5節参照）から、同じ失敗パターン（追加時にヘッダ更新を忘れる）が繰り返されていると読める。

4. **RANK_7S_AXIS_SUM_MAX の値**（prediction-factors.md:358-359 「1.5」）と、
   CLAUDE.md（keirin側、本監査の直接対象外だが参照関係にある）の「変更時チェックリスト」節にある
   記述「現行本番定数 `RANK_7S_AXIS_SUM_MAX`（`src/strategy_wt.py:329`、値は`1.5`）」も同じ「1.5」を
   主張しており、**2つの文書が同じ誤った値で足並みを揃えて古くなっている**（コードの実測は1.40）。
   これは「ドキュメントを写して別のドキュメントを書いた結果、誤りが伝播した」典型例の可能性が高い。

5. **RECOMMENDATION.md内部**: §3.3（行197-214）の軸信頼ゲート表と、後段§7「決着済み」表
   （行363-378）は矛盾しないが、§3.3の「一連の検証で初めて無作為対照20本に20/20で勝った腕」という
   表現は、2026-09-11の追加検証（さらに対照20/20に勝った上で件数を22-23%削る変更）の存在を
   踏まえると**「初めて」の主張がその後2回も上書きされている**にもかかわらず文言が最初のまま
   残っている。内容が古いだけでなく、「初めて」という強い表現が更新されずに残ることで
   読者に誤った印象（この検証が最終形である）を与えるリスクがある。

---

## 4. スクリプト実在確認・方法論の懸念

### 実在確認

以下はいずれも実在を確認済み（見つからなかったものはゼロ）:
`scripts/exp_type_lab/tier_realized_factor.py`, `docs/type_lab/synthetic_floor_recheck_2026_09_02.md`,
`docs/type_lab/gap_gate_2026_08_28.md`, `docs/type_lab/RUNBOOK.md`,
`docs/type_lab/GO_LIVE_2026_08_28.md`, `docs/type_lab/type_{a..f}.md`, `docs/type_lab/SUMMARY.md`,
`docs/trifecta_playbook.md`, `docs/vintage_model_policy.md`, `docs/netkeirin-input-api-spec.md`,
`docs/system-architecture.md`, `docs/sales_kpi.md`, `docs/highpay_5slots_2026_09_06.md`,
`docs/type_lab/typef_racetype_2026_09_02.md`, `docs/type_lab/band_by_race_2026_09_10.md`,
`docs/type_lab/type_e_2026_09_01.md`, `docs/type_lab/axis_gate_recheck_2026_09_09.md`,
`docs/type_lab/rerun_20months_2026_08_28.md`, `docs/type_lab/highpay_slots_measured_2026_09_12.md`,
`docs/type_lab/PLAN_axis_gate_inventory_2026_09_12.md`, `docs/type_lab/confident_pick_hit_2026_09_19.md`,
`scripts/exp_type_lab/axis_gate_scope2.py`, `scripts/exp_type_lab/typef_racetype.py`,
`scripts/exp_type_lab/type_c_lowband.py`, `scripts/exp_type_lab/big30.py`,
`scripts/fit_race_gate_7c.py`, `scripts/exp_gate7c_walkforward_ab.py`,
`docs/analysis/56-race-selection-meta.md`, `src/race_gate_7c.py`, `src/p3_calibration.py`,
`scripts/fit_p3_calibration.py`。

### 方法論の流し読み所見（実行はしていない・コード読解ベース）

- `scripts/exp_type_lab/tier_realized_factor.py`: 2025年をvintage（train_end 2024-12-31）のOOSと
  明記し、n>=30セルのみ使用、レース単位で最初の1件のみ（重複排除）という設計。目立った欠陥は
  見当たらない。**確定オッズ列（`wtf`）を目的変数側（下振れ係数の算出）として使っており、
  発走前情報として使っているわけではない**ので「確定オッズを発走前情報として使用」の懸念には
  当たらない（この係数は事後にゲート閾値を較正するためのものであり、リアルタイム判定には
  予測オッズのみを使う設計—`type_lab.py`側のTIER_REALIZED_FACTOR適用箇所を合わせて確認済み）。
- `scripts/exp_type_lab/axis_gate_scope2.py`: 探索窓2025-01-01〜12-31 / 確認窓2026-01-01〜08-26と
  明確に分離。「無作為対照20本」「レース単位ブートストラップの95%CI」を明記し、
  本番の`sell_plans_for`・`build_with_gate_fallback`をそのまま呼ぶ設計（母集団の作り方を
  独自に再実装していない）。学習窓=評価窓の混同、生存者バイアスの明白な兆候は確認できなかった。
  ただし本監査は実行していないため、実装の細部（例えば`trio_ok`判定の的確性）までは検証していない。
- 上記2本以外の個別スクリプト（`typef_racetype.py`, `type_c_lowband.py`, `big30.py`, `fit_race_gate_7c.py`,
  `exp_gate7c_walkforward_ab.py`）はファイル存在のみ確認し、中身の流し読みは行っていない
  （効果順位・時間予算の都合で対象を絞った）。追加監査が必要であれば別途依頼されたい。
- `keirin_type_lab_gate.py`自体のdocstring（1-44行）は「探索窓／確認窓の分離」「無作為対照20本」
  「両窓で符号一致」という、このプロジェクトが定めた作法（RECOMMENDATION.md §6）に沿った書き方を
  一貫して守っている。**方法論そのものに明白な欠陥は見当たらない**——問題は主に
  「検証・実装は正しく更新されているのに、上位のまとめ文書（RECOMMENDATION.md /
  prediction-factors.md）の追従が数日〜数週間単位で遅れる」という**ドキュメント同期の運用上の穴**
  である。

---

## 5. 監査範囲外・未実施の事項（申し送り）

- DBの実データ確認（type_lab_picks・netkeirin_submissions等）は行っていない（依頼スコープ外）。
- RECOMMENDATION.md §1.2の荒れ度`s`の加算式、§2.2の`sell_plans_for`分岐順序の完全な行単位トレース、
  cron実際のVPS crontab（07:20/13:05/18:05/09:15等）の現地確認は未実施。
- prediction-factors.md §5更新履歴（約50件）は全件を個別に再検証しておらず、
  2026-08-03/08-04（FEATURE_COLS_WT件数）と2026-09-14〜19（type_lab関連）のみ重点的に照合した。
  他のエントリ（DNF検証・7B新設・7T1新設等）はCLAUDE.md本文の記述と概ね整合していることを
  俯瞰確認したのみで、個別の数値の再計算はしていない。
