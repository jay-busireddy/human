import os,time,numpy as np
from hc_tests.study2 import _load_dinov3,_openvino_model,dino_features

def main():
    print('DINOV3_REPO=',os.environ.get('DINOV3_REPO'));print('DINOV3_WEIGHTS=',os.environ.get('DINOV3_WEIGHTS'))
    try:
        import openvino as ov
        c=ov.Core();print('OpenVINO',ov.__version__,'devices=',c.available_devices)
    except Exception as e:print('OpenVINO import failed:',e)
    m=_load_dinov3();print('PyTorch DINO loaded:',type(m).__name__)
    x=np.random.default_rng(1).integers(0,256,size=(8,96,96,3),dtype=np.uint8)
    old=os.environ.get('HC_DINO_BACKEND');os.environ['HC_DINO_BACKEND']='torch';t=time.time();a=dino_features(x);print('Torch reference seconds=',round(time.time()-t,3),'shape=',a.shape)
    os.environ['HC_DINO_BACKEND']='openvino';t=time.time();b=dino_features(x);print('OpenVINO seconds=',round(time.time()-t,3),'shape=',b.shape);print('max_abs_diff=',float(np.max(np.abs(a-b))),'mean_abs_diff=',float(np.mean(np.abs(a-b))))
    if not np.allclose(a,b,rtol=2e-2,atol=2e-2):raise SystemExit('OpenVINO equivalence check failed; use HC_DINO_BACKEND=torch')
    print('PASS: OpenVINO DINO feature equivalence within tolerance')
    if old is None:os.environ.pop('HC_DINO_BACKEND',None)
    else:os.environ['HC_DINO_BACKEND']=old
if __name__=='__main__':main()
