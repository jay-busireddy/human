from pathlib import Path
import json, math, random, hashlib, os
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ.get("HC_RESULTS_DIR", str(ROOT / "results"))).resolve()
PLOTS = RESULTS / "plots"
RESULTS.mkdir(exist_ok=True)
PLOTS.mkdir(exist_ok=True)

def seed_all(seed):
    random.seed(seed); np.random.seed(seed)
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available(): torch.cuda.manual_seed_all(seed)
    except Exception:
        pass

def load_config():
    return json.loads((ROOT/"config.json").read_text())

def seeds_for(study, mode):
    cfg=load_config()
    if mode=="smoke":
        n=cfg["seed_counts"]["smoke"]
        start=cfg["smoke_seed_starts"][f"study{study}"]
    else:
        n=cfg["seed_counts"][f"study{study}_confirmatory"]
        start=cfg["seed_starts"][f"study{study}"]
    return list(range(start,start+n))

def append_csv(path, rows):
    if rows is None or len(rows)==0: return
    path=Path(path)
    df=pd.DataFrame(rows)
    if path.exists():
        old=pd.read_csv(path)
        if len(old):
            # replace same study/hypothesis/seed/arm where possible
            keys=[k for k in ["study_id","hypothesis_id","seed","arm"] if k in old.columns and k in df.columns]
            if keys:
                merged=old.merge(df[keys].drop_duplicates().assign(_drop=1), on=keys, how="left")
                old=merged[merged["_drop"].isna()].drop(columns="_drop")
            df=pd.concat([old,df],ignore_index=True)
    df.to_csv(path,index=False)

def primary_rows(study,h,seed,arm,metric,value,**extra):
    row={"study_id":study,"hypothesis_id":h,"seed":seed,"arm":arm,
         "primary_metric":metric,"primary_value":float(value),"run_valid":True}
    row.update(extra)
    return row

def paired_stats(df, h, treatment, control, higher=True):
    d=df[df.hypothesis_id==h]
    piv=d.pivot_table(index="seed",columns="arm",values="primary_value",aggfunc="mean").dropna()
    if treatment not in piv or control not in piv: return None
    diff=(piv[treatment]-piv[control]).values
    alt="greater" if higher else "less"
    t=stats.ttest_1samp(diff,0,alternative=alt)
    n=len(diff); mean=float(np.mean(diff)); sd=float(np.std(diff,ddof=1)) if n>1 else np.nan
    se=sd/math.sqrt(n) if n>1 else np.nan
    ci=(mean-1.96*se,mean+1.96*se) if n>1 else (np.nan,np.nan)
    try: wp=float(stats.wilcoxon(diff,alternative=alt).pvalue)
    except Exception: wp=np.nan
    return {"hypothesis_id":h,"n":n,"mean_difference":mean,"ci95_low":ci[0],"ci95_high":ci[1],
            "p_one_sided":float(t.pvalue),"cohen_dz":mean/sd if sd and np.isfinite(sd) else np.nan,
            "wilcoxon_p":wp,"wins":int(np.sum(diff>0)),"ties":int(np.sum(diff==0)),"losses":int(np.sum(diff<0))}

def savefig(name):
    plt.tight_layout()
    plt.savefig(PLOTS/f"{name}.png",dpi=220,bbox_inches="tight")
    plt.savefig(PLOTS/f"{name}.pdf",bbox_inches="tight")
    plt.close()

def hash_file(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()

def holm_adjust(pvals):
    p=np.asarray(pvals,dtype=float)
    m=len(p); order=np.argsort(np.where(np.isfinite(p),p,np.inf))
    out=np.full(m,np.nan); running=0.0
    for rank,idx in enumerate(order):
        if not np.isfinite(p[idx]): continue
        val=min(1.0,(m-rank)*p[idx]); running=max(running,val); out[idx]=running
    return out
