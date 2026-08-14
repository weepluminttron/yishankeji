# -*- coding: utf-8 -*-
"""公开企业信息聚合（对齐“企查查=采集公开信息再加工”的思路，但不爬企查查/天眼查）。

只采集互联网上已公开的页面摘要与网页内容：
- 政府公示：国家企业信用信息公示系统、信用中国、裁判文书网、执行公开
- 招投标：中国政府采购网、地方政采、公共资源交易
- 上市公司公告：巨潮资讯
- 企业官网：公开座机/邮箱/地址
- 招聘网站：公开岗位 → 扩张信号

流程：多源检索 → 去重合并 → 打标签 → 提取公开联系方式 → 输出结构化画像。
企查查/天眼查等商业站点只作为“发现入口”的搜索摘要，绝不抓取其页面；
完整工商底档请配置官方开放平台 API（core.company_api）。
"""
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from core import buyer, crawler

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
ADDR_RE = re.compile(r"([\u4e00-\u9fff]{2,}(?:省|市|区|县|镇)[\u4e00-\u9fff、，,号栋楼室()（）\-\s]{2,40})")

# 公开来源域名 → (来源类型, 默认标签)
PUBLIC_SITES = [
    ("gsxt.gov.cn", "工商公示", "工商公示"),
    ("creditchina.gov.cn", "信用中国", "信用公示"),
    ("wenshu.court.gov.cn", "裁判文书", "司法记录"),
    ("zxgk.court.gov.cn", "执行公开", "司法记录"),
    ("ccgp.gov.cn", "政府采购", "招投标"),
    ("zfcg.gov.cn", "政府采购", "招投标"),
    ("ggzy.gov.cn", "公共资源交易", "招投标"),
    ("cninfo.com.cn", "上市公司公告", "上市公司"),
    ("sse.com.cn", "交易所公告", "上市公司"),
    ("szse.cn", "交易所公告", "上市公司"),
    ("zhipin.com", "招聘", "招聘扩张"),
    ("liepin.com", "招聘", "招聘扩张"),
    ("51job.com", "招聘", "招聘扩张"),
]

# 绝不抓取页面的商业聚合站点（只保留搜索结果摘要）
NO_FETCH_DOMAINS = ("qcc.com", "qichacha.com", "tianyancha.com", "aiqicha.baidu.com",
                    "gongshang.mingluji.com", "qixin.com", "shuidi.cn", "qichacha.com.cn")

TAG_RULES = [
    ("正在招标/采购", ("招标", "中标", "采购公告", "询价公告", "供应商征集", "采购项目", "招标公告", "中标公告", "采购结果", "集采")),
    ("司法/被执行", ("裁判文书", "执行信息", "被执行", "失信", "限制高消费", "开庭公告", "诉讼")),
    ("信用风险", ("行政处罚", "经营异常", "严重违法", "列入异常")),
    ("高新技术/专精特新", ("高新技术企业", "科技型中小企业", "专精特新", "瞪羚企业", "小巨人")),
    ("招聘扩张", ("招聘", "急聘", "诚聘", "社招", "岗位", "人才")),
    ("上市公司", ("cninfo.com.cn", "sse.com.cn", "szse.cn", "股票代码", "年度报告", "董事会公告")),
    ("政府采购供应商", ("ccgp.gov.cn", "zfcg.gov.cn", "ggzy.gov.cn", "政府采购")),
]


