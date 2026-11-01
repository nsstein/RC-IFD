@echo off
REM RC Instrument Flight Display - Windows launcher
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo First run: creating virtual environment...
  %PY% -m venv .venv || goto :nopy
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python rc_ifd.py %*
goto :eof
:nopy
echo Python 3.9+ is required: https://www.python.org/downloads/  (tick "Add python.exe to PATH")
pause
