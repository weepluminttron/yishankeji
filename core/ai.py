# -*- coding: utf-8 -*-
"""AI 营销文案生成（OpenAI 兼容接口）。

增强点（相对原版）：
- 缓存：复用 core.llm_cache，相同提示词 7 天内直接返回，省费用、秒回；
- 重试：5xx / 网络抖动 / 超时最多重试 2 次，指数退避；
- 新增 generate_json：在 generate_copy 基础上自动提取 JSON，统一解析逻辑。

外部签名保持兼容：generate_copy 仍返回 (text, err)。
"""
import json
import time
import urllib.error
import urllib.request

from core import llm_cache

DEFAULT_API_BASE = "https://api.openai.com/v1"
_MAX_RETRIES = 2
_RETRY_BASE_WAIT = 1.5  # 秒，指数退避


def _do_request(api_key, model, system, user, api_base):
    base = (api_base or DEFAULT_API_BASE).rstrip("/")
    if base.endswith("/chat/completions"):
        url = base
    else:
        url = base + "/chat/completions"
    payload = {
        "model": model or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.8,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def generate_copy(api_key, model, system, user, api_base=None, use_cache=True, ttl=None):
    """生成文案，返回 (text, err)。

    use_cache=True 时优先命中 core.llm_cache（相同 model+system+user 7 天内复用）。
    """
    if not api_key:
        return "", "未配置 AI 密钥"
    cache_key = llm_cache.make_key(model or "", system or "", user or "")
    if use_cache:
        hit = llm_cache.cache_get(cache_key)
        if hit is not None:
            return hit, None
    last_err = ""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            text = _do_request(api_key, model, system, user, api_base)
            if use_cache and text:
                llm_cache.cache_set(cache_key, text)
            return text, None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            last_err = f"接口返回错误 {e.code}：{detail[:200]}"
            # 4xx（含 401/429）不重试，直接返回
            if 400 <= e.code < 500:
                return "", last_err
        except Exception as e:  # 网络超时 / URLError 等，可重试
            last_err = f"请求失败：{e}"
        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_BASE_WAIT * (2 ** attempt))
    return "", last_err


def generate_json(api_key, model, system, user, api_base=None, use_cache=True, ttl=None):
    """生成并解析 JSON，返回 (obj, err)。

    自动从模型输出中提取第一个 {...} 或 [...]；解析失败 err 非空、obj 为 None。
    """
    text, err = generate_copy(api_key, model, system, user, api_base, use_cache=use_cache, ttl=ttl)
    if err:
        return None, err
    obj, perr = _extract_json(text)
    if perr:
        return None, perr
    return obj, None


def _extract_json(text):
    """从可能包含解释性文字的回复中提取 JSON（对象或数组）。"""
    import re
    if not text:
        return None, "AI 返回为空"
    # 优先尝试整段解析
    try:
        return json.loads(text), None
    except Exception:
        pass
    # 退而求其次：提取第一个 {...} 或 [...] 块
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        return None, "AI 返回格式无法解析"
    try:
        return json.loads(m.group(0)), None
    except Exception as e:
        return None, f"AI 返回解析失败：{e}"
