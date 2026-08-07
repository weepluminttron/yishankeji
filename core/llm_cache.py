# -*- coding: utf-8 -*-
"""AI 响应缓存：相同提示词 7 天内直接秒回，节省 API 费用。"""
import hashlib
import time

from core.db import get_conn

CACHE_TTL = 7 * 24 * 3600
MAX_ENTRIES = 500


def make_key(*parts):
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cache_get(key):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT result, ts FROM ai_cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        if time.time() - row["ts"] > CACHE_TTL:
            return None
        return row["result"]
    finally:
        conn.close()


def cache_set(key, result):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO ai_cache (key, result, ts) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET result = excluded.result, ts = excluded.ts",
            (key, result, int(time.time())),
        )
        # 防止无限膨胀：保留最近 500 条
        conn.execute(
            "DELETE FROM ai_cache WHERE key NOT IN "
            "(SELECT key FROM ai_cache ORDER BY ts DESC LIMIT ?)",
            (MAX_ENTRIES,),
        )
        conn.commit()
    finally:
        conn.close()
