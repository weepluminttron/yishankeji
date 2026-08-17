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
import urllib.request
from collections import Counter

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from lxml import html as lh

from core.crawler import _clean_text, fetch_page
from core.scorer import fit_comp_score, rule_score, tier_of
from core import search_cache
from core import concurrent_search
from core import antibot  # 反爬策略：搜索请求前随机延时 + 限流退避
from core import channels  # 惰性：channels 内部才 import 本模块，导入期安全
from core import contact_probe

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
# 强商业意向词：命中直接加权，白名单机制
STRONG_INTENT = [
    "采购经理", "采购部", "采购公告", "招标公告", "询价公告", "采购需求", "采购计划",
    "总包", "项目方", "供应商征集", "招标文件", "中标",
    "RFP", "RFQ", "procurement manager", "tender notice", "request for proposal",
    "request for quotation", "invitation to bid",
]

# 关键词必须包含的买方意图词（AI 方案生成后用来自动补齐）
INTENT_REQUIRED_CN = ("采购", "招标", "询价", "求购", "需求", "项目", "工程", "批发", "经销商", "代理", "供应商", "采购经理")
INTENT_REQUIRED_EN = ("buyer", "purchase", "procurement", "tender", "rfq", "sourcing", "inquiry",
                      "wholesale", "distributor", "dealer", "contractor", "import", "supplier")
PLAN_NOISE_WORDS = (
    "百科", "论文", "新闻", "资讯", "黄页", "知乎", "博客", "高校", "大学", "学院", "期刊", "学报",
    "招聘", "求职", "文库", "大会", "展会", "论坛", "行业资讯", "教程",
)
# 供应商/同行信号（出现则降权）
SUPPLIER_WORDS = ["厂家直供", "厂家直销", "现货供应", "批发价", "出厂价", "价格优惠", "量大从优",
                  "supplier", "manufacturer", "wholesale price", "factory price", "export",
                  "我们生产", "我司生产", "主营产品", "热销", "产品中心", "产品介绍",
                  "产品系列", "产品展示", "产品分类", "产品参数", "光纤预制棒"]
# 纯噪音站点信号（直接过滤）
NOISE_WORDS = ["黄页", "百科", "招聘", "求职", "文库", "下载", "登录", "注册", "论坛", "博客",
               "新闻", "资讯", "峰会", "大会", "展会", "知道", "问答", "教程", "视频", "小说",
               "论文", "期刊", "高校", "大学", "学院", "学术", "学报",
               "企业库", "名录", "大全", "厂商名录", "公司库", "联系方式大全",
               # 页面级噪音：错误页/反爬验证页/招标门户/免费发布平台
               "错误页面", "404", "access verification", "安全验证", "人机验证",
               "招标网", "采购网", "千里马", "优云优客", "免费发布",
               "mouser", "贸泽", "szlcsc", "立创", "c-fol", "讯石", "维库", "得捷", "digikey",
               "元器件商城", "电子元件商城", "元件查询", "现货商城"]

# 商业搜索源熔断：配额用完/鉴权失败后暂停该源，避免每个查询都白等几十秒
_PROVIDER_BLOCKED_UNTIL = {}
_PROVIDER_BLOCK_SECONDS = 600
_COMMERCIAL_NAMES = ("SerpAPI", "博查", "Google CSE")

# 商业搜索源并发排队限速：同一接口的并发请求串行化，避免瞬时并发触发 429
_PROVIDER_GATES = {
    "SerpAPI": {"lock": threading.Lock(), "last": 0.0, "min_interval": 0.9},
    "博查": {"lock": threading.Lock(), "last": 0.0, "min_interval": 1.1},
    "Google CSE": {"lock": threading.Lock(), "last": 0.0, "min_interval": 0.6},
}


def _gate_wait(name):
    """同一商业搜索源请求间的最小间隔（线程安全，超出的等待直接进入）。"""
    g = _PROVIDER_GATES.get(name)
    if not g:
        return
    with g["lock"]:
        wait = g["min_interval"] - (time.time() - g["last"])
        if wait > 0:
            time.sleep(wait)
        g["last"] = time.time()


def _provider_blocked(name):
    """商业搜索源是否处于熔断窗口内。"""
    until = _PROVIDER_BLOCKED_UNTIL.get(name)
    if not until:
        return False
    if time.time() < until:
        return True
    _PROVIDER_BLOCKED_UNTIL.pop(name, None)
    return False


def _mark_provider_blocked(name, err):
    """配额/鉴权类错误触发熔断；429 属于短期限流只暂停 60 秒，真没额度/鉴权失败才熔断 10 分钟。"""
    text = str(err).lower()
    if "429" in text or "too many" in text:
        _PROVIDER_BLOCKED_UNTIL[name] = time.time() + 60
        return True
    if any(k in text for k in ("401", "403", "quota", "unauthorized",
                               "forbidden", "配额", "无效", "鉴权")):
        _PROVIDER_BLOCKED_UNTIL[name] = time.time() + _PROVIDER_BLOCK_SECONDS
        return True
    return False

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
    "gongchang.com", "11467.com", "ofweek.com", "cnpp.com",
    # 黄页/免费信息站/招标门户类域名（多为噪音或反爬拦截页）
    "huangye88.com", "chaomiw.com", "fengj.com", "qianlima.com",
)

WEBMAILS = {"qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "hotmail.com",
            "foxmail.com", "sina.com", "139.com", "aliyun.com", "icloud.com", "yahoo.com"}

# 光纤行业同义词扩展（提升线索量）
SYNONYMS = {
    "光缆": ["光纤光缆", "通信光缆"],
    "光纤": ["光纤光缆", "光缆"],
    "光纤光缆": ["光缆", "通信光缆"],
    "通信光缆": ["光缆", "光纤光缆"],
    "光纤收发器": ["光收发器", "光电转换器"],
    "熔接机": ["光纤熔接机", "熔接设备"],
    "FTTH": ["光纤到户", "光纤入户"],
}

