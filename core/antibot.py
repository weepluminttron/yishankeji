# -*- coding: utf-8 -*-
"""反爬策略引擎：参考"快启精线索"综合反爬体系，集成 请求伪装/IP轮换/行为模拟/动态渲染降级/重试退避。

设计原则
--------
1. 渐进增强：所有反爬能力默认启用"轻量级"策略（UA 轮换、随机延时、重试）；
   代理池、动态渲染等重资源能力仅在 settings 配置密钥/URL 后启用，避免无配置时拖垮抓取。
2. 安全降级：单次请求失败时按指数退避重试，全部失败才抛异常，不中断整体抓取流程。
3. 线程安全：UA 池、代理池的状态共享给多线程抓取（buyer.run 并行检索），通过锁保护。
4. 可审计：每次请求的 UA、代理、延时、重试次数都可记录到日志（可选），便于排查被封原因。

能力覆盖（对应"快启精线索"五大反爬层面）：
- 请求特征伪装：UA 轮换池 + Referer + Accept-Language + Cookie 管理
- IP 访问控制：代理池轮换（HTTP/SOCKS 代理 URL 列表，按权重/健康度选取）
- 行为模拟与频率控制：请求间随机延时 + 抖动，模拟人类不规则访问节奏
- 动态内容抓取：Selenium/Splash/Pyppeteer 渲染降级（需配置，默认走 Jina Reader 兜底）
- 分布式系统架构：通过 concurrent_search 多线程 + 代理池分散请求源
"""
import os
import re
import json
import base64
import http.client
import time
import random
import threading
import urllib.parse
import urllib.request
import urllib.error
from http.cookiejar import CookieJar


# ----------------------------------------------------------------------------
# 1. 请求特征伪装：UA 池 + 完整请求头
# ----------------------------------------------------------------------------
# 真实浏览器 UA 池（覆盖主流浏览器+操作系统，避免单一 UA 被识别为爬虫）
USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # 移动端（移动版页面可能更容易通过反爬）
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
]

# 常见 Referer 池（模拟从搜索引擎/社交平台跳转过来的真实流量）
REFERERS = [
    "https://www.bing.com/",
    "https://www.google.com/",
    "https://www.baidu.com/",
    "https://www.sogou.com/",
    "https://www.so.com/",
    "",  # 部分请求不带 Referer 更自然
]


def random_ua():
    """随机返回一个 User-Agent。"""
    return random.choice(USER_AGENTS)


def random_referer():
    """随机返回一个 Referer（含一定概率为空，模拟直接访问）。"""
    return random.choice(REFERERS)


