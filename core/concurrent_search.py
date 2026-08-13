# -*- coding: utf-8 -*-
"""通用并行执行工具：把串行循环改成线程池并行，显著缩短 I/O 密集型（搜索/抓取）总耗时。

设计要点：
- 纯标准库（concurrent.futures），不依赖 lxml，可在无网络的沙箱里独立测试；
- 按输入顺序返回结果，单个任务抛异常时原样保留为 Exception 对象，不中断整体；
- stagger：提交任务前的小幅间隔，避免对免费搜索源瞬时并发过高被限流；
  反爬增强：stagger 支持区间 [min, max]，实际 sleep 取随机值，模拟人类不规则节奏；
- progress：每完成一个任务回调一次（done/total），供前端进度条使用。

反爬策略对应"快启精线索"的"分布式系统架构"层：
- 多线程并发把请求分散到不同任务，降低单点高频被识别风险；
- stagger 随机化避免多线程同时发请求形成"脉冲式"流量特征。
"""
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def _resolve_stagger(stagger):
    """把 stagger 解析成实际 sleep 秒数。

    支持三种形式：
    - float/int：固定值（向后兼容）
    - (min, max) 元组：区间内随机
    - "min,max" 字符串：区间内随机
    返回 0 时不 sleep。
    """
    if not stagger:
        return 0.0
    if isinstance(stagger, (int, float)):
        return float(stagger)
    if isinstance(stagger, (list, tuple)) and len(stagger) == 2:
        lo, hi = float(stagger[0]), float(stagger[1])
        return random.uniform(min(lo, hi), max(lo, hi))
    s = str(stagger).strip()
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) == 2:
            try:
                lo, hi = float(parts[0]), float(parts[1])
                return random.uniform(min(lo, hi), max(lo, hi))
            except ValueError:
                pass
    try:
        return float(s)
    except ValueError:
        return 0.0


def parallel_map(func, items, max_workers=8, stagger=0.0, desc="", progress=None):
    """对 items 逐个调用 func，并行执行，返回与 items 等长的列表。

    - 第 i 个元素 = func(items[i]) 的结果；若抛异常则为该 Exception 实例。
    - max_workers 控制并发数；stagger>0 时每提交一个任务前 sleep（支持随机区间）。
    - stagger 反爬增强：传 (0.2, 0.8) 或 "0.2,0.8" 可让每次提交间隔随机化，
      避免多线程同时发请求形成"脉冲式"流量特征（对应"行为模拟"层）。
    """
    items = list(items)
    n = len(items)
    if n == 0:
        return []
    workers = max(1, min(int(max_workers), 32))
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for idx, it in enumerate(items):
            if stagger:
                time.sleep(_resolve_stagger(stagger))
            futs[ex.submit(func, it)] = idx
        done = 0
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                out[idx] = fut.result()
            except Exception as e:  # 保留异常，交由调用方处理
                out[idx] = e
            done += 1
            if progress:
                try:
                    progress({"stage": desc, "done": done, "total": n})
                except Exception:
                    pass
    return [out.get(i) for i in range(n)]