# 光纤行业获客词模板（长尾词 + 目标市场），对应行业获客方案
INDUSTRY_PRESETS = {
    "光缆/跳线长尾词": {
        "keywords": ["光纤跳线 3米 SC/UPC 电信级", "4芯室外单模光缆 钢丝铠装", "1U 96芯 MPO 光纤配线架"],
        "markets": ["广东", "浙江"],
    },
    "MPO/预端接（高利润）": {
        "keywords": ["MPO MTP 预端接光缆", "高密度光纤配线架 机房", "预端接 布线 方案"],
        "markets": ["广东", "上海", "北京"],
    },
    "分纤箱/终端盒/收发器（走量）": {
        "keywords": ["分纤箱 采购", "光纤终端盒 询价", "光纤收发器 工程商"],
        "markets": ["广东", "浙江", "江苏"],
    },
    "弱电/安防工程商（金矿）": {
        "keywords": ["弱电工程公司 光缆", "安防监控公司 光纤", "网络科技公司 综合布线", "机房建设 工程商"],
        "markets": ["广东", "湖南", "江西", "广西"],
    },
    "通信工程/总包公司": {
        "keywords": ["通信工程有限公司 光缆", "通信工程 采购 光缆", "FTTH 施工 公司"],
        "markets": ["广东", "四川", "湖北"],
    },
    "外贸买家（海外）": {
        "keywords": ["fiber optic cable distributor", "ISP telecom installation company", "FTTH contractor", "fiber patch cord wholesale"],
        "markets": ["Vietnam", "Indonesia", "UAE", "Saudi Arabia", "Nigeria", "Brazil"],
    },
}

# 搜索源被反爬时固定返回的“罐头结果”域名
JUNK_DOMAINS = ("baike.baidu.com", "zhihu.com", "zhuanlan.zhihu.com", "csdn.net",
                "toutiao.com", "1688.com", "b2bwiki.baidu.com", "sohu.com",
                "recommend_list.baidu.com")


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
    """为一个关键词 + 地区生成多组意图查询（场景词矩阵，覆盖采购/招标/扩产/展会等场景）。"""
    overseas = _market_overseas(market)
    if overseas:
        variants = [
            _with_words(keyword, ["buyer", "purchase", "procurement"]),
            _with_words(keyword, ["rfq", "tender", "project"]),
            _with_words(keyword, ["distributor", "dealer", "import"]),
            _with_words(keyword, ["procurement manager", "RFP"]),
            _with_words(keyword, ["supplier sourcing", "vendor registration"]),
            _with_words(keyword, ["factory expansion", "new capacity"]),
            _with_words(keyword, ["exhibition", "exhibitor"]),
            _with_words(keyword, ["annual report", "procurement announcement"]),
        ]
    else:
        variants = [
            _with_words(keyword, ["采购", "询价"]),
            _with_words(keyword, ["招标", "公告"]),
            _with_words(keyword, ["求购", "信息"]),
            _with_words(keyword, ["需要", "报价"]),
            _with_words(keyword, ["采购经理", "项目"]),
            _with_words(keyword, ["供应商征集", "供应商入围"]),
            _with_words(keyword, ["扩产", "募投", "新产线"]),
            _with_words(keyword, ["集采", "中标"]),
            _with_words(keyword, ["展会", "展商"]),
            _with_words(keyword, ["长期合作", "代理招募"]),
        ]
    out = []
    for v in variants:
        out.append(f"{v} {market}".strip())
    return out


def _market_overseas(market):
    """判断市场是否海外（支持英文与中文海外市场名）。"""
    m = market or ""
    if re.search(r"[a-zA-Z]{2,}", m):
        return True
    return any(w in m for w in (
        "印度", "印尼", "越南", "泰国", "马来西亚", "日本", "韩国", "新加坡", "沙特", "阿联酋",
        "巴西", "尼日利亚", "德国", "英国", "法国", "美国", "欧洲", "中东", "非洲", "拉美", "海外",
    ))


def expand_keywords(keyword):
    """同义词扩展：光缆 → 光缆/光纤光缆/通信光缆。"""
    kw = keyword.strip()
    if kw in SYNONYMS:
        return [kw] + SYNONYMS[kw]
    return [kw]


def polish_plan_keywords(keywords, markets):
    """清洗 AI 方案关键词：去噪音、自动补齐买方意图词、去重。"""
    overseas = any(re.search(r"[a-zA-Z]{2,}", m or "") for m in (markets or []))
    out = []
    for raw in keywords or []:
        k = str(raw).strip()
        if len(k) < 2:
            continue
        if any(n in k for n in PLAN_NOISE_WORDS):
            continue
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", k))
        is_abbr = not has_cjk and len(k) <= 8 and " " not in k
        use_en = not has_cjk and (overseas if is_abbr else True)
        required = INTENT_REQUIRED_EN if use_en else INTENT_REQUIRED_CN
        if not any(w in k.lower() for w in required):
            k = k + (" buyer" if use_en else " 采购")
        if k not in out:
            out.append(k)
    return out[:12]


