@echo off
REM Humanizing Control v1.4.0 Windows environment template
call conda activate human
set "PROJECT_ROOT=C:\CHANGE_ME\Humanizing_Control_Full_v1_4_0_WindowsNative"
set "DINOV3_WEIGHTS=C:\CHANGE_ME\dinov3_vits16_pretrain_lvd1689m-08c60483.pth"
set "BESU_HOME=C:\besu\besu-26.5.0"
set "DINOV3_REPO=%PROJECT_ROOT%\external\dinov3"
set "HC_DINO_BACKEND=auto"
set "HC_OPENVINO_DEVICE=GPU"
set "HC_DINO_BATCH=128"
set "HC_CNN_BATCH=256"
set "HC_BESU_OPTS_PARALLEL=-Xms96m -Xmx256m"
set "HC_BESU_START_TIMEOUT_PARALLEL=180"
set "HC_BESU_START_RETRIES=2"
set "HC_BESU_START_RETRY_DELAY=5"
set "HC_BESU_BURST_GAP=0.20"
set "HC_BESU_POST_BURST_PAUSE=1.0"
set "HC_QBFT_HEALTH_TIMEOUT=90"
set "HC_QBFT_HEALTH_RESTARTS=2"
set "HC_QBFT_RESTART_PAUSE=4"
set "HC_DEPLOY_TIMEOUT=180"
cd /d "%PROJECT_ROOT%"
python --version
java -version
"%BESU_HOME%\bin\besu.bat" --version
