# Humanizing Control + Cognitive Framework — Final Reproducibility Runbook

**Platform:** Windows 10/11, Windows CMD for the Humanizing Control H1–H24 suite  
**Reference implementation:** final stabilized Windows-native code after the H22/QBFT recovery fixes (post-v1.4.13)  
**Python:** 3.10.x  
**Blockchain:** Hyperledger Besu 26.5.0, Java 21, local permissioned QBFT  

This is the canonical runbook to place in the public GitHub repository. It replaces older v1.2/v1.3/v1.4.x execution notes that were produced during debugging.

The runbook has two independent parts:

1. **Humanizing Control H1–H24** — the three-study stress test used by the Humanizing Control paper and as companion evidence in the Cognitive Framework paper.
2. **Cognitive Framework 18-mechanism suite** — the separate synthetic validation already reported in the Cognitive Framework paper. Its statistics are not merged with the H1–H24 families.

---

## 1. Reproducibility rules

Before running confirmatory tests, keep these rules fixed:

- Do not alter hypotheses after seeing confirmatory outcomes.
- Do not replace failed seeds simply because they are unfavorable.
- Do not tune thresholds, safety margins, quorum rules, fault durations, seed schedules, model checkpoints, or analysis rules on confirmatory results.
- Smoke tests are software/infrastructure checks only; their p-values have no inferential role.
- Confirmatory Study 1 uses 60 seeds.
- Confirmatory Study 2 uses 40 seeds.
- Confirmatory Study 3 targets 40 seeds.
- H22 is intentionally stressful. If a seed terminates because the finalized recovery code reaches a genuine no-progress failure, preserve it as an execution failure rather than silently replacing it.
- H22 on Windows is a **validator process outage/isolation and recovery test**, not a packet-level network-partition experiment.
- QBFT agreement/finality is not itself semantic AI safety. Cognitive validators provide semantic judgments; QBFT provides ordering, agreement, finality, and auditable provenance for records.

The published reference run completed 37 of the 40 planned H22 seeds. A new replication should still attempt the frozen 40-seed schedule and report the actual completion count.

---

# PART I — HUMANIZING CONTROL H1–H24

## 2. Clone the repository and select the Python environment

Example:

```bat
git clone <YOUR_GITHUB_REPOSITORY_URL> Humanizing_Control
cd /d Humanizing_Control
set "PROJECT_ROOT=%CD%"
```

If the `human` Conda environment already exists:

```bat
conda activate human
python --version
```

Expected Python family:

```text
Python 3.10.x
```

If creating a new environment:

```bat
conda create -n human python=3.10 -y
conda activate human
python -m pip install --upgrade pip setuptools wheel
```

---

## 3. Install and verify Python dependencies

```bat
cd /d "%PROJECT_ROOT%"
python -m pip install -r requirements.txt
python -m pip check
```

The final Windows configuration was tested with the following important compatibility pins:

```text
numpy==1.23.5
gymnasium==0.28.1
gymnasium-robotics==1.2.2
pygame==2.1.0
mujoco==2.3.3
safety-gymnasium==1.0.0
```

Verify core imports:

```bat
python -c "import numpy,gymnasium,safety_gymnasium; print('numpy',numpy.__version__); print('gymnasium',gymnasium.__version__); print('Safety-Gymnasium OK')"
```

Optional syntax check of the main final files:

```bat
python -m py_compile run_tests.py
python -m py_compile run_study2_optimized.py
python -m py_compile run_study3_parallel.py
python -m py_compile study3_lanes.py
python -m py_compile hc_tests\study1.py
python -m py_compile hc_tests\study2.py
python -m py_compile hc_tests\study3.py
python -m py_compile blockchain\manage_qbft.py
python -m py_compile blockchain\qbft_health.py
python -m py_compile blockchain\tx_canary.py
```

---

## 4. Configure DINOv3 for H9/H10

Confirmatory H9/H10 require the official frozen DINOv3 ViT-S/16 representation configured by the repository.

Keep the external DINOv3 repository outside Git history or clone it into the ignored path:

```text
%PROJECT_ROOT%\external\dinov3
```

Set the source and checkpoint paths. Replace the checkpoint path with the local path on the machine running the test:

```bat
set "DINOV3_REPO=%PROJECT_ROOT%\external\dinov3"
set "DINOV3_WEIGHTS=C:\path\to\dinov3_vits16_pretrain_lvd1689m-08c60483.pth"
set "HC_DINO_BACKEND=auto"
set "HC_OPENVINO_DEVICE=GPU"
set "HC_DINO_BATCH=128"
set "HC_CNN_BATCH=256"
```

Verify the checkpoint exists:

```bat
dir "%DINOV3_WEIGHTS%"
```

If OpenVINO is installed, inspect available devices:

```bat
python -c "import openvino as ov; c=ov.Core(); print('OpenVINO',ov.__version__); print('devices',c.available_devices)"
```

Run the representation/acceleration equivalence preflight:

```bat
python study2_accel_preflight.py
```