def search_bing_rss(query, count=5, qdr="", settings=None):
    """Bing RSS 订阅接口：比 HTML 版更稳，反爬拦截少；失败时由调用方降级。"""
    antibot.human_delay(settings, key="search")
    last_err = ""
    for host in ("https://www.bing.com/search?", "https://cn.bing.com/search?"):
        url = (host + "format=rss&q=" + urllib.parse.quote(query)
               + "&count=" + str(max(3, min(10, count)))
               + (("&qdr=" + qdr) if qdr else ""))
        try:
            html_text, _ = fetch_page(url, timeout=12, use_jina=False, settings=settings)
        except Exception as e:
            last_err = str(e)[:100]
            continue
        if "<rss" not in html_text[:3000].lower() and "<item>" not in html_text.lower():
            last_err = "未返回 RSS 内容（可能被反爬）"
            continue
        doc = lh.fromstring(html_text.encode("utf-8", errors="replace"))
        results = []
        for item in doc.xpath("//item")[:count]:
            title = _clean_text(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            desc = _clean_text(item.findtext("description") or "")
            if not title or not link.startswith("http"):
                continue
            results.append({"title": title, "url": link, "snippet": desc[:220]})
        if results:
            return results
        last_err = "RSS 无有效结果"
    raise ValueError("Bing RSS 无有效结果（" + last_err + "）")


def search_bing(query, count=5, qdr="", settings=None):
    """Bing 搜索：优先 RSS 订阅接口（反爬更稳），失败再走 HTML。"""
    try:
        return search_bing_rss(query, count, qdr, settings=settings)
    except Exception:
        pass
    antibot.human_delay(settings, key="search")  # 行为模拟：请求前随机延时
    common = ("&count=" + str(max(3, min(10, count)))
              + "&mkt=zh-CN&setlang=zh-hans"
              + (("&qdr=" + qdr) if qdr else ""))
    # 主站 → cn 站 双地址兜底（机房 IP 常被 www 版反爬）
    for host in ("https://www.bing.com/search?", "https://cn.bing.com/search?"):
        url = host + "q=" + urllib.parse.quote(query) + common
        try:
            html_text, _ = fetch_page(url, settings=settings)
        except Exception:
            continue
        if antibot.detect_block(html_text):
            antibot.record_stats("blocked_detected")
            continue
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
        if results:
            return results
    raise ValueError("Bing 检测到反爬拦截，已触发重试/降级")


def search_so(query, count=6, settings=None):
    """360 搜索（免费，国内服务器可用）。真实地址在 data-mdurl 属性里。"""
    antibot.human_delay(settings, key="search")
    # 桌面版 → 移动版 双地址兜底
    for host in ("https://www.so.com/s?", "https://m.so.com/s?"):
        url = host + "q=" + urllib.parse.quote(query)
        try:
            html_text, final = fetch_page(url, settings=settings)
        except Exception:
            continue
        if "qcaptcha" in (final or "").lower() or "访问异常" in html_text or "安全验证" in html_text or "captcha" in html_text.lower():
            antibot.record_stats("blocked_detected")
            raise ValueError("360 搜索被风控（验证码拦截）：当前 IP 被 360 限制，请更换代理 IP 或稍后再试")
        if len(html_text) < 12000:
            continue
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
        if results:
            return results
        fallback = []
        seen = set()
        for a in doc.xpath("//h3/a | //a[contains(@class,'res-title')] | //li//a[@href]")[:count * 2]:
            href = (a.get("data-mdurl") or a.get("href") or "").strip()
            title = _clean_text(a.text_content())
            if not title or len(title) < 4 or not href.startswith("http") or href in seen:
                continue
            seen.add(href)
            fallback.append({"title": title, "url": href, "snippet": ""})
            if len(fallback) >= count:
                break
        if fallback:
            return fallback
    raise ValueError("360 搜索暂时被限流（访问异常），请稍后再试，或到“设置 → 搜索接口”配置 SerpAPI 更稳定")


def search_sogou(query, count=6, settings=None):
    """搜狗搜索（免费备用源）。链接是 /link?url= 跳转，抓取时自动跟随。"""
    antibot.human_delay(settings, key="search")
    # 先访问搜狗首页种下 SNUID 等 Cookie，降低首次请求触发安全验证的概率
    try:
        fetch_page("https://www.sogou.com/", settings=settings, use_jina=False, timeout=8)
    except Exception:
        pass
    url = "https://www.sogou.com/web?query=" + urllib.parse.quote(query) + "&ie=utf8"
    last_err = ""
    for attempt in range(3):
        html_text, _ = fetch_page(url, settings=settings, use_jina=False, timeout=15)
        if ("antispider" in html_text.lower() or "访问过于频繁" in html_text
                or "安全验证" in html_text or "请输入验证码" in html_text or "captcha" in html_text.lower()):
            antibot.record_stats("blocked_detected")
            last_err = "触发安全验证"
            time.sleep(2 + attempt)
            continue
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
        if results:
            return results
        # 兜底：跳过 javascript 与站内导航链接
        fallback = []
        seen = set()
        for a in doc.xpath("//a[@href]")[:count * 4]:
            href = (a.get("href") or "").strip()
            title = _clean_text(a.text_content())
            if not title or len(title) < 4 or href.startswith("javascript") or href in seen:
                continue
            seen.add(href)
            if href.startswith("/"):
                href = "https://www.sogou.com" + href
            if not href.startswith("http"):
                continue
            fallback.append({"title": title, "url": href, "snippet": ""})
            if len(fallback) >= count:
                break
        if fallback:
            return fallback
        last_err = "页面无搜索结果"
        time.sleep(1 + attempt)
    raise ValueError("搜狗搜索暂时不可用：" + last_err)




def search_baidu(query, count=6, settings=None):
    """百度搜索（免费，国内服务器+代理可用）。真实地址在结果块 li/div 的 mu 属性里。"""
    antibot.human_delay(settings, key="search")
    url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(query) + "&ie=utf-8"
    html_text, _ = fetch_page(url, settings=settings)
    head = html_text[:5000]
    if len(html_text) < 8000 or any(w in head for w in ("安全验证", "访问异常", "captcha", "验证码")):
        antibot.record_stats("blocked_detected")
        raise ValueError("百度搜索被风控（验证码/安全验证），当前 IP 被限制，请更换代理 IP 或稍后再试")
    doc = lh.fromstring(html_text)
    results = []
    seen = set()
    for li in doc.xpath("//div[contains(@class,'result')] | //li[contains(@class,'result')] | //div[contains(@class,'c-container')]")[:count * 2]:
        a = li.xpath(".//h3/a | .//a")
        if not a:
            continue
        a = a[0]
        title = _clean_text(a.text_content())
        if not title or len(title) < 4:
            continue
        href = (a.get("href") or "").strip()
        real = (li.get("mu") or "").strip()
        if real.startswith("http"):
            href = real
        elif href.startswith("/link?"):
            href = "https://www.baidu.com" + href
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        block = _clean_text(li.text_content())
        snippet = block.replace(title, "", 1).strip()[:160]
        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= count:
            break
    if results:
        return results
    fallback = []
    for a in doc.xpath("//h3/a")[:count * 2]:
        href = (a.get("href") or "").strip()
        title = _clean_text(a.text_content())
        if not title or len(title) < 4 or not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        fallback.append({"title": title, "url": href, "snippet": ""})
        if len(fallback) >= count:
            break
    return fallback

def search_serpapi(query, count, api_key, tbs="", settings=None):
    antibot.human_delay(settings, key="search")
    _gate_wait("SerpAPI")
    url = ("https://serpapi.com/search.json?engine=google&google_domain=google.com.hk"
           "&q=" + urllib.parse.quote(query) + "&num=" + str(count) + "&hl=zh-cn&gl=cn"
           + (("&tbs=" + urllib.parse.quote(tbs)) if tbs else "")
           + "&api_key=" + urllib.parse.quote(api_key))
    try:
        # API 返回 JSON，不走 Jina（Jina 只会浪费 12s 超时）
        html_text, _ = fetch_page(url, timeout=20, use_jina=False, settings=settings)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ValueError("SerpAPI 配额已用完（429 Too Many Requests），已自动暂停该源 10 分钟；"
                             "请到“设置 → 搜索接口”充值或更换 Key")
        if e.code in (401, 403):
            raise ValueError("SerpAPI 鉴权失败（HTTP %d），请检查 search_api_key 是否正确" % e.code)
        raise
    data = json.loads(html_text)
    results = []
    for item in (data.get("organic_results") or [])[:count]:
        title = _clean_text(item.get("title", ""))
        link = item.get("link", "")
        snippet = _clean_text(item.get("snippet", ""))
        if title and link.startswith("http"):
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


def search_google_cse(query, count, api_key, engine_id, settings=None):
    antibot.human_delay(settings, key="search")
    _gate_wait("Google CSE")
    url = ("https://www.googleapis.com/customsearch/v1?key=" + urllib.parse.quote(api_key)
           + "&cx=" + urllib.parse.quote(engine_id) + "&q=" + urllib.parse.quote(query)
           + "&num=" + str(min(10, count)))
    try:
        html_text, _ = fetch_page(url, timeout=20, use_jina=False, settings=settings)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ValueError("Google CSE 配额已用完（429），已自动暂停该源 10 分钟；"
                             "请到“设置 → 搜索接口”查看配额")
        if e.code in (401, 403):
            raise ValueError("Google CSE 鉴权失败（HTTP %d），请检查 Key/引擎 ID" % e.code)
        raise
    data = json.loads(html_text)
    results = []
    for item in (data.get("items") or [])[:count]:
        title = _clean_text(item.get("title", ""))
        link = item.get("link", "")
        snippet = _clean_text(item.get("snippet", ""))
        if title and link.startswith("http"):
            results.append({"title": title, "url": link, "snippet": snippet})
    return results


def search_bocha(query, count, api_key, freshness="noLimit", settings=None):
    """博查 AI 搜索（国内稳定，密钥在博查控制台获取）。"""
    antibot.human_delay(settings, key="search")
    _gate_wait("博查")
    payload = json.dumps({
        "query": query,
        "count": max(3, min(10, count)),
        "freshness": freshness,
        "summary": False,
    }).encode("utf-8")
    headers = antibot.build_headers("https://api.bochaai.com", settings, with_referer=False)
    headers.update({
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    })
    req = urllib.request.Request(
        "https://api.bochaai.com/v1/web-search",
        data=payload,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            import gzip as _gzip
            import io as _io
            raw = _gzip.GzipFile(fileobj=_io.BytesIO(raw)).read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
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


def _js_unescape(s):
    """反转义 JSON 字符串里的 \\u003c 等转义（头条结果内嵌 JSON 常见）。"""
    if not s:
        return ""
    try:
        return json.loads('"' + s.replace('"', '\\"') + '"')
    except Exception:
        return s.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\/", "/")


def search_toutiao(query, count=6, settings=None):
    """头条搜索（国内免费源，命中“采购/中标/订单”类商机信息质量高）。"""
    antibot.human_delay(settings, key="search")
    url = ("https://so.toutiao.com/search?dvpf=pc&source=input&keyword="
           + urllib.parse.quote(query) + "&pd=synthesis")
    html_text, _ = fetch_page(url, timeout=12, use_jina=False, settings=settings)
    if "emphasized" not in html_text and '"title"' not in html_text:
        raise ValueError("头条搜索无有效结果（可能被风控）")
    items = []
    seen = set()
    # 每个 emphasized.title 即一条结果：标题在前，source_url 在它前面的同一 JSON 对象里
    for mt in re.finditer(r'"emphasized"\s*:\s*\{[^{}]*"title"\s*:\s*"((?:[^"\\]|\\.)*)"', html_text):
        title = _js_unescape(mt.group(1)).strip()
        seg = html_text[max(0, mt.start() - 5000):mt.end()]
        url = ""
        for pat in (r'"source_url"\s*:\s*"(https?://[^"]+)"',
                    r'"share_url"\s*:\s*"(https?://[^"]+)"',
                    r'"media_url"\s*:\s*"(https?://[^"]+)"'):
            mu = re.findall(pat, seg)
            if mu:
                url = _js_unescape(mu[-1])
                break
        snippet = ""
        ms = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', html_text[mt.start():mt.end() + 3000])
        if ms:
            snippet = _js_unescape(ms.group(1)).strip()
        if not title or not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "snippet": snippet[:300]})
        if len(items) >= count:
            break
    if not items:
        raise ValueError("头条搜索无有效结果（页面结构变化或风控）")
    return items


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


