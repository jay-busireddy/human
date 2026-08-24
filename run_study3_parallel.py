import argparse,os,sys,subprocess,shutil,time
from pathlib import Path
import pandas as pd
from hc_tests.common import ROOT,RESULTS,append_csv,seeds_for

PY=sys.executable;BC=ROOT/'blockchain'
def lane_env(lane,mode,seeds):
    e=os.environ.copy();e['HC_LANE']=str(lane);e['HC_CHAIN_DIR']=str((BC/f'generated_lane{lane}').resolve());e['HC_RPC_BASE']=str(8545+(lane-1)*100);e['HC_P2P_BASE']=str(30303+(lane-1)*100);e['HC_CHAIN_ID']=str(1336+lane);e['HC_SEEDS']=','.join(map(str,seeds));e['HC_RESULTS_DIR']=str((RESULTS/f'_study3_lane{lane}').resolve());e['HC_BESU_OPTS']=e.get('HC_BESU_OPTS_PARALLEL','-Xms96m -Xmx256m');e['HC_BESU_START_TIMEOUT']=e.get('HC_BESU_START_TIMEOUT_PARALLEL','180');e['HC_BESU_START_RETRIES']=e.get('HC_BESU_START_RETRIES','2');return e

def call(args,e,check=True):
    print('+',' '.join(map(str,args)),flush=True);return subprocess.run(args,cwd=ROOT,env=e,check=check)
def prepare(e,fresh):
    if fresh:
        call([PY,str(BC/'manage_qbft.py'),'stop'],e,False)
        d=Path(e['HC_CHAIN_DIR']);shutil.rmtree(d,ignore_errors=True)
        # A fresh chain invalidates Study3 per-lane checkpoints.  Clear only
        # this lane's temporary Study3 output so stale seeds are never skipped
        # against a newly generated contract/network.
        shutil.rmtree(Path(e['HC_RESULTS_DIR']),ignore_errors=True)
        call([PY,str(BC/'setup_qbft.py'),'--generate'],e)
        call([PY,str(BC/'fix_bootnodes_newline.py')],e)
    call([PY,str(BC/'manage_qbft.py'),'start'],e)
    # RPC readiness is not consensus readiness. Before deployment, prove that
    # all 7 validators are reachable, the validator set is correct, block
    # height advances, and all nodes converge. Repair via coordinated lane
    # restart if a fresh Windows QBFT lane is alive-but-stalled.
    call([PY,str(BC/'qbft_health.py'),'--timeout',e.get('HC_QBFT_HEALTH_TIMEOUT','90'),
          '--repair-restarts',e.get('HC_QBFT_HEALTH_RESTARTS','2')],e)
    dep=Path(e['HC_CHAIN_DIR'])/'deployed.json'
    if fresh or not dep.exists():
        call([PY,str(BC/'deploy_contract.py')],e)
    # Prove that a normal contract transaction is selected into a block after
    # startup/restart, not merely that empty QBFT blocks advance.
    call([PY,str(BC/'tx_canary.py')],e)
def merge_lane(e):
    d=Path(e['HC_RESULTS_DIR'])
    for name in ['primary_seed_metrics.csv','study3_trajectories.csv']:
        p=d/name
        if p.exists():append_csv(RESULTS/name,pd.read_csv(p).to_dict('records'))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['smoke','confirmatory'],default='smoke');ap.add_argument('--lanes',type=int,default=2,choices=[1,2]);ap.add_argument('--fresh',action='store_true');ap.add_argument('--hypotheses',nargs='*',default=None);ap.add_argument('--lane-stagger',type=float,default=8.0);ap.add_argument('--prepare-only',action='store_true');ap.add_argument('--keep-running',action='store_true',help='with --prepare-only, leave healthy validators running for the next command');a=ap.parse_args();allseeds=seeds_for(3,a.mode);chunks=[allseeds[i::a.lanes] for i in range(a.lanes)];envs=[lane_env(i+1,a.mode,chunks[i]) for i in range(a.lanes)]
    groups=[['H19','H20','H21','H23','H17'],['H18'],['H22'],['H24']];
    if a.hypotheses:
        wanted=set(a.hypotheses);groups=[[h for h in g if h in wanted] for g in groups];groups=[g for g in groups if g]
    prepared_ok=False
    try:
        # Preparation is inside the cleanup scope. If setup, health-gating, or
        # deployment fails, both lanes are still stopped before this process exits.
        for e in envs:prepare(e,a.fresh)
        prepared_ok=True
        if a.prepare_only:
            print('PREPARE_ONLY PASS: all requested lanes proved QBFT consensus liveness, contract deployment, and tx canary.', flush=True)
            if a.keep_running:
                print('KEEP_RUNNING: validators remain running for the next Study3 command.', flush=True)
            return
        for hs in groups:
            ps=[]
            for idx,e in enumerate(envs):
                if idx and a.lane_stagger>0:
                    print(f"Staggering lane {e['HC_LANE']} by {a.lane_stagger:.1f}s to avoid simultaneous Besu JVM recovery starts",flush=True);time.sleep(a.lane_stagger)
                cmd=[PY,str(ROOT/'run_tests.py'),'--study','3','--mode',a.mode,'--real-besu','--hypotheses',*hs];print('+',' '.join(cmd),'lane',e['HC_LANE'],flush=True);ps.append(subprocess.Popen(cmd,cwd=ROOT,env=e))
            rc=[p.wait() for p in ps]
            if any(rc):raise SystemExit(f'One or more Study3 lanes failed for {hs}: rc={rc}')
        for e in envs:merge_lane(e)
        print('Merged Study3 lane results into',RESULTS)
    finally:
        # A successful prepare-only may intentionally keep both lanes alive so
        # the next smoke/confirmatory command does not pay a second 14-JVM
        # startup cost. Failures always clean up. Normal test runs also clean up.
        leave_up=bool(a.prepare_only and a.keep_running and prepared_ok)
        if not leave_up:
            for e in envs:call([PY,str(BC/'manage_qbft.py'),'stop'],e,False)
if __name__=='__main__':main()
