@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo 请在浏览器打开 http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn m1_demo.app:app --host 127.0.0.1 --port 8000
pause
exit /b
:failed
echo 启动失败，请确认已安装 Python 3.11 或更新版本并可联网安装依赖。
pause
exit /b 1
