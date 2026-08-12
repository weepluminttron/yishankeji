# -*- coding: utf-8 -*-
"""统一的错误日志写入（避免 core 模块反向依赖 server）。"""
import os
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def log_error(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        err_path = os.path.join(DATA_DIR, "error.log")
        if os.path.exists(err_path) and os.path.getsize(err_path) > 5 * 1024 * 1024:
            os.replace(err_path, err_path + ".old")
        with open(err_path, "a", encoding="utf-8") as f:
            f.write("[" + time.strftime("%Y-%m-%d %H:%M:%S") + "]\n" + msg + "\n")
    except Exception:
        pass