If Intel GPU/OpenVINO does not work but the PyTorch DINO path works:

```bat
set "HC_DINO_BACKEND=torch"
```

Do not change the DINO model, weights, dataset semantics, thresholds, or hyperparameters merely to reduce runtime.

---

## 5. Install and configure native Besu QBFT

The final Windows-native suite was tested with:

```text
Hyperledger Besu 26.5.0
Java 21
```

Set the tested Besu location:

```bat
set "BESU_HOME=C:\besu\besu-26.5.0"
```

Verify:

```bat
java -version
"%BESU_HOME%\bin\besu.bat" --version
```

The expected Besu version string is in the 26.5.0 family.

### 5.1 Base Study 3 operational environment

Set these before any Study 3 smoke or confirmatory run:

```bat
set "HC_BESU_OPTS_PARALLEL=-Xms96m -Xmx256m"
set "HC_BESU_START_TIMEOUT_PARALLEL=180"
set "HC_BESU_START_RETRIES=2"
set "HC_BESU_START_RETRY_DELAY=5"
set "HC_BESU_BURST_GAP=0.20"
set "HC_BESU_POST_BURST_PAUSE=1.0"
set "HC_BESU_TCP_PROBE_TIMEOUT=1.5"
set "HC_RPC_TIMEOUT=8"
set "HC_QBFT_HEALTH_TIMEOUT=90"
set "HC_QBFT_HEALTH_RESTARTS=2"
set "HC_QBFT_RESTART_PAUSE=4"
set "HC_DEPLOY_TIMEOUT=180"
set "HC_TX_TIMEOUT=60"
set "HC_H23_TX_TIMEOUT=180"
set "HC_TX_CANARY_TIMEOUT=45"
set "HC_TX_GAS_PRICE=1000"
set "HC_BESU_TX_POOL_MODE=sequenced"
```

The transaction canary internally enforces a longer minimum attempt window when required after QBFT recovery.

### 5.2 Recovery-specific environment

These settings change only infrastructure waiting/recovery behavior; they do not change hypothesis seeds, outage durations, transaction counts, quorum rules, or scientific endpoints.

```bat
set "HC_STUDY3_RECOVERY_LOCK_TIMEOUT=1200"
set "HC_BESU_RECOVERY_START_TIMEOUT=300"
set "HC_BESU_RECOVERY_LOCK_TIMEOUT=900"
set "HC_BESU_RECOVERY_RPC_TIMEOUT=15"
set "HC_H18_RECOVERY_HEALTH_TIMEOUT=300"
set "HC_H22_RECOVERY_HEALTH_TIMEOUT=300"
set "HC_H22_CONVERGENCE_TIMEOUT=180"
set "HC_H22_CONVERGENCE_MAX_TIMEOUT=600"
set "HC_H22_HASH_RPC_TIMEOUT=60"
set "HC_H24_HEALTH_TIMEOUT=300"
set "HC_H24_TX_TIMEOUT=180"
```

### 5.3 Final H22 progress-based recovery settings

The final H22 implementation uses fixed checkpoints and progress/no-progress detection rather than requiring every node to equal a continuously moving chain head.

```bat
set "HC_QBFT_MAX_CATCHUP_TIMEOUT=3600"
set "HC_QBFT_PROGRESS_GRACE=300"
set "HC_H22_TOTAL_CATCHUP_TIMEOUT=3600"
set "HC_H22_NO_PROGRESS_TIMEOUT=300"
set "HC_H22_POST_REPAIR_NO_PROGRESS_TIMEOUT=600"
set "HC_H22_PROGRESS_REPORT_EVERY=60"
set "HC_H22_CATCHUP_POLL=2"
```

The final behavior is:

- a lagging validator that is making progress is allowed to continue syncing;
- a validator is targeted for one restart only after a frozen no-progress interval;
- H22 waits for all seven validators to possess the same **fixed post-recovery block**;
- all seven block hashes at that fixed block must agree;
- before the next H22 fault case, a synchronized baseline barrier is restored so synchronization debt does not accumulate across cases.

---

## 6. Verify no old Besu test processes are running

Before a fresh Study 3 run:

```bat
cd /d "%PROJECT_ROOT%"
python study3_lanes.py stop
python study3_lanes.py status
```

Check test RPC ports if necessary:

```bat
netstat -ano | findstr ":8545 :8546 :8547 :8548 :8549 :8550 :8551 :8645 :8646 :8647 :8648 :8649 :8650 :8651"
```

Check P2P ports if necessary:

```bat
netstat -ano | findstr ":30303 :30304 :30305 :30306 :30307 :30308 :30309 :30403 :30404 :30405 :30406 :30407 :30408 :30409"
```

Inspect a PID before killing anything:

```bat
powershell -Command "Get-CimInstance Win32_Process -Filter \"ProcessId=<PID>\" | Select-Object ProcessId,ParentProcessId,CommandLine | Format-List"
```

Only terminate a process after confirming it belongs to this local Besu test network.

---

# PART II — SMOKE TEST