def _site_scopes(query):
    """提取查询里的 site: 限定域名列表。"""
    return [m.lower() for m in re.findall(r"site:([\w.\-]+)", query or "")]


def _matches_site_scope(query, results):
    """site: 限定查询必须返回限定站点结果，否则视为反爬/无结果（Bing 国内版常见）。"""
    scopes = _site_scopes(query)
    if not scopes or not results:
        return True
    hit = 0
    for r in results:
        u = (r.get("url") or "").lower()
        if any(d in u for d in scopes):
            hit += 1
    return hit >= max(1, len(results) // 2)


def _query_tokens(query):
    """提取查询里的有效关键词（去掉 site: 限定；中文 2 字起、英文 3 字符起）。"""
    q = re.sub(r"site:[\w.\-]+", " ", query or "").lower()
    toks = set()
    for t in re.findall(r"[\w\u4e00-\u9fff]+", q):
        if re.search(r"[\u4e00-\u9fff]", t):
            if len(t) >= 2:
                toks.add(t)
        elif len(t) >= 3:
            toks.add(t)
    return toks


def _results_relevant(query, results):
    """结果里必须至少有一半命中查询关键词；否则判定为通用/无关结果（Bing 常见）。"""
    toks = _query_tokens(query)
    if not toks or not results:
        return True
    hit = 0
    for r in results:
        text = ("%s %s %s" % (r.get("title") or "", r.get("snippet") or "", r.get("url") or "")).lower()
        if any(t in text for t in toks):
            hit += 1
    return hit >= max(1, int(len(results) * 0.5))


def _apply_search_filters(query, settings):
    """按设置给查询加 时间范围 + 站点/域名过滤（对应 WebSearch 的时间与域名收窄）。"""
    q = query
    site = (settings or {}).get("search_site_filter") or ""
    if site:
        for d in [x.strip() for x in str(site).split(",") if x.strip()]:
            d = d if "site:" in d else "site:" + d
            if d not in q:
                q = q + " " + d
    return q.strip()


def _freshness_params(freshness):
    """搜索时间范围 → 各引擎参数（bocha 原值 / serpapi tbs / bing qdr）。"""
    f = str(freshness or "").strip().lower()
    if f in ("day", "d"):
        return {"bocha": "oneDay", "tbs": "qdr:d", "qdr": "d"}
    if f in ("week", "w"):
        return {"bocha": "oneWeek", "tbs": "qdr:w", "qdr": "w"}
    if f in ("month", "m"):
        return {"bocha": "oneMonth", "tbs": "qdr:m", "qdr": "m"}
    if f in ("year", "y"):
        return {"bocha": "oneYear", "tbs": "qdr:y", "qdr": "y"}
    return {"bocha": "noLimit", "tbs": "", "qdr": ""}


def search_web(query, count, settings=None):
    """按设置选择搜索源；主源失败（限流/配额/反爬）自动降级到备用源，避免“一个都搜不到”。"""
    settings = settings or {}
    provider = settings.get("search_provider", "bing_free")
    key = settings.get("search_api_key", "")
    engine_id = settings.get("search_engine_id", "")
    query = _apply_search_filters(query, settings)
    fp = _freshness_params(settings.get("search_freshness") or "")
    chain = []

    def _try_free(q=None):
        """免费源并行兜底：360/搜狗/头条/百度/Bing RSS 并发，命中即合并返回。"""
        q = q or query
        s = settings
        attempts = [
            ("360", lambda: search_so(q, count, settings=s)),
            ("搜狗", lambda: search_sogou(q, count, settings=s)),
            ("头条", lambda: search_toutiao(q, count, settings=s)),
            ("百度", lambda: search_baidu(q, count, settings=s)),
            ("Bing RSS", lambda: search_bing_rss(q, count, "", settings=s)),
        ]
        if str(s.get("parallel_free_search", "1")) == "0":
            errs = []
            for name, fn in attempts:
                try:
                    got = fn()
                    if got:
                        return got
                    errs.append(name + " 无结果")
                except Exception as e:
                    errs.append(f"{name}：{e}")
            raise ValueError("免费搜索源不可用（" + "；".join(errs) + "）")

        def _one(item):
            name, fn = item
            try:
                return name, fn(), ""
            except Exception as e:
                return name, [], str(e)[:100]

        results = []
        errs = []
        rlock = threading.Lock()
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = [ex.submit(_one, it) for it in attempts]
            for fut in as_completed(futs):
                name, got, err = fut.result()
                if got:
                    with rlock:
                        results.extend(got)
                else:
                    errs.append(f"{name}：{err}" if err else name + " 无结果")
        if results:
            seen = set()
            merged = []
            for r in results:
                u = _resolve_url(r.get("url", ""))
                if u in seen:
                    continue
                seen.add(u)
                if is_blocked(u) or _is_vpn_link(u) or _is_noise(r.get("title", ""), r.get("snippet", ""), u):
                    continue
                merged.append(r)
                if len(merged) >= count * 2:
                    break
            return merged
        raise ValueError("免费搜索源并行兜底不可用（" + "；".join(errs) + "）")

    def _try_direct(q=None, s=None):
        """代理被限流/风控时，清空代理用服务器直连重试（直连对搜狗通常可用）。"""
        q = q or query
        s = dict(s or settings)
        s["proxy_pool"] = ""
        s["proxy_url"] = ""
        errs = []
        for name, fn in (
            ("搜狗直连", lambda: search_sogou(q, count, settings=s)),
            ("头条直连", lambda: search_toutiao(q, count, settings=s)),
            ("百度直连", lambda: search_baidu(q, count, settings=s)),
            ("Bing直连", lambda: search_bing(q, count, fp["qdr"], settings=s)),
        ):
            try:
                results = fn()
                if not results:
                    errs.append(name + " 无结果")
                    continue
                if _is_canned(results):
                    errs.append(name + " 被反爬（只返回通用结果）")
                    continue
                if not _matches_site_scope(query, results):
                    errs.append(name + " 未返回 site: 限定站点结果")
                    continue
                if not _results_relevant(query, results):
                    errs.append(name + " 返回与查询无关的通用结果")
                    continue
                return results
            except Exception as e:
                errs.append(name + "：" + str(e)[:100])
        raise ValueError("直连重试也失败（" + "；".join(errs) + "）")

    sources = []
    if provider == "serpapi" and key and not _provider_blocked("SerpAPI"):
        sources.append(("SerpAPI", lambda: search_serpapi(query, count, key, fp["tbs"], settings=settings)))
    if provider == "google_cse" and key and engine_id and not _provider_blocked("Google CSE"):
        sources.append(("Google CSE", lambda: search_google_cse(query, count, key, engine_id, settings=settings)))
    if provider == "bocha" and key and not _provider_blocked("博查"):
        sources.append(("博查", lambda: search_bocha(query, count, key, fp["bocha"], settings=settings)))
    if provider == "toutiao":
        sources.append(("头条", lambda: search_toutiao(query, count, settings=settings)))
    elif provider == "baidu_free":
        sources.append(("百度", lambda: search_baidu(query, count, settings=settings)))
    elif provider not in ("serpapi", "google_cse", "bocha"):
        sources.append(("Bing", lambda: search_bing(query, count, fp["qdr"], settings=settings)))

    # 通用兜底：免费链 + Bing，保证任何主源失败都有备用
    if not any(n == "360/搜狗/头条/百度" for n, _ in sources):
        sources.append(("360/搜狗/头条/百度", _try_free))
    if not any(n == "Bing" for n, _ in sources):
        sources.append(("Bing", lambda: search_bing(query, count, fp["qdr"], settings=settings)))

    # 最后一道兜底：代理被限流时用服务器直连重试一次
    if settings.get("proxy_pool") or settings.get("proxy_url") or settings.get("proxy_api_url"):
        sources.append(("直连重试", _try_direct))
    # site: 限定全部失败时，去掉限定重试一次（海外站点国内免费源不索引）
    if _site_scopes(query) and str(settings.get("site_fallback_search", "1")) != "0":
        sources.append(("去掉site限定重试", lambda: _try_free(q=re.sub(r"site:[\w.\-]+", " ", query))))

    for name, fn in sources:
        try:
            results = fn()
            if not results:
                chain.append(f"{name} 无结果")
                continue
            if _is_canned(results):
                chain.append(f"{name} 被反爬（只返回通用结果）")
                continue
            if name != "去掉site限定重试" and not _matches_site_scope(query, results):
                chain.append(f"{name} 未返回 site: 限定站点结果")
                continue
            if not _results_relevant(query, results):
                chain.append(f"{name} 返回与查询无关的通用结果")
                continue
            return results
        except Exception as e:
            chain.append(f"{name}：{str(e)[:120]}")
            if name in _COMMERCIAL_NAMES and _mark_provider_blocked(name, e):
                chain.append(f"{name} 已熔断 10 分钟（配额/鉴权失败）")
    hint = ""
    if _site_scopes(query):
        hint = (" 当前查询带 site: 限定；国内免费源通常不索引海外站"
                "（reddit/x/linkedin/facebook 等），请在“设置 → 搜索接口”"
                "配置可用配额的 SerpAPI/博查，或给服务器配置海外出口代理后重试")
    else:
        hint = "建议到“设置 → 搜索接口”更换或补充分配额（SerpAPI/博查）后重试"
    raise ValueError("所有搜索源均不可用（" + "；".join(chain) + "）。" + hint)


def search_web_cached(query, count, settings):
    """带落盘缓存的搜索：命中缓存直接返回（增量复用，避免重复爬取）。"""
    settings = settings or {}
    if not settings.get("use_search_cache", True):
        return search_web(query, count, settings)
    provider = settings.get("search_provider", "bing_free")
    # 缓存键纳入时间/站点过滤，避免不同过滤条件互相串缓存
    cache_q = query + " [f:" + str(settings.get("search_freshness") or "") + "|" + str(settings.get("search_site_filter") or "") + "]"
    cached = search_cache.cache_get(provider, cache_q, count)
    if cached is not None:
        return cached
    results = search_web(query, count, settings)
    if results:
        search_cache.cache_set(provider, cache_q, count, results)
    return results


def _is_noise(title, snippet, url):
    if is_blocked(url):
        return True
    if _is_vpn_link(url):
        return True
    t = ((title or "") + (snippet or "") + " " + (url or "")).lower()
    return any(w in t for w in NOISE_WORDS)


def _text_signals(text):
    t = (text or "").lower()
    cn = sum(t.count(w) for w in INTENT_CN)
    en = sum(t.count(w) for w in INTENT_EN)
    sup = sum(t.count(w) for w in SUPPLIER_WORDS)
    strong = sum(t.count(w.lower()) for w in STRONG_INTENT)
    return cn, en, sup, strong


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


def _looks_verified(cand):
    """核验分级：企业邮箱（非免费邮箱）+ 电话 + 独立网站 → 视为可核验。"""
    email = str(cand.get("email") or "")
    phone = str(cand.get("phone") or "")
    website = str(cand.get("website") or "")
    if email and any(email.endswith("@" + w) for w in WEBMAILS):
        return False
    return bool(email and phone and website)


def extract_contacts(html_text, url=""):
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    emails = contact_probe.clean_emails(m.lower() for m in EMAIL_RE.findall(text))
    phones = contact_probe.clean_phones(m.replace("-", "") for m in MOBILE_RE.findall(text) + LANDLINE_RE.findall(text))
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
    cn, en, sup, strong = _text_signals(page_text)
    if cn:
        score += min(3, cn)
        reasons.append(f"采购意向词×{cn}")
    if en:
        score += min(2, en)
        reasons.append(f"intent×{en}")
    if sup:
        score -= min(2, sup)
        reasons.append(f"疑似供应商×{sup}")
    if strong:
        score += min(4, strong * 2)
        reasons.append(f"强意向词×{strong}")
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


def _to_candidate(contact, title, snippet, keyword, market, page_text, channels=None):
    email = (contact.get("emails") or [""])[0]
    phone = (contact.get("phones") or [""])[0]
    name = contact.get("company") or _clean_company(title, contact.get("website") or "") or ""
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
        "website": contact.get("website") or "",
        "whatsapp": ",".join((contact.get("whatsapp") or [])[:3]),
        "wechat": ",".join((contact.get("wechat") or [])[:3]),
        "snippet": snippet[:200],
        "note": (title + "。" + snippet)[:300],
        "next_action": "",
        "verified": False,
        "path": "官网公开信息栏 / 年报 / 行业展商名录（合法公开渠道）",
        "channels": ",".join(channels) if channels else "",
    }
    score, reason = _score_candidate(cand, page_text)
    cand["score"] = score
    cand["score_reason"] = reason
    cand["verified"] = _looks_verified(cand)
    # WorkBuddy 式双维度：匹配度 + 实力 → S/A/B/C 等级
    fc = fit_comp_score(cand)
    cand["fit"] = fc["fit"]
    cand["comp"] = fc["comp"]
    cand["tier"] = fc["tier"]
    # tags 只保留页面真实信号（等级），不再回显搜索关键词，
    # 避免“搜索词命中”被误当成“客户真的命中规格”
    cand["tags"] = fc["tier"] + "级"
    cand["query"] = keyword
    return cand


