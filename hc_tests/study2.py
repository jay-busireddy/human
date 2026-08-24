import os, math, json, time, hashlib
from pathlib import Path
import numpy as np, pandas as pd, torch
from torch import nn
from torch.utils.data import TensorDataset,DataLoader
from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from PIL import Image, ImageFilter
from .common import *
from .safe_rl import train_ppo_lag,evaluate,train_world_model,eval_world_planner

DEVICE="cuda" if torch.cuda.is_available() else "cpu"
CACHE_ROOT=ROOT/"cache"/"study2"
CACHE_ROOT.mkdir(parents=True,exist_ok=True)

class SmallCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.f=nn.Sequential(nn.Conv2d(3,24,5,2,2),nn.ReLU(),nn.Conv2d(24,48,3,2,1),nn.ReLU(),nn.Conv2d(48,96,3,2,1),nn.ReLU(),nn.AdaptiveAvgPool2d(1))
        self.h=nn.Linear(96,1)
    def forward(self,x): return self.h(self.f(x).flatten(1)).squeeze(1)

def _flat_obs_component(env,obs,key):
    sd=getattr(env,"obs_space_dict",None) or getattr(getattr(env,"unwrapped",None),"obs_space_dict",None)
    if sd is None: raise RuntimeError("Safety-Gymnasium env does not expose obs_space_dict")
    off=0; flat=np.asarray(obs).reshape(-1)
    for name,space in sd.items():
        size=int(np.prod(space.shape))
        if name==key:return flat[off:off+size]
        off+=size
    raise KeyError(f"{key!r} not found in obs_space_dict")

def _seed_cache(seed,mode):
    p=CACHE_ROOT/f"h9h10_{mode}"/str(seed); p.mkdir(parents=True,exist_ok=True); return p

