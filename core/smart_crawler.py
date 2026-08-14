# -*- coding: utf-8 -*-
"""智能爬虫引擎（对应“DeepSeek 大脑 + 爬虫手脚”方案）。

设计参考：SmartCrawler / B2BLeadCrawler（Playwright + Asyncio），并整合本项目的
反爬体系（core.antibot）：
- 优先用 Playwright 无头浏览器渲染 JS 页面（需 `pip install playwright && playwright install chromium`）；
- 未安装 Playwright 时自动降级到 core.crawler.fetch_page（urllib + 随机延时 + 代理池 + Jina），开箱即用；
- 批量并发受信号量限制（默认 3 路），随机 UA/视口指纹，支持代理池；
- 自动提取邮箱/电话/tel 链接；正文截断控制 AI Token 成本；
- 单页失败不拖垮整批，逐条返回 success/error。
"""
import asyncio
import os
import random
import re

# Playwright 浏览器目录：优先使用服务器上已安装的 /ms-playwright（避免去用户目录找空目录）
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    for _pw_dir in ("/ms-playwright", "/data/yishankeji/.cache/ms-playwright"):
        if os.path.isdir(_pw_dir):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _pw_dir
            break

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}")
PHONE_CN_RE = re.compile(r"(?<!\d)(?:0\d{2,3}-?)?\d{7,8}(?!\d)")
PHONE_INTL_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.2088.69",
]

_PLAYWRIGHT_OK = None


def playwright_available():
    """检测 Playwright 是否已安装（只在首次调用时探测一次）。"""
    global _PLAYWRIGHT_OK
    if _PLAYWRIGHT_OK is None:
        try:
            import playwright  # noqa: F401
            _PLAYWRIGHT_OK = True
        except Exception:
            _PLAYWRIGHT_OK = False
    return _PLAYWRIGHT_OK


def _proxy_from_settings(settings):
    """从设置取代理 URL（带认证原样透传，Playwright 支持）。"""
    return (settings or {}).get("proxy_pool") or (settings or {}).get("proxy_url") or ""


def _extract_contacts(html_text, text, url=""):
    """从渲染后 HTML/纯文本中提取邮箱、电话、tel 链接（清洗去重，企业邮箱优先）。"""
    from core import contact_probe
    hay = (html_text or "") + " " + (text or "")
    emails = []
    # mailto: 里的邮箱信号最强，排最前
    for m in re.finditer(r'href=["\']mailto:([^"\']+)', html_text or ""):
        e = m.group(1).strip().split("?")[0]
        if EMAIL_RE.match(e):
            emails.append(e)
    emails += EMAIL_RE.findall(hay)
    phones = PHONE_CN_RE.findall(text or "") + PHONE_INTL_RE.findall(text or "")
    for m in re.finditer(r'href=["\']tel:([^"\']+)', html_text or ""):
        p = m.group(1).strip()
        if p:
            phones.append(p)
    return {
        "emails": contact_probe.clean_emails(emails)[:20],
        "phones": contact_probe.clean_phones(phones)[:20],
    }


