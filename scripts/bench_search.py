# -*- coding: utf-8 -*-
"""提速验证（无需 lxml/网络）：用模拟延迟证明「并行 + 缓存」的加速比。

运行：python scripts/bench_search.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import concurrent_search, search_cache


def _fake_search(q):
    # 模拟单次搜索网络延迟 0.8s
    time.sleep(0.8)
    return [{"url": f"http://example.com/{q}", "title": q, "snippet": ""}]


def bench_parallel():
    queries = [f"q{i}" for i in range(30)]
    # 串行基线
    t0 = time.time()
    for q in queries:
        _fake_search(q)
    ts = time.time() - t0
    # 并行（8 worker）
    t0 = time.time()
    concurrent_search.parallel_map(_fake_search, queries, max_workers=8, stagger=0.0)
    tp = time.time() - t0
    print(f"[并行] 30 条搜索：串行≈{ts:.1f}s，并行≈{tp:.1f}s，加速≈{ts / max(tp, 1e-6):.1f}x")


def bench_cache():
    provider = "bench"
    search_cache.clear()
    t0 = time.time()
    for q in [f"c{i}" for i in range(20)]:
        search_cache.cache_set(provider, q, 5, [{"url": q}])
    cold = time.time() - t0
    t0 = time.time()
    hits = sum(1 for q in [f"c{i}" for i in range(20)] if search_cache.cache_get(provider, q, 5) is not None)
    hot = time.time() - t0
    print(f"[缓存] 20 条：写入≈{cold * 1000:.1f}ms；命中读取≈{hot * 1000:.2f}ms（{hits}/20 命中）")
    search_cache.clear()


if __name__ == "__main__":
    print("=== 本地搜索提速验证（模拟，无需联网）===")
    bench_parallel()
    bench_cache()
    print("说明：真实搜索延迟来自网络；并行把 N 次串行 I/O 重叠为 ~N/workers 次，")
    print("缓存让‘刷新同一批条件’的搜索阶段趋近于 0（直接读盘）。")