def _merge_same_domain(cands):
    """同域名线索合并：合并邮箱/电话，保留评分最高的一条。"""
    groups = {}
    order = []
    for c in cands or []:
        host = _domain(c.get("website") or "")
        name = str(c.get("name") or "").strip().lower()
        shared = any(host == h or host.endswith("." + h) for h in contact_probe.SHARED_HOSTS)
        key = "n:" + name if (not host or shared) else "h:" + host
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)
    out = []
    for key in order:
        group = groups[key]
        if len(group) == 1:
            out.append(group[0])
            continue
        best = max(group, key=lambda c: c.get("score", 0) or 0)
        emails, phones, whats, wechats, urls = [], [], [], [], []
        for c in group:
            if c.get("email") and c["email"] not in emails:
                emails.append(c["email"])
            if c.get("phone") and c["phone"] not in phones:
                phones.append(c["phone"])
            if c.get("whatsapp") and c["whatsapp"] not in whats:
                whats.append(c["whatsapp"])
            if c.get("wechat") and c["wechat"] not in wechats:
                wechats.append(c["wechat"])
            if c.get("website") and c["website"] not in urls:
                urls.append(c["website"])
        best = dict(best)
        best["email"] = ",".join(emails[:6])
        best["phone"] = ",".join(phones[:4])
        best["whatsapp"] = ",".join(whats[:3])
        best["wechat"] = ",".join(wechats[:3])
        if urls:
            best["website"] = urls[0]
        best["score_reason"] = (best.get("score_reason") or "") + "；已合并同域名线索×%d" % len(group)
        best["verified"] = _looks_verified(best)
        out.append(best)
    return out


