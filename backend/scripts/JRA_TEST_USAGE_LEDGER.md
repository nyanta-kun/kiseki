# JRA TEST_START 使用履歴台帳

TEST_START（当四半期の初日・四半期ローリング）以降のデータを採否判断に使った記録。
同一期間を条件探索に使い回さないための追跡台帳。

定義は `backend/src/jra_protocol.py`。TEST 期間を使ったら
`jra_protocol.record_test_usage()` を呼んでここに 1 行追記すること。

---

## プロトコル制定（2026-08-14）以前に既に使われていた窓

中央には 2026-08-14 までプロトコルも台帳も無く、**2026 年の窓は少なくとも
以下 6 件の採否判断に使い回されている**。ここから得た結論は
「有意な改善」として扱わないこと（`jra_protocol.BURNED_DECISIONS` と同内容）。

| 判断 | 使ったもの |
|---|---|
| v27 合成係数 `V27_OUT_WEIGHT=0.5` の選定 | `composite.py`（「honest 2窓で比較」） |
| 着外率の足切り閾値 `OUT_PROB_CUTOFF=0.80` の選定 | `train_jra_out_rate.py` |
| v26→v27 の目的関数変更（LambdaRank → 順位回帰）の採否 | `train_jra_reg_rank.py` |
| ランキング品質の再設計提案 | `jra_rank_redesign_proposal.py` |
| `recommend_rank` の `market_agree` 第一分岐化 | `confidence.py` |
| train/serve 不整合の監査（DM・馬場・馬体重） | `jra_train_serve_skew_audit.py` |

⚠️ **さらに、2026-08-14 時点の本番モデルは全期間 refit** で作られており、
上記の評価はいずれも厳密には in-sample を含む。
refit 境界を `TEST_START` の前日に変えたのは同日（`docs/jra_rebuild_2026_08.md` 13章）。
**最初の真に honest な一度きり評価は 2026Q4 のローリング（2027-01 実行）になる。**

---