def _host_of(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _domain(url):
    h = _host_of(url)
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def _company_core(company):
    """去掉常见公司后缀，取核心名（如 腾景科技股份有限公司 → 腾景科技）。"""
    c = re.sub(r"(股份有限公司|有限责任公司|有限公司|集团|公司)$", "", company or "").strip()
    return c


def _about_company(s, company):
    """判断一条来源是否确实提到目标公司（避免把同名/无关页面当官网抓取）。"""
    core = _company_core(company)
    blob = ((s.get("title") or "") + " " + (s.get("url") or "") + " " + (s.get("snippet") or "")).lower()
    if len(core) >= 4:
        return core.lower() in blob
    return (company or "").lower() in blob


def _site_type(url):
    h = _host_of(url)
    for dom, typ, tag in PUBLIC_SITES:
        if h == dom or h.endswith("." + dom):
            return typ, tag
    return "官网/网页", "公开网页"


def _clean(t):
    return re.sub(r"\s+", " ", t or "").strip()


def _queries(company, region):
    c = company.strip()
    r = region.strip()
    core = _company_core(c)
    loc = (c + " " + r).strip()
    quoted = '"' + (core if len(core) >= 4 else c) + '"'
    return [
        (f"{loc} site:gsxt.gov.cn", "工商公示"),
        (f"{loc} site:creditchina.gov.cn", "信用中国"),
        (f"{loc} 裁判文书 被执行", "司法公开"),
        (f"{loc} 中标 采购公告", "政府采购"),
        (f"{loc} 公告 site:cninfo.com.cn", "上市公司公告"),
        (f"{quoted} 官网", "官网"),
        (f"{quoted} 招聘", "招聘"),
    ]


def _safe_search(fn, query, count, settings):
    try:
        return fn(query, count, settings=settings) or []
    except Exception:
        return []


def _run_search_pair(query, count, settings):
    """并发跑 百度+搜狗；返回 (原始结果列表, 是否有任一路成功)。"""
    rows = []
    ok = False
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [
            ex.submit(_safe_search, buyer.search_baidu, query, count, settings),
            ex.submit(_safe_search, buyer.search_sogou, query, count, settings),
        ]
        for fut in as_completed(futs, timeout=40):
            try:
                got = fut.result()
            except Exception:
                continue
            if got:
                ok = True
                rows += got
    return rows, ok


def _search_one(query, label, settings, count=4):
    """百度 + 搜狗 并发检索（国内公司/招标类最稳；Bing 在本代理上常返回罐头结果，暂不用）。"""
    rows, ok = _run_search_pair(query, count, settings)
    if not rows:
        # 代理被限流时，直连重试一次（服务器直连对搜狗通常可用）
        direct = dict(settings or {})
        direct["proxy_pool"] = ""
        direct["proxy_url"] = ""
        rows, ok = _run_search_pair(query, count, direct)
    out = []
    seen = set()
    for r in rows:
        url = (r.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        # 过滤搜索引擎跳转/百科/内容站等噪音域名（保留政府、公司官网与搜狗跳转链接）
        d = _domain(url)
        if any(d == b or d.endswith("." + b) for b in buyer.BLOCKED_DOMAINS):
            continue
        if url in seen:
            continue
        seen.add(url)
        typ, tag = _site_type(url)
        out.append({
            "title": _clean(r.get("title", ""))[:120],
            "url": url,
            "snippet": _clean(r.get("snippet", ""))[:300],
            "type": typ,
            "tag": tag,
            "query": label,
        })
    return out


def _extract_contacts(html_text, url):
    phones, emails, addresses = [], [], []
    try:
        for c in crawler.extract_candidates(html_text, url):
            if c.get("phone"):
                phones.append(c["phone"])
            if c.get("address") and len(addresses) < 3:
                addresses.append(c["address"])
    except Exception:
        pass
    text = re.sub(r"<[^>]+>", " ", html_text or "")
    for m in EMAIL_RE.finditer(text):
        e = m.group(0).strip(".")
        if e and e not in emails:
            emails.append(e)
    if not phones:
        for m in MOBILE_RE.finditer(text):
            phones.append(m.group(0))
    if not phones:
        for m in LANDLINE_RE.finditer(text):
            phones.append(m.group(0).replace("-", ""))
    if not addresses:
        m = ADDR_RE.search(text)
        if m:
            addresses.append(m.group(1).strip())
    return phones[:4], emails[:4], addresses[:2]


def discover(company, region="", settings=None, max_results=8, max_fetch=3):
    """多源公开信息聚合，返回结构化画像 dict（失败返回空画像，不抛异常）。"""
    company = (company or "").strip()
    if not company:
        return {"company": "", "tags": [], "sources": [], "contacts": [], "errors": ["公司名为空"]}
    settings = settings or {}
    sources = []
    seen_urls = set()

    def _dedupe(row):
        u = row["url"].split("#")[0]
        if u in seen_urls:
            return False
        seen_urls.add(u)
        return True

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_search_one, q, label, settings, 4): label for q, label in _queries(company, region)}
        for fut in as_completed(futs, timeout=60):
            try:
                for row in fut.result():
                    if not _dedupe(row):
                        continue
                    # 通用网页必须确实提到目标公司才作为证据（政府/招投标/公告/招聘来源除外）
                    if row["type"] not in ("工商公示", "信用中国", "司法公开", "政府采购", "上市公司公告", "招聘") and not _about_company(row, company):
                        continue
                    sources.append(row)
            except Exception:
                pass

    # 打标签（标题/摘要）
    tags = []
    text_all = " ".join(s["title"] + " " + s["snippet"] for s in sources)
    for tag, kws in TAG_RULES:
        if any(k.lower() in text_all.lower() for k in kws):
            tags.append(tag)
    tags = list(dict.fromkeys(tags))

    # 抓取官网/企业网页的公开联系方式（跳过政府与商业聚合站点）
    # 只抓取确实提到该公司的页面，且优先抓官网/关于我们，跳过跳转链接与职业门户
    job_portals = ("jobui.com", "kanzhun.com", "maimai.cn", "zhaopin.com", "bosszhipin.com")
    fetch_candidates = []
    for s in sources:
        h = _host_of(s["url"])
        if s["type"] in ("工商公示", "信用中国", "司法公开", "政府采购", "上市公司公告", "招聘"):
            continue
        d = _domain(s["url"])
        if any(d == b or d.endswith("." + b) for b in buyer.BLOCKED_DOMAINS):
            continue
        if any(h == d or h.endswith("." + d) for d in NO_FETCH_DOMAINS):
            continue
        if any(h == j or h.endswith("." + j) for j in job_portals):
            continue
        if not _about_company(s, company):
            continue
        fetch_candidates.append(s)

    def _candidate_rank(s):
        h = _host_of(s["url"])
        if h.endswith("sogou.com") or h.endswith("baidu.com"):
            return 2
        if "官网" in (s.get("title") or "") or "关于我们" in (s.get("title") or "") or "联系我们" in (s.get("title") or ""):
            return 0
        return 1

    fetch_candidates.sort(key=_candidate_rank)
    fetch_candidates = fetch_candidates[:max_fetch]

    contacts = []
    contact_urls = set()

    def _fetch_one(s):
        try:
            html, final = crawler.fetch_page(s["url"], timeout=8, use_jina=False, settings=settings)
        except Exception:
            return None
        phones, emails, addresses = _extract_contacts(html, s["url"])
        return {"s": s, "final": final or s["url"], "phones": phones, "emails": emails, "addresses": addresses}

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_fetch_one, s): s for s in fetch_candidates}
        for fut in as_completed(futs, timeout=40):
            try:
                got = fut.result()
            except Exception:
                continue
            if not got:
                continue
            s = got["s"]
            src_url = got.get("final") or s["url"]
            conf = "官网" if s["type"] == "官网/网页" else "公开网页"
            for p in got["phones"]:
                key = ("phone", p)
                if key not in contact_urls:
                    contact_urls.add(key)
                    contacts.append({"type": "phone", "value": p, "source_url": src_url, "confidence": conf})
            for e in got["emails"]:
                key = ("email", e)
                if key not in contact_urls:
                    contact_urls.add(key)
                    contacts.append({"type": "email", "value": e, "source_url": src_url, "confidence": conf})
            for a in got["addresses"]:
                key = ("address", a)
                if key not in contact_urls:
                    contact_urls.add(key)
                    contacts.append({"type": "address", "value": a, "source_url": src_url, "confidence": conf})

    sources = sources[:max_results] if max_results else sources
    profile = {
        "company": company,
        "region": region,
        "tags": tags,
        "sources": sources,
        "contacts": contacts,
        "evidence_count": len(sources),
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "errors": [],
    }
    return profile


