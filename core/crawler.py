# -*- coding: utf-8 -*-
"""网页采集：抓取黄页/目录页，提取公司名与电话。"""
import re
import urllib.request
from urllib.parse import urlparse

from lxml import html as lh

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def fetch_page(url, timeout=15, use_jina=True, jina_timeout=12):
    """抓取网页；直接访问失败时自动降级到 Jina Reader（fetchrouter 思路）。

    use_jina=False 时跳过 Jina 回退（直接失败，更快、避免长超时）；
    jina_timeout 控制回退超时（默认 12s，原实现为 timeout+15，过长会拖垮并行抓取）。
    """
    direct_err = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            final = resp.geturl() or url
    except Exception as e:
        direct_err = e
        if not use_jina:
            raise direct_err
        try:
            jina_url = "https://r.jina.ai/" + url
            jreq = urllib.request.Request(jina_url, headers={"User-Agent": UA, "Accept": "text/plain"})
            with urllib.request.urlopen(jreq, timeout=jina_timeout) as jresp:
                raw = jresp.read()
                final = url
        except Exception:
            raise direct_err
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc), final
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), final


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


def crawl(url=None, html_text=None):
    """返回 (candidates, error)。"""
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
        page_html, final_url = fetch_page(url.strip())
        candidates = extract_candidates(page_html, final_url)
        if not candidates:
            return [], "页面里没有提取到电话号码。可能页面需要登录、有验证码，或电话以图片形式展示。"
        return candidates, None
    except Exception as e:
        return [], f"采集失败：{e}"