## 7. Create a clean smoke result directory

```bat
cd /d "%PROJECT_ROOT%"
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_smoke"
if exist "%HC_RESULTS_DIR%" rmdir /S /Q "%HC_RESULTS_DIR%"
```

Smoke seed ranges are intentionally separate from confirmatory seeds:

```text
Study 1: 900000-900003
Study 2: 901000-901003
Study 3: 902000-902003
```

---

## 8. Study 1 smoke — H1–H8

```bat
python run_tests.py --study 1 --mode smoke
```

---

## 9. Study 2 smoke — H9–H16

```bat
python run_study2_optimized.py --mode smoke
```

Optional focused smoke commands:

```bat
python run_study2_optimized.py --mode smoke --hypotheses H9 H10
python run_study2_optimized.py --mode smoke --hypotheses H11 H12 H13 H14 H15 H16
```

The optimized Study 2 runner uses checkpoints/cache files. If interrupted, restore the environment variables and rerun the same command.

---

## 10. Study 3 smoke preflight

```bat
python study3_lanes.py stop
python run_study3_parallel.py --mode smoke --lanes 2 --fresh --prepare-only --keep-running
```

Both lanes must reach:

```text
QBFT CONSENSUS_READY
TX_CANARY PASS
```

and the runner must report:

```text
PREPARE_ONLY PASS
```

Do not run H17–H24 if infrastructure preflight fails.

The health proof requires seven-validator membership, chain advancement, all seven RPCs, a common fixed checkpoint/hash, and the expected transaction-pool mode.

---

## 11. Study 3 smoke — H17–H24

For the short four-seed smoke suite, reuse the prepared two-lane networks:

```bat
python run_study3_parallel.py --mode smoke --lanes 2
```

Do **not** use `--fresh` on that second command.

The internal Study 3 ordering intentionally isolates disruptive groups. H22 remains a process outage/isolation test rather than a packet-level network partition.

---

## 12. Validate smoke coverage

```bat
python validate_results.py "%HC_RESULTS_DIR%" --mode smoke
```

Expected target:

```text
H1-H24: 4 smoke seeds each
```

Smoke output is not used in the confirmatory statistical claims.

Stop the smoke lanes:

```bat
python study3_lanes.py stop
```

Keep `results_smoke` as a software-validation artifact.

---

# PART III — FREEZE BEFORE CONFIRMATORY

## 13. Freeze code, environment, and model provenance

Do this only after smoke passes and before inspecting any confirmatory outcome.

```bat
cd /d "%PROJECT_ROOT%"
if not exist freeze mkdir freeze
```

### 13.1 Git state

```bat
git rev-parse HEAD > freeze\repo_git_commit.txt
git status --porcelain > freeze\repo_git_status.txt
```

For a clean archival confirmatory run, `repo_git_status.txt` should ideally be empty.

### 13.2 Runtime versions

```bat
python -m pip freeze > freeze\pip_freeze.txt
python --version > freeze\python_version.txt 2>&1
java -version > freeze\java_version.txt 2>&1
"%BESU_HOME%\bin\besu.bat" --version > freeze\besu_version.txt 2>&1
```

### 13.3 DINOv3 provenance

```bat
certutil -hashfile "%DINOV3_WEIGHTS%" SHA256 > freeze\dinov3_checkpoint_sha256.txt
cd /d "%DINOV3_REPO%"
git rev-parse HEAD > "%PROJECT_ROOT%\freeze\dinov3_git_commit.txt"
cd /d "%PROJECT_ROOT%"
```

### 13.4 Core experiment/source hashes

```bat
certutil -hashfile requirements.txt SHA256 > freeze\requirements_sha256.txt
certutil -hashfile config.json SHA256 > freeze\config_sha256.txt
certutil -hashfile run_tests.py SHA256 > freeze\run_tests_sha256.txt
certutil -hashfile run_study2_optimized.py SHA256 > freeze\run_study2_optimized_sha256.txt
certutil -hashfile run_study3_parallel.py SHA256 > freeze\run_study3_parallel_sha256.txt
certutil -hashfile study3_lanes.py SHA256 > freeze\study3_lanes_sha256.txt
certutil -hashfile hc_tests\study1.py SHA256 > freeze\study1_sha256.txt
certutil -hashfile hc_tests\study2.py SHA256 > freeze\study2_sha256.txt
certutil -hashfile hc_tests\study3.py SHA256 > freeze\study3_sha256.txt
certutil -hashfile hc_tests\safe_rl.py SHA256 > freeze\safe_rl_sha256.txt
certutil -hashfile blockchain\native_control.py SHA256 > freeze\native_control_sha256.txt
certutil -hashfile blockchain\setup_qbft.py SHA256 > freeze\setup_qbft_sha256.txt
certutil -hashfile blockchain\manage_qbft.py SHA256 > freeze\manage_qbft_sha256.txt
certutil -hashfile blockchain\qbft_health.py SHA256 > freeze\qbft_health_sha256.txt
certutil -hashfile blockchain\tx_canary.py SHA256 > freeze\tx_canary_sha256.txt
certutil -hashfile blockchain\deploy_contract.py SHA256 > freeze\deploy_contract_sha256.txt
```