def summary_text(profile, max_len=2000):
    """把画像压成一段文本，供 AI 上下文 / 跟进记录使用。"""
    p = profile or {}
    parts = [f"公司：{p.get('company', '')}"]
    if p.get("region"):
        parts.append(f"地区：{p['region']}")
    if p.get("tags"):
        parts.append("标签：" + "、".join(p["tags"]))
    if p.get("contacts"):
        parts.append("公开联系方式：" + "；".join(f"{c['type']}={c['value']}（{c.get('confidence', '')}）" for c in p["contacts"][:6]))
    if p.get("sources"):
        parts.append("公开来源：")
        for s in p["sources"][:10]:
            parts.append(f"- [{s.get('type', '')}] {s.get('title', '')} {s.get('url', '')}")
    txt = "\n".join(parts)
    return txt[:max_len]


def to_company_info(profile):
    """转成与 company_api 返回一致的结构（供“查工商”写入跟进记录/补充客户资料）。"""
    p = profile or {}
    phone = next((c["value"] for c in p.get("contacts", []) if c["type"] == "phone"), "")
    email = next((c["value"] for c in p.get("contacts", []) if c["type"] == "email"), "")
    address = next((c["value"] for c in p.get("contacts", []) if c["type"] == "address"), "")
    return {
        "source": "公开信息聚合（多源）",
        "company": p.get("company", ""),
        "credit_code": "",
        "legal_person": "",
        "reg_capital": "",
        "estiblish_time": "",
        "reg_status": "",
        "address": address,
        "phone": phone,
        "email": email,
        "tags": p.get("tags", []),
        "sources": p.get("sources", []),
        "updated": p.get("updated", ""),
    }