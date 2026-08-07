# -*- coding: utf-8 -*-
"""AI 营销文案生成（OpenAI 兼容接口）。"""
import json
import urllib.request

DEFAULT_API_BASE = "https://api.openai.com/v1"


def generate_copy(api_key, model, system, user, api_base=None):
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
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip(), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return "", f"接口返回错误 {e.code}：{detail[:200]}"
    except Exception as e:
        return "", f"请求失败：{e}"
