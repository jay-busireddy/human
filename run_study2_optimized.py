import argparse, os
from hc_tests import study2
from hc_tests.common import RESULTS

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['smoke','confirmatory'],default='smoke');ap.add_argument('--hypotheses',nargs='*');a=ap.parse_args()
    print('Study2 optimized results:',RESULTS);print('DINO backend:',os.environ.get('HC_DINO_BACKEND','auto'),'OpenVINO device:',os.environ.get('HC_OPENVINO_DEVICE','GPU'),'DINO batch:',os.environ.get('HC_DINO_BATCH','128'))
    study2.run(a.mode,a.hypotheses)
if __name__=='__main__':main()
