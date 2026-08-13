# -*- coding: utf-8 -*-
"""通用并行执行工具：把串行循环改成线程池并行，显著缩短 I/O 密集型（搜索/抓取）总耗时。

设计要点：
- 纯标准库（concurrent.futures），不依赖 lxml，可在无网络的沙箱里独立测试；
- 按输入顺序返回结果，单个任务抛异常时原样保留为 Exception 对象，不中断整体；
- stagger：提交任务前的小幅间隔，避免对免费搜索源瞬时并发过高被限流；
- progress：每完成一个任务回调一次（done/total），供前端进度条使用。
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def parallel_map(func, items, max_workers=8, stagger=0.0, desc="", progress=None):
    """对 items 逐个调用 func，并行执行，返回与 items 等长的列表。

    - 第 i 个元素 = func(items[i]) 的结果；若抛异常则为该 Exception 实例。
    - max_workers 控制并发数；stagger>0 时每提交一个任务前 sleep(stagger)。
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
                time.sleep(stagger)
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
