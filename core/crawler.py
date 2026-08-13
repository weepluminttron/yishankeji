# -*- coding: utf-8 -*-
"""网页采集：抓取黄页/目录页，提取公司名与电话。

反爬增强：集成 core.antibot 综合反爬策略（UA 轮换 + 代理池 + 随机延时 + 重试退避 + Jina 兜底）。
- settings 透传后自动启用代理池（配置 proxy_pool/proxy_url 后生效）；
- 未配置代理时走直连 + UA 轮换 + 重试，仍比单一 UA 更难被识别。
"""
import re
import urllib.request
from urllib.parse import urlparse

from lxml import html as lh

from core import antibot  # 反爬策略引擎

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def fetch_page(url, timeout=15, use_jina=True, jina_timeout=12, settings=None):
    """抓取网页；带反爬策略 + 失败时降级到 Jina Reader（动态渲染兜底）。

    反爬策略（对应"快启精线索"体系）：
    - 请求特征伪装：每次随机 UA + Referer + Accept-Language（antibot.build_headers）
    - IP 访问控制：配置代理池后自动轮换（antibot.ProxyPool）
    - 行为模拟：请求前随机延时（antibot.human_delay）
    - 重试退避：失败时指数退避重试（antibot.request_with_antibot）
    - 动态内容抓取：直接请求失败时降级到 Jina Reader（渲染 JS 后返回纯文本）

    use_jina=False 时跳过 Jina 回退（直接失败，更快、避免长超时）；
    jina_timeout 控制回退超时（默认 12s）；
    settings 透传反爬配置（proxy_pool / delay_* / retry_*）。
    """
    antibot.record_stats("requests_total")
    direct_err = None
    try:
        # 一次性走完整链路：直接请求 + 重试 + 反爬检测 + Jina 兜底
        # （antibot.fetch_with_antibot 内部已含 Jina 降级，无需外层再包一层）
        html_text, final = antibot.fetch_with_antibot(
            url, settings=settings, timeout=timeout,
            use_jina_fallback=use_jina,  # 直接把 use_jina 透传，避免双重降级
            jina_timeout=jina_timeout, delay_key="fetch",
        )
        # 反爬检测：识别是否被拦截（Jina 兜底返回的内容通常已绕过反爬）
        if antibot.detect_block(html_text):
            antibot.record_stats("blocked_detected")
            # 不直接 raise，让调用方根据内容判断（Jina 可能已成功但返回了拦截页提示）
        antibot.record_stats("requests_success")
        return html_text, final
    except Exception as e:
        antibot.record_stats("requests_failed")
        raise


def _clean_text(t):
    if not t:
        return ""
    return re.sub(r"\s+", " ", t).strip()


def _guess_name(el, doc_title):
    """从电话所在元素附近猜公司名。"""
    # 1) 元素自身的纯净文本（不含电话/联系人等噪音词）
    own = _clean_text(el.text_content())
    if 4 <= len(own) <= 40 and CJK_RE.search(own) and not re.search(r"电话|手机|联系人|地址|邮编|：|:", own):
        return own
    # 2) 同列表/同行的链接文本（公司名通常在 <a> 里）
    parent = el.getparent()
    if parent is not None:
        for a in parent.findall(".//a"):
            t = _clean_text(a.text_content())
            if 4 <= len(t) <= 60 and CJK_RE.search(t):
                return t
        # 3) 父容器文本：截取到“电话/联系人/地址”之前的公司部分
        t = _clean_text(parent.text_content())
        cut = re.split(r"电话|手机|联系人|地址|邮编", t)[0].strip(" ：:，,、|/")
        if 2 <= len(cut) <= 60 and CJK_RE.search(cut):
            return cut
    # 4) 页面标题去掉常见后缀
    title = doc_title or ""
    for suffix in ("-手机版", "-企业名录", "-黄页", "-首页", "_官网", " - 百度百科"):
        title = re.sub(re.escape(suffix) + r"\s*$", "", title)
    if 2 <= len(title.strip()) <= 60:
        return title.strip()
    return ""


def extract_candidates(html_text, source_url=""):
    doc = lh.fromstring(html_text)
    title = _clean_text(doc.findtext(".//title"))
    seen = {}
    for el in doc.iter():
        if el.tag not in ("div", "td", "li", "tr", "p", "span", "a"):
            continue
        text = el.text_content()
        phones = []
        for pat in (MOBILE_RE, LANDLINE_RE):
            phones += [m.group().replace("-", "") for m in pat.finditer(text)]
        if not phones:
            continue
        block = _clean_text(text)
        name = _guess_name(el, title)
        address = ""
        am = re.search(r"([\u4e00-\u9fff]{2,}(?:省|市|区|县|镇|乡)[\u4e00-\u9fff、·]{0,30})", block)
        if am:
            address = am.group(1)
        for p in phones:
            cand = {
                "name": name or f"未命名-{p[-4:]}",
                "phone": p,
                "address": address,
                "source": source_url,
                "contact": "",
            }
            old = seen.get(p)
            # 更短的公司名通常更准确（避免整行文本）
            if not old or len(cand["name"]) < len(old["name"]):
                seen[p] = cand
    return list(seen.values())


def crawl(url=None, html_text=None, settings=None):
    """返回 (candidates, error)。settings 透传反爬配置。"""
    try:
        if html_text and html_text.strip():
            src = ""
            return extract_candidates(html_text, src), None
        if not url or not url.strip():
            return [], "请填写要采集的网页地址"
        if not re.match(r"^https?://", url.strip()):
            url = "http://" + url.strip()
        parsed = urlparse(url)
        if not parsed.hostname:
            return [], "网址格式不正确"
        page_html, final_url = fetch_page(url.strip(), settings=settings)
        candidates = extract_candidates(page_html, final_url)
        if not candidates:
            return [], "页面里没有提取到电话号码。可能页面需要登录、有验证码，或电话以图片形式展示。"
        return candidates, None
    except Exception as e:
        return [], f"采集失败：{e}"
