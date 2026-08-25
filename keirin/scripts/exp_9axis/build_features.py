import sys, time
sys.path.insert(0,'.')
from src.preprocessing.feature_wt import build_features_wt, load_raw_data_wt, FEATURE_COLS_WT
t=time.time(); raw=load_raw_data_wt(min_date='2022-12-01', max_date='2026-08-04')
print('load %.1fs rows=%d' % (time.time()-t, len(raw)), flush=True)
t=time.time(); df=build_features_wt(raw)
print('feat %.1fs shape=%s ncols=%d' % (time.time()-t, df.shape, len(FEATURE_COLS_WT)), flush=True)
df.to_pickle('/tmp/feat_all.pkl'); print('saved', flush=True)
