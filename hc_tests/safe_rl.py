import numpy as np, torch
from torch import nn
from torch.distributions import Normal

DEVICE="cuda" if torch.cuda.is_available() else "cpu"

class PointSafeEnv:
    """Small continuous CMDP used for H14/H15. State/action are continuous."""
    def __init__(self,seed=0):
        self.rng=np.random.default_rng(seed); self.max_steps=120
    def reset(self):
        self.pos=np.array([-1.2,-1.0])+self.rng.normal(0,.08,2)
        self.goal=np.array([1.2,1.0])
        self.hazard=np.array([0.,0.])
        self.t=0
        return self.state()
    def state(self):
        return np.r_[self.pos,self.goal-self.pos,self.hazard-self.pos].astype(np.float32)
    def step(self,a):
        a=np.clip(a,-1,1)*.12
        old=np.linalg.norm(self.goal-self.pos)
        self.pos=np.clip(self.pos+a,-1.6,1.6); self.t+=1
        new=np.linalg.norm(self.goal-self.pos)
        cost=float(np.linalg.norm(self.pos-self.hazard)<.38)
        reward=float((old-new)*5 - .02 + (3 if new<.18 else 0))
        done=bool(new<.18 or self.t>=self.max_steps)
        return self.state(),reward,cost,done

class ActorCritic(nn.Module):
    def __init__(self):
        super().__init__()
        self.body=nn.Sequential(nn.Linear(6,64),nn.Tanh(),nn.Linear(64,64),nn.Tanh())
        self.mu=nn.Linear(64,2); self.logstd=nn.Parameter(torch.full((2,),-0.4))
        self.v=nn.Linear(64,1); self.cv=nn.Linear(64,1)
    def forward(self,x):
        h=self.body(x); return self.mu(h),self.v(h).squeeze(-1),self.cv(h).squeeze(-1)
    def dist(self,x):
        mu,_,_=self(x); return Normal(mu,self.logstd.exp())

def train_ppo_lag(seed, updates=40, horizon=128, constrained=True):
    torch.manual_seed(seed); np.random.seed(seed)
    env=PointSafeEnv(seed); m=ActorCritic().to(DEVICE); opt=torch.optim.Adam(m.parameters(),3e-4)
    lam=torch.tensor(0.0,device=DEVICE); lam_lr=.03; cost_limit=.06
    s=env.reset(); history=[]
    for upd in range(updates):
        S=[];A=[];LP=[];R=[];C=[];D=[];V=[];CV=[]
        for _ in range(horizon):
            st=torch.tensor(s,device=DEVICE).float().unsqueeze(0)
            with torch.no_grad():
                dist=m.dist(st); a=dist.sample(); lp=dist.log_prob(a).sum(-1); _,v,cv=m(st)
            ns,r,c,d=env.step(a.cpu().numpy()[0])
            S.append(s);A.append(a.cpu().numpy()[0]);LP.append(lp.item());R.append(r);C.append(c);D.append(d);V.append(v.item());CV.append(cv.item())
            s=env.reset() if d else ns
        # returns / cost returns
        ret=[]; cret=[]; gr=0.; gc=0.
        for r,c,d in zip(R[::-1],C[::-1],D[::-1]):
            gr=r+.99*gr*(1-d); gc=c+.99*gc*(1-d); ret.append(gr); cret.append(gc)
        ret=np.array(ret[::-1],np.float32); cret=np.array(cret[::-1],np.float32)
        st=torch.tensor(np.array(S),device=DEVICE); at=torch.tensor(np.array(A),device=DEVICE); oldlp=torch.tensor(LP,device=DEVICE)
        rt=torch.tensor(ret,device=DEVICE); ct=torch.tensor(cret,device=DEVICE)
        for _ in range(5):
            dist=m.dist(st); lp=dist.log_prob(at).sum(-1); mu,v,cv=m(st)
            adv=(rt-v.detach()); cadv=(ct-cv.detach())
            adv=(adv-adv.mean())/(adv.std()+1e-6); cadv=(cadv-cadv.mean())/(cadv.std()+1e-6)
            ratio=torch.exp(lp-oldlp)
            obj=torch.min(ratio*adv,torch.clamp(ratio,.8,1.2)*adv)
            if constrained:
                cobj=torch.max(ratio*cadv,torch.clamp(ratio,.8,1.2)*cadv)
                policy_loss=-(obj-lam.detach()*cobj).mean()
            else: policy_loss=-obj.mean()
            loss=policy_loss+.5*((v-rt)**2).mean()+.25*((cv-ct)**2).mean()-.005*dist.entropy().sum(-1).mean()
            opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(m.parameters(),1.0); opt.step()
        mean_c=float(np.mean(C))
        if constrained: lam=torch.clamp(lam+lam_lr*(torch.tensor(mean_c,device=DEVICE)-cost_limit),0,20)
        history.append({"update":upd,"reward":float(np.mean(R)),"cost":mean_c,"lambda":float(lam.item())})
    return m,history

@torch.no_grad()
def evaluate(m,seed,episodes=30):
    env=PointSafeEnv(seed+999); rets=[]; costs=[]
    for ep in range(episodes):
        s=env.reset(); rr=cc=0
        while True:
            st=torch.tensor(s,device=DEVICE).float().unsqueeze(0)
            mu,_,_=m(st); a=torch.tanh(mu).cpu().numpy()[0]
            s,r,c,d=env.step(a); rr+=r; cc+=c
            if d: break
        rets.append(rr); costs.append(cc)
    return float(np.mean(rets)),float(np.mean(costs))

