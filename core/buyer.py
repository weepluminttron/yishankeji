# -*- coding: utf-8 -*-
"""买家发现 v2：意图组合搜索 → 噪音过滤 → 页面抓取 → 联系方式提取 → 买家/供应商信号评分。

思路参考 b2b-buyer-discovery，并针对“精准度”做了强化：
1. 每个关键词自动生成多组“采购/招标/询价/RFQ”意图查询；
2. 搜索阶段过滤黄页、新闻、招聘、平台等噪音站点；
3. 抓取阶段识别“采购意向词”和“供应商信号词”，疑似供应商直接降权；
4. 可选 AI 精筛（配置 API Key 后，让大模型判断是否为潜在买家）。
"""
import json
import re
import time
import urllib.error
import urllib.parse

from lxml import html as lh

from core.crawler import _clean_text, fetch_page
from core.scorer import rule_score

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
WHATSAPP_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
WECHAT_RE = re.compile(r"(?:wxid_[A-Za-z0-9_\-]+|微信号[：:]\s*[\w\-]{6,20})")

# 采购意向信号（中文）
INTENT_CN = ["采购", "求购", "询价", "招标", "投标", "项目", "工程", "需求", "订购", "购买", "需要", "批发", "经销商", "代理商", "合作"]
# 采购意向信号（英文，海外市场）
INTENT_EN = ["buyer", "purchase", "procurement", "import", "wholesale", "distributor", "dealer",
             "rfq", "sourcing", "inquiry", "tender", "project", "need"]
# 供应商/同行信号（出现则降权）
SUPPLIER_WORDS = ["厂家直供", "厂家直销", "现货供应", "批发价", "出厂价", "价格优惠", "量大从优",
                  "supplier", "manufacturer", "wholesale price", "factory price", "export",
                  "我们生产", "我司生产", "主营产品", "热销", "产品中心", "产品介绍",
                  "产品系列", "产品展示", "产品分类", "产品参数", "光纤预制棒"]
# 纯噪音站点信号（直接过滤）
NOISE_WORDS = ["黄页", "百科", "招聘", "求职", "文库", "下载", "登录", "注册", "论坛", "博客",
               "新闻", "资讯", "峰会", "大会", "展会", "知道", "问答", "教程", "视频", "小说"]

BLOCKED_DOMAINS = (
    "alibaba.com", "made-in-china.com", "1688.com", "taobao.com", "tmall.com", "jd.com",
    "baidu.com", "zhihu.com", "xiaohongshu.com", "douyin.com", "bilibili.com",
    "weibo.com", "sohu.com", "sina.com", "163.com", "qq.com", "toutiao.com",
    "facebook.com", "linkedin.com", "youtube.com", "instagram.com", "wikipedia.org",
    "icp.chinaz.com", "beian.miit.gov.cn", "tianyancha.com", "qichacha.com", "aiqicha.baidu.com",
    "gongshang.mingluji.com", "qcc.com",
    # 内容/技术博客与招聘平台（大概率不是买家）
    "csdn.net", "cnblogs.com", "juejin.cn", "segmentfault.com", "51cto.com", "oschina.net",
    "infoq.cn", "eefocus.com", "elecfans.com", "21ic.com", "zhipin.com", "liepin.com",
    "51job.com", "c114.com.cn",
)

WEBMAILS = {"qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "hotmail.com",
            "foxmail.com", "sina.com", "139.com", "aliyun.com", "icloud.com", "yahoo.com"}

# 搜索源被反爬时固定返回的“罐头结果”域名
JUNK_DOMAINS = ("baike.baidu.com", "zhihu.com", "zhuanlan.zhihu.com", "csdn.net",
                "toutiao.com", "1688.com", "b2bwiki.baidu.com", "sohu.com")


