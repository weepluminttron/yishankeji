# -*- coding: utf-8 -*-
"""搜索结果缓存（增量索引/缓存机制）：避免对相同查询重复爬取。

- 键：(搜索源 provider, 查询词 query, 条数 count) 的 sha1 摘要；
- 内存镜像 + 磁盘持久化：cache_get 走内存（O(1)），只有 cache_set 才写盘，
  因此“刷新同一批条件”的搜索阶段趋近于 0（直接读内存，不再每次重新读盘）；
- TTL 默认 24h，过期自动失效（行情/线索会变，不宜永久缓存）；
- 容量上限 2000 条，超出按时间淘汰最旧；
- 线程安全（单进程内加锁），可被 buyer.run 的多线程搜索共用。
"""
import hashlib
import json
import os
import threading
import time

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "search_cache.json")
_DEFAULT_TTL = 24 * 3600
_MAX_ENTRIES = 2000
_lock = threading.Lock()
_mem = {}
_loaded = False


def make_key(provider, query, count):
    s = f"{provider}|{query}|{count}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _ensure_loaded():
    global _loaded, _mem
    if _loaded:
        return
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            _mem = json.load(f)
    except Exception:
        _mem = {}
    _loaded = True


def _save(db):
    try:
        d = os.path.dirname(_CACHE_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False)
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        pass


def cache_get(provider, query, count, ttl=_DEFAULT_TTL):
    """命中且未过期返回缓存结果（内存 O(1)），否则返回 None。"""
    with _lock:
        _ensure_loaded()
        item = _mem.get(make_key(provider, query, count))
    if not item:
        return None
    if time.time() - item.get("ts", 0) > ttl:
        return None
    return item.get("data")


def cache_set(provider, query, count, data, ttl=_DEFAULT_TTL):
    """写入缓存（更新内存镜像并持久化到磁盘）。"""
    with _lock:
        _ensure_loaded()
        _mem[make_key(provider, query, count)] = {"ts": time.time(), "data": data}
        if len(_mem) > _MAX_ENTRIES:
            oldest = sorted(_mem.items(), key=lambda kv: kv[1].get("ts", 0))[:500]
            for k, _ in oldest:
                _mem.pop(k, None)
        _save(_mem)


def stats():
    with _lock:
        _ensure_loaded()
    return {"entries": len(_mem), "path": _CACHE_PATH, "memory_mirror": True}


def clear():
    with _lock:
        global _loaded
        _mem = {}
        _loaded = True
        try:
            if os.path.exists(_CACHE_PATH):
                os.remove(_CACHE_PATH)
            return True
        except Exception:
            return False
