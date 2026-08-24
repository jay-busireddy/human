import sys,pandas as pd
from pathlib import Path
from hc_tests.common import RESULTS,append_csv
src=Path(sys.argv[1]) if len(sys.argv)>1 else None
if not src:raise SystemExit('usage: python import_study1_results.py C:\\path\\to\\old\\results')
for name in ['primary_seed_metrics.csv','study1_trajectories.csv']:
 p=src/name
 if not p.exists():continue
 d=pd.read_csv(p);d=d[d.study_id==1] if 'study_id' in d.columns else d
 if 'seed' in d.columns:d=d[(d.seed>=1000)&(d.seed<=1059)]
 append_csv(RESULTS/name,d.to_dict('records'));print('Imported',len(d),name)
