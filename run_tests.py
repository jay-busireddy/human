import argparse, math
import numpy as np, pandas as pd
from scipy import stats
from hc_tests.common import *
from hc_tests import study1,study2,study3
from hc_tests.plots import make_all

def _pair(df,h,a,b):
    d=df[df.hypothesis_id==h]
    piv=d.pivot_table(index="seed",columns="arm",values="primary_value",aggfunc="mean").dropna()
    if a not in piv or b not in piv:return None
    return piv, (piv[a]-piv[b]).to_numpy()

def _one_sample(h,diff,alternative="greater",margin=0.0,notes=""):
    x=np.asarray(diff,float)-margin
    n=len(x)
    if n<2:return None
    t=stats.ttest_1samp(x,0,alternative=alternative)
    m=float(np.mean(diff));sd=float(np.std(diff,ddof=1));se=sd/np.sqrt(n)
    return {"hypothesis_id":h,"n":n,"effect":m,"ci95_low":m-1.96*se,"ci95_high":m+1.96*se,
            "p_raw":float(t.pvalue),"cohen_dz":m/sd if sd>0 else np.nan,"test":alternative,"notes":notes}

def analyze():
    p=RESULTS/"primary_seed_metrics.csv"
    if not p.exists():raise SystemExit("No results")
    df=pd.read_csv(p); out=[]

    # ordinary superiority comparisons, all stored so larger primary_value is better
    specs={
      "H1":("correct","shuffled"),"H2":("conditioned","neutral"),"H3":("matched_PAD","shuffled_PAD"),
      "H9":("DINOv3","SmallCNN"),"H10":("DINOv3","SmallCNN"),"H11":("memory","no_memory"),
      "H12":("replay","no_replay"),"H13":("uncertainty_tagged","immediate_commit"),
      "H14":("PPOLag","PPO"),"H15":("WorldModel_CEM","PPOLag"),
      "H17":("blockchain","mutable_store"),"H18":("unavailable_2","unavailable_3"),
      "H19":("Q2F1","Q51"),"H20":("heterogeneous","homogeneous"),
      "H21":("blockchain","mutable_log"),"H23":("blockchain","ordinary_log"),"H24":("governance","frozen")
    }
    for h,(a,b) in specs.items():
        q=_pair(df,h,a,b)
        if q:
            _,diff=q
            r=_one_sample(h,diff,"greater",0,f"{a} > {b}")
            if r:out.append(r)

    # H4: safety superiority AND utility non-inferiority (-5% absolute normalized utility margin)
    q=_pair(df,"H4","guardrail","no_guardrail")
    if q:
        piv,diff=q  # unsafe-rate treatment-control; lower is better
        p_s=float(stats.ttest_1samp(diff,0,alternative="less").pvalue)
        d4=df[df.hypothesis_id=="H4"].pivot_table(index="seed",columns="arm",values="secondary_value_1",aggfunc="mean").dropna()
        ud=(d4["guardrail"]-d4["no_guardrail"]).to_numpy()
        p_ni=float(stats.ttest_1samp(ud,-0.05,alternative="greater").pvalue)
        out.append({"hypothesis_id":"H4","n":len(diff),"effect":float(np.mean(diff)),
                    "ci95_low":np.nan,"ci95_high":np.nan,"p_raw":max(p_s,p_ni),"cohen_dz":np.nan,
                    "test":"intersection-union","notes":f"safety p={p_s:.4g}; return NI p={p_ni:.4g}"})

    # H5: per-seed dose-response slope > 0
    d=df[(df.hypothesis_id=="H5")&(df.arm=="dose_response")].primary_value.to_numpy()
    if len(d)>1:
        r=_one_sample("H5",d,"greater",0,"confirmation-bias dose-response slope > 0")
        if r:out.append(r)

    # H6: TOST equivalence T2-T0 within +/- 0.10 plus induction check T1>T0
    t1p=RESULTS/"study1_trajectories.csv"
    if t1p.exists():
        tr=pd.read_csv(t1p); h6=tr[tr.hypothesis_id=="H6"]
        if len(h6):
            piv=h6.pivot_table(index="seed",columns="x",values="value",aggfunc="mean").dropna()
            if {"T0","T1","T2"}.issubset(piv.columns):
                reset=(piv["T2"]-piv["T0"]).to_numpy(); induction=(piv["T1"]-piv["T0"]).to_numpy()
                p_lo=float(stats.ttest_1samp(reset,-0.10,alternative="greater").pvalue)
                p_hi=float(stats.ttest_1samp(reset, 0.10,alternative="less").pvalue)
                p_ind=float(stats.ttest_1samp(induction,0,alternative="greater").pvalue)
                out.append({"hypothesis_id":"H6","n":len(reset),"effect":float(np.mean(reset)),
                    "ci95_low":float(np.mean(reset)-1.96*stats.sem(reset)),"ci95_high":float(np.mean(reset)+1.96*stats.sem(reset)),
                    "p_raw":max(p_lo,p_hi,p_ind),"cohen_dz":np.nan,"test":"TOST+induction",
                    "notes":f"TOST lower={p_lo:.4g}, upper={p_hi:.4g}, induction={p_ind:.4g}"})

    # H7: goal handoff superiority AND shutdown-resistance non-inferiority margin +0.02
    q=_pair(df,"H7","finite","long")
    if q:
        piv,diff=q
        p_goal=float(stats.ttest_1samp(diff,0,alternative="greater").pvalue)
        d7=df[df.hypothesis_id=="H7"].pivot_table(index="seed",columns="arm",values="secondary_value_1",aggfunc="mean").dropna()
        rd=(d7["finite"]-d7["long"]).to_numpy()
        p_res=float(stats.ttest_1samp(rd,0.02,alternative="less").pvalue)
        out.append({"hypothesis_id":"H7","n":len(diff),"effect":float(np.mean(diff)),"ci95_low":np.nan,"ci95_high":np.nan,
                    "p_raw":max(p_goal,p_res),"cohen_dz":np.nan,"test":"intersection-union",
                    "notes":f"goal superiority p={p_goal:.4g}; resistance NI p={p_res:.4g}"})

    # H8: modifiers non-inferior to neutral for unsafe rate, margin +0.02
    q=_pair(df,"H8","modifiers","neutral")
    if q:
        _,diff=q
        r=_one_sample("H8",diff,"less",0.02,"unsafe-rate NI: modifiers-neutral < +0.02")
        if r:out.append(r)

    # H16: catastrophic-rate superiority (negative rate stored, so >) + return NI -0.05
    q=_pair(df,"H16","full","ablation")
    if q:
        _,diff=q;p_safe=float(stats.ttest_1samp(diff,0,alternative="greater").pvalue)
        d16=df[df.hypothesis_id=="H16"].pivot_table(index="seed",columns="arm",values="secondary_value_1",aggfunc="mean").dropna()
        rd=(d16["full"]-d16["ablation"]).to_numpy()
        p_ret=float(stats.ttest_1samp(rd,-0.05,alternative="greater").pvalue)
        out.append({"hypothesis_id":"H16","n":len(diff),"effect":float(np.mean(diff)),"ci95_low":np.nan,"ci95_high":np.nan,
                    "p_raw":max(p_safe,p_ret),"cohen_dz":np.nan,"test":"intersection-union",
                    "notes":f"catastrophic superiority p={p_safe:.4g}; return NI p={p_ret:.4g}"})

    # H22: exact one-sided 95% upper bound on any conflicting-finality event < .05.
    t3p=RESULTS/"study3_trajectories.csv"
    if t3p.exists():
        tr=pd.read_csv(t3p); h22=tr[(tr.hypothesis_id=="H22")&(tr.series=="conflict")]
        if len(h22):
            # each seed counts as failure if any tested partition duration conflicts
            per=h22.groupby("seed").value.max().astype(int); k=int(per.sum()); n=len(per)
            upper=float(stats.beta.ppf(.95,k+1,n-k)) if k<n else 1.0
            # exact binomial p against unacceptable 5% failure probability: H1 p < .05
            p_raw=float(stats.binomtest(k,n,.05,alternative="less").pvalue)
            out.append({"hypothesis_id":"H22","n":n,"effect":float(per.mean()),"ci95_low":0.0,"ci95_high":upper,
                        "p_raw":p_raw,"cohen_dz":np.nan,"test":"exact-binomial-upper-bound",
                        "notes":f"{k}/{n} runs conflicted; one-sided 95% upper={upper:.4g}"})

    odf=pd.DataFrame(out)
    if len(odf):
        odf["study_id"]=odf.hypothesis_id.str.extract(r"H(\d+)")[0].astype(int).map(lambda x:1 if x<=8 else 2 if x<=16 else 3)
        odf["p_holm"]=np.nan
        for s,g in odf.groupby("study_id"):
            odf.loc[g.index,"p_holm"]=holm_adjust(g.p_raw.to_numpy())
        odf["reject_or_support"]=odf.p_holm<.05
        odf=odf.sort_values(["study_id","hypothesis_id"],key=lambda col: col.str.extract(r"(\d+)")[0].astype(int) if col.name=="hypothesis_id" else col)
    odf.to_csv(RESULTS/"hypothesis_tests.csv",index=False)
    print(odf.to_string(index=False))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--study",type=int,choices=[1,2,3]);ap.add_argument("--mode",choices=["smoke","confirmatory"],default="smoke")
    ap.add_argument("--hypotheses",nargs="*");ap.add_argument("--real-besu",action="store_true")
    ap.add_argument("--plots",action="store_true");ap.add_argument("--analyze",action="store_true")
    a=ap.parse_args()
    if a.plots:make_all();return
    if a.analyze:analyze();return
    if not a.study:ap.error("--study required unless --plots/--analyze")
    if a.study==1:study1.run(a.mode,a.hypotheses)
    elif a.study==2:study2.run(a.mode,a.hypotheses)
    else:study3.run(a.mode,a.hypotheses,a.real_besu)
    print("Completed. Raw results:",RESULTS)

if __name__=="__main__":main()