After freezing, do not modify scientific parameters based on partial confirmatory outcomes. A later execution-bug correction must be explicitly documented.

---

# PART IV — CONFIRMATORY STUDIES 1 AND 2

## 14. Create the base confirmatory result directory

This directory will hold:

- H1–H16;
- H17–H21 and H23;
- later it will be copied into the final combined result tree.

```bat
cd /d "%PROJECT_ROOT%"
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_confirmatory"
if exist "%HC_RESULTS_DIR%" rmdir /S /Q "%HC_RESULTS_DIR%"
```

Confirmatory seed targets:

```text
Study 1: 1000-1059  = 60 seeds
Study 2: 2000-2039  = 40 seeds
Study 3: 3000-3039  = 40 seeds
```

---

## 15. Study 1 confirmatory — H1–H8

```bat
python run_tests.py --study 1 --mode confirmatory
```

Do not delete `results_confirmatory` after this point.

---

## 16. Study 2 confirmatory — H9–H16

Restore the DINO variables if this is a new CMD session:

```bat
set "DINOV3_REPO=%PROJECT_ROOT%\external\dinov3"
set "DINOV3_WEIGHTS=C:\path\to\dinov3_vits16_pretrain_lvd1689m-08c60483.pth"
set "HC_DINO_BACKEND=auto"
set "HC_OPENVINO_DEVICE=GPU"
set "HC_DINO_BATCH=128"
set "HC_CNN_BATCH=256"
```

Run:

```bat
python run_study2_optimized.py --mode confirmatory
```

If Windows reboots or the process is interrupted:

```bat
conda activate human
cd /d <YOUR_REPOSITORY_DIRECTORY>
set "PROJECT_ROOT=%CD%"
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_confirmatory"
```

restore the DINO variables and rerun:

```bat
python run_study2_optimized.py --mode confirmatory
```

Completed stages/seeds are checkpointed and skipped.

---

# PART V — STUDY 3 FINAL STABILIZED EXECUTION STRATEGY

The final public runbook intentionally does **not** run every Study 3 hypothesis through one long two-lane lifecycle.

Use:

```text
H17,H18,H19,H20,H21,H23 -> normal two-lane confirmatory tree
H22                    -> dedicated fresh one-lane tree
H24                    -> dedicated fresh one-lane tree
```

This preserves the scientific load while avoiding unnecessary carry-over of repeated H22 outage/recovery history into H24.

---

## 17. Study 3A — H17,H18,H19,H20,H21,H23 on two lanes

Point back to the base confirmatory directory:

```bat
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_confirmatory"
```

Restore all Besu/recovery environment variables from Sections 5.1–5.3.

Stop any previous lanes:

```bat
python study3_lanes.py stop
```

Create and verify two fresh seven-validator lanes:

```bat
python run_study3_parallel.py --mode confirmatory --lanes 2 --fresh --prepare-only --keep-running
```

Require on both lanes:

```text
QBFT CONSENSUS_READY
TX_CANARY PASS
```

Run only the six hypotheses assigned to this base Study 3 block:

```bat
python run_study3_parallel.py --mode confirmatory --lanes 2 --hypotheses H17 H18 H19 H20 H21 H23
```

Target seed coverage is 40 for each of these hypotheses, split across the two lanes.

### Interruption rule

If this invocation is interrupted **before it completes**, restore the same environment and rerun the same command without `--fresh`:

```bat
python run_study3_parallel.py --mode confirmatory --lanes 2 --hypotheses H17 H18 H19 H20 H21 H23
```

Do not use `--fresh` when resuming existing checkpoints.

After a fully successful invocation has completed and merged its lane output, do not repeatedly rerun the already-completed successful command merely for reassurance; preserve the generated result tree.

Stop the lanes after this block is complete:

```bat
python study3_lanes.py stop
```

---

## 18. Study 3B — H22 on one fresh dedicated lane

H22 uses the full frozen scientific workload:

```text
40 planned seeds: 3000-3039
2 outage/isolation patterns: 5|2 and 4|3
4 durations per pattern: 5, 15, 30, 60 seconds
8 fault/recovery cases per completed seed
```

The final implementation restores a synchronized seven-validator baseline before every fault case and compares finality at a fixed post-recovery checkpoint.

Create a separate result tree:

```bat
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_h22_final"
```

For the **first** final H22 start only:

```bat
python study3_lanes.py stop
if exist "%HC_RESULTS_DIR%" rmdir /S /Q "%HC_RESULTS_DIR%"
```

Make sure the final H22 settings are present:

```bat
set "HC_QBFT_MAX_CATCHUP_TIMEOUT=3600"
set "HC_QBFT_PROGRESS_GRACE=300"
set "HC_H22_RECOVERY_HEALTH_TIMEOUT=300"
set "HC_H22_CONVERGENCE_TIMEOUT=180"
set "HC_H22_CONVERGENCE_MAX_TIMEOUT=600"
set "HC_H22_HASH_RPC_TIMEOUT=60"
set "HC_H22_TOTAL_CATCHUP_TIMEOUT=3600"
set "HC_H22_NO_PROGRESS_TIMEOUT=300"
set "HC_H22_POST_REPAIR_NO_PROGRESS_TIMEOUT=600"
set "HC_H22_PROGRESS_REPORT_EVERY=60"
set "HC_H22_CATCHUP_POLL=2"
```

Start the clean final H22 run:

```bat
python run_study3_parallel.py --mode confirmatory --lanes 1 --fresh --hypotheses H22
```

### H22 resume rule

If the process is interrupted, **do not use `--fresh`**. Resume using the same result directory and chain:

```bat
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_h22_final"
python run_study3_parallel.py --mode confirmatory --lanes 1 --hypotheses H22
```

Completed seed checkpoints are skipped.

### Expected progress messages

It is normal to see messages similar to:

```text
[Study3 H22] baseline barrier target=...
[Study3 H22] waiting for fixed block ... validators=[...]
```

If `eth_syncing`/block height is moving forward, do not manually restart the validator. The finalized code waits for progress.

A meaningful execution failure is a validator that remains unable to reach the fixed checkpoint after the frozen no-progress/repair rules, or a hash disagreement at the same fixed checkpoint.

### Published-reference note

The published reference run completed 37/40 planned seeds. It recorded zero conflicting-finality seed outcomes among those completed executions, but the three missing seeds remain reported as execution failures/incomplete runs. Do not change the code to manufacture 40/40 completion after seeing the result.

Stop the lane when H22 is complete or intentionally terminated under the frozen stopping rule:

```bat
python study3_lanes.py stop
```

---

## 19. Study 3C — H24 on a separate fresh dedicated lane

H24 should not inherit the heavily faulted H22 blockchain history.

Create a separate result tree:

```bat
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_h24_final"
```

First start only:

```bat
python study3_lanes.py stop
if exist "%HC_RESULTS_DIR%" rmdir /S /Q "%HC_RESULTS_DIR%"
```

Set the H24 operational timeouts:

```bat
set "HC_H24_HEALTH_TIMEOUT=300"
set "HC_H24_TX_TIMEOUT=180"
```

Run H24 on one fresh seven-validator lane:

```bat
python run_study3_parallel.py --mode confirmatory --lanes 1 --fresh --hypotheses H24
```

If interrupted, resume without `--fresh`:

```bat
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_h24_final"
python run_study3_parallel.py --mode confirmatory --lanes 1 --hypotheses H24
```

The final H24 code resets the two affected cognitive-validator contract flags before each seed, verifies the pre-seed active state, performs governance deactivation, and verifies the resulting on-chain state.

Stop the lane after completion:

```bat
python study3_lanes.py stop
```

---

# PART VI — MERGE THE THREE FINAL RESULT TREES

## 20. Create `results_final_combined`

At this point the final evidence lives in three locations:

```text
results_confirmatory   -> H1-H21 except H22, plus H23
results_h22_final      -> H22
results_h24_final      -> H24
```

Create a clean combined tree from the base confirmatory output:

```bat
cd /d "%PROJECT_ROOT%"
if exist "%PROJECT_ROOT%\results_final_combined" rmdir /S /Q "%PROJECT_ROOT%\results_final_combined"
xcopy /E /I /Y "%PROJECT_ROOT%\results_confirmatory" "%PROJECT_ROOT%\results_final_combined"
```

Now merge only H22 and H24 primary/trajectory rows. The command deliberately filters H22/H24 out of the base before adding the dedicated final sources, so repeated earlier development rows are not mixed in.

```bat
python -c "import pandas as pd,pathlib; r=pathlib.Path(r'%PROJECT_ROOT%'); o=r/'results_final_combined'; pick=lambda d,f:(d/'_study3_lane1'/f) if (d/'_study3_lane1'/f).exists() else d/f; p=pd.read_csv(o/'primary_seed_metrics.csv'); p=p[~p['hypothesis_id'].isin(['H22','H24'])]; d22=r/'results_h22_final'; d24=r/'results_h24_final'; h22=pd.read_csv(pick(d22,'primary_seed_metrics.csv')); h22=h22[h22['hypothesis_id'].eq('H22')]; h24=pd.read_csv(pick(d24,'primary_seed_metrics.csv')); h24=h24[h24['hypothesis_id'].eq('H24')]; pd.concat([p,h22,h24],ignore_index=True).to_csv(o/'primary_seed_metrics.csv',index=False); t=pd.read_csv(o/'study3_trajectories.csv'); t=t[~t['hypothesis_id'].isin(['H22','H24'])]; t22=pd.read_csv(pick(d22,'study3_trajectories.csv')); t22=t22[t22['hypothesis_id'].eq('H22')]; t24=pd.read_csv(pick(d24,'study3_trajectories.csv')); t24=t24[t24['hypothesis_id'].eq('H24')]; pd.concat([t,t22,t24],ignore_index=True).to_csv(o/'study3_trajectories.csv',index=False)"
```

