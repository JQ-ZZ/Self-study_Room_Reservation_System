@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto failed

if exist ".venv\Scripts\python.exe" goto install
py -3 --version >nul 2>&1
if not errorlevel 1 goto use_py
python --version >nul 2>&1
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed
goto install

:use_py
py -3 -m venv .venv
if errorlevel 1 goto failed

:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto missing_python
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Open http://127.0.0.1:8000 in your browser.
echo Keep this window open while using the demo.
".venv\Scripts\python.exe" -m uvicorn m1_demo.app:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto failed
pause
exit /b 0

:missing_python
echo Python 3.11 or newer is required.
echo Install Python and enable Add Python to PATH.
echo If an older .venv exists, rename it and run this script again.
goto failed

:failed
echo.
echo Startup failed. Please send a screenshot of this window.
pause
exit /b 1
