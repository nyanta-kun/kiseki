#!/usr/bin/env python3
"""05-1 可用性: 各波の入稿時点で板がどれだけ埋まっているか（2026-06-08〜08-25・板7車）。

入稿の波（scripts/type_lab_daily.sh / type_lab_wave.sh の cron）:
  morning 07:20 / noon 13:00 / evening 18:00
その時点で存在しうるスナップショット:
  morning→ 'morning'(07:0x・snapshot_at<07:20 のものだけ) / noon→ 'h12' / evening→ 'h14'（'h18' は 18:00:01〜 で同時刻＝境界）
"""
import os, sys, numpy as np
D = os.path.dirname(os.path.abspath(__file__))
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
m = np.load(os.path.join(D, "market.npz"), allow_pickle=True)
IDX2 = m["IDX2"]; SNAP = m["SNAP"]; AT = m["SNAP_AT"]; TF = m["TF_FILL"]; ST = list(m["SNAP_TYPES"])
WAVE = m["WAVE"][IDX2]; DATE = z["DATE"][IDX2]; TYPE = z["TYPE"][IDX2]; OK = z["OKPRED"][IDX2]
START = m["START"][IDX2]
ok = (TYPE != "") & OK & (z["TRIO_WIN"][IDX2] >= 0)
print(f"対象 {ok.sum()}R / {len(IDX2)}  {min(DATE)}〜{max(DATE)}  日数 {len(set(DATE[ok]))}")
fill = np.isfinite(SNAP).sum(2)                     # (n2,T) 三連複の確定点数
have = AT != ""
def row(mask, j, label):
    n = mask.sum()
    if n == 0:
        print(f"    {label:28s} n=0"); return
    h = have[mask, j]
    f = fill[mask, j].astype(float); f[~h] = 0
    tf = TF[mask, j].astype(float); tf[~h] = 0; tf[tf < 0] = 0
    print(f"    {label:28s} n={n:5d} スナップ有り {h.mean()*100:5.1f}%  三連複 平均{f.mean()/35*100:5.1f}% "
          f"全35点 {np.mean(f==35)*100:5.1f}% 半分未満 {np.mean(f<18)*100:5.1f}% 0点 {np.mean(f==0)*100:5.1f}%"
          f"  三連単 平均{tf.mean()/210*100:5.1f}% 半分未満 {np.mean(tf<105)*100:5.1f}%")
print("\n== A. 現行運用（全レースを朝 07:20 で組む）: 'morning' スナップ(07:0x) の充足 ==")
jm = ST.index("morning")
early = np.array([a != "" and a < "07:20:00" for a in AT[:, jm]])
print(f"  morning スナップの取得時刻 <07:20 の割合: {early[have[:, jm]].mean()*100:.1f}%（それ以外は入稿後に撮られた＝使えない）")
for w in ("morning", "noon", "night"):
    mk = ok & (WAVE == w) & early
    row(mk, jm, f"波={w} (morningスナップ<07:20)")
print("\n== B. 波を遅らせた場合: その波の入稿時点で使えるスナップ ==")
for w, st in (("morning", "morning"), ("noon", "h10"), ("noon", "h12"), ("night", "h12"), ("night", "h14"), ("night", "h18")):
    j = ST.index(st)
    mk = ok & (WAVE == w)
    if st == "morning":
        mk &= early
    row(mk, j, f"波={w} スナップ={st}")
print("\n== C. 参考: 発走からの時間で見た板の育ち（三連複 確定点数の平均％・全波）==")
import collections
buckets = collections.defaultdict(list)
for r in range(len(IDX2)):
    if not ok[r]: continue
    for j, st in enumerate(ST):
        if not have[r, j]: continue
        hh, mm, ss = AT[r, j].split(":");
        d = DATE[r]
        import datetime as dt
        t = dt.datetime.strptime(d + " " + AT[r, j], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
        hrs = (START[r] - t.timestamp()) / 3600
        b = min(int(hrs // 1), 14) if hrs >= 0 else -1
        buckets[b].append(fill[r, j] / 35)
for b in sorted(buckets):
    v = buckets[b]
    print(f"    発走まで {b:3d}〜{b+1:3d}h : n={len(v):6d} 三連複充足 {np.mean(v)*100:5.1f}%  全点 {np.mean(np.array(v)==1)*100:5.1f}%")