def build_headers(url="", settings=None, with_referer=True):
    """构建完整请求头（UA + Accept + Accept-Language + Referer + Cookie 槽位）。

    对应"请求特征伪装"层：让每次请求看起来都像是来自真实浏览器。
    """
    headers = {
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if with_referer:
        ref = random_referer()
        if ref:
            headers["Referer"] = ref
    # 搜索引擎域名访问时，Referer 用对应引擎更自然
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if "bing" in host:
            headers["Referer"] = "https://www.bing.com/"
        elif "google" in host:
            headers["Referer"] = "https://www.google.com/"
        elif "baidu" in host:
            headers["Referer"] = "https://www.baidu.com/"
        elif "sogou" in host:
            headers["Referer"] = "https://www.sogou.com/"
        elif "so.com" in host:
            headers["Referer"] = "https://www.so.com/"
    except Exception:
        pass
    return headers


# ----------------------------------------------------------------------------
# 2. IP 访问控制：代理池轮换
# ----------------------------------------------------------------------------
class ProxyPool:
    """代理 IP 池：轮换使用，自动健康检查与失败剔除。

    用法：
      pool = ProxyPool(settings)
      proxy = pool.get()  # 获取一个可用代理（None 表示直连）
      pool.mark_bad(proxy)  # 标记该代理失败，降低权重
      pool.mark_good(proxy)  # 标记该代理成功，提升权重

    代理来源：settings["proxy_pool"]（逗号分隔的代理 URL 列表），如
      "http://1.2.3.4:8080,http://user:pass@5.6.7.8:3128,socks5://9.10.11.12:1080"
    未配置时 get() 返回 None，走直连（不影响主流程）。
    """
    def __init__(self, settings=None):
        self._lock = threading.Lock()
        self._api_lock = threading.Lock()
        self._proxies = []  # [{url, fails, last_used, last_ok}]
        self._idx = 0
        self._api_url = ""
        self._api_refresh_sec = 180
        self._api_last_msg = ""
        self._last_api_fetch = 0.0
        self._load(settings or {})

    def _load(self, settings):
        raw = settings.get("proxy_pool") or ""
        if isinstance(raw, (list, tuple)):
            urls = [str(x).strip() for x in raw if str(x).strip()]
        else:
            urls = [x.strip() for x in re.split(r"[\n,，;；]", str(raw)) if x.strip()]
        for u in urls:
            self._proxies.append({"url": u, "fails": 0, "last_used": 0, "last_ok": 0})
        # 单个代理 URL 也支持（proxy_url 字段）
        single = (settings.get("proxy_url") or "").strip()
        if single and not any(p["url"] == single for p in self._proxies):
            self._proxies.append({"url": single, "fails": 0, "last_used": 0, "last_ok": 0})
        # 动态代理 API（有代理/快代理等短效IP接口）：池空时自动拉取
        self._api_url = str(settings.get("proxy_api_url") or "").strip()
        try:
            self._api_refresh_sec = max(60, int(float(settings.get("proxy_api_refresh") or 3) * 60))
        except Exception:
            self._api_refresh_sec = 180

    def __bool__(self):
        return bool(self._proxies)

    @staticmethod
    def _parse_api_text(raw):
        """解析代理 API 文本：支持 ip:port、user:pass@ip:port、http://ip:port。"""
        out = []
        for line in re.split(r"[\r\n;；,，]", raw or ""):
            line = line.strip()
            if not line:
                continue
            if "://" in line:
                out.append(line)
                continue
            parts = line.split(":")
            if len(parts) == 4 and parts[1].isdigit():
                out.append("http://%s:%s@%s:%s" % (parts[2], parts[3], parts[0], parts[1]))
            elif len(parts) == 2 and parts[1].isdigit():
                out.append("http://" + line)
            elif len(parts) > 2 and parts[-1].isdigit():
                out.append("http://" + ":".join(parts[:-1]) + ":" + parts[-1])
        return out[:50]

    def _refresh_from_api(self, force=False):
        """从动态代理 API 拉取短效 IP（失败静默，不阻塞主流程）。"""
        if not self._api_url:
            return False
        with self._api_lock:
            now = time.time()
            if not force and (now - self._last_api_fetch) < self._api_refresh_sec:
                return False
            self._last_api_fetch = now
            try:
                req = urllib.request.Request(
                    self._api_url,
                    headers={"User-Agent": random_ua(), "Accept": "text/plain"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                self._api_last_msg = "接口请求失败: " + str(e)[:120]
                return False
            self._api_last_msg = re.sub(r"\s+", " ", raw or "").strip()[:200]
        parsed = self._parse_api_text(raw)
        if not parsed:
            return False
        with self._lock:
            existing = {p["url"] for p in self._proxies}
            for u in parsed:
                if u not in existing:
                    self._proxies.append({"url": u, "fails": 0, "last_used": 0, "last_ok": 0})
                    existing.add(u)
        return True

    def get(self):
        """获取下一个可用代理（轮询 + 跳过失败次数过高的；池空时自动从 API 拉取）。

        返回代理 URL 字符串；无可用代理时返回 None（走直连）。
        """
        if not self._proxies and self._api_url:
            self._refresh_from_api()
        if not self._proxies:
            return None
        with self._lock:
            # 最多尝试 len 个代理，找到健康度最好的
            n = len(self._proxies)
            for _ in range(n):
                p = self._proxies[self._idx % n]
                self._idx += 1
                # 失败次数 >=3 且最近 60s 内无成功 → 暂时跳过
                if p["fails"] >= 3 and (time.time() - p["last_ok"]) > 60:
                    continue
                p["last_used"] = time.time()
                return p["url"]
            # 全部失败但仍有代理 → 返回第一个（让调用方尝试一次，可能已恢复）
            return self._proxies[0]["url"]

    def mark_bad(self, proxy_url):
        """标记代理失败（连续失败会被剔除轮换）。"""
        if not proxy_url:
            return
        with self._lock:
            for p in self._proxies:
                if p["url"] == proxy_url:
                    p["fails"] += 1
                    break

    def mark_good(self, proxy_url):
        """标记代理成功（重置失败计数，记录成功时间）。"""
        if not proxy_url:
            return
        with self._lock:
            for p in self._proxies:
                if p["url"] == proxy_url:
                    p["fails"] = 0
                    p["last_ok"] = time.time()
                    break

    def stats(self):
        with self._lock:
            return {
                "total": len(self._proxies),
                "healthy": sum(1 for p in self._proxies if p["fails"] < 3),
                "details": [{"url": p["url"], "fails": p["fails"]} for p in self._proxies],
                "api_url": (self._api_url or "")[:80],
                "api_refresh_sec": self._api_refresh_sec,
                "api_last_msg": (self._api_last_msg or "")[:200],
            }


# 进程内代理池缓存（按代理配置分别缓存，不同配置可同时并存、互不污染）
_pool_cache = {}
_pool_lock = threading.Lock()


def _proxy_key(settings):
    s = settings or {}
    return str(s.get("proxy_pool") or s.get("proxy_url") or "") + "|api:" + str(s.get("proxy_api_url") or "")


def get_proxy_pool(settings=None):
    """获取进程内代理池（按代理配置缓存；空配置=直连池，与代理池互不影响）。"""
    key = _proxy_key(settings)
    with _pool_lock:
        if key not in _pool_cache:
            _pool_cache[key] = ProxyPool(settings)
        return _pool_cache[key]


def reset_proxy_pool(settings=None):
    """重置代理池（配置变更后重新加载）。"""
    key = _proxy_key(settings)
    with _pool_lock:
        _pool_cache[key] = ProxyPool(settings)
    return _pool_cache[key]


# ----------------------------------------------------------------------------
# 3. 行为模拟与频率控制：随机延时 + 抖动
# ----------------------------------------------------------------------------
def human_delay(settings=None, key="default"):
    """模拟人类浏览节奏的随机延时。

    对应"行为模拟与频率控制"层：请求间设置随机延时，避免高频访问触发警报。
    不同 key 对应不同场景的延时基准（搜索/抓取/翻页）。
    """
    settings = settings or {}
    # 从 settings 读取延时基准（允许用户调整）
    base_map = {
        "search": _safe_float(settings.get("delay_search"), 0.8),
        "fetch": _safe_float(settings.get("delay_fetch"), 0.3),
        "page": _safe_float(settings.get("delay_page"), 1.5),
        "default": _safe_float(settings.get("delay_default"), 0.5),
    }
    base = base_map.get(key, base_map["default"])
    if base <= 0:
        return 0
    # 抖动：base ± 50%，模拟不规则访问节奏
    jitter = base * 0.5
    sleep_time = max(0.1, random.uniform(base - jitter, base + jitter))
    time.sleep(sleep_time)
    return sleep_time


# ----------------------------------------------------------------------------
# 4. 重试 + 指数退避（遇到限流/封禁时自动退避）
# ----------------------------------------------------------------------------
# 触发重试的异常类型（网络错误 + HTTP 429/403/503）
RETRYABLE_HTTP_CODES = (429, 403, 503, 502, 500)


def is_retryable_error(e):
    """判断异常是否值得重试（网络错误、限流、临时封禁）。"""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in RETRYABLE_HTTP_CODES
    if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return True
    # SSL 错误等也重试
    return "timeout" in str(e).lower() or "connection" in str(e).lower() or "reset" in str(e).lower()


def retry_with_backoff(func, args=None, kwargs=None, max_retries=3, base_delay=1.0, settings=None):
    """带指数退避的重试封装。

    对应"行为模拟与频率控制"层：被限流/封禁时自动等待并重试，
    退避时间指数增长（1s → 2s → 4s）+ 随机抖动，避免多个客户端同时重试。

    返回 func 的返回值；所有重试失败后抛最后一次异常。
    """
    args = args or ()
    kwargs = kwargs or {}
    settings = settings or {}
    # settings 可覆盖默认重试参数
    max_retries = _safe_int(settings.get("retry_max"), max_retries)
    base_delay = _safe_float(settings.get("retry_base_delay"), base_delay)

    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if not is_retryable_error(e) or attempt >= max_retries:
                raise
            # 指数退避 + 抖动：delay = base * 2^attempt + random
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
    raise last_err


# ----------------------------------------------------------------------------
# 5. 统一请求入口：整合 UA + 代理 + 延时 + 重试 + Cookie
# ----------------------------------------------------------------------------
# 进程级 Cookie 管理（模拟浏览器会话保持）
_cookie_jar = CookieJar()
_cookie_processor = urllib.request.HTTPCookieProcessor(_cookie_jar)
_opener = urllib.request.build_opener(_cookie_processor)

# 带认证代理的手动请求路径不经过 urllib CookieProcessor，这里单独按域名保存 Cookie
_proxy_cookie_jar = {}
_proxy_cookie_lock = threading.Lock()


def _merge_cookies(*lists):
    """按 Cookie 名去重合并多个 Cookie 列表（后者覆盖同名）。"""
    out, seen = [], set()
    for lst in lists:
        for c in lst or []:
            name = c.split("=", 1)[0]
            if name and name not in seen:
                seen.add(name)
                out.append(c)
    return out


def _host_cookies(host):
    with _proxy_cookie_lock:
        return list(_proxy_cookie_jar.get((host or "").lower(), []))


def _save_cookies(host, values):
    if not values:
        return
    host = (host or "").lower()
    with _proxy_cookie_lock:
        cur = [c for c in _proxy_cookie_jar.get(host, []) if c.split("=", 1)[0] not in {v.split("=", 1)[0] for v in values}]
        _proxy_cookie_jar[host] = cur + list(values)


def _safe_float(v, default):
    """安全解析浮点配置，非法值回退默认，避免一个坏配置让搜索直接崩溃。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(v, default):
    """安全解析整数配置。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _proxy_parts(proxy_url):
    """解析代理 URL：提取认证信息（urllib 对带账号的代理认证不稳定，手动补头更可靠）。"""
    m = re.match(r"^(https?://)([^/@]+)@(.*)$", str(proxy_url or "").strip())
    if not m:
        return str(proxy_url or "").strip(), ""
    scheme, cred, host = m.groups()
    user, _, pwd = cred.partition(":")
    token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
    return scheme + host, "Basic " + token


class _ProxyResponse:
    """http.client 响应包装，兼容调用方的 read()/geturl()/headers。"""

    def __init__(self, resp, final_url):
        self._r = resp
        self.final_url = final_url

    def read(self, *args, **kwargs):
        return self._r.read(*args, **kwargs)

    def geturl(self):
        return self.final_url

    @property
    def headers(self):
        return self._r.headers

    @property
    def status(self):
        return self._r.status


def _open_with_proxy(req, proxy_url, timeout, max_redirects=5):
    """通过代理发起请求（HTTP/HTTPS 都支持，自动跟随跳转并传递代理认证）。

    HTTPS 走显式 CONNECT 隧道（把 Proxy-Authorization 传给代理），解决
    urllib 的 HTTPS 代理隧道不转发手动认证头导致的 407；同时手动跟随 3xx
    跳转，避免跳转后的请求重新走 urllib 丢失认证。
    """
    proxy_clean, proxy_auth = _proxy_parts(proxy_url)
    p = urllib.parse.urlparse(proxy_clean)
    phost = p.hostname
    pport = p.port or 80
    current = req
    local_cookies = []
    for _ in range(max_redirects + 1):
        target = urllib.parse.urlparse(current.full_url)
        thost = target.hostname
        tport = target.port or (443 if target.scheme == "https" else 80)
        path = target.path or "/"
        if target.query:
            path += "?" + target.query
        headers = {k: v for k, v in current.headers.items() if k.lower() != "proxy-authorization"}
        headers.pop("Host", None)
        merged_cookies = _merge_cookies(local_cookies, _host_cookies(thost))
        if merged_cookies:
            headers["Cookie"] = "; ".join(merged_cookies)
        conn = None
        try:
            if target.scheme == "https":
                conn = http.client.HTTPSConnection(phost, pport, timeout=timeout)
                tunnel = {"Proxy-Authorization": proxy_auth} if proxy_auth else {}
                conn.set_tunnel(thost, tport, headers=tunnel)
                conn.request(current.get_method(), path, body=current.data, headers=headers)
            else:
                conn = http.client.HTTPConnection(phost, pport, timeout=timeout)
                req_headers = dict(headers)
                if proxy_auth:
                    req_headers["Proxy-Authorization"] = proxy_auth
                conn.request(current.get_method(), current.full_url, body=current.data, headers=req_headers)
            resp = conn.getresponse()
            if hasattr(resp, "getheaders"):
                for k, v in resp.getheaders():
                    if k.lower() == "set-cookie":
                        piece = v.split(";", 1)[0]
                        _save_cookies(thost, [piece])
                        local_cookies = _merge_cookies(local_cookies, [piece])
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                conn.close()
                conn = None
                if not loc:
                    raise urllib.error.HTTPError(
                        current.full_url, resp.status, "redirect without location", resp.headers, None)
                new_url = urllib.parse.urljoin(current.full_url, loc)
                new_method = current.get_method()
                if resp.status in (302, 303) and new_method != "GET":
                    new_method = "GET"
                new_headers = dict(headers)
                next_host = urllib.parse.urlparse(new_url).hostname
                next_cookies = _merge_cookies(local_cookies, _host_cookies(next_host))
                if next_cookies:
                    new_headers["Cookie"] = "; ".join(next_cookies)
                current = urllib.request.Request(
                    new_url, data=None if new_method == "GET" else current.data,
                    headers=new_headers, method=new_method)
                continue
            if resp.status in (407, 403, 429, 502, 503):
                raise urllib.error.HTTPError(
                    current.full_url, resp.status, http.client.responses.get(resp.status, "error"),
                    resp.headers, None)
            return _ProxyResponse(resp, current.full_url)
        except Exception:
            if conn is not None:
                conn.close()
            raise
    raise urllib.error.URLError("proxy redirect loop")


def request_with_antibot(url, settings=None, timeout=15, method="GET", data=None,
                         extra_headers=None, use_proxy=True, use_delay=True,
                         delay_key="default", max_retries=3):
    """带完整反爬策略的 HTTP 请求。

    整合所有反爬层：UA 轮换 + Referer + 代理池 + 随机延时 + 指数退避重试 + Cookie 保持。

    返回 (response, used_proxy) ：response 是 urllib 的响应对象，used_proxy 是本次使用的代理 URL。
    失败时抛异常（已重试 max_retries 次仍失败）。

    对应"快启精线索"综合反爬体系的核心入口。
    """
    settings = settings or {}
    headers = build_headers(url, settings)
    if extra_headers:
        headers.update(extra_headers)

    pool = get_proxy_pool(settings) if use_proxy else None
    used_proxy = None
    last_err = None

    max_retries = _safe_int(settings.get("retry_max"), max_retries)
    retry_base = _safe_float(settings.get("retry_base_delay"), 1.0)

    for attempt in range(max_retries + 1):
        # 行为模拟：请求前随机延时
        if use_delay and attempt == 0:
            human_delay(settings, key=delay_key)

        # 代理轮换
        if pool:
            used_proxy = pool.get()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            if used_proxy:
                # 带代理的请求：每次新建 opener（代理可能不同）
                proxy_clean, proxy_auth = _proxy_parts(used_proxy)
                if proxy_auth:
                    # 带认证代理：HTTP/HTTPS 都走手动请求（含跳转跟随），避免 urllib 丢认证
                    resp = _open_with_proxy(req, used_proxy, timeout)
                    if pool:
                        pool.mark_good(used_proxy)
                    return resp, used_proxy
                proxy_handler = urllib.request.ProxyHandler({
                    "http": proxy_clean,
                    "https": proxy_clean,
                })
                opener = urllib.request.build_opener(proxy_handler, _cookie_processor)
                if proxy_auth:
                    # 手动带认证头，兼容需要 Proxy-Authorization 的代理（HTTP 与 HTTPS CONNECT 都生效）
                    req.add_unredirected_header("Proxy-Authorization", proxy_auth)
            else:
                opener = _opener

            resp = opener.open(req, timeout=timeout)
            if pool and used_proxy:
                pool.mark_good(used_proxy)
            return resp, used_proxy

        except Exception as e:
            last_err = e
            if pool and used_proxy:
                pool.mark_bad(used_proxy)
            # 代理本身挂了（连接失败/超时）：不重试，立即交给上层直连兜底，避免整轮搜索卡几分钟
            if used_proxy and is_retryable_error(e):
                raise
            if not is_retryable_error(e) or attempt >= max_retries:
                raise
            # 指数退避
            delay = retry_base * (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
            # 重试时换 UA 和代理（可能被目标识别）
            headers["User-Agent"] = random_ua()

    raise last_err


def _maybe_decompress(raw, headers):
    """按 Content-Encoding 解压 gzip/deflate（urllib 不会自动解压，不解压解析全是乱码）。"""
    if not raw:
        return raw
    try:
        enc = (headers.get("Content-Encoding") or "").lower()
    except Exception:
        enc = ""
    if "gzip" in enc:
        import gzip as _gzip
        import io as _io
        try:
            return _gzip.GzipFile(fileobj=_io.BytesIO(raw)).read()
        except Exception:
            return raw
    if "deflate" in enc:
        import zlib as _zlib
        try:
            return _zlib.decompress(raw)
        except Exception:
            try:
                return _zlib.decompress(raw, -_zlib.MAX_WBITS)
            except Exception:
                return raw
    return raw


def fetch_with_antibot(url, settings=None, timeout=15, use_jina_fallback=True,
                       jina_timeout=12, delay_key="fetch"):
    """带反爬策略的页面抓取（返回 (html_text, final_url)）。

    直接请求失败时：
    1. 自动重试（带 UA/代理轮换 + 指数退避）；
    2. 仍失败则降级到 Jina Reader（动态渲染兜底，对应"动态内容抓取"层）；
    3. 全部失败才抛异常。
    """
    settings = settings or {}
    direct_err = None
    try:
        resp, used_proxy = request_with_antibot(
            url, settings=settings, timeout=timeout, delay_key=delay_key,
            max_retries=_safe_int(settings.get("retry_max"), 2),
        )
        raw = _maybe_decompress(resp.read(), resp.headers)
        final = resp.geturl() or url
        for enc in ("utf-8", "gb18030", "gbk"):
            try:
                return raw.decode(enc), final
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), final
    except Exception as e:
        direct_err = e
        if not use_jina_fallback:
            raise direct_err
        # 降级到 Jina Reader（动态渲染兜底）
        try:
            jina_url = "https://r.jina.ai/" + url
            jheaders = build_headers(jina_url, settings, with_referer=False)
            jheaders["Accept"] = "text/plain"
            jreq = urllib.request.Request(jina_url, headers=jheaders)
            with urllib.request.urlopen(jreq, timeout=jina_timeout) as jresp:
                raw = _maybe_decompress(jresp.read(), jresp.headers)
                final = url
            for enc in ("utf-8", "gb18030", "gbk"):
                try:
                    return raw.decode(enc), final
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace"), final
        except Exception:
            raise direct_err


# ----------------------------------------------------------------------------
# 6. 反爬状态与统计（可审计）
# ----------------------------------------------------------------------------
_stats_lock = threading.Lock()
_stats = {
    "requests_total": 0,
    "requests_success": 0,
    "requests_failed": 0,
    "retries_total": 0,
    "proxy_used": 0,
    "jina_fallback": 0,
    "blocked_detected": 0,
}


def record_stats(key, count=1):
    """记录反爬统计（线程安全）。"""
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + count


def get_stats():
    """获取反爬统计快照。"""
    with _stats_lock:
        return dict(_stats)


def reset_stats():
    """重置统计。"""
    with _stats_lock:
        for k in list(_stats.keys()):
            _stats[k] = 0


# ----------------------------------------------------------------------------
# 7. 反爬检测：识别被封/被限流的信号
# ----------------------------------------------------------------------------
BLOCK_SIGNALS = [
    "访问过于频繁", "访问异常", "安全验证", "请输入验证码", "captcha",
    "请稍后再试", "blocked", "forbidden", "rate limit", "too many requests",
    "您的请求过于频繁", " IP 已被", "暂时无法访问", "verify you are human",
    "请完成安全验证", "challenge",
]


def detect_block(html_text, status_code=200):
    """检测响应是否为反爬拦截页。

    对应"请求特征伪装"层的反向应用：识别被反爬后触发重试/降级策略。
    """
    if status_code in RETRYABLE_HTTP_CODES:
        return True
    if not html_text:
        return False
    text_lower = html_text[:5000].lower()  # 只看前 5000 字符，提速
    for sig in BLOCK_SIGNALS:
        if sig.lower() in text_lower:
            return True
    # 页面过短且无实质内容（可能是空白拦截页）
    if len(html_text) < 500 and not re.search(r"[\u4e00-\u9fff]{10,}", html_text):
        return True
    return False


def configure_from_settings(settings):
    """从 settings 重新加载代理池（配置变更后调用）。"""
    return reset_proxy_pool(settings)
