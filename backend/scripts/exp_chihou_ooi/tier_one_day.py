import sys, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv; load_dotenv(__import__('pathlib').Path(__file__).resolve().parents[3]/'.env')
import psycopg2
from src.indices.confidence import calculate_race_confidence, CHIHOU_GAP_FULL_SCORE, CHIHOU_DISPERSION_FULL_SCORE
c=psycopg2.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=os.environ['DB_NAME'],
                   user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'])
cur=c.cursor()
cur.execute("""select r.race_number, ci.composite_index, ci.win_probability, r.head_count,
                      (ci.speed_index=50.0 and ci.last3f_index=50.0)::int dead
               from chihou.calculated_indices ci join chihou.races r on r.id=ci.race_id
               where r.date='20260831' and r.course='44' and ci.version=14
               order by r.race_number""")
from collections import defaultdict
g=defaultdict(list)
for rn,comp,wp,hc,dead in cur.fetchall(): g[rn].append((comp,wp,hc,dead))
print(f"{'R':>3} {'頭数':>4} {'指数なし馬':>10} {'tier':>5} {'score':>5} {'gap1-2':>7}")
for rn in sorted(g):
    rows=g[rn]
    comps=[x[0] for x in rows]; wps=[x[1] for x in rows]
    hc=rows[0][2] or len(rows); deadf=sum(x[3] for x in rows)/len(rows)
    r=calculate_race_confidence(comps, hc, wps,
        gap_full_score=CHIHOU_GAP_FULL_SCORE, dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE)
    print(f"{rn:>3} {hc:>4} {100*deadf:>9.0f}% {r['rank']:>5} {r['score']:>5} {r['gap_1_2']:>7.2f}")