class Dynamics(nn.Module):
    def __init__(self):
        super().__init__(); self.net=nn.Sequential(nn.Linear(8,96),nn.ReLU(),nn.Linear(96,96),nn.ReLU(),nn.Linear(96,8))
    def forward(self,s,a):
        z=torch.cat([s,a],-1); out=self.net(z)
        return out[...,:6], out[...,6], out[...,7]

def collect_random(seed,n=3000):
    env=PointSafeEnv(seed); data=[]; s=env.reset()
    for _ in range(n):
        a=env.rng.uniform(-1,1,2); ns,r,c,d=env.step(a); data.append((s,a,ns,r,c)); s=env.reset() if d else ns
    return data

def train_world_model(seed,n=3000,epochs=25):
    torch.manual_seed(seed); data=collect_random(seed,n)
    s=torch.tensor(np.array([x[0] for x in data]),device=DEVICE).float()
    a=torch.tensor(np.array([x[1] for x in data]),device=DEVICE).float()
    ns=torch.tensor(np.array([x[2] for x in data]),device=DEVICE).float()
    r=torch.tensor([x[3] for x in data],device=DEVICE).float()
    c=torch.tensor([x[4] for x in data],device=DEVICE).float()
    m=Dynamics().to(DEVICE); opt=torch.optim.Adam(m.parameters(),1e-3)
    for _ in range(epochs):
        pns,pr,pc=m(s,a)
        loss=((pns-ns)**2).mean()+.2*((pr-r)**2).mean()+.5*nn.functional.binary_cross_entropy_with_logits(pc,c)
        opt.zero_grad(); loss.backward(); opt.step()
    return m,len(data)

@torch.no_grad()
def cem_action(model,state,horizon=12,samples=256,iters=4):
    """Vectorized CEM planner. All candidate trajectories are evaluated as one batch.

    This is mathematically equivalent to the previous nested Python loop but is
    dramatically faster on CPU/GPU because each model call evaluates all samples.
    """
    mean=torch.zeros((horizon,2),device=DEVICE)
    std=torch.full((horizon,2),0.7,device=DEVICE)
    s0=torch.as_tensor(state,dtype=torch.float32,device=DEVICE)
    elite_n=max(8,samples//10)
    for _ in range(iters):
        acts=(mean.unsqueeze(0)+std.unsqueeze(0)*torch.randn(samples,horizon,2,device=DEVICE)).clamp(-1,1)
        s=s0.unsqueeze(0).expand(samples,-1).clone()
        score=torch.zeros(samples,device=DEVICE)
        for t in range(horizon):
            ns,rlog,clog=model(s,acts[:,t,:])
            cp=torch.sigmoid(clog)
            score += rlog - 6.0*cp
            s=ns
        elite_idx=torch.topk(score,k=elite_n,largest=True).indices
        elite=acts[elite_idx]
        mean=elite.mean(0)
        std=elite.std(0,unbiased=False).clamp_min(0.05)
    return torch.tanh(mean[0]).cpu().numpy()

@torch.no_grad()
def cem_action_batch(model,states,horizon=12,samples=256,iters=4):
    """CEM for B independent states, preserving candidates/horizon/iterations per episode."""
    s0=torch.as_tensor(states,dtype=torch.float32,device=DEVICE);B=s0.shape[0];mean=torch.zeros((B,horizon,2),device=DEVICE);std=torch.full((B,horizon,2),.7,device=DEVICE);elite_n=max(8,samples//10)
    for _ in range(iters):
        acts=(mean[:,None]+std[:,None]*torch.randn(B,samples,horizon,2,device=DEVICE)).clamp(-1,1);s=s0[:,None,:].expand(B,samples,6).clone();score=torch.zeros(B,samples,device=DEVICE)
        for tt in range(horizon):
            flat_s=s.reshape(B*samples,6);flat_a=acts[:,:,tt,:].reshape(B*samples,2);ns,rlog,clog=model(flat_s,flat_a);ns=ns.reshape(B,samples,6);score += rlog.reshape(B,samples)-6*torch.sigmoid(clog.reshape(B,samples));s=ns
        idx=torch.topk(score,k=elite_n,dim=1).indices;g=idx[:,:,None,None].expand(B,elite_n,horizon,2);elite=torch.gather(acts,1,g);mean=elite.mean(1);std=elite.std(1,unbiased=False).clamp_min(.05)
    return torch.tanh(mean[:,0]).cpu().numpy()

def eval_world_planner(model,seed,episodes=20,smoke=False):
    """Evaluate all episodes in lock-step so CEM model calls are batched across episodes."""
    envs=[PointSafeEnv(seed+777+i*7919) for i in range(episodes)];states=np.stack([e.reset() for e in envs]);rets=np.zeros(episodes);costs=np.zeros(episodes);active=np.ones(episodes,bool);steps=0
    while active.any():
        ids=np.flatnonzero(active);acts=cem_action_batch(model,states[ids],horizon=6 if smoke else 12,samples=48 if smoke else 256,iters=2 if smoke else 4)
        for j,a in zip(ids,acts):
            ns,r,c,d=envs[j].step(a);states[j]=ns;rets[j]+=r;costs[j]+=c;steps+=1
            if d:active[j]=False
    return float(rets.mean()),float(costs.mean()),steps