def collect_frames(seed,n,allow_fallback=True,cache_dir=None):
    cache=Path(cache_dir)/"dataset.npz" if cache_dir else None
    if cache and cache.exists():
        z=np.load(cache,allow_pickle=False)
        return z["X"],z["Y"],bool(z["used_fallback"]),float(z["threshold"]),float(z["score_min"]),float(z["score_max"])
    import safety_gymnasium
    env=safety_gymnasium.make("SafetyPointGoal1-v0",render_mode="rgb_array",width=96,height=96)
    obs,_=env.reset(seed=seed); X=[];scores=[]; max_attempts=max(n*8,2000); target=n*3
    for i in range(max_attempts):
        # Preserve the original collection workload/corner coverage: up to 3*n rendered states.
        frame=env.render(); hazards=_flat_obs_component(env,obs,"hazards_lidar")
        scores.append(float(np.max(hazards))); X.append(np.asarray(frame,dtype=np.uint8))
        a=env.action_space.sample(); obs,r,c,term,trunc,info=env.step(a)
        if term or trunc or ((i+1)%250==0): obs,_=env.reset(seed=seed+1+(i//250))
        if len(X)>=target: break
        if (i+1)%3000==0: print(f"[H9/H10 seed {seed}] collected {i+1}/{target} raw frames",flush=True)
    env.close(); X=np.stack(X); scores=np.asarray(scores,np.float32); threshold=.60; Y=(scores>=threshold).astype(np.int64)
    idx0=np.flatnonzero(Y==0);idx1=np.flatnonzero(Y==1);used=False
    if len(idx0)==0 or len(idx1)==0:
        if not allow_fallback: raise RuntimeError(f"Confirmatory hazard labels are one-class under fixed threshold {threshold}: score range=({scores.min():.4f},{scores.max():.4f})")
        med=float(np.median(scores));Y=(scores>=med).astype(np.int64);idx0=np.flatnonzero(Y==0);idx1=np.flatnonzero(Y==1);used=True
    k=min(len(idx0),len(idx1),max(1,n//2))
    if k<2: raise RuntimeError(f"Could not construct two-class hazard dataset: counts={(len(idx0),len(idx1))}")
    rng=np.random.default_rng(seed+991);take=np.r_[rng.choice(idx0,k,False),rng.choice(idx1,k,False)];rng.shuffle(take)
    out=(X[take],Y[take],used,float(threshold),float(scores.min()),float(scores.max()))
    if cache:
        np.savez_compressed(cache,X=out[0],Y=out[1],used_fallback=np.array(out[2]),threshold=np.array(out[3]),score_min=np.array(out[4]),score_max=np.array(out[5]))
        print(f"[H9/H10 seed {seed}] cached balanced dataset: {len(out[0])} frames",flush=True)
    return out

def corrupt(images,severity,seed=0):
    if severity==0:return images
    a=np.asarray(images)
    if severity%4==2:
        return np.clip(a.astype(np.float32)*max(.2,1-.12*severity),0,255).astype(np.uint8)
    if severity%4==3:
        out=a.copy();h,w=out.shape[1:3];q=max(4,int(min(h,w)*.08*severity));out[:,h//2-q:h//2+q,w//2-q:w//2+q]=0;return out
    if severity%4==0:
        rng=np.random.default_rng(seed+severity*100003); noise=rng.normal(0,7*severity,a.shape).astype(np.float32)
        return np.clip(a.astype(np.float32)+noise,0,255).astype(np.uint8)
    # Gaussian blur is kept exact through PIL.
    return np.stack([np.asarray(Image.fromarray(x).filter(ImageFilter.GaussianBlur(radius=.5*severity))) for x in a])

def train_smallcnn(X,Y,epochs):
    m=SmallCNN().to(DEVICE);opt=torch.optim.AdamW(m.parameters(),1e-3)
    x=torch.from_numpy(X).permute(0,3,1,2).float()/255.;y=torch.from_numpy(Y).float()
    bs=int(os.environ.get("HC_CNN_BATCH","256"));dl=DataLoader(TensorDataset(x,y),batch_size=bs,shuffle=True,num_workers=0)
    for ep in range(epochs):
        for xb,yb in dl:
            xb=xb.to(DEVICE);yb=yb.to(DEVICE);z=m(xb);loss=nn.functional.binary_cross_entropy_with_logits(z,yb);opt.zero_grad();loss.backward();opt.step()
    return m

@torch.inference_mode()
def cnn_scores(m,X):
    out=[];bs=int(os.environ.get("HC_CNN_BATCH","256"))
    for i in range(0,len(X),bs):
        x=torch.from_numpy(np.ascontiguousarray(X[i:i+bs])).permute(0,3,1,2).float().to(DEVICE)/255.;out.append(torch.sigmoid(m(x)).cpu().numpy())
    return np.concatenate(out)

_DINO_CACHE={};_OV_CACHE={}
def _load_dinov3():
    repo=os.environ.get("DINOV3_REPO","").strip();weights=os.environ.get("DINOV3_WEIGHTS","").strip();key=(repo,weights)
    if key in _DINO_CACHE:return _DINO_CACHE[key]
    if not repo or not Path(repo).exists():raise RuntimeError("DINOV3_REPO missing")
    if not weights or not Path(weights).is_file():raise RuntimeError("DINOV3_WEIGHTS must be a real local checkpoint")
    m=torch.hub.load(str(Path(repo).resolve()),"dinov3_vits16",source="local",weights=weights).cpu().eval();_DINO_CACHE[key]=m;return m

class _DinoWrapper(nn.Module):
    def __init__(self,m):super().__init__();self.m=m
    def forward(self,x):
        f=self.m(x)
        if isinstance(f,dict):f=f.get("x_norm_clstoken",next(iter(f.values())))
        if f.ndim>2:f=f.mean(1)
        return f

def _openvino_model():
    backend=os.environ.get("HC_DINO_BACKEND","auto").lower()
    if backend=="torch":return None
    try:
        import openvino as ov
        core=ov.Core(); devices=list(core.available_devices)
        want=os.environ.get("HC_OPENVINO_DEVICE","GPU")
        if want.startswith("GPU") and not any(d.startswith("GPU") for d in devices):
            if backend=="openvino":raise RuntimeError(f"OpenVINO GPU unavailable; devices={devices}")
            return None
        key=(want,os.environ.get("DINOV3_WEIGHTS",""))
        if key in _OV_CACHE:return _OV_CACHE[key]
        repo=Path(os.environ.get("DINOV3_REPO","")); weights=Path(os.environ.get("DINOV3_WEIGHTS","")); sig=(hash_file(weights)[:12] if weights.is_file() else "noweights")+"_"+(hash_file(repo/"hubconf.py")[:8] if (repo/"hubconf.py").is_file() else "nohub")
        ir=CACHE_ROOT/f"dinov3_openvino_{sig}.xml"
        if ir.exists(): om=core.read_model(str(ir))
        else:
            wrap=_DinoWrapper(_load_dinov3()).eval();example=torch.randn(1,3,224,224)
            om=ov.convert_model(wrap,example_input=example);om.reshape({om.input(0):[-1,3,224,224]});ov.save_model(om,str(ir))
        compiled=core.compile_model(om,want,{"PERFORMANCE_HINT":"THROUGHPUT"});_OV_CACHE[key]=compiled
        print(f"[DINO] OpenVINO backend={want}, devices={devices}",flush=True);return compiled
    except Exception as e:
        if backend=="openvino":raise
        print(f"[DINO] OpenVINO unavailable/fallback to PyTorch CPU: {e}",flush=True);return None

def _prep_dino(batch):
    x=torch.from_numpy(np.ascontiguousarray(batch)).permute(0,3,1,2).float()/255.;x=nn.functional.interpolate(x,(224,224),mode="bilinear",align_corners=False)
    mean=torch.tensor([.485,.456,.406]).view(1,3,1,1);std=torch.tensor([.229,.224,.225]).view(1,3,1,1);return ((x-mean)/std).numpy().astype(np.float32)

def dino_features(X,cache_file=None):
    if cache_file and Path(cache_file).exists():return np.load(cache_file,mmap_mode=None)
    bs=int(os.environ.get("HC_DINO_BATCH","128"));ovm=_openvino_model();feats=[]
    if ovm is not None:
        outport=ovm.output(0)
        for i in range(0,len(X),bs):feats.append(np.asarray(ovm([_prep_dino(X[i:i+bs])])[outport]))
    else:
        m=_load_dinov3();
        with torch.inference_mode():
            for i in range(0,len(X),bs):
                x=torch.from_numpy(_prep_dino(X[i:i+bs]));f=m(x)
                if isinstance(f,dict):f=f.get("x_norm_clstoken",next(iter(f.values())))
                if f.ndim>2:f=f.mean(1)
                feats.append(f.cpu().numpy())
    F=np.concatenate(feats).astype(np.float32)
    if cache_file:np.save(cache_file,F)
    return F

def _dinov3_configured():return bool(os.environ.get("DINOV3_REPO")) and Path(os.environ.get("DINOV3_REPO","")).exists() and Path(os.environ.get("DINOV3_WEIGHTS","")).is_file()

def h9_h10(seed,mode,rows,traj):
    cfg=load_config()["study2"];n=cfg["frames_smoke"] if mode=="smoke" else cfg["frames_confirmatory"];ep=cfg["probe_epochs_smoke"] if mode=="smoke" else cfg["probe_epochs_confirmatory"]
    if mode=="confirmatory" and not _dinov3_configured():raise RuntimeError("Confirmatory H9/H10 require local DINOv3 repo/checkpoint")
    cd=_seed_cache(seed,mode);X,Y,used,thr,smin,smax=collect_frames(seed,n,allow_fallback=(mode=="smoke"),cache_dir=cd);actual=len(X)
    split=cd/"split.npz"
    if split.exists():z=np.load(split);tr=z["tr"];te=z["te"]
    else:
        tr,te=train_test_split(np.arange(actual),test_size=.30,random_state=seed+1701,shuffle=True,stratify=Y);np.savez(split,tr=tr,te=te)
    if len(np.unique(Y[tr]))<2 or len(np.unique(Y[te]))<2:raise RuntimeError("H9/H10 stratified split lost a class")
    traj += [{"study_id":2,"hypothesis_id":"H9","seed":seed,"series":"effective_balanced_frames","x":0,"value":float(actual)},{"study_id":2,"hypothesis_id":"H9","seed":seed,"series":"train_frames","x":0,"value":float(len(tr))},{"study_id":2,"hypothesis_id":"H9","seed":seed,"series":"test_frames","x":0,"value":float(len(te))}]
    cnnfile=cd/"smallcnn.pt";m=train_smallcnn(X[tr],Y[tr],ep);torch.save(m.state_dict(),cnnfile)
    sc=cnn_scores(m,X[te]);rows.append(primary_rows(2,"H9",seed,"SmallCNN","ood_hazard_auroc",roc_auc_score(Y[te],sc),secondary_metric_1="label_fallback_used",secondary_value_1=float(used),secondary_metric_2="hazard_score_range",secondary_value_2=smax-smin))
    cnn_fns=[]
    for sev in range(6):
        ss=cnn_scores(m,corrupt(X[te],sev,seed));fn=float(np.mean((ss<.5)&(Y[te]==1)));cnn_fns.append(fn);traj.append({"study_id":2,"hypothesis_id":"H10","seed":seed,"series":"SmallCNN","x":sev,"value":fn})
    rows.append(primary_rows(2,"H10",seed,"SmallCNN","negative_corruption_fnr_auc",-float(np.trapz(cnn_fns,dx=1)/5)))
    if _dinov3_configured():
        Ftr=dino_features(X[tr],cd/"dino_train.npy");Fte=dino_features(X[te],cd/"dino_clean_test.npy");clf=LogisticRegression(max_iter=1500,n_jobs=1).fit(Ftr,Y[tr]);sd=clf.predict_proba(Fte)[:,1]
        rows.append(primary_rows(2,"H9",seed,"DINOv3","ood_hazard_auroc",roc_auc_score(Y[te],sd),secondary_metric_1="label_fallback_used",secondary_value_1=float(used),secondary_metric_2="hazard_score_range",secondary_value_2=smax-smin))
        dino_fns=[]
        for sev in range(6):
            Fc=Fte if sev==0 else dino_features(corrupt(X[te],sev,seed),cd/f"dino_corrupt_{sev}.npy")
            ss=clf.predict_proba(Fc)[:,1];fn=float(np.mean((ss<.5)&(Y[te]==1)));dino_fns.append(fn);traj.append({"study_id":2,"hypothesis_id":"H10","seed":seed,"series":"DINOv3","x":sev,"value":fn})
        rows.append(primary_rows(2,"H10",seed,"DINOv3","negative_corruption_fnr_auc",-float(np.trapz(dino_fns,dx=1)/5)))

class GRUJudge(nn.Module):
    def __init__(self):
        super().__init__();self.g=nn.GRU(4,32,batch_first=True);self.h=nn.Linear(32,2)
    def forward(self,x):o,_=self.g(x);return self.h(o[:,-1])

def delayed_data(rng,n,T=20):
    x=rng.normal(0,1,(n,T,4)).astype(np.float32); cue=rng.integers(0,2,n)
    x[:,0,0]=cue*2-1; x[:,-1,1]=rng.integers(0,2,n)*2-1
    y=(cue ^ (x[:,-1,1]>0).astype(int)).astype(np.int64)
    return x,y,cue

def train_gru(seed,n,explicit=False):
    rng=np.random.default_rng(seed);x,y,cue=delayed_data(rng,n)
    if explicit:
        # explicit memory repeats the first cue at final step
        x[:,-1,3]=x[:,0,0]
    m=GRUJudge().to(DEVICE);opt=torch.optim.Adam(m.parameters(),1e-3)
    dl=DataLoader(TensorDataset(torch.tensor(x),torch.tensor(y)),batch_size=128,shuffle=True)
    for _ in range(4 if n < 5000 else 12):
        for xb,yb in dl:
            z=m(xb.to(DEVICE));loss=nn.functional.cross_entropy(z,yb.to(DEVICE));opt.zero_grad();loss.backward();opt.step()
    xt,yt,_=delayed_data(rng,1500)
    if explicit:xt[:,-1,3]=xt[:,0,0]
    with torch.no_grad():acc=float((m(torch.tensor(xt).to(DEVICE)).argmax(1).cpu().numpy()==yt).mean())
    return acc

def continual(seed,replay=True,steps=500):
    rng=np.random.default_rng(seed);m=nn.Sequential(nn.Linear(2,32),nn.ReLU(),nn.Linear(32,2)).to(DEVICE);opt=torch.optim.Adam(m.parameters(),2e-3)
    buf=[]
    def data(task,n=64):
        x=rng.normal(size=(n,2)).astype(np.float32); ang=task*np.pi/3; w=np.array([np.cos(ang),np.sin(ang)]); y=(x@w>0).astype(np.int64);return x,y
    before=[]; after=[]
    for task in range(3):
        for _ in range(steps//3):
            x,y=data(task)
            if replay and buf:
                old=buf[rng.integers(len(buf))];x=np.r_[x,old[0]];y=np.r_[y,old[1]]
            z=m(torch.tensor(x).to(DEVICE));loss=nn.functional.cross_entropy(z,torch.tensor(y).to(DEVICE))
            opt.zero_grad();loss.backward();opt.step()
        buf.append(data(task,128))
        # immediate prior-task accuracy
        vals=[]
        for t in range(task+1):
            x,y=data(t,500)
            with torch.no_grad():vals.append(float((m(torch.tensor(x).to(DEVICE)).argmax(1).cpu().numpy()==y).mean()))
        before.append(vals)
    final=[]
    for t in range(3):
        x,y=data(t,1000)
        with torch.no_grad():final.append(float((m(torch.tensor(x).to(DEVICE)).argmax(1).cpu().numpy()==y).mean()))
    learned=[before[t][t] for t in range(3)]
    br=float(np.mean(np.array(final)-np.array(learned)))
    return br,final

def recombination(seed,uncertainty=True,n=1500):
    rng=np.random.default_rng(seed); truth=rng.integers(0,2,n); candidate=truth.copy()
    # generator errors
    flip=rng.random(n)<.30;candidate[flip]=1-candidate[flip]
    evidence=rng.binomial(3,.75,n)
    if uncertainty:
        commit=evidence>=3
    else:
        commit=np.ones(n,dtype=bool)
    false=float(np.mean(commit & (candidate!=truth)))
    return false

def integrated(seed,full=True,n=3000):
    rng=np.random.default_rng(seed)
    hazard=rng.random(n); cue=rng.integers(0,2,n); delayed=rng.integers(0,2,n); pad=rng.normal(0,.5,n)
    perception=hazard+rng.normal(0,.10 if full else .25,n)
    memory=cue if full else rng.integers(0,2,n)
    raw_risky=(memory==delayed)
    risky=raw_risky & (perception<.45)
    if full: risky = risky & (pad<1.0) & (hazard<.65) # safety critic + modulation
    reward=np.where(risky,1.0,.55)-np.where(risky & (hazard>.55),1.8,0)
    catastrophic=(risky & (hazard>.72)).mean()
    return float(reward.mean()),float(catastrophic)



def _checkpoint_dir(mode):
    p=RESULTS/"checkpoints"/"study2"/mode;p.mkdir(parents=True,exist_ok=True);return p

def _done(mode,h,seed):return (_checkpoint_dir(mode)/f"{h}_{seed}.done").exists()
def _mark(mode,h,seed):(_checkpoint_dir(mode)/f"{h}_{seed}.done").write_text("ok\n")

def compute_one(h,seed,mode):
    seed_all(seed);cfg=load_config()["study2"];rows=[];traj=[]
    if h in ("H9","H10"):
        h9_h10(seed,mode,rows,traj)
        # H9/H10 are coupled by identical images/features; caller marks both.
    elif h=="H11":
        n=cfg["delayed_train_smoke"] if mode=="smoke" else cfg["delayed_train_confirmatory"];a=train_gru(seed,n,False);b=train_gru(seed,n,True);rows += [primary_rows(2,"H11",seed,"memory","delayed_success",b),primary_rows(2,"H11",seed,"no_memory","delayed_success",a)]
    elif h=="H12":
        st=cfg["continual_steps_smoke"] if mode=="smoke" else cfg["continual_steps_confirmatory"];a,fa=continual(seed,False,st);b,fb=continual(seed,True,st);rows += [primary_rows(2,"H12",seed,"replay","backward_retention",b),primary_rows(2,"H12",seed,"no_replay","backward_retention",a)];traj += [{"study_id":2,"hypothesis_id":"H12","seed":seed,"series":"replay","x":j,"value":v} for j,v in enumerate(fb)]+[{"study_id":2,"hypothesis_id":"H12","seed":seed,"series":"no_replay","x":j,"value":v} for j,v in enumerate(fa)]
    elif h=="H13":
        a=recombination(seed,False);b=recombination(seed,True);rows += [primary_rows(2,"H13",seed,"uncertainty_tagged","negative_false_commitment",-b),primary_rows(2,"H13",seed,"immediate_commit","negative_false_commitment",-a)]
    elif h=="H14":
        u=cfg["rl_updates_smoke"] if mode=="smoke" else cfg["rl_updates_confirmatory"];m0,h0=train_ppo_lag(seed,u,cfg["rl_horizon"],False);m1,h1=train_ppo_lag(seed,u,cfg["rl_horizon"],True);r0,c0=evaluate(m0,seed,8 if mode=="smoke" else 30);r1,c1=evaluate(m1,seed,8 if mode=="smoke" else 30);rows += [primary_rows(2,"H14",seed,"PPOLag","negative_safety_cost",-c1,secondary_metric_1="return",secondary_value_1=r1),primary_rows(2,"H14",seed,"PPO","negative_safety_cost",-c0,secondary_metric_1="return",secondary_value_1=r0)]
    elif h=="H15":
        n=cfg["world_model_random_steps_smoke"] if mode=="smoke" else cfg["world_model_random_steps_confirmatory"];wm,_=train_world_model(seed,n,epochs=3 if mode=="smoke" else 20);rw,cw,steps=eval_world_planner(wm,seed,3 if mode=="smoke" else 20,smoke=(mode=="smoke"));u=cfg["rl_updates_smoke"] if mode=="smoke" else cfg["rl_updates_confirmatory"];lag,_=train_ppo_lag(seed,u,cfg["rl_horizon"],True);rp,cp=evaluate(lag,seed,3 if mode=="smoke" else 20);rows += [primary_rows(2,"H15",seed,"WorldModel_CEM","negative_safety_cost",-cw,secondary_metric_1="return",secondary_value_1=rw),primary_rows(2,"H15",seed,"PPOLag","negative_safety_cost",-cp,secondary_metric_1="return",secondary_value_1=rp)]
    elif h=="H16":
        r0,c0=integrated(seed,False);r1,c1=integrated(seed,True);rows += [primary_rows(2,"H16",seed,"full","negative_catastrophic_rate",-c1,secondary_metric_1="return",secondary_value_1=r1),primary_rows(2,"H16",seed,"ablation","negative_catastrophic_rate",-c0,secondary_metric_1="return",secondary_value_1=r0)]
    return rows,traj

def run(mode="smoke",hypotheses=None):
    hs=hypotheses or [f"H{i}" for i in range(9,17)];seeds=seeds_for(2,mode)
    # H9/H10 share the expensive representation pipeline and are always computed together.
    if "H9" in hs or "H10" in hs:
        for idx,seed in enumerate(seeds,1):
            if _done(mode,"H9",seed) and _done(mode,"H10",seed):print(f"[Study2 H9/H10] {idx}/{len(seeds)} seed {seed}: cached complete",flush=True);continue
            print(f"[Study2 H9/H10] {idx}/{len(seeds)} seed {seed}: START",flush=True);rows,traj=compute_one("H9",seed,mode);append_csv(RESULTS/"primary_seed_metrics.csv",rows);append_csv(RESULTS/"study2_trajectories.csv",traj);_mark(mode,"H9",seed);_mark(mode,"H10",seed);print(f"[Study2 H9/H10] {idx}/{len(seeds)} seed {seed}: SAVED",flush=True)
    for h in [x for x in hs if x not in ("H9","H10")]:
        for idx,seed in enumerate(seeds,1):
            if _done(mode,h,seed):continue
            print(f"[Study2 {h}] {idx}/{len(seeds)} seed {seed}: START",flush=True);rows,traj=compute_one(h,seed,mode);append_csv(RESULTS/"primary_seed_metrics.csv",rows);append_csv(RESULTS/"study2_trajectories.csv",traj);_mark(mode,h,seed);print(f"[Study2 {h}] {idx}/{len(seeds)} seed {seed}: SAVED",flush=True)
    return []
