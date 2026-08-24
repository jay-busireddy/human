from pathlib import Path
import argparse,pandas as pd
from hc_tests.common import load_config

ap=argparse.ArgumentParser()
ap.add_argument('results',nargs='?',default='results_confirmatory')
ap.add_argument('--mode',choices=['smoke','confirmatory'],default='confirmatory')
a=ap.parse_args()
root=Path(a.results)
p=root/'primary_seed_metrics.csv'
assert p.exists(),f'primary_seed_metrics.csv missing: {p}'
df=pd.read_csv(p)
req={'study_id','hypothesis_id','seed','arm','primary_metric','primary_value','run_valid'}
assert req.issubset(df.columns),f'missing columns {req-set(df.columns)}'
cfg=load_config(); errors=[]
for hnum in range(1,25):
    h=f'H{hnum}'; study=1 if hnum<=8 else 2 if hnum<=16 else 3
    if a.mode=='smoke':
        n=cfg['seed_counts']['smoke'];start=cfg['smoke_seed_starts'][f'study{study}']
    else:
        n=cfg['seed_counts'][f'study{study}_confirmatory'];start=cfg['seed_starts'][f'study{study}']
    expected=set(range(start,start+n))
    got=set(df.loc[df.hypothesis_id==h,'seed'].dropna().astype(int).unique())
    missing=sorted(expected-got); extra=sorted(got-expected)
    if missing or extra:
        errors.append(f'{h}: expected {n} seeds {start}-{start+n-1}; got {len(got)}; missing={missing[:8]} extra={extra[:8]}')
    print(f'{h}: {len(got)}/{n} seeds')
if errors:
    print('\nVALIDATION FAILED')
    for e in errors:print(' -',e)
    raise SystemExit(2)
print('\nValidation OK: all H1-H24 have the expected seed set for',a.mode)