Do not blindly `drop_duplicates()` from H22 trajectory rows: some exact-looking rows can represent distinct configured fault cases. The merge above uses source separation and hypothesis filtering instead.

---

## 21. Check final seed coverage

```bat
python -c "import pandas as pd; d=pd.read_csv(r'%PROJECT_ROOT%\results_final_combined\primary_seed_metrics.csv'); print(d.groupby(['study_id','hypothesis_id'])['seed'].nunique().to_string())"
```

Target for a completely executed replication:

```text
H1-H8   : 60 each
H9-H16  : 40 each
H17-H24 : 40 each
```

For the published reference evidence, H22 has 37 completed seeds. Preserve the actual count.

---

# PART VII — FINAL ANALYSIS, PLOTS, VALIDATION, AND PACKAGE

## 22. Select the combined result directory

```bat
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_final_combined"
```

Remove stale analysis/plot products copied from an earlier base tree, if present:

```bat
if exist "%HC_RESULTS_DIR%\hypothesis_tests.csv" del /Q "%HC_RESULTS_DIR%\hypothesis_tests.csv"
if exist "%HC_RESULTS_DIR%\plots" rmdir /S /Q "%HC_RESULTS_DIR%\plots"
```

---

## 23. Run the final statistical analysis

```bat
python run_tests.py --analyze
```

The analyzer writes:

```text
results_final_combined\hypothesis_tests.csv
```

The analysis applies the repository's frozen rules, including separate Holm families for:

```text
H1-H8
H9-H16
H17-H24
```

and special logic for the preregistered equivalence/composite/zero-event hypotheses.

Inspect:

```bat
type "%HC_RESULTS_DIR%\hypothesis_tests.csv"
```

Do not alter the analyzer because a hypothesis fails to meet its prespecified rule.

---

## 24. Generate final P01–P24 plots

```bat
python run_tests.py --plots
```

Inspect:

```bat
dir "%HC_RESULTS_DIR%\plots"
```

Plots are generated programmatically from the final combined data. Do not manually delete unfavorable seeds or rescale plots to exaggerate effects.

---

## 25. Run structural validation

```bat
python validate_results.py "%HC_RESULTS_DIR%" --mode confirmatory
```

For a fully completed 40-seed H22 replication, strict coverage validation should pass.

If H22 has documented execution failures (as in the published 37/40 reference run), the strict validator may correctly report incomplete H22 coverage. Preserve that validation output and document the deviation; do not fabricate missing rows.

A useful independent count check is:

```bat
python -c "import pandas as pd; d=pd.read_csv(r'%HC_RESULTS_DIR%\primary_seed_metrics.csv'); print(d.groupby(['study_id','hypothesis_id'])['seed'].nunique().to_string())"
```

---

## 26. Copy frozen provenance into the final result tree

```bat
xcopy /E /I /Y "%PROJECT_ROOT%\freeze" "%HC_RESULTS_DIR%\freeze"
copy /Y "%PROJECT_ROOT%\config.json" "%HC_RESULTS_DIR%\config.json"
```

If the repository contains a final patch/release note, copy it as well, for example:

```bat
if exist "%PROJECT_ROOT%\PATCH_NOTES_FINAL.md" copy /Y "%PROJECT_ROOT%\PATCH_NOTES_FINAL.md" "%HC_RESULTS_DIR%\PATCH_NOTES_FINAL.md"
```

Recommended: add a small `PROTOCOL_DEVIATIONS.md` or `EXECUTION_NOTES.md` in the final result tree describing any incomplete H22 seeds and any already-disclosed implementation/endpoint deviations.

---

## 27. Package the final results

```bat
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_final_combined"
python package_results.py
```

Expected artifact name in the final repository implementation:

```text
HUMANIZING_CONTROL_EXECUTABLE_TEST_RESULTS.zip
```

If a manual archive is preferred:

```bat
powershell -NoProfile -Command "Compress-Archive -Path '%HC_RESULTS_DIR%\*' -DestinationPath '%PROJECT_ROOT%\HUMANIZING_CONTROL_EXECUTABLE_TEST_RESULTS.zip' -Force"
```

The archive should contain at minimum:

```text
primary_seed_metrics.csv
study1_trajectories.csv
study2_trajectories.csv
study3_trajectories.csv
hypothesis_tests.csv
plots\
freeze\
config.json
execution/protocol notes if applicable
```

---

# PART VIII — H22/QBFT TROUBLESHOOTING WITHOUT CHANGING THE EXPERIMENT

## 28. `QBFT CONSENSUS_READY` takes a long time

The final health check uses a fixed post-start checkpoint. A validator can be hundreds of blocks behind while actively catching up.

If its height is increasing, waiting is expected. Do not repeatedly restart it just because the other validators continue producing blocks.

The health proof still requires all seven validators to reach and agree on one fixed checkpoint.

