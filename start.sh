#!/usr/bin/env bash
# 光纤获客助手 - Linux 快速启动（前台运行，Ctrl+C 停止）
cd "$(dirname "$0")"

# 未安装依赖时先安装
if [ ! -d "venv" ]; then
  echo "首次运行：创建虚拟环境并安装依赖..."
  python3 -m venv venv
  ./venv/bin/pip install -r requirements.txt
fi

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8017}"
./venv/bin/python server.py
