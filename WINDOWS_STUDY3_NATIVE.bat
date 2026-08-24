@echo off
setlocal
if "%BESU_HOME%"=="" (
  echo BESU_HOME is not set.
  echo Example: set BESU_HOME=C:\besu\besu-26.5.0
  exit /b 2
)
java -version
if errorlevel 1 exit /b 2
"%BESU_HOME%\bin\besu.bat" --version
if errorlevel 1 exit /b 2
python blockchain\setup_qbft.py --generate
if errorlevel 1 exit /b 2
python blockchain\manage_qbft.py start
if errorlevel 1 exit /b 2
python blockchain\deploy_contract.py
if errorlevel 1 exit /b 2
python run_tests.py --study 3 --mode smoke --real-besu
if errorlevel 1 exit /b 2
python run_tests.py --plots
python run_tests.py --analyze
python validate_results.py results
endlocal