## 使用履歴
- 2026-08-15 `TEST_START=20260701` **scripts/anagusa_top3_walkforward.py**: 穴ぐさ×指数3位以内の優位性検証（walk-forward honest 再構築） — 2026Q3 を評価窓の1つとして使用。結論は 2026Q3 に依存しない（n=23）
- 2026-08-16 `TEST_START=20260701` **scripts/jra_thin_career_head_walkforward.py**: キャリア0-2走専用の残差補正ヘッドの A/B。2026Q3 を 11 窓のうち 1 つとして使用。**増分ゼロ＝不採用**。あわせて調教腕（既存 `ck_preds`）をキャリア帯で事後分割し、キャリア0-2走×1-4番人気で +1.31pt [+0.15, +2.46]。⚠️ 事後分割かつ多重比較なので **2026Q4 での確認を必須**とする
- 2026-08-16 `TEST_START=20260701` **scripts/jra_pedigree_thin_career_walkforward.py**: 血統特徴（父・母の産駒成績）をキャリア0-2走で A/B。2026Q3 を 11 窓のうち 1 つとして使用。**全セグメント非有意（最大 +0.44pt）＝5代化に進まない**
- 2026-08-16 `TEST_START=20260701` **scripts/jra_chokyo_peer_walkforward.py**: 調教のレース内他馬比較の A/B。評価窓 4 四半期のうち 2026Q3 が 1 つ。**増分は全帯で負＝不採用**
- 2026-08-16 `TEST_START=20260701` **scripts/jra_race_level_walkforward.py**: レース単位の「本命崩れ」特徴の A/B。評価窓 4 四半期のうち 2026Q3 が 1 つ。**増分ゼロ（race − ck_x が全帯で 0 を跨ぐ）＝不採用**
- 2026-08-16 `TEST_START=20260701` **scripts/jra_relative_walkforward.py**: 特徴量のレース内相対化（rel_add / rel_only / frame）の A/B。2026Q3 を 11 窓のうち 1 つとして使用。**全腕が base と有意差なし（点推定はむしろ負）＝不採用**。採用しないので TEST の消費としては軽いが、同じ設計を再評価するなら別窓を使うこと
- 2026-08-16 `TEST_START=20260701` **scripts/jra_chokyo_walkforward.py**: 調教（坂路）を期待水準との交互作用として入れる A/B（base / ck / ck_x）。⚠️ **採否に直結する評価**で、評価窓 4 四半期のうち 2026Q3 が 1 つを占める（他は 2025Q4・2026Q1・2026Q2）。結果は 1-4番人気で ck_x−base +1.22pt [+0.30, +2.18]。**本番採用を決める前に、次の独立窓（2026Q4）での再現確認を必須とする**
- 2026-08-16 `TEST_START=20260701` **scripts/jra_darkhorse_walkforward.py**: 人気帯別の識別力の honest ベースライン測定（11四半期の walk-forward） — 2026Q3 を 11 窓のうち 1 つとして使用。**採否判断ではなく現状測定**であり、2026Q3 を除いても結論（人気が下がるほど層化リフトが単調に消える）は変わらない。四半期別内訳を必ず出力するので確認可能
- 2026-08-22 `TEST_START=20260701` **scripts/train_jra_iswin_head.py**: is_win 較正ヘッドの全期間refit修正（TRAIN_DATA_END で切る）を採用。旧/新モデルを honest test で比較 — 489R: ECE 0.0052→0.0065（差は n=489 のノイズ域）/ Brier 0.06362→0.06341 / 本命実測勝率 0.256→0.284。精度差は非有意で、目的は評価窓の汚染除去
- 2026-08-23 `TEST_START=20260701` **scratchpad/anaba（券種間整合性の探索）**: 確定オッズがある 3,450R で、エキゾチック各プールから逆算した「3着以内」marginal と単勝プール由来のものの食い違い（D）が穴馬の複勝を当てるかを測定。2026Q3 を評価窓の一部として使用（探索 2025Q3-Q4 / 検証 2026Q1-Q2 / 確認 2026Q3、および学習 2025Q3-2026Q1 / 評価 2026Q2-Q3 の2通り）。⚠️ **閾値を分位表を見てから選んでいるため、これは探索であって確認ではない**。採否は決めていない。結論の確定は `docs/jra_exotic_ev_preregistration_2026_08_23.md` の事前登録に従い、odds backfill 後の母集団（約9,264R）で別途行う
- 2026-09-04 `TEST_START=20260701` **scripts/jra_winplace_final_confirm.py**: 単勝確率の構造見直し（`feat` 特徴）と複勝の独立ヘッド化（Σ=place_slots）の 2026Q3 最終確認（事前登録 §15） — prod（現行34特徴→Harville）vs new（feat特徴 + 独立 is_placed ヘッド）を 648R / 8443頭（20260704〜20260830）で比較。単勝 多項対数損失 Δ=-0.00356 [-0.01358, +0.00707] / 複勝 place_ll(slots=3) Δ=-0.01184 [-0.01515, -0.00832]。**確認成功**
- 2026-09-06 `TEST_START=20260701` **scripts/heihachi/（分析のみ・スクリプトは未コミット）**: 平八バッジのしきい値の頑健性評価（2026Q3 を一度きり評価として開封） — VAL 2窓(25H2/26H1)では候補の30〜47%が複勝ROI>1を満たし選別力が無かったため TEST を開封。結果、現行既定(OP+/rk3/15-40/pp.40)は TEST n=9・複0.19、旧既定(10-40/pp.30)は n=64・複0.77、最も広い候補(全レース/rk3/10-50/pp.30)でも n=262・複0.98 と、全候補が分岐点未満。3着内率の分離(29.8% vs ベースライン22.8%)は TEST でも残る。以後このしきい値選定に 2026Q3 を使い回さないこと。