def _domain(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        return host.lower().lstrip("www.")
    except Exception:
        return ""


def _with_words(kw, words):
    """关键词 + 意图词，避免重复（如关键词已含“采购”就不再拼一次）。"""
    return " ".join([kw] + [w for w in words if w not in kw])


def _resolve_url(url):
    """把 Bing 的 r.bing.com 跳转链接还原成真实地址。"""
    try:
        p = urllib.parse.urlparse(url)
        if p.hostname and p.hostname.replace("www.", "") in ("r.bing.com", "bing.com"):
            q = urllib.parse.parse_qs(p.query)
            if q.get("url"):
                return q["url"][0]
    except Exception:
        pass
    return url


def is_blocked(url):
    d = _domain(_resolve_url(url))
    return any(d == b or d.endswith("." + b) for b in BLOCKED_DOMAINS)


def _is_vpn_link(url):
    """识别学校/机构 VPN 网关链接（内网页面，外部抓不到）。"""
    try:
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower()
        path = p.path.lower()
        if "vpn" in host:
            return True
        if re.search(r"/(?:https|http)/", path):
            return True
    except Exception:
        pass
    return False


def build_queries(keyword, market=""):
    """为一个关键词 + 地区生成多组意图查询。"""
    overseas = bool(re.search(r"[a-zA-Z]{2,}", market or ""))
    if overseas:
        variants = [
            _with_words(keyword, ["buyer", "purchase", "procurement"]),
            _with_words(keyword, ["rfq", "tender", "project"]),
            _with_words(keyword, ["distributor", "dealer", "import"]),
        ]
    else:
        variants = [
            _with_words(keyword, ["采购", "询价"]),
            _with_words(keyword, ["招标", "项目", "工程"]),
            _with_words(keyword, ["需要", "报价"]),
        ]
    out = []
    for v in variants:
        out.append(f"{v} {market}".strip())
    return out


def search_bing(query, count=5):
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&count=" + str(max(3, min(10, count)))
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
        results.append({"title": title, "url": href, "snippet": snippet})
    return results


def search_so(query, count=6):
    """360 搜索（免费，国内服务器可用）。真实地址在 data-mdurl 属性里。"""
    url = "https://www.so.com/s?q=" + urllib.parse.quote(query)
    html_text, _ = fetch_page(url)
    if len(html_text) < 12000 and ("访问异常" in html_text or "安全验证" in html_text or "captcha" in html_text.lower()):
        raise ValueError("360 搜索暂时被限流（访问异常），请稍后再试，或到“设置 → 搜索接口”配置 SerpAPI 更稳定")
    doc = lh.fromstring(html_text)
    results = []
    for li in doc.xpath("//li[contains(@class,'res-list')]")[:count]:
        a = li.xpath(".//h3/a | .//a[contains(@class,'res-title')]")
        if not a:
            continue
        a = a[0]
        href = (a.get("data-mdurl") or a.get("href") or "").strip()
        title = _clean_text(a.text_content())
        if not title or not href.startswith("http"):
            continue
        block = _clean_text(li.text_content())
        snippet = block.replace(title, "", 1).strip()[:160]
        results.append({"title": title, "url": href, "snippet": snippet})
    return results


def search_sogou(query, count=6):
    """搜狗搜索（免费备用源）。链接是 /link?url= 跳转，抓取时自动跟随。"""
    url = "https://www.sogou.com/web?query=" + urllib.parse.quote(query)
    html_text, _ = fetch_page(url)
    if len(html_text) < 12000 and any(w in html_text for w in ("访问过于频繁", "安全验证", "请输入验证码", "captcha")):
        raise ValueError("搜狗搜索暂时不可用")
    doc = lh.fromstring(html_text)
    results = []
    for a in doc.xpath("//h3/a | //a[contains(@class,'vr-title')]")[:count]:
        href = (a.get("href") or "").strip()
        title = _clean_text(a.text_content())
        if not title or len(title) < 4:
            continue
        if href.startswith("/"):
            href = "https://www.sogou.com" + href
        if not href.startswith("http"):
            continue
        block = _clean_text(a.xpath("ancestor::li[1]")[0].text_content()) if a.xpath("ancestor::li[1]") else title
        snippet = block.replace(title, "", 1).strip()[:160]
        results.append({"title": title, "url": href, "snippet": snippet})
    return results


def search_serpapi(query, count, api_key):
    url = ("https://serpapi.com/search.json?engine=google&google_domain=google.com.hk"
           "&q=" + urllib.parse.quote(query) + "&num=" + str(count) + "&hl=zh-cn&gl=cn&api_key=" + urllib.parse.quote(api_key))
    html_text, _ = fetch_page(url, timeout=20)
    data = json.loads(html_text)
    results = []
    for item in (data.get("organic_results") or [])[:count]:
        title = _clean_text(item.get("title", ""))
        link = item.get("link", "")
        snippet = _clean_text(item.get("snippet", ""))
        if title and link.startswith("http"):
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


def search_google_cse(query, count, api_key, engine_id):
    url = ("https://www.googleapis.com/customsearch/v1?key=" + urllib.parse.quote(api_key)
           + "&cx=" + urllib.parse.quote(engine_id) + "&q=" + urllib.parse.quote(query)
           + "&num=" + str(min(10, count)))
    html_text, _ = fetch_page(url, timeout=20)
    data = json.loads(html_text)
    results = []
    for item in (data.get("items") or [])[:count]:
        title = _clean_text(item.get("title", ""))
        link = item.get("link", "")
        snippet = _clean_text(item.get("snippet", ""))
        if title and link.startswith("http"):
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


def search_bocha(query, count, api_key):
    """博查 AI 搜索（国内稳定，API Key 格式为 64 位 hex）。"""
    payload = json.dumps({
        "query": query,
        "count": max(3, min(10, count)),
        "freshness": "noLimit",
        "summary": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.bochaai.com/v1/web-search",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    if data.get("code") not in (None, 200, 0):
        raise ValueError(f"博查搜索返回错误：{data.get('message') or data.get('msg') or data}")
    results = []
    for item in ((data.get("data") or {}).get("webPages") or {}).get("value") or []:
        title = _clean_text(item.get("name", ""))
        url = item.get("url", "")
        snippet = _clean_text(item.get("snippet") or item.get("summary") or "")
        if title and url.startswith("http"):
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


def _is_canned(results):
    """判断搜索源是否返回了反爬“罐头结果”（全是百科/知乎/内容站）。"""
    if not results:
        return False
    junk = 0
    for r in results:
        d = _domain(_resolve_url(r.get("url", "")))
        if any(d == j or d.endswith("." + j) for j in JUNK_DOMAINS):
            junk += 1
    return junk >= max(1, int(len(results) * 0.6))


def search_web(query, count, settings=None):
    """按设置选择搜索源；免费源被反爬限制时抛出明确错误。"""
    settings = settings or {}
    provider = settings.get("search_provider", "bing_free")
    key = settings.get("search_api_key", "")
    engine_id = settings.get("search_engine_id", "")
    if provider == "serpapi" and key:
        return search_serpapi(query, count, key)
    if provider == "google_cse" and key and engine_id:
        return search_google_cse(query, count, key, engine_id)
    if provider == "bocha" and key:
        return search_bocha(query, count, key)
    if provider == "so_free":
        chain = []
        try:
            results = search_so(query, count)
            if results:
                return results
            chain.append("360 无结果")
        except Exception as e:
            chain.append(f"360：{e}")
        try:
            time.sleep(2)
            results = search_sogou(query, count)
            if results:
                return results
            chain.append("搜狗无结果")
        except Exception as e:
            chain.append(f"搜狗：{e}")
        raise ValueError("免费搜索源均不可用（" + "；".join(chain) + "）。建议到“设置 → 搜索接口”配置 SerpAPI，更稳定精准")
    results = search_bing(query, count)
    if _is_canned(results):
        raise ValueError(
            "免费搜索源（Bing）被反爬限制，只能返回通用结果。请到“设置 → 搜索接口”"
            "配置 SerpAPI（免费200次/月）或 Google 自定义搜索 API 密钥后重试。"
        )
    return results


def _is_noise(title, snippet, url):
    if is_blocked(url):
        return True
    if _is_vpn_link(url):
        return True
    t = (title or "") + (snippet or "")
    return any(w in t for w in NOISE_WORDS)


def _text_signals(text):
    t = (text or "").lower()
    cn = sum(t.count(w) for w in INTENT_CN)
    en = sum(t.count(w) for w in INTENT_EN)
    sup = sum(t.count(w) for w in SUPPLIER_WORDS)
    return cn, en, sup


def _clean_company(raw, url):
    if not raw:
        return ""
    name = raw.strip()
    name = re.sub(r"[-_|｜]\s*(官网|首页|企业黄页|黄页|官方网站|公司简介|about|home|official)\s*$", "", name, flags=re.I)
    name = re.sub(r"\s*[-_|｜]\s*[^-_|｜]{0,12}(网|站|平台)\s*$", "", name)
    name = re.sub(r"(官网|首页|欢迎访问|欢迎来到)[：: ]*", "", name)
    name = name.strip(" -_|｜，。")
    if any(w in name for w in ("电话", "联系", "邮箱", "地址", "首页", "欢迎")):
        return ""
    return name[:80]


def extract_contacts(html_text, url=""):
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    emails = sorted(set(m.lower() for m in EMAIL_RE.findall(text)))
    phones = sorted(set(m.replace("-", "") for m in MOBILE_RE.findall(text) + LANDLINE_RE.findall(text)))
    whatsapp = sorted(set(m for m in WHATSAPP_RE.findall(text)))
    wechat = sorted(set(m for m in WECHAT_RE.findall(text)))
    doc = lh.fromstring(html_text)
    company = ""
    for xp in ("//meta[@property='og:site_name']/@content", "//meta[@property='og:title']/@content"):
        v = doc.xpath(xp)
        if v and _clean_company(v[0], url):
            company = _clean_company(v[0], url)
            break
    if not company:
        t = doc.findtext(".//title")
        company = _clean_company(t or "", url)
    if not company:
        h1 = doc.findtext(".//h1")
        company = _clean_company(h1 or "", url)
    return {
        "company": company,
        "emails": emails,
        "phones": phones,
        "whatsapp": whatsapp,
        "wechat": wechat,
        "website": url,
    }


def _score_candidate(cand, page_text):
    """买家意向评分：联系方式基础分 + 意图信号加分 - 供应商信号扣分。"""
    score = 0
    reasons = []
    cn, en, sup = _text_signals(page_text)
    if cn:
        score += min(3, cn)
        reasons.append(f"采购意向词×{cn}")
    if en:
        score += min(2, en)
        reasons.append(f"intent×{en}")
    if sup:
        score -= min(2, sup)
        reasons.append(f"疑似供应商×{sup}")
    email = cand.get("email", "")
    if email:
        dom = email.split("@")[-1].lower()
        if dom in WEBMAILS:
            score += 1
            reasons.append("有邮箱")
        else:
            score += 2
            reasons.append("企业邮箱")
    if cand.get("phone"):
        score += 2
        reasons.append("有电话")
    if cand.get("company"):
        score += 1
        reasons.append("有公司名")
    if cand.get("whatsapp") or cand.get("wechat"):
        score += 1
        reasons.append("有WhatsApp/微信")
    if cand.get("website"):
        score += 1
    score = max(0, min(10, score))
    return score, "；".join(reasons)


def _to_candidate(contact, title, snippet, keyword, market, page_text):
    email = contact["emails"][0] if contact["emails"] else ""
    phone = contact["phones"][0] if contact["phones"] else ""
    name = contact["company"] or _clean_company(title, contact["website"]) or ""
    if not name or name.lower() in ("undefined", "none", "null"):
        name = "未命名"
    cand = {
        "name": name,
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
        "snippet": snippet[:200],
        "note": (title + "。" + snippet)[:300],
    }
    score, reason = _score_candidate(cand, page_text)
    cand["score"] = score
    cand["score_reason"] = reason
    return cand


def ai_filter(settings, candidates):
    """AI 精筛：让大模型判断每条线索是否为潜在买家。失败时静默返回 None。"""
    if not candidates or not settings or not settings.get("openai_api_key"):
        return None
    from core import ai
    rows = []
    for i, c in enumerate(candidates):
        rows.append(f"{i}|{c.get('name','')[:30]}|{c.get('website','')}|{c.get('email','')}|{c.get('snippet','')[:80]}")
    system = (
        "你是光纤通信行业的采购线索评估专家。判断每条线索是【潜在买家】还是【供应商/同行/无关内容】。"
        '只输出一行 JSON 数组，元素格式：{"i":序号,"buyer":true或false,"score":0-10,"reason":"一句话"}，不要输出其他内容。'
    )
    text, err = ai.generate_copy(
        settings.get("openai_api_key"),
        settings.get("openai_model", "gpt-4o-mini"),
        system,
        "线索列表：\n" + "\n".join(rows),
        settings.get("openai_api_base", ""),
    )
    if err:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        result = {}
        for item in data:
            i = int(item.get("i", -1))
            if 0 <= i < len(candidates):
                result[i] = item
        return result
    except Exception:
        return None


def run(keywords, markets=None, max_results=6, urls=None, use_ai=False, settings=None):
    """执行一次买家发现，返回 {candidates, errors, filtered}。"""
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
    filtered = 0
    seen = set()
    targets = []

    if urls:
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        for u in urls:
            u = str(u).strip()
            if u:
                targets.append({"url": u, "title": "", "snippet": "", "keyword": "指定网址", "market": ""})
    else:
        if not keywords:
            return {"candidates": [], "errors": ["请至少填写一个关键词"]}
        queries = []
        for market in markets or [""]:
            for kw in keywords:
                queries.extend(build_queries(kw, market))
        queries = queries[:15]
        for q in queries:
            try:
                results = search_web(q, max_results, settings)
            except Exception as e:
                msg = f"搜索“{q}”失败：{e}"
                if msg not in errors:
                    errors.append(msg)
                time.sleep(2)
                continue
            time.sleep(2)
            for r in results:
                r["url"] = _resolve_url(r["url"])
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                if _is_noise(r["title"], r["snippet"], r["url"]):
                    filtered += 1
                    continue
                cn, en, sup = _text_signals(r["title"] + " " + r["snippet"])
                # 片段里只有供应商信号、没有任何采购意向 → 大概率是同行，跳过抓取
                if sup > 0 and cn == 0 and en == 0:
                    filtered += 1
                    continue
                r["keyword"] = q.split(" ")[0]
                r["market"] = q.split(" ", 1)[1] if " " in q else ""
                targets.append(r)
        if not targets:
            return {"candidates": [], "errors": errors or ["没有找到符合条件的线索，请换个关键词或地区"]}

    candidates = []
    for t in targets[:20]:
        try:
            html_text, final_url = fetch_page(t["url"], timeout=10)
            contact = extract_contacts(html_text, final_url or t["url"])
            page_text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
            page_text = re.sub(r"<[^>]+>", " ", page_text)[:2000]
            cand = _to_candidate(contact, t.get("title", ""), t.get("snippet", ""), t.get("keyword", ""), t.get("market", ""), page_text)
            candidates.append(cand)
        except urllib.error.HTTPError as e:
            errors.append(f"{t['url']} 抓取失败：页面返回 {e.code}（可能已失效或需登录）")
        except Exception as e:
            errors.append(f"{t['url']} 抓取失败：{e}")

    if use_ai:
        ai_map = ai_filter(settings, candidates)
        if ai_map:
            keep = []
            for i, c in enumerate(candidates):
                item = ai_map.get(i)
                if item is None:
                    keep.append(c)
                    continue
                if item.get("buyer") is False:
                    filtered += 1
                    continue
                try:
                    c["score"] = max(0, min(10, int(item.get("score", c["score"]))))
                except Exception:
                    pass
                if item.get("reason"):
                    c["score_reason"] = c.get("score_reason", "") + "；AI：" + str(item["reason"])[:60]
                keep.append(c)
            candidates = keep

    # 招标平台共享邮箱/电话去重：同一联系方式只保留在最高分候选上
    from collections import Counter
    email_counts = Counter(c.get("email", "") for c in candidates if c.get("email"))
    phone_counts = Counter(c.get("phone", "") for c in candidates if c.get("phone"))
    for c in candidates:
        if c.get("email") and email_counts[c["email"]] >= 2:
            c["email"] = ""
            c["score"] = max(0, c["score"] - 2)
            c["score_reason"] = (c.get("score_reason", "") + "；该邮箱为招标平台共享").strip("；")
        if c.get("phone") and phone_counts[c["phone"]] >= 2:
            c["phone"] = ""
            c["score"] = max(0, c["score"] - 2)
            c["score_reason"] = (c.get("score_reason", "") + "；该电话为招标平台共享").strip("；")

    candidates.sort(key=lambda c: c["score"], reverse=True)
    dropped_low = 0
    keep = []
    for c in candidates:
        if c["score"] < 2:
            dropped_low += 1
            continue
        keep.append(c)
    return {"candidates": keep, "errors": errors, "filtered": filtered, "dropped_low": dropped_low}
