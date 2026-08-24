import argparse,os,sys,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent;PY=sys.executable

def env(l):
 e=os.environ.copy();e['HC_LANE']=str(l);e['HC_CHAIN_DIR']=str((ROOT/'blockchain'/f'generated_lane{l}').resolve());e['HC_RPC_BASE']=str(8545+(l-1)*100);e['HC_P2P_BASE']=str(30303+(l-1)*100);e['HC_CHAIN_ID']=str(1336+l);e['HC_BESU_OPTS']=e.get('HC_BESU_OPTS_PARALLEL','-Xms96m -Xmx256m');return e

def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['start','stop','status','ports','health']);a=ap.parse_args()
 if a.action=='ports':
  for l in (1,2): print(f'lane{l}: RPC {8545+(l-1)*100}-{8551+(l-1)*100}; P2P {30303+(l-1)*100}-{30309+(l-1)*100}')
  return
 for l in (1,2):
  cmd=[PY,str(ROOT/'blockchain/qbft_health.py')] if a.action=='health' else [PY,str(ROOT/'blockchain/manage_qbft.py'),a.action]
  subprocess.run(cmd,cwd=ROOT,env=env(l),check=(a.action!='stop'))
if __name__=='__main__':main()
