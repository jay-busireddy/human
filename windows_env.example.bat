@echo off
REM Humanizing Control Windows environment template.
REM Copy this file to windows_env.bat, edit the three local paths, then run:
REM   call windows_env.bat

call conda activate human

set "PROJECT_ROOT=C:\CHANGE_ME\Humanizing_Control_Executable_H1-H24_Tests_v1_2_5"
set "DINOV3_WEIGHTS=C:\CHANGE_ME\dinov3_vits16_pretrain_lvd1689m-08c60483.pth"
set "BESU_HOME=C:\besu\besu-26.5.0"

set "DINOV3_REPO=%PROJECT_ROOT%\external\dinov3"
set "HC_BESU_OPTS=-Xms128m -Xmx512m"

cd /d "%PROJECT_ROOT%"

echo PROJECT_ROOT=%PROJECT_ROOT%
echo DINOV3_REPO=%DINOV3_REPO%
echo DINOV3_WEIGHTS=%DINOV3_WEIGHTS%
echo BESU_HOME=%BESU_HOME%
echo HC_BESU_OPTS=%HC_BESU_OPTS%
python --version
java -version
"%BESU_HOME%\bin\besu.bat" --version
