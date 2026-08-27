# 厳選AIマスター（netkeirin 予想家 id=614）の逆解析 + 三連単の二軸/板の見直し — 2026-08-26

記録: `keirin/docs/tf_rival614_line_pair_2026_08_26.md` / memory `keirin_tf_line_pair_bonus_2026_08_26`

```bash
python3 fetch_gensen.py 20260101 20260826 0.8   # 生HTMLを ./gensen/{list,detail} へ（並列可・キャッシュ式）
python3 parse_gensen.py                          # -> gensen/parsed.jsonl
../../.venv/bin/python join_gensen.py            # -> gensen/joined.jsonl（wt_entries/wt_odds と結合）
python3 shape.py                                 # 商品の形と公開実績（DB不要）
python3 axes.py                                  # 軸・相手の選び方を自社指標と突き合わせ
```

検証台 `/tmp/honmei_attr.npz`（`scripts/exp_honmei_tf/build_attr.py` が作る）を使うもの:

| スクリプト | 用途 |
|---|---|
| `h2h.py` / `h2h_ci.py` | 同一レース上の直接対決（彼ら vs 自社の各組み立て）＋日次ブートストラップ |
| `racesel.py` / `topdec.py` / `topdec2.py` | 「厳選」に情報があるか（窓分割で否定） |
| `axis2.py` / `axis2_ci.py` / `axis2_pool.py` | 二軸の型の比較（PL / pw / 最強ライン先頭→番手） |
| `mech.py` | ライン型が勝つ機序（PL が食い違うとき何を選ぶか） |
| `pairlambda.py` / `lam_ci.py` | 順序対への同ライン隣接ボーナス λ の掃引と CI |
| `boardlam.py` / `boardlam_ci.py` | **210点の板へ λ/μ を入れる（本命の結論）**・四半期別 CI |
| `t1lam.py` | 7T1 の組み立てに λ を入れた場合（効きにくいことの確認） |
| `sweep.py` / `product.py` / `final.py` | 運用点メニュー（母集団 × 帯 × 点数 × 日次上限） |

- **レース確定後の予想だけが無料で読める**（未確定は「この予想は未購入です」）＝事後分析専用
- 詳細ページに「予想の転載はお控えください」。**内部分析限定**とし買い目を再配信しない
- ⚠️ 予測オッズ `odds_tf_n7` の train_end は 2025-12-31。**オッズを使う数字は 2026 窓だけを読む**