---

## 29. H22 prints `waiting for fixed block`

This is expected when a recovered validator is syncing:

```text
[Study3 H22] waiting for fixed block T; validators=[...]; heights=[...]
```

The criterion is possession of fixed block `T`, not equality with the moving latest head.

---

## 30. H22 baseline barrier

Before every H22 fault injection, the final code restores a common baseline. This prevents lag from one 5/15/30/60-second outage from contaminating the next outage.

Do not remove this barrier to make the test faster.

---

## 31. Transaction canary fails while blocks advance

`TX_CANARY` is an infrastructure readiness test, not a hypothesis endpoint. It broadcasts a valid signed transaction and searches for the receipt across all seven RPCs.

If it fails, do not start scientific Study 3 cases until the infrastructure problem is understood.

---

## 32. Optional native-library warnings

Messages about unavailable native alt-bn128/BoringSSL implementations are not automatically fatal if Besu continues through synchronization, JSON-RPC startup, and BFT mining startup.

Use the explicit health/canary gates rather than treating every warning as a test failure.

---

## 33. Resume versus `--fresh`

Use `--fresh` only when intentionally starting a new experimental blockchain/result tree.

Use **no `--fresh`** when resuming an interrupted run:

```text
H17-H21/H23 resume -> same results_confirmatory, same two-lane command, no --fresh
H22 resume          -> same results_h22_final, one lane, no --fresh
H24 resume          -> same results_h24_final, one lane, no --fresh
```

`--fresh` intentionally creates new chain state and clears lane checkpoints. Using it accidentally during a resume discards experimental continuity.

---

# PART IX — GITHUB SAFETY AND REPOSITORY HYGIENE

## 34. Files that must never be committed

Generated cognitive and disposable accounts contain private keys. Never publish them:

```text
blockchain\generated_lane1\cognitive_accounts.json
blockchain\generated_lane1\h17_accounts.json
blockchain\generated_lane2\cognitive_accounts.json
blockchain\generated_lane2\h17_accounts.json
```

The same rule applies to equivalent files under any `generated` directory.

Validator node private keys and chain databases are generated locally and should not be committed.

---

## 35. Minimum public `.gitignore`

At minimum include:

```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.venv*/

# Local Conda/IDE artifacts
.vscode/
.idea/

# External model source/weights
external/dinov3/
*.pth
*.pt
*.ckpt

# Generated Besu/QBFT state and PRIVATE KEYS
blockchain/generated/
blockchain/generated_lane1/
blockchain/generated_lane2/
blockchain/generated_lane*/
blockchain/.study3_recovery.lock
**/cognitive_accounts.json
**/h17_accounts.json
**/node.log
*.pid

# Local transient logs/cache
*.log
*.tmp
```

If final result data will be distributed through a GitHub Release/Zenodo artifact rather than tracked in Git, optionally also ignore:

```gitignore
results_smoke/
results_confirmatory/
results_h22_final/
results_h24_final/
results_final_combined/
HUMANIZING_CONTROL_EXECUTABLE_TEST_RESULTS.zip
```

If small CSVs/plots are intentionally committed for reproducibility, do not add those result patterns; still exclude every private-key/generated-chain path.

---

## 36. Recommended public repository contents

Commit:

```text
README.md
FINAL_TEST_RUN_INSTRUCTIONS_WINDOWS.md
requirements.txt
config.json
run_tests.py
run_study2_optimized.py
run_study3_parallel.py
study3_lanes.py
study2_accel_preflight.py
package_results.py
validate_results.py
hc_tests\
blockchain\ source scripts only
protocol / hypothesis documentation
paper-facing result schema documentation
LICENSE
CITATION.cff (recommended)
```

Do not commit the DINO checkpoint itself unless its license explicitly permits redistribution and you intentionally want to host a large model file. Prefer recording its SHA-256 and acquisition instructions.

---

# PART X — SCIENTIFIC INTERPRETATION BOUNDARIES

## 37. Study 1

H1–H8 are controlled behavioral mechanism tests. Personality/PAD/bias labels are engineering constructs and should not be treated as proof of human psychological equivalence.

---

## 38. Study 2

H9/H10 use real frozen DINOv3 representations and controlled Safety-Gymnasium-derived data/tasks. H11–H16 are controlled memory/replay/safe-control mechanism tests.

A positive synthetic mechanism result is not a claim of universal embodied performance.

---

## 39. Study 3

- H17/H18/H21/H22/H23 use real local Besu/QBFT/on-chain execution where implemented.
- H19/H20 are cognitive-validator simulations for quorum/diversity comparisons.
- H22 is validator process outage/isolation/recovery on one Windows host, not a packet-level network partition and not a test of arbitrary malicious Byzantine peers.
- H24 evaluates cognitive-validator governance and records the governance state change on-chain when the real chain is enabled.
- The seven local Besu validators are separate processes but run on one physical machine; this is a protocol-level local testbed, not a geographically distributed production deployment.

---

