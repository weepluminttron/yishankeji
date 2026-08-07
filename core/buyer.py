# -*- coding: utf-8 -*-
"""买家发现：关键词 × 市场搜索 → 抓取页面 → 提取联系方式 → 评分入库。

思路参考 b2b-buyer-discovery：批量搜索潜在买家、抓取网站、提取邮箱/电话/WhatsApp/微信，
并用规则引擎评分，AI 深度评分可选。
"""
import re
import urllib.parse

from lxml import html as lh

from core.crawler import _clean_text, fetch_page
from core.scorer import rule_score

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
WHATSAPP_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
WECHAT_RE = re.compile(r"(?:wxid_[A-Za-z0-9_\-]+|微信号[：:]\s*[\w\-]{6,20})")

BLOCKED_DOMAINS = (
    "alibaba.com", "made-in-china.com", "1688.com", "taobao.com", "tmall.com", "jd.com",
    "baidu.com", "zhihu.com", "xiaohongshu.com", "douyin.com", "bilibili.com",
    "weibo.com", "sohu.com", "sina.com", "163.com", "qq.com", "toutiao.com",
    "facebook.com", "linkedin.com", "youtube.com", "instagram.com", "wikipedia.org",
)


def _domain(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        return host.lower().lstrip("www.")
    except Exception:
        return ""


def is_blocked(url):
    d = _domain(url)
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


def search_bing(keyword, market="", count=8):
    """Bing 网页搜索（无需 API Key）。返回 [{title,url,snippet,keyword,market}]。"""
    q = (keyword + " " + market).strip()
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(q) + "&count=" + str(max(5, min(20, count)))
    html_text, _ = fetch_page(url)
    doc = lh.fromstring(html_text)
    results = []
    for li in doc.xpath("//li[contains(@class,'b_algo')]")[:count]:
        a = li.xpath(".//h2/a | .//a")
        if not a:
            continue
        a = a[0]
        href = (a.get("href") or "").strip()
        title = _clean_text(a.text_content())
        if not title or not href.startswith("http"):
            continue
        p = li.xpath(".//p")
        snippet = _clean_text(p[0].text_content()) if p else ""
        results.append({"title": title, "url": href, "snippet": snippet, "keyword": keyword, "market": market})
    return results


def extract_contacts(html_text, url=""):
    """从页面提取公司名、邮箱、电话、WhatsApp、微信。"""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    emails = sorted(set(m.lower() for m in EMAIL_RE.findall(text)))
    phones = sorted(set(m.replace("-", "") for m in MOBILE_RE.findall(text) + LANDLINE_RE.findall(text)))
    whatsapp = sorted(set(m for m in WHATSAPP_RE.findall(text)))
    wechat = sorted(set(m for m in WECHAT_RE.findall(text)))
    doc = lh.fromstring(html_text)
    company = ""
    og = doc.xpath("//meta[@property='og:title']/@content")
    if og:
        company = _clean_text(og[0])
    if not company:
        t = doc.findtext(".//title")
        company = _clean_text(t or "")
    if not company:
        h1 = doc.findtext(".//h1")
        company = _clean_text(h1 or "")
    return {
        "company": company,
        "emails": emails,
        "phones": phones,
        "whatsapp": whatsapp,
        "wechat": wechat,
        "website": url,
    }


def _to_candidate(contact, title="", snippet="", keyword="", market=""):
    email = contact["emails"][0] if contact["emails"] else ""
    phone = contact["phones"][0] if contact["phones"] else ""
    name = contact["company"] or title or (email.split("@")[-1] if email else "未命名")
    note = (title + "。\n" + snippet if title else snippet).strip()
    cand = {
        "name": name[:80],
        "contact": "",
        "phone": phone,
        "email": email,
        "region": market,
        "type": "终端客户",
        "source": "买家发现",
        "tags": keyword,
        "website": contact["website"],
        "whatsapp": ",".join(contact["whatsapp"][:3]),
        "wechat": ",".join(contact["wechat"][:3]),
        "note": (note + "\n更多联系方式：" + "；".join(contact["emails"][1:3] + contact["phones"][1:3]))[:500],
    }
    score, reason = rule_score({**cand, "email": email, "phone": phone})
    cand["score"] = score
    cand["score_reason"] = reason
    return cand


def run(keywords, markets=None, max_results=6, urls=None):
    """执行一次买家发现，返回 {candidates, errors}。"""
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.splitlines() if k.strip()]
    else:
        keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if isinstance(markets, str):
        markets = [m.strip() for m in markets.splitlines() if m.strip()]
    else:
        markets = [str(m).strip() for m in (markets or []) if str(m).strip()]
    keywords = keywords[:5]
    markets = markets[:5]
    errors = []
    seen = set()
    targets = []

    if urls:
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        for u in urls:
            u = str(u).strip()
            if u:
                targets.append({"url": u, "title": "", "keyword": "指定网址", "market": ""})
    else:
        if not keywords:
            return {"candidates": [], "errors": ["请至少填写一个关键词"]}
        for market in markets or [""]:
            for kw in keywords:
                try:
                    results = search_bing(kw, market, max_results)
                except Exception as e:
                    errors.append(f"搜索 {kw} {market} 失败：{e}")
                    continue
                for r in results:
                    if r["url"] in seen or is_blocked(r["url"]):
                        continue
                    seen.add(r["url"])
                    targets.append(r)
        if not targets:
            return {"candidates": [], "errors": errors or ["没有搜索到结果，请更换关键词"]}

    candidates = []
    for t in targets[:30]:
        try:
            html_text, _ = fetch_page(t["url"], timeout=12)
            contact = extract_contacts(html_text, t["url"])
            cand = _to_candidate(contact, t.get("title", ""), t.get("snippet", ""), t.get("keyword", ""), t.get("market", ""))
            candidates.append(cand)
        except Exception as e:
            errors.append(f"{t['url']} 抓取失败：{e}")
    return {"candidates": candidates, "errors": errors}
