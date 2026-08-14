# -*- coding: utf-8 -*-
"""联系方式深挖：主页 → 自动发现联系页 → 抓取合并 → 清洗去重。

优化点（对应“获取客户信息的能力”）：
1. 很多官网首页没有联系方式，联系方式藏在 /contact、/about、/contact-us 等子页；
2. 自动识别“联系我们/关于我们”等链接，抓 1~2 个联系页，合并邮箱/电话；
3. 过滤示例/占位/图片类假邮箱，企业邮箱优先于免费邮箱；
4. 全程不抛异常：单页失败不影响主流程，全部失败返回空结果。
"""
import re
import threading
import urllib.parse

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
INTL_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")

WEBMAILS = {
    "qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "hotmail.com",
    "foxmail.com", "sina.com", "139.com", "aliyun.com", "icloud.com", "yahoo.com",
    "live.com", "msn.com", "yandex.com", "protonmail.com", "gmx.com",
}

# 假邮箱/占位邮箱特征：图片资源、示例域名、模板占位
_JUNK_EMAIL = re.compile(
    r"\.(?:png|jpe?g|gif|webp|svg|bmp|css|js|ico|woff2?|ttf)(?:$|[?#])"
    r"|(?:example|yourname|your[-_]?email|name@|email@|domain|sentry|wixpress|"
    r"placeholder|test|localhost|\.invalid|\.local)(?:$|[.@])"
    r"|@2x|@3x|@1x",
    re.I,
)

# 联系/关于页链接识别（路径 + 锚文本双通道）
CONTACT_PATH_RE = re.compile(
    r"(contact|联系我们|联系|about|关于我们|关于|company|公司简介|cooperation|"
    r"商务合作|join[-_]?us|inquiry|enquiry|support|联系信息)",
    re.I,
)
CONTACT_ANCHOR_RE = re.compile(
    r"(联系我们|联系|contact|关于我们|关于|about|咨询|inquiry|enquiry|support|"
    r"留言|商务合作|cooperation)",
    re.I,
)

# 共享托管域名：不同公司可能共用同一域名（合并线索时不能用域名做唯一键）
SHARED_HOSTS = (
    "wordpress.com", "wixsite.com", "weebly.com", "github.io", "blogspot.com",
    "webnode.page", "webflow.io", "shopify.com", "squarespace.com", "myshopify.com",
    "pages.dev", "vercel.app", "netlify.app",
)

_lock = threading.Lock()


def _uniq(seq):
    out = []
    for x in seq or []:
        if x and x not in out:
            out.append(x)
    return out


def clean_emails(emails):
    """清洗邮箱：去重、去假邮箱；企业邮箱排前、免费邮箱排后。"""
    corp, free, seen = [], [], set()
    for raw in emails or []:
        e = str(raw).strip().lower().strip(".")
        if len(e) < 6 or len(e) > 64 or "@" not in e:
            continue
        if _JUNK_EMAIL.search(e):
            continue
        local, _, dom = e.partition("@")
        if not local or "." not in dom or len(dom) < 5:
            continue
        if e in seen:
            continue
        seen.add(e)
        (free if dom in WEBMAILS else corp).append(e)
    return corp[:12] + [e for e in free[:8] if e not in corp]


def clean_phones(phones):
    """清洗电话：去重、过滤伪号码；裸 7~8 位数字必须有 0 前缀或分隔符。"""
    out, seen = [], set()
    for raw in phones or []:
        p = str(raw).strip()
        digits = re.sub(r"\D", "", p)
        n = len(digits)
        if n < 7 or n > 15:
            continue
        if len(set(digits)) <= 2:
            continue
        has_sep = bool(re.search(r"[-.\s]", p))
        if n in (7, 8):
            if not (digits.startswith("0") or has_sep):
                continue
        elif n >= 10:
            is_mobile = n == 11 and digits.startswith("1") and digits[1] in "3456789"
            if not (is_mobile or has_sep or "+" in p or digits.startswith("0")):
                continue
        if digits in seen:
            continue
        seen.add(digits)
        out.append(p)
    return out[:12]