# PART XI — OPTIONAL COGNITIVE FRAMEWORK 18-MECHANISM SUITE

The Cognitive Framework paper also reports a separate 18-hypothesis synthetic validation suite. It is a different confirmatory family from Humanizing Control H1–H24.

Do **not** merge its p-values with H1–H24.

## 40. Cognitive validation smoke

If `cognitive_validation.py` and its dependencies are included in the same repository/environment:

```bat
python -m py_compile cognitive_validation.py
python -m py_compile resource_benchmark.py
```

Run smoke/software verification:

```bat
python cognitive_validation.py --preset smoke --mode all --output cognitive_smoke
```

Do not use smoke p-values in the paper.

---

## 41. Cognitive Framework final confirmatory core family

Run the 18 prespecified core mechanisms:

```bat
python cognitive_validation.py --preset confirmatory --mode core --output cognitive_confirmatory_core
```

The suite writes raw primary rows, secondary metrics, hypothesis tests, a claim matrix, a summary, and plots under the output directory.

If the repository also contains secondary/proxy constructs and they are desired, run them separately:

```bat
python cognitive_validation.py --preset confirmatory --mode all --output cognitive_confirmatory_all
```

Use the core-family Holm column for the 18 core confirmatory claims. Treat additional proxy constructs as secondary unless the manuscript explicitly prespecified otherwise.

---

## 42. Cognitive Framework resource benchmark

```bat
python resource_benchmark.py --output cognitive_resource_benchmark
```

These are machine-specific representation-memory and query-latency measurements. Do not extrapolate them to complete-agent RAM/latency without profiling the complete system.

---

# PART XII — MINIMAL FINAL COMMAND CHECKLIST

The following is a compact reminder after the environment is already installed and configured.

```bat
REM ============================================================
REM SMOKE
REM ============================================================
conda activate human
cd /d <YOUR_REPOSITORY_DIRECTORY>
set "PROJECT_ROOT=%CD%"
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_smoke"

python run_tests.py --study 1 --mode smoke
python run_study2_optimized.py --mode smoke
python study3_lanes.py stop
python run_study3_parallel.py --mode smoke --lanes 2 --fresh --prepare-only --keep-running
python run_study3_parallel.py --mode smoke --lanes 2
python validate_results.py "%HC_RESULTS_DIR%" --mode smoke
python study3_lanes.py stop

REM ============================================================
REM FREEZE
REM ============================================================
git rev-parse HEAD > freeze\repo_git_commit.txt
python -m pip freeze > freeze\pip_freeze.txt

REM ============================================================
REM CONFIRMATORY STUDY 1 + STUDY 2
REM ============================================================
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_confirmatory"
python run_tests.py --study 1 --mode confirmatory
python run_study2_optimized.py --mode confirmatory

REM ============================================================
REM STUDY 3A: H17,H18,H19,H20,H21,H23 -- TWO LANES
REM ============================================================
python study3_lanes.py stop
python run_study3_parallel.py --mode confirmatory --lanes 2 --fresh --prepare-only --keep-running
python run_study3_parallel.py --mode confirmatory --lanes 2 --hypotheses H17 H18 H19 H20 H21 H23
python study3_lanes.py stop

REM ============================================================
REM STUDY 3B: H22 -- FRESH ONE LANE
REM ============================================================
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_h22_final"
python run_study3_parallel.py --mode confirmatory --lanes 1 --fresh --hypotheses H22
python study3_lanes.py stop

REM ============================================================
REM STUDY 3C: H24 -- SEPARATE FRESH ONE LANE
REM ============================================================
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_h24_final"
python run_study3_parallel.py --mode confirmatory --lanes 1 --fresh --hypotheses H24
python study3_lanes.py stop

REM ============================================================
REM MERGE -> results_final_combined
REM Use the merge command from Section 20.
REM ============================================================
set "HC_RESULTS_DIR=%PROJECT_ROOT%\results_final_combined"
python run_tests.py --analyze
python run_tests.py --plots
python validate_results.py "%HC_RESULTS_DIR%" --mode confirmatory
python package_results.py
```

---

# PART XIII — WHAT TO CITE IN THE PAPERS

For the final Humanizing Control and Cognitive Framework revisions, cite a stable repository state rather than a moving branch whenever possible.

Recommended publication workflow:

1. Merge the final code and this runbook to the default branch.
2. Verify that no generated private-key files are tracked.
3. Create a Git tag/release, for example:

```text
humanizing-control-h1-h24-final
```

4. Record the release commit SHA.
5. Add the repository URL and release/tag/commit to both papers' reproducibility statements.
6. If the final result ZIP is too large for ordinary Git history, attach it to a GitHub Release and/or archive it separately (for example, Zenodo) while keeping the exact code commit linked.

The code commit, frozen configuration, model checkpoint hash, Besu/Java versions, seed-level CSVs, and analysis scripts together define the reproducible experimental record.

---

## End of canonical runbook

Older debugging runbooks are retained only as historical development notes. For a new replication, use this file as the primary execution reference.
