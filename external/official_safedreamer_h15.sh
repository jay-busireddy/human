#!/usr/bin/env bash
set -euo pipefail
: "${SAFEDREAMER_REPO:?Set SAFEDREAMER_REPO to the cloned official PKU-Alignment/SafeDreamer repo}"
SEED="${1:-0}"
STEPS="${2:-500000}"
cd "$SAFEDREAMER_REPO"
python SafeDreamer/train.py \
  --configs osrp_lag \
  --method osrp_lag \
  --task safetygym_SafetyPointGoal1-v0 \
  --seed "$SEED" \
  --run.steps "$STEPS" \
  --jax.platform cpu
