import sys, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv; load_dotenv(__import__('pathlib').Path(__file__).resolve().parents[3]/'.env')
import psycopg2
from collections import defaultdict, Counter
from src.indices.confidence import calculate_race_confidence, CHIHOU_GAP_FULL_SCORE, CHIHOU_DISPERSION_FULL_SCORE
c=psycopg2.connect(host=os.environ['DB_HOST'],port=os.environ['DB_PORT'],dbname=os.environ['DB_NAME'],
                   user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'])
cur=c.cursor()
cur.execute("""select r.date, r.race_number, ci.composite_index, ci.win_probability, r.head_count
               from chihou.calculated_indices ci join chihou.races r on r.id=ci.race_id
               where r.course='44' and ci.version=14 and r.date between '20260601' and '20260831'""")
g=defaultdict(list)
for d,rn,comp,wp,hc in cur.fetchall(): g[(d,rn)].append((comp,wp,hc))
per_date=defaultdict(Counter)
for (d,rn),rows in g.items():
    comps=[x[0] for x in rows]; wps=[x[1] for x in rows]; hc=rows[0][2] or len(rows)
    r=calculate_race_confidence(comps,hc,wps,gap_full_score=CHIHOU_GAP_FULL_SCORE,
                                dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE)
    per_date[d][r['rank']]+=1
print(f"{'date':>10} {'n':>3}  {'S':>3} {'A':>3} {'B':>3} {'C':>3}   S+A%")
for d in sorted(per_date):
    cn=per_date[d]; n=sum(cn.values())
    print(f"{d:>10} {n:>3}  {cn['S']:>3} {cn['A']:>3} {cn['B']:>3} {cn['C']:>3}   {100*(cn['S']+cn['A'])/n:5.1f}%")
