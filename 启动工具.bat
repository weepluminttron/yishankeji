@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
  echo 未找到 Python，请先安装 Python 3.10 或以上版本（python.org 下载）。
  pause
  exit /b 1
)

echo 正在启动 AI 获客系统...
"%PYEXE%" server.py
pause
