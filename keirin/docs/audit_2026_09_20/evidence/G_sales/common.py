import pandas as pd, numpy as np, sys
sys.path.insert(0,'/Users/ysuzuki/GitHub/kiseki/backend/src')
from services.keirin_marquee import is_marquee_race, is_big_event_race, is_fill_target
from api.keirin_meeting import meeting_type_of_first_hour

def load():
    d = pd.read_csv('races.csv', dtype={'race_id':str,'race_date':str,'venue_code':str})
    d['date'] = pd.to_datetime(d['race_date'], format='%Y%m%d')
    d['dow'] = d['date'].dt.dayofweek
    d['is_marquee'] = d['race_type'].apply(lambda x: is_marquee_race(x if isinstance(x,str) else None))
    d['is_bigevent'] = d['race_type'].apply(lambda x: is_big_event_race(x if isinstance(x,str) else None))
    d['fill_target'] = [is_fill_target(rt if isinstance(rt,str) else None, cg if pd.notna(cg) else None)
                        for rt,cg in zip(d['race_type'], d['cup_grade'])]
    d['meeting'] = d['first_hour'].apply(lambda h: meeting_type_of_first_hour(h) if pd.notna(h) else None)
    d['start_hour'] = ((d['start_at'].astype('Int64').astype('float') + 9*3600) % 86400)/3600.0
    d['paid'] = d['sold_paid_points'].astype(float)
    d['nsold'] = d['n_sold'].astype(float)
    d['hit'] = (d['n_hits_incl_garami']>0).astype(int)
    d['hit_excl'] = (d['n_hits_excl_garami']>0).astype(int)
    d['payout'] = d['payout_amount'].astype(float)
    d['stake'] = d['stake_amount'].astype(float)
    d['cup_grade'] = d['cup_grade'].fillna(-1).astype(int)
    return d

def rtg(rt):
    if not isinstance(rt,str): return 'other'
    if '準決勝' in rt: return 'semifinal'
    if '決勝' in rt: return 'final'
    if '特選' in rt or '選抜' in rt or '特秀' in rt or '優秀' in rt: return 'tokusen'
    if '予選' in rt: return 'yosen'
    if '一般' in rt: return 'ippan'
    return 'other'

def load2():
    d = load()
    d['rtg'] = d['race_type'].apply(rtg)
    d['lpaid'] = np.log1p(d['paid'])
    d['pub'] = pd.to_datetime(d['published_at'], format='mixed', errors='coerce')
    d['start_jst'] = pd.to_datetime(pd.to_numeric(d['start_at'],errors='coerce'),unit='s',errors='coerce')+pd.Timedelta(hours=9)
    lm = (d['start_jst']-d['pub']).dt.total_seconds()/60
    d['lead_min'] = lm.where(lm.between(0,1440))
    d['cg'] = d['cup_grade'].astype(str)
    d['sold_any'] = (d['nsold']>0).astype(float)
    # 🔴 プラン単位の列は (race_key, plan_key=rank_key) で結合し直す
    tl = pd.read_csv('tl.csv')
    tl = tl.rename(columns={'plan_key':'rank_key'})
    for c in ['type_label','plan_key','bet_type','n_legs','budget','pred_mean_payout','axis_sum','gap']:
        if c in d.columns: d = d.drop(columns=[c])
    d = d.merge(tl[['race_key','rank_key','type_label','n_legs','budget','pred_mean_payout','pred_min_payout','axis_sum','gap','bet_type','tl_hit','final_odds']],
                on=['race_key','rank_key'], how='left')
    d['is_conf'] = (d['is_confident'].astype(str).str.lower().isin(['t','true'])).astype(float)
    return d
