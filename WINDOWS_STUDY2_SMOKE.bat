@echo off
setlocal

echo === Humanizing Control v1.1 - Windows Study 2 smoke ===
python --version
python -m pip install --upgrade pip
python -m pip install torchmetrics omegaconf ftfy regex submitit termcolor "transformers>=4.56.0" huggingface-hub accelerate

python -c "import torch, torchmetrics; print('torch', torch.__version__, 'torchmetrics', torchmetrics.__version__)"
python -c "import safety_gymnasium; e=safety_gymnasium.make('SafetyPointGoal1-v0'); o,i=e.reset(seed=1); print('SafetyPointGoal1-v0 OK', o.shape); e.close()"

REM Run the non-DINO Study 2 tests first.
python run_tests.py --study 2 --mode smoke --hypotheses H11 H12 H13 H14 H15 H16
if errorlevel 1 exit /b %errorlevel%

echo.
echo Non-DINO Study 2 smoke tests completed.
echo Configure DINOv3, then run:
echo   python run_tests.py --study 2 --mode smoke --hypotheses H9 H10
endlocal
