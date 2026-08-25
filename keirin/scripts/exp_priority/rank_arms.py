#!/usr/bin/env python3
"""商品の優先順位を「期待値で並べる」ことができるか（2026-08-25・ユーザー提起）。

## 問い

> 硬くても当たる推奨と、波乱期待で買い続ける推奨は単純な優先順位ではなく、
> 期待値で並べないといけないかもしれません。

現行は `netkeirin_submit_wt.RANK_CONFIGS` の**定義順**が優先順位で、
1レース1商品なので先に来たランクが取る。これを EV 順に替えられるか。

## 測り方

- 母集団: `picks_history` に**2ランク以上**の候補が立ったレース（三連複ランク
  7S/7B/7C/7M1・2026-06-01〜08-24）。買い目は記録どおり
- EV = Σ(PL同時確率 × 予測オッズ × 賭け金) ÷ 予算。配分は本番の
  `tilted_stakes`（1/予測オッズ）、採点は確定配当（`picks_history.trio_payout`）
- ⚠️ **オッズモデルは現行版**（2026 は in-sample）。絶対水準ではなく
  「どの腕が上か」という**向き**だけを読む

## 🔴🔴 結果: EV では並べられない（n=523）

    腕                    的中     ROI    2倍+   倍率中央
    現行（定義順）        38.0%   75.5%   14.3%   1.78
    EV が最大のもの       29.8%   76.0%   14.5%   1.97
    EV が最小のもの       29.4%   75.5%   14.9%   2.05
    無作為（seed 1本）    30.2%   81.0%   15.5%   2.04

  **EV 最大と EV 最小が同じ結果**（ROI 76.0 ↔ 75.5 / 的中 29.8 ↔ 29.4）。
  無作為選択を 200 seed で回すと ROI は **中央 76.1% / 90%区間 [68.8%, 82.3%]**
  ——13.5pt の幅がある。現行も EV 最大も EV 最小も**この帯のど真ん中**で、
  n=523 では選び方の違いを ROI で判別できない
  （上表の無作為 81.0% は帯の中の1本を引いただけ。**単独では読まないこと**）。

## 🔴 なぜ効かないか: 全商品の EV がほぼ同じ

    ランク   n     平均EV   中央EV
    7S     390    0.871    0.860
    7B     138    0.996    0.941
    7C     106    0.979    0.900
    7M1    412    0.882    0.777

  0.87〜1.00 に収まる。**市場が全商品を同じところ（控除率の壁 74.85%）へ
  値付けしている**ので、EV には並べ替える情報が残っていない。
  7S vs 7M1 だけを見ると平均 EV は **0.870 ↔ 0.876** で、
  EV で 7S が勝つのは **52.1%** ＝ ほぼコイン投げ。

## ✅ 動くのは的中率と配当の交換だけ

  現行の定義順は的中率 38.0% を出しており、他のどの並べ方より **8pt 高い**。
  これは偶然ではなく設計どおり（7S > 7M1 は表示的中 +20.3pt を根拠に
  2026-08-19 に決めた）。EV 順に替えると**的中率を 8pt 落として ROI は動かない**。

  → 優先順位は **EV の問題ではなく、1日の商品ミックス（的中頻度 × 配当規模 ×
    投資総額）の問題**。設計の論点は `docs/rank_priority_redesign_2026_08_25.md`。

DB は読み取りのみ。
"""
import os, re, statistics, random, psycopg2, psycopg2.extras, sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from src.odds_prediction import load_race_inputs, predict_board, _pl_trio
from src.stake_allocation import tilted_stakes
COMBO = re.compile(r"^\s*(\d+)\s*=\s*(\d+)\s*-\s*([\d,]+)")
ORDER = ["7S","7B","7C","7M1"]
c = psycopg2.connect(os.environ['KEIRIN_DB_URL'])
cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute("""SELECT split_part(race_key,'#',1) rk, replace(rank,'RANK_','') rank_key,
   pred_combo, trio_payout FROM keirin.picks_history
 WHERE race_date BETWEEN '2026-06-01' AND '2026-08-24' AND route='wt'
   AND rank IN ('RANK_7S','RANK_7B','RANK_7C','RANK_7M1') AND pred_combo IS NOT NULL""")
byrace = {}
for r in cur.fetchall():
    m = COMBO.match(r['pred_combo'] or "")
    if m:
        byrace.setdefault(r['rk'], {})[r['rank_key']] = (
            int(m.group(1)), int(m.group(2)), [int(x) for x in m.group(3).split(",")], r['trio_payout'] or 0)
cont = {k:v for k,v in byrace.items() if len(v) >= 2}
cur.execute("SELECT race_key, frame_no, finish_order FROM keirin.wt_entries WHERE finish_order BETWEEN 1 AND 3 AND race_key = ANY(%s)",(list(cont),))
fin = {}
for r in cur.fetchall(): fin.setdefault(r['race_key'],[]).append((r['finish_order'],r['frame_no']))
rows=[]
for i,(rk,d) in enumerate(cont.items()):
    f=sorted(fin.get(rk,[]))
    if len(f)!=3: continue
    try:
        cars,mp3,pw,meta=load_race_inputs(rk); board=predict_board(cars,mp3,pw,meta); pl=_pl_trio(pw,cars)
    except Exception: continue
    p3={k:float(v) for k,v in mp3.items()}; win={x for _,x in f}; got={}
    for name,(a1,a2,legs,trio) in d.items():
        odds,prob={},{}; ok=True
        for t in legs:
            k=frozenset({a1,a2,t})
            if k not in board or not board[k] or k not in pl: ok=False;break
            odds[t],prob[t]=float(board[k]),float(pl[k])
        if not ok or not legs: continue
        st,_=tilted_stakes(legs,None,p3,budget=10000,unit=100,predicted_odds=odds)
        got[name]=(sum(prob[t]*odds[t]*st[t] for t in legs)/10000,
                   next((trio*st[t]//100 for t in legs if {a1,a2,t}==win),0))
    if len(got)>=2: rows.append(got)
    if i%150==0: print(f"  ...{i}/{len(cont)}",flush=True)
import json
Path=__import__("pathlib").Path
Path("/tmp/rank_arms_rows.json").write_text(json.dumps(rows))

def rep(label,pick):
    pays=[pick(g)[1] for g in rows]; hits=[p for p in pays if p>0]
    print(f"  {label:<22}n={len(pays):>4}  的中 {len(hits)/len(pays):>5.1%}  "
          f"ROI {sum(pays)/(len(pays)*10000):>6.1%}  2倍+ {sum(1 for p in hits if p>=20000)/len(pays):>5.1%}  "
          f"倍率中央 {statistics.median([p/10000 for p in hits]) if hits else 0:.2f}")
print(f"\n三連複ランクが2つ以上競合したレース {len(rows)}本（2026-06-01〜08-24）")
rep("現行（定義順）",lambda g:g[next(r for r in ORDER if r in g)])
rep("EV が最大のもの",lambda g:g[max(g,key=lambda k:g[k][0])])
rep("EV が最小のもの",lambda g:g[min(g,key=lambda k:g[k][0])])
rng=random.Random(1); rep("無作為",lambda g:g[rng.choice(sorted(g))])
print("\n  ランク別の平均EV（競合レースのみ）")
for name in ORDER:
    evs=[g[name][0] for g in rows if name in g]
    if evs: print(f"    {name:<5} n={len(evs):>4}  平均EV {statistics.mean(evs):.3f}  中央 {statistics.median(evs):.3f}")