def find_contact_urls(html_text, base_url, max_links=6):
    """从页面 HTML 中找“联系/关于”页链接，返回绝对 URL（同源优先）。"""
    if not html_text or not base_url:
        return []
    try:
        from lxml import html as lh
        doc = lh.fromstring(html_text)
    except Exception:
        return []
    try:
        base_host = (urllib.parse.urlparse(base_url).hostname or "").lower().lstrip("www.")
    except Exception:
        base_host = ""
    cands, seen = [], set()
    try:
        anchors = doc.xpath("//a[@href]")
    except Exception:
        anchors = []
    for a in anchors:
        href = (a.get("href") or "").strip()
        text = re.sub(r"\s+", " ", (a.text_content() or "")).strip()
        low = href.lower()
        if not href or low.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            abs_url = urllib.parse.urljoin(base_url, href)
            p = urllib.parse.urlparse(abs_url)
        except Exception:
            continue
        if p.scheme not in ("http", "https") or abs_url in seen:
            continue
        seen.add(abs_url)
        host = (p.hostname or "").lower().lstrip("www.")
        same_origin = (not base_host) or host == base_host or host.endswith("." + base_host)
        path_hit = bool(CONTACT_PATH_RE.search(p.path))
        anchor_hit = bool(CONTACT_ANCHOR_RE.search(text))
        if not path_hit and not anchor_hit:
            continue
        if re.search(r"(login|signin|sign-in|register|cart|checkout|download|\.pdf)", p.path, re.I):
            continue
        score = (2 if path_hit else 0) + (1 if anchor_hit else 0)
        cands.append((score, same_origin, abs_url))
    cands.sort(key=lambda x: (-x[0], 0 if x[1] else 1))
    return [c[2] for c in cands[:max_links]]


def _extract_raw(html_text):
    """从 HTML/纯文本中提取原始邮箱/电话（不做清洗，供合并）。"""
    if not html_text:
        return {"emails": [], "phones": [], "whatsapp": [], "wechat": []}
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    whats = re.findall(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", text)
    wechats = re.findall(r"(?:wxid_[A-Za-z0-9_\-]+|微信号[：:]\s*[\w\-]{6,20})", text)
    return {
        "emails": EMAIL_RE.findall(text),
        "phones": MOBILE_RE.findall(text) + LANDLINE_RE.findall(text) + INTL_RE.findall(text),
        "whatsapp": whats,
        "wechat": wechats,
    }


def merge_contact_sets(sets):
    """合并多个页面提取结果，统一清洗去重。"""
    emails, phones, whats, wechats = [], [], [], []
    for s in sets or []:
        emails += list(s.get("emails") or [])
        phones += list(s.get("phones") or [])
        whats += list(s.get("whatsapp") or [])
        wechats += list(s.get("wechat") or [])
    return {
        "emails": clean_emails(emails),
        "phones": clean_phones(phones),
        "whatsapp": _uniq(whats)[:6],
        "wechat": _uniq(wechats)[:6],
    }


def _fetch_text(url, settings, smart):
    """抓单页：有智能爬虫（Playwright）用智能爬虫，否则 urllib+Jina。"""
    if smart is not None:
        try:
            r = smart.scrape_sync(url, settings)
            if r.get("success"):
                blob = r.get("content") or ""
                for e in r.get("emails") or []:
                    blob += " " + str(e)
                for pn in r.get("phones") or []:
                    blob += " " + str(pn)
                return blob
        except Exception:
            pass
        return ""
    try:
        from core import crawler
        html, _ = crawler.fetch_page(
            url, timeout=12, use_jina=True, jina_timeout=12, settings=settings,
        )
        return html or ""
    except Exception:
        return ""


def probe_contacts(url, settings=None, max_pages=2, smart=None, main_html=None):
    """抓主页 + 自动发现的联系页，合并返回清洗后的联系方式。

    返回 dict：{emails, phones, whatsapp, wechat, probed_pages, contact_urls}。
    任何一步失败都静默跳过，保证调用方主流程不受影响。
    """
    settings = settings or {}
    try:
        max_pages = max(0, min(int(max_pages or 0), 3))
    except Exception:
        max_pages = 2
    result = {"emails": [], "phones": [], "whatsapp": [], "wechat": [],
              "probed_pages": 0, "contact_urls": []}
    if not url or not url.startswith(("http://", "https://")):
        return result
    html = main_html or _fetch_text(url, settings, smart)
    if not html:
        return result
    contact_urls = find_contact_urls(html, url, max_links=6)[:max_pages]
    result["contact_urls"] = contact_urls
    raw_sets = [_extract_raw(html)]
    if contact_urls:
        from core import concurrent_search

        def _one(u):
            t = _fetch_text(u, settings, smart)
            return _extract_raw(t) if t else None

        got = concurrent_search.parallel_map(
            _one, contact_urls, max_workers=min(2, len(contact_urls)), stagger=0.3,
        )
        for g in got:
            if g:
                raw_sets.append(g)
    merged = merge_contact_sets(raw_sets)
    result.update(merged)
    result["probed_pages"] = len(raw_sets)
    return result