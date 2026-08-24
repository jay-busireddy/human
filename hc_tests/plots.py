import numpy as np,pandas as pd,matplotlib.pyplot as plt
from .common import RESULTS,PLOTS,savefig

PAIR={
"H1":("correct","shuffled","P01_trait_behavior_alignment"),
"H2":("conditioned","neutral","P02_cross_context"),
"H3":("matched_PAD","shuffled_PAD","P03_PAD_causal"),
"H4":("guardrail","no_guardrail","P04a_guardrail_unsafe"),
"H7":("finite","long","P07a_mortality_goal"),
"H8":("modifiers","neutral","P08_safety_invariance"),
"H9":("DINOv3","SmallCNN","P09_DINOv3_vs_CNN"),
"H10":("DINOv3","SmallCNN","P10_corruption_robustness"),
"H11":("memory","no_memory","P11_delayed_memory"),
"H12":("replay","no_replay","P12a_replay_retention"),
"H13":("uncertainty_tagged","immediate_commit","P13_recombination"),
"H14":("PPOLag","PPO","P14a_safe_RL"),
"H15":("WorldModel_CEM","PPOLag","P15_world_model"),
"H16":("full","ablation","P16a_full_architecture_safety"),
"H17":("blockchain","mutable_store","P17_equivocation"),
"H19":("Q2F1","Q51","P19a_quorum"),
"H20":("heterogeneous","homogeneous","P20a_validator_diversity"),
"H21":("blockchain","mutable_log","P21_tamper"),
"H23":("blockchain","ordinary_log","P23_audit"),
"H24":("governance","frozen","P24a_governance")
}

def pairplot(df,h,a,b,name,value_col="primary_value",ylabel=None):
    d=df[df.hypothesis_id==h]
    piv=d.pivot_table(index="seed",columns="arm",values=value_col,aggfunc="mean").dropna()
    if a not in piv or b not in piv:return
    vals=[piv[a].values,piv[b].values];means=[v.mean() for v in vals]
    sem=[v.std(ddof=1)/np.sqrt(len(v)) if len(v)>1 else 0 for v in vals]
    plt.figure(figsize=(6,4));plt.bar([0,1],means,yerr=np.asarray(sem)*1.96,capsize=5)
    for x0,x1 in zip(vals[0],vals[1]):plt.plot([0,1],[x0,x1],alpha=.18,linewidth=.8)
    plt.xticks([0,1],[a,b]);plt.ylabel(ylabel or (d.primary_metric.iloc[0] if len(d) else "metric"));plt.title(h);savefig(name)

def trajplot(path,h,name,series_filter=None):
    if not path.exists():return
    d=pd.read_csv(path);d=d[d.hypothesis_id==h]
    if series_filter is not None:d=d[d.series.isin(series_filter)]
    if not len(d):return
    plt.figure(figsize=(7,4));artists=0
    for s,g in d.groupby("series"):
        xx=pd.to_numeric(g.x,errors="coerce")
        if xx.notna().sum()!=len(g):continue
        q=g.assign(_x=xx).groupby("_x").value.agg(["mean","sem"]).reset_index()
        plt.plot(q._x,q["mean"],label=s);plt.fill_between(q._x,q["mean"]-1.96*q["sem"].fillna(0),q["mean"]+1.96*q["sem"].fillna(0),alpha=.15);artists+=1
    if artists:plt.legend()
    plt.title(h);plt.xlabel("Condition / step");savefig(name)

def categorical_h6(path):
    if not path.exists():return
    d=pd.read_csv(path);d=d[d.hypothesis_id=="H6"]
    if not len(d):return
    order=["T0","T1","T2"];q=d.groupby("x").value.agg(["mean","sem"]).reindex(order)
    plt.figure(figsize=(6,4));plt.plot(order,q["mean"],marker="o")
    plt.fill_between(np.arange(3),q["mean"]-1.96*q["sem"].fillna(0),q["mean"]+1.96*q["sem"].fillna(0),alpha=.15)
    plt.ylabel("Bias score");plt.title("H6 bias induction and reset");savefig("P06_bias_reversal")

def make_all():
    p=RESULTS/"primary_seed_metrics.csv"
    if not p.exists():raise SystemExit("No primary_seed_metrics.csv yet")
    df=pd.read_csv(p)
    for h,(a,b,name) in PAIR.items():pairplot(df,h,a,b,name)

    # required secondary-gate plots
    pairplot(df,"H4","guardrail","no_guardrail","P04b_guardrail_return","secondary_value_1","Utility")
    pairplot(df,"H7","finite","long","P07b_shutdown_resistance","secondary_value_1","Shutdown-resistance rate")
    pairplot(df,"H16","full","ablation","P16b_full_architecture_return","secondary_value_1","Return")

    t1=RESULTS/"study1_trajectories.csv";t2=RESULTS/"study2_trajectories.csv";t3=RESULTS/"study3_trajectories.csv"
    trajplot(t1,"H5","P05_bias_dose_response");categorical_h6(t1)
    trajplot(t2,"H12","P12b_continual_trajectory");trajplot(t2,"H14","P14b_cost_learning_curve")
    trajplot(t3,"H18","P18_QBFT_liveness");trajplot(t3,"H19","P19b_quorum_collusion")
    trajplot(t3,"H20","P20b_error_correlation");trajplot(t3,"H22","P22_partition_recovery")
    trajplot(t3,"H24","P24b_governance_trajectory")
    print("Plots written to",PLOTS)
