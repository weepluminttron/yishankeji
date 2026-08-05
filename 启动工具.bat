@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
  set "PYEXE=C:\Users\eason\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

if not exist "%PYEXE%" (
  echo 未找到 Python，请先安装 Python 3.10 或以上版本。
  pause
  exit /b 1
)

echo 正在启动光纤获客助手...
"%PYEXE%" server.py
pause
