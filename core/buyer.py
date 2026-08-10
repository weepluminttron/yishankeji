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


def _domain(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        return host.lower().lstrip("www.")
    except Exception:
        return ""


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


def build_queries(keyword, market=""):
    """为一个关键词 + 地区生成多组意图查询。"""
    overseas = bool(re.search(r"[a-zA-Z]{2,}", market or ""))
    if overseas:
        variants = [
            f"{keyword} buyer purchase procurement",
            f"{keyword} rfq tender project",
            f"{keyword} distributor dealer import",
        ]
    else:
        variants = [
            f"{keyword} 采购 询价",
            f"{keyword} 招标 项目 工程",
            f"{keyword} 需要 报价",
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


def _is_noise(title, snippet, url):
    if is_blocked(url):
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
    name = contact["company"] or _clean_company(title, contact["website"]) or "未命名"
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
                results = search_bing(q, max_results)
            except Exception as e:
                errors.append(f"搜索“{q}”失败：{e}")
                continue
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
            html_text, _ = fetch_page(t["url"], timeout=10)
            contact = extract_contacts(html_text, t["url"])
            page_text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
            page_text = re.sub(r"<[^>]+>", " ", page_text)[:2000]
            cand = _to_candidate(contact, t.get("title", ""), t.get("snippet", ""), t.get("keyword", ""), t.get("market", ""), page_text)
            candidates.append(cand)
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

    candidates.sort(key=lambda c: c["score"], reverse=True)
    dropped_low = 0
    keep = []
    for c in candidates:
        if c["score"] < 2:
            dropped_low += 1
            continue
        keep.append(c)
    return {"candidates": keep, "errors": errors, "filtered": filtered, "dropped_low": dropped_low}
