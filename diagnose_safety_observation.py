import safety_gymnasium
import numpy as np

env=safety_gymnasium.make("SafetyPointGoal1-v0")
obs,info=env.reset(seed=1)
print("observation shape:", np.asarray(obs).shape)
print("obs_space_dict:")
print(env.obs_space_dict)
offset=0
for name,space in env.obs_space_dict.items():
    size=int(np.prod(space.shape))
    print(f"{name:24s} flat[{offset}:{offset+size}] shape={space.shape}")
    offset += size
env.close()
