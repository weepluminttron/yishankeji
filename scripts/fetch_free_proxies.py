# -*- coding: utf-8 -*-
"""获取免费代理列表（测试用），输出可直接粘贴到 设置 → 反爬策略 → 代理池 的逗号分隔串。

用法：
  python scripts/fetch_free_proxies.py

免费代理仅适合验证配置通路；正式使用建议换付费代理网关（http://user:pass@网关:端口）。
"""
import json
import sys
import time
import urllib.request

SOURCES = [
    ("proxyscrape",
     "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"),
    ("geonode",
     "https://proxylist.geonode.com/api/proxy-list?limit=50&page=1&sort_by=lastChecked&sort_type=desc&protocols=http%2Chttps"),
    ("PROXY-List(github)",
     "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
]


def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_source(name, text):
    """按源格式解析出 http/https 代理串列表。"""
    if name == "geonode":
        data = json.loads(text)
        items = ["%s://%s:%s" % (x["protocol"], x["ip"], x["port"]) for x in data.get("data", [])]
    else:
        items = [ln.strip() for ln in text.splitlines() if ln.strip() and ":" in ln]
    out = []
    seen = set()
    for it in items:
        it = it.strip()
        low = it.lower()
        if not low.startswith(("http://", "https://")):
            it = "http://" + it
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def main():
    collected = []
    for name, url in SOURCES:
        try:
            text = fetch(url)
            items = parse_source(name, text)
            collected += items
            print("[OK] %s: %d 条" % (name, len(items)))
        except Exception as e:
            print("[跳过] %s: %s" % (name, str(e)[:120]))
        time.sleep(1)

    final = []
    seen = set()
    for p in collected:
        if p not in seen:
            seen.add(p)
            final.append(p)

    if not final:
        print("\n没有获取到可用代理（网络受限或免费源不可用）。")
        print("正式使用请向付费代理服务商购买，把网关地址填进 设置 → 反爬策略 → 代理池。")
        sys.exit(1)

    print("\n把下面整行复制到 设置 → 反爬策略 → 代理池：\n")
    print(",".join(final))
    print("\n共 %d 条。免费代理不稳定，正式使用建议换付费代理网关。" % len(final))


if __name__ == "__main__":
    main()