class SmartCrawler:
    """异步智能爬虫：Playwright 渲染为主，urllib+反爬 兜底。"""

    def __init__(self, max_concurrent=3, proxy=None, timeout=15000, content_limit=3000):
        self.max_concurrent = max(1, int(max_concurrent or 3))
        self.proxy = proxy or ""
        self.timeout = max(5000, int(timeout or 15000))
        self.content_limit = max(500, int(content_limit or 3000))
        self._semaphore = None

    def _sem(self):
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    async def _fetch_playwright(self, url):
        """用 Playwright 渲染 JS 页面，返回 {html, text, title}。"""
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            launch_kwargs = {"headless": True}
            if self.proxy:
                launch_kwargs["proxy"] = {"server": self.proxy}
            browser = await p.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    user_agent=random.choice(UA_POOL),
                    viewport={
                        "width": random.randint(1280, 1920),
                        "height": random.randint(800, 1080),
                    },
                )
                page = await context.new_page()
                await page.goto(url, timeout=self.timeout, wait_until="domcontentloaded")
                # 行为模拟：随机阅读延迟
                await asyncio.sleep(random.uniform(1.0, 2.5))
                html = await page.content()
                text = ""
                title = ""
                try:
                    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                except Exception:
                    pass
                try:
                    title = await page.title()
                except Exception:
                    pass
                return {"html": html or "", "text": text or "", "title": title or ""}
            finally:
                await browser.close()

    def _fetch_urllib(self, url, settings):
        """降级：urllib + 反爬体系（代理池/随机延时/Jina 兜底）。"""
        from core import crawler
        html, final = crawler.fetch_page(
            url, timeout=min(20, self.timeout // 1000), use_jina=True,
            jina_timeout=12, settings=settings,
        )
        from lxml import html as lh
        doc = lh.fromstring(html)
        nodes = doc.xpath("//body//text()") or doc.xpath("//text()")
        text = " ".join(str(x).strip() for x in nodes if str(x).strip())
        title = doc.findtext(".//title") or ""
        return {"html": html, "text": text, "title": title, "final_url": final or url}

    async def scrape_single(self, url, settings=None):
        """抓取单个 URL（受并发信号量控制），单页失败不抛异常。"""
        async with self._sem():
            try:
                page_data = None
                if playwright_available():
                    try:
                        page_data = await self._fetch_playwright(url)
                    except Exception:
                        page_data = None  # 渲染失败 → 降级 urllib
                if page_data is None:
                    page_data = self._fetch_urllib(url, settings)
                html_text = page_data.get("html") or ""
                text = page_data.get("text") or ""
                title = page_data.get("title") or ""
                contacts = _extract_contacts(html_text, text, url)
                if str((settings or {}).get("crawler_probe_contacts", "1")) != "0" and not (contacts["emails"] and contacts["phones"]):
                    try:
                        from core import contact_probe
                        max_pages = 1
                        try:
                            max_pages = max(0, min(int((settings or {}).get("crawler_probe_pages") or 1), 3))
                        except Exception:
                            max_pages = 1
                        probe = contact_probe.probe_contacts(
                            url, settings=settings, max_pages=max_pages,
                            smart=None, main_html=html_text,
                        )
                        contacts = contact_probe.merge_contact_sets([contacts, probe])
                    except Exception:
                        pass
                return {
                    "success": True,
                    "url": url,
                    "title": title[:300],
                    "emails": contacts["emails"],
                    "phones": contacts["phones"],
                    "content": text[:self.content_limit],
                    "text_len": len(text),
                    "engine": "playwright" if (page_data.get("final_url") is None and playwright_available()) else "urllib",
                }
            except Exception as e:
                return {
                    "success": False,
                    "url": url,
                    "error": str(e)[:300],
                    "emails": [],
                    "phones": [],
                    "content": "",
                }

    async def batch_scrape(self, urls, settings=None):
        """批量并发抓取（并发数受 max_concurrent 限制）。"""
        tasks = [self.scrape_single(u, settings) for u in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def batch_scrape_sync(self, urls, settings=None):
        """同步入口（供 server.py / AI 工具调用），每次独立事件循环。"""
        urls = [u for u in (urls or []) if isinstance(u, str) and u.startswith(("http://", "https://"))]
        if not urls:
            return []
        try:
            self._semaphore = None  # 每次调用重建信号量，跨线程/跨事件循环复用安全
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.batch_scrape(urls, settings))
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        except Exception as e:
            return [{"success": False, "url": u, "error": str(e)[:200]} for u in urls]

    def scrape_sync(self, url, settings=None):
        """单条同步入口。"""
        res = self.batch_scrape_sync([url], settings)
        return (res[0] if res else {"success": False, "url": url, "error": "无结果", "emails": [], "phones": [], "content": ""})