def ai_filter(settings, candidates, context=""):
    """AI 精筛：结合业务描述判断每条线索是否为潜在买家，并给出评分与依据。
    失败时静默返回 None（不阻塞主流程）。"""
    if not candidates or not settings or not settings.get("openai_api_key"):
        return None
    from core import ai
    rows = []
    for i, c in enumerate(candidates):
        rows.append(f"{i}|{c.get('name','')[:30]}|{c.get('website','')}|{c.get('email','')}|{c.get('snippet','')[:80]}")
    industry = settings.get("industry", "") or "通用"
    company = settings.get("company_name", "") or "我方公司"
    products = settings.get("product_name", "") or "我们的产品"
    desc = str(context or "").strip() or f"我们主营{products}，想找有采购意向的客户。"
    system = (
        f"你是{industry}行业的资深采购线索分析师，服务对象是{company}。"
        "结合下面的【业务与理想客户描述】判断每条线索是【潜在买家】还是【供应商/同行/无关内容】。"
        "只输出一行 JSON 数组，元素格式："
        '{"i":序号,"buyer":true或false,"score":0到10的整数,"reason":"一句话结论",'
        '"points":["2到3条具体判断依据，如采购意向、规模信号、匹配度"],'
        '"fit":0到50的整数（品类/规格/定制匹配度）,'
        '"comp":0到50的整数（体量/层级/活跃度/可持续性）,'
        '"signal":"该客户最明显的采购信号(一句话，如：正在招标/刚中标/扩产采购)",'
        '"window":"最佳触达窗口(如：Q3扩产投产前送样 / 中标后48小时内)",'
        '"next_action":"针对该线索的下一步动作建议（如：电话确认采购预算 / 发样品报价单 / 加微信发案例，20字内）"}，'
        "不要输出任何其他内容。reason、points、next_action 必须基于线索本身的真实信息，禁止编造。"
    )
    user = (
        f"【业务与理想客户描述】\n{desc}\n\n"
        f"【我方主营】{products}\n\n"
        "【线索列表】\n" + "\n".join(rows) + "\n\n"
        "请逐条评估。"
    )
    text, err = ai.generate_copy(
        settings.get("openai_api_key"),
        settings.get("openai_model", "gpt-4o-mini"),
        system,
        user,
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
                # 兼容字段缺失：next_action 不存在时置空
                if "next_action" not in item or not str(item.get("next_action", "")).strip():
                    item["next_action"] = ""
                result[i] = item
        return result
    except Exception:
        return None


def run(keywords, markets=None, max_results=6, urls=None, use_ai=False, settings=None, progress=None, context="", channel_ids=None):
    """执行一次买家发现，返回 {candidates, errors, filtered}。"""
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.splitlines() if k.strip()]
    else:
        keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if isinstance(markets, str):
        markets = [m.strip() for m in markets.splitlines() if m.strip()]
    else:
        markets = [str(m).strip() for m in (markets or []) if str(m).strip()]
    keywords = keywords[:10]
    markets = markets[:5]
    expanded = []
    for kw in keywords:
        for e in expand_keywords(kw):
            if e not in expanded:
                expanded.append(e)
    keywords = expanded[:12]
    errors = []
    filtered = 0
    seen = set()
    targets = []
    timings = {"search": 0.0, "fetch": 0.0, "ai": 0.0}
    cache_hits = [0]

    use_cache = bool((settings or {}).get("use_search_cache", True))
    raw_results = []
    channel_stats = {}
    if urls:
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        for u in urls:
            u = str(u).strip()
            if u:
                raw_results.append({"url": u, "title": "", "snippet": "", "keyword": "指定网址", "market": ""})
    elif channel_ids:
        # 多源获客：从不同渠道（搜索引擎/社交媒体/行业站/论坛/招投标/地图…）并行搜索并聚合
        ch_ids = [c for c in channel_ids if c]
        if ch_ids:
            ch_raw, channel_stats = channels.run_channel_search(
                ch_ids, keywords=expanded, markets=markets, settings=settings,
                progress=progress, use_cache=use_cache,
                max_per_channel=int(settings.get("channel_max_per_channel") or 12),
            )
            raw_results = ch_raw
            # 把渠道级失败/跳过原因汇入 errors，避免“找不到客户”却看不到哪个渠道失败
            for cid, st in channel_stats.items():
                if st.get("error"):
                    msg = f"渠道[{cid}]搜索失败：{st['error']}"
                    if msg not in errors:
                        errors.append(msg)
                elif st.get("status") == "skipped" and st.get("reason"):
                    msg = f"渠道[{cid}]已跳过：{st['reason']}"
                    if msg not in errors:
                        errors.append(msg)
        if not raw_results and not keywords:
            return {"candidates": [], "errors": errors or ["请至少填写一个关键词"],
                    "channel_stats": channel_stats, "timings": timings, "cache_hits": cache_hits[0]}
    else:
        if not keywords:
            return {"candidates": [], "errors": ["请至少填写一个关键词"]}
        queries = []
        for market in markets or [""]:
            for kw in keywords:
                queries.extend(build_queries(kw, market))
        queries = queries[:40]
        q_total = len(queries)
        workers = max(1, min(int((settings or {}).get("max_search_workers", 8) or 8), 16))
        _stg = (settings or {}).get("search_stagger")
        stagger = 0.15 if _stg is None else float(_stg)  # 0.0 表示不限流（提交不 sleep）

        def _search_worker(q):
            try:
                provider = (settings or {}).get("search_provider", "bing_free")
                if use_cache and search_cache.cache_get(provider, q, max_results) is not None:
                    cache_hits[0] += 1
                    return ("ok", search_web_cached(q, max_results, settings), q)
                # 仅真正发起网络请求前做小幅限流；缓存命中则跳过 sleep，刷新即瞬时
                if stagger:
                    time.sleep(stagger)
                return ("ok", search_web_cached(q, max_results, settings), q)
            except Exception as e:
                return ("err", str(e), q)

        t0 = time.time()
        res_list = concurrent_search.parallel_map(
            _search_worker, queries, max_workers=workers, stagger=0.0,
            desc="正在搜索方案词", progress=progress,
        )
        timings["search"] = time.time() - t0
        for res in res_list:
            if res is None or isinstance(res, Exception):
                continue
            status, payload, q = res
            if status == "err":
                msg = f"搜索“{q}”失败：{payload}"
                if msg not in errors:
                    errors.append(msg)
                continue
            for r in payload:
                r["keyword"] = q.split(" ")[0]
                r["market"] = q.split(" ", 1)[1] if " " in q else ""
                raw_results.append(r)

    # ---- 统一过滤：resolve_url → URL 去重 → 噪音过滤 → 同行供应商过滤 ----
    # 多源与单源走同一套过滤口径，保证去重/归一化一致。
    for r in raw_results:
        r["url"] = _resolve_url(r.get("url", ""))
        if not r.get("url"):
            continue
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        if _is_noise(r.get("title", ""), r.get("snippet", ""), r["url"]):
            filtered += 1
            continue
        cn, en, sup, strong = _text_signals(r.get("title", "") + " " + r.get("snippet", ""))
        # 片段里只有供应商信号、没有任何采购意向 → 大概率是同行，跳过抓取
        if sup >= 2 and cn == 0 and en == 0 and strong == 0:
            filtered += 1
            continue
        r.setdefault("channels", [])
        targets.append(r)
    if not targets and not urls:
        return {"candidates": [], "errors": errors or ["没有找到符合条件的线索，请换个关键词或地区"]}

    candidates = []
    probe_contact_pages = str((settings or {}).get("probe_contact_pages", "1")) != "0"
    probe_limit = 12
    try:
        probe_limit = max(0, min(int((settings or {}).get("contact_probe_limit") or 12), 60))
    except Exception:
        probe_limit = 12
    probes_used = [0]
    probes_lock = threading.Lock()
    try:
        from core.smart_crawler import SmartCrawler
        _probe_smart = SmartCrawler(max_concurrent=2)
    except Exception:
        _probe_smart = None
    use_jina = bool((settings or {}).get("use_jina_fallback", True))
    fetch_targets = targets[:30]
    workers = max(1, min(int((settings or {}).get("max_fetch_workers", 8) or 8), 16))

    def _fetch_worker(t):
        try:
            html_text, final_url = fetch_page(t["url"], timeout=10, use_jina=use_jina, jina_timeout=12, settings=settings)
            contact = extract_contacts(html_text, final_url or t["url"])
            if probe_contact_pages and not (contact["emails"] and contact["phones"]):
                with probes_lock:
                    if probes_used[0] < probe_limit:
                        probes_used[0] += 1
                        allow_probe = True
                    else:
                        allow_probe = False
                if allow_probe:
                    try:
                        probe = contact_probe.probe_contacts(
                            t["url"], settings=settings, max_pages=2, main_html=html_text,
                            smart=_probe_smart,
                        )
                        contact = contact_probe.merge_contact_sets([contact, probe])
                    except Exception:
                        pass
            page_text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
            page_text = re.sub(r"<[^>]+>", " ", page_text)[:2000]
            cand = _to_candidate(contact, t.get("title", ""), t.get("snippet", ""), t.get("keyword", ""), t.get("market", ""), page_text, t.get("channels", []))
            return ("ok", cand)
        except urllib.error.HTTPError as e:
            return ("err", f"{t['url']} 抓取失败：页面返回 {e.code}（可能已失效或需登录）")
        except Exception as e:
            return ("err", f"{t['url']} 抓取失败：{e}")

    t0 = time.time()
    futs = concurrent_search.parallel_map(
        _fetch_worker, fetch_targets, max_workers=workers, stagger=0.0,
        desc="正在分析页面", progress=progress,
    )
    timings["fetch"] = time.time() - t0
    for res in futs:
        if res is None or isinstance(res, Exception):
            continue
        status, payload = res
        if status == "err":
            errors.append(payload)
        else:
            candidates.append(payload)

    candidates = _merge_same_domain(candidates)

    if use_ai:
        if progress:
            progress({"stage": "AI 智能筛选", "done": 0, "total": len(candidates)})
        ta = time.time()
        ai_map = ai_filter(settings, candidates, context)
        timings["ai"] = time.time() - ta
        if ai_map:
            keep = []
            for i, c in enumerate(candidates):
                item = ai_map.get(i)
                if item is None:
                    keep.append(c)
                    continue
                if item.get("buyer") is False:
                    c["score"] = min(c["score"], 3)
                    c["score_reason"] = c.get("score_reason", "") + "；AI：疑似非买家（保留供判断）"
                else:
                    try:
                        c["score"] = max(0, min(10, int(item.get("score", c["score"]))))
                    except Exception:
                        pass
                if item.get("reason"):
                    c["score_reason"] = c.get("score_reason", "") + "；AI：" + str(item["reason"])[:60]
                c["next_action"] = str(item.get("next_action") or "").strip()
                c["signal"] = str(item.get("signal") or "").strip()
                c["window"] = str(item.get("window") or "").strip()
                # AI 给出的匹配度/实力双维度（缺失时保留规则值）
                try:
                    if item.get("fit") is not None:
                        c["fit"] = max(0, min(50, int(item["fit"])))
                    if item.get("comp") is not None:
                        c["comp"] = max(0, min(50, int(item["comp"])))
                    c["tier"] = tier_of(int(c.get("fit", 0)) + int(c.get("comp", 0)))
                except Exception:
                    pass
                keep.append(c)
            candidates = keep

    # 把 AI 给出的下一步动作建议回写到候选（供前端展示 / 落库到 note）
    for c in candidates:
        c.setdefault("next_action", "")
        na = (c.get("next_action") or "").strip()
        if na:
            c["note"] = (c.get("note", "") or "").rstrip("；") + f"；AI建议：{na}"[:200]
            c["score_reason"] = (c.get("score_reason", "") or "").rstrip("；") + f"；AI建议：{na}"
        sig = (c.get("signal") or "").strip()
        if sig:
            c["note"] = (c.get("note", "") or "").rstrip("；") + f"；采购信号：{sig}"[:150]
        win = (c.get("window") or "").strip()
        if win:
            c["note"] = (c.get("note", "") or "").rstrip("；") + f"；最佳窗口：{win}"[:150]
        # 补充双维度等级信息，便于排序与前端展示
        c.setdefault("fit", 0)
        c.setdefault("comp", 0)
        c.setdefault("tier", tier_of(int(c.get("fit", 0)) + int(c.get("comp", 0))))
        c["score_reason"] = (
            (c.get("score_reason", "") or "").rstrip("；")
            + f"；{c['tier']}级（匹配{c.get('fit', 0)}+实力{c.get('comp', 0)}）"
        )

    # 招标平台共享邮箱/电话去重：同一联系方式只保留在最高分候选上
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

    tier_order = {"S": 0, "A": 1, "B": 2, "C": 3}
    candidates.sort(key=lambda c: (tier_order.get(c.get("tier", ""), 9), -int(c.get("score", 0))))
    dropped_low = 0
    keep = []
    for c in candidates:
        if c["score"] < 2:
            dropped_low += 1
            continue
        keep.append(c)
    return {"candidates": keep, "errors": errors, "filtered": filtered,
            "dropped_low": dropped_low, "timings": timings, "cache_hits": cache_hits[0],
            "channel_stats": channel_stats}
