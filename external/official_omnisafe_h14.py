"""Optional external replication of H14 using the official OmniSafe package."""
import argparse,json
from pathlib import Path
import numpy as np
import omnisafe

ap=argparse.ArgumentParser();ap.add_argument("--algo",choices=["PPO","PPOLag"],required=True)
ap.add_argument("--seed",type=int,required=True);ap.add_argument("--steps",type=int,default=500000)
ap.add_argument("--out",required=True);a=ap.parse_args()
cfg={"seed":a.seed,
     "train_cfgs":{"total_steps":a.steps,"vector_env_nums":1,"parallel":1,"device":"cpu"},
     "logger_cfgs":{"use_wandb":False,"use_tensorboard":True}}
agent=omnisafe.Agent(a.algo,"SafetyPointGoal1-v0",custom_cfgs=cfg)
ret=agent.learn()
def scalar(x):
    try:return float(np.asarray(x).reshape(-1)[-1])
    except Exception:return None
out={"algo":a.algo,"seed":a.seed,"raw_return_repr":repr(ret)}
if isinstance(ret,(tuple,list)) and len(ret)>=3:
    out.update({"ep_ret":scalar(ret[0]),"ep_cost":scalar(ret[1]),"ep_len":scalar(ret[2])})
Path(a.out).write_text(json.dumps(out,indent=2))
print(out)
