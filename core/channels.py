# -*- coding: utf-8 -*-
"""多源获客渠道框架（Multi-Channel Search Sources）。

目标：在不改动原有单源搜索（`core.buyer.search_web`）的前提下，把“获客途径”抽象成
一组可配置、可扩展、可独立启停的「渠道（channel）」：

  - 搜索引擎：Bing / 360 / 搜狗 / SerpAPI / Google CSE / 博查
  - 社交媒体：LinkedIn / Facebook / Reddit / X
  - 行业网站：C114 / OFweek / Light Reading
  - 论坛社区：知乎 / CSDN / Quora
  - 招投标平台、工商企业库、地图 POI

每个渠道的配置集中在 ``data/channels_config.json``（见 _load_channel_config），包含：
  - provider        后台搜索方式（复用 core.buyer 的搜索函数 / 地图 POI）
  - site_scope      域名限定（site: 注入，实现“社交媒体/行业站/论坛”的渠道隔离）
  - query_template  关键词模板（{kw}/{intent}/{market}/{site} 占位符）
  - enabled_default / requires_key / rate_limit / freshness / access_params / keyword_config

接入/扩展方式：
  - 新增渠道：在 channels_config.json 的 channels 数组加一项即可，代码零改动；
  - 启用/停用：改 enabled_default，或在引擎 conditions.channels 指定类别，或用 CLI --channels；
  - 搜索关键词配置：改 query_template 与 intents。

对外主要函数：
  - load_channel_config(path=None)            加载并校验配置（缺文件用内置兜底）
  - list_channels(settings=None)              列出全部渠道 + 是否可达（密钥是否齐）
  - get_enabled_channel_ids(conditions, settings)  把类别/显式列表解析为具体渠道 id
  - build_channel_queries(channel, keywords, markets)  渲染某渠道的全部检索式
  - search_channel(channel, query, count, settings, use_cache)  单渠道单次搜索（带 site 限定）
  - run_channel_search(channel_ids, keywords, markets, settings, ...)  并行跨渠道搜索+聚合
  - normalize_merge(results)                  跨渠道按 URL 去重 + 渠道归因归并

设计要点（不破坏原功能）：
  - 本模块**惰性 import core.buyer**（buyer 顶层 import lxml，沙箱无 lxml 时不能导入期失败）；
  - 旧路径 buyer.run(channel_ids=None) 行为完全不变；仅当显式传入 channel_ids 才走多源逻辑；
  - search_channel 复用 buyer.search_web / search_web_cached 的整套 provider 选择 + 落盘缓存 + 降级链。
"""
import json
import os
import re
import threading

from core import concurrent_search

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "channels_config.json")
_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "channels_default.json")

# provider 取值 → 是否由 buyer 的“网页搜索后端”承接（需要走 search_web 的 provider 选择/缓存）
_WEB_PROVIDERS = ("bing", "so", "sogou", "baidu", "serpapi", "google_cse", "bocha")
# 类别别名：中文短标签 / 英文 key 都能识别（CLI --channels 与 conditions.channels 通用）
_CATEGORY_ALIASES = {
    "搜索引擎": "search_engine", "搜索": "search_engine", "search_engine": "search_engine",
    "社交媒体": "social_media", "社媒": "social_media", "social": "social_media", "social_media": "social_media",
    "行业网站": "industry_site", "行业站": "industry_site", "industry": "industry_site", "industry_site": "industry_site",
    "论坛": "forum", "forum": "forum",
    "招投标": "procurement", "招标": "procurement", "procurement": "procurement",
    "工商库": "company_db", "工商": "company_db", "company_db": "company_db",
    "地图": "map", "地图poi": "map", "map": "map",
    # 兼容旧版 conditions.channels 命名
    "web_search": "search_engine", "搜索引擎源": "search_engine",
    "exhibition": "exhibition", "展会": "exhibition", "展商": "exhibition",
}
# 国内免费源（无需密钥即可用，作为搜索引擎类别的可达候选）
_FREE_PROVIDERS = ("bing", "so", "sogou", "baidu")
# settings.search_provider 取值 → 渠道 id 映射（web_search 类别只选一个主引擎，避免重复）
_PROVIDER_TO_CHANNEL = {
    "bing_free": "bing", "so_free": "so360", "sogou": "sogou", "baidu_free": "baidu",
    "serpapi": "serpapi", "google_cse": "google_cse", "bocha": "bocha",
}

# 内置兜底配置：即使 channels_config.json 缺失也能跑（仅搜索引擎 4 个免费源）
_BUILTIN = {
    "categories": {
        "search_engine": "通用搜索引擎", "social_media": "社交媒体",
        "industry_site": "行业网站", "forum": "论坛", "procurement": "招投标",
        "company_db": "工商库", "map": "地图 POI", "exhibition": "行业展会",
    },
    "channels": [
        {"id": "bing", "name": "Bing 搜索", "category": "search_engine", "provider": "bing",
         "site_scope": "", "query_template": "{kw} {intent} {market}", "enabled_default": True,
         "requires_key": "", "rate_limit": 0.3, "freshness": ""},
        {"id": "so360", "name": "360 搜索", "category": "search_engine", "provider": "so",
         "site_scope": "", "query_template": "{kw} {intent} {market}", "enabled_default": True,
         "requires_key": "", "rate_limit": 0.3, "freshness": ""},
        {"id": "sogou", "name": "搜狗搜索", "category": "search_engine", "provider": "sogou",
         "site_scope": "", "query_template": "{kw} {intent} {market}", "enabled_default": True,
         "requires_key": "", "rate_limit": 0.3, "freshness": ""},
        {"id": "baidu", "name": "百度搜索", "category": "search_engine", "provider": "baidu",
         "site_scope": "", "query_template": "{kw} {intent} {market}", "enabled_default": True,
         "requires_key": "", "rate_limit": 0.3, "freshness": ""},
        {"id": "expo_directory", "name": "展商名录/企业列表", "category": "exhibition", "provider": "bing",
         "site_scope": "", "query_template": "{kw} {intent} {market}", "enabled_default": True,
         "requires_key": "", "rate_limit": 0.3, "freshness": "",
         "intents": ["展商 名录", "参展企业", "company list", "exhibitor list"]},
    ],
}

_cache = {"cfg": None}


# ----------------------------------------------------------------------------
# 1. 配置加载
# ----------------------------------------------------------------------------
def load_channel_config(path=None):
    """加载渠道配置；缺文件/解析失败时回退内置兜底配置。结果带默认字段补全。"""
    if _cache["cfg"] is not None and path is None:
        return _cache["cfg"]
    cfg = None
    # 优先用户配置 data/channels_config.json，其次仓库默认 core/channels_default.json，最后内置最小兜底
    for cand in (path or _CONFIG_PATH, _DEFAULT_CONFIG_PATH):
        try:
            with open(cand, encoding="utf-8") as f:
                cand_cfg = json.load(f)
            if cand_cfg and isinstance(cand_cfg.get("channels"), list) and cand_cfg["channels"]:
                cfg = cand_cfg
                break
        except Exception:
            cfg = None
    if not cfg or not isinstance(cfg.get("channels"), list) or not cfg["channels"]:
        cfg = json.loads(json.dumps(_BUILTIN))
    # 字段补全，保证下游不 KeyError
    for ch in cfg["channels"]:
        ch.setdefault("name", ch.get("id", "未知渠道"))
        ch.setdefault("category", "search_engine")
        ch.setdefault("provider", "bing")
        ch.setdefault("site_scope", "")
        ch.setdefault("query_template", "{kw} {intent} {market}")
        ch.setdefault("enabled_default", True)
        ch.setdefault("requires_key", "")
        ch.setdefault("rate_limit", 0.3)
        ch.setdefault("freshness", "")
        ch.setdefault("intents", [])
        ch.setdefault("access_params", {})
        ch.setdefault("keyword_config", "")
    cfg.setdefault("categories", _BUILTIN["categories"])
    if path is None:
        _cache["cfg"] = cfg
    return cfg


def get_channel(channel_id, path=None):
    for ch in load_channel_config(path)["channels"]:
        if ch["id"] == channel_id:
            return ch
    return None


def list_channels(settings=None, path=None):
    """返回所有渠道的元信息 + 是否可达（密钥是否齐）/ 是否启用。"""
    settings = settings or {}
    out = []
    for ch in load_channel_config(path)["channels"]:
        out.append({
            "id": ch["id"], "name": ch["name"], "category": ch["category"],
            "provider": ch["provider"], "site_scope": ch["site_scope"],
            "enabled": _channel_enabled(ch, settings),
            "reachable": _channel_reachable(ch, settings),
            "requires_key": ch["requires_key"],
            "rate_limit": ch["rate_limit"],
        })
    return out


# ----------------------------------------------------------------------------
# 2. 启用 / 可达判定
# ----------------------------------------------------------------------------
def _channel_reachable(ch, settings):
    """该渠道在当前 settings 下能否真正发起搜索（密钥是否齐、provider 是否支持）。"""
    prov = ch.get("provider")
    if prov == "map":
        return bool(settings.get("map_api_key"))
    if prov in ("serpapi", "google_cse", "bocha"):
        if not settings.get("search_api_key"):
            return False
        if prov == "google_cse" and not settings.get("search_engine_id"):
            return False
        return True
    if prov in _WEB_PROVIDERS:
        return True
    return False


def _channel_enabled(ch, settings):
    """配置开关 + 密钥就绪自动启用 + 显式 override。

    requires_key 的渠道（如 地图POI/SerpAPI/博查）只要密钥已配置就自动启用，
    避免“配了高德 Key 却一直没用上地图渠道”。
    settings.channel_overrides = {id: bool} 可强制启停。
    """
    requires_key = ch.get("requires_key") or ""
    has_key = bool(settings.get(requires_key)) if requires_key else True
    enabled = bool(ch.get("enabled_default", True)) or has_key
    ov = (settings or {}).get("channel_overrides") or {}
    if ch["id"] in ov:
        enabled = bool(ov[ch["id"]])
    return enabled


def _provider_channel_for_web_search(settings):
    """web_search 类别只选一个主引擎渠道（与原有 search_provider 行为一致，避免重复搜索）。"""
    prov = (settings or {}).get("search_provider", "bing_free")
    return _PROVIDER_TO_CHANNEL.get(prov, "bing")


# ----------------------------------------------------------------------------
# 3. 渠道解析：类别 / 显式列表 → 具体渠道 id
# ----------------------------------------------------------------------------
def get_enabled_channel_ids(conditions=None, settings=None, explicit=None):
    """把「类别集合」或「显式渠道 id」解析为本次要跑的具体渠道 id 列表。

    优先级：
      1) explicit（CLI --channels 或 settings.channel_ids）显式指定 → 直接用（按配置校验可达/启用）
      2) conditions.channels（类别名列表）展开为各目录下 enabled 且 reachable 的渠道
      3) 都不给 → 仅启用 web_search 主引擎（保持最小可用）
    """
    settings = settings or {}
    cfg = load_channel_config()
    by_cat = {}
    for ch in cfg["channels"]:
        by_cat.setdefault(ch["category"], []).append(ch)
    cat_by_display = {v: k for k, v in cfg["categories"].items()}  # 中文展示名 → 英文 key

    def _resolve_category(token):
        t = str(token).strip()
        if t in cfg["categories"]:
            return t
        if t in cat_by_display:
            return cat_by_display[t]
        low = t.lower()
        if low in _CATEGORY_ALIASES:
            return _CATEGORY_ALIASES[low]
        for disp, key in cat_by_display.items():  # 子串匹配展示名，如「社交媒体」命中 social_media 描述
            if t and t in disp:
                return key
        return None

    if explicit:
        ids = []
        for e in explicit:
            e = str(e).strip()
            if not e:
                continue
            cat = _resolve_category(e)
            if cat:
                ids += [c["id"] for c in by_cat.get(cat, [])]
            else:
                ids.append(e)  # 视为具体渠道 id
    elif conditions and conditions.get("channels"):
        cats = set()
        for c in conditions["channels"]:
            r = _resolve_category(c)
            if r:
                cats.add(r)
        ids = []
        main_engine = _provider_channel_for_web_search(settings)
        for cat, chs in by_cat.items():
            if cat not in cats:
                continue
            for ch in chs:
                # 搜索引擎类别只跑配置的主引擎（如 SerpAPI/博查/Bing），
                # 避免多引擎重复搜索、烧 API 配额、触发免费源限流；
                # 其它类别（招投标/行业站/地图等）保持全量。
                if cat == "search_engine" and ch["id"] != main_engine:
                    continue
                ids.append(ch["id"])
    else:
        ids = [_provider_channel_for_web_search(settings)]

    # 过滤：跳过不可达；显式列表视为用户主动启用（不受 enabled_default 开关约束）
    mode_explicit = bool(explicit)
    final = []
    for i in ids:
        ch = get_channel(i)
        if not ch:
            continue
        if not _channel_reachable(ch, settings):
            continue
        if not mode_explicit and not _channel_enabled(ch, settings):
            continue
        if i not in final:
            final.append(i)
    return final


# ----------------------------------------------------------------------------
# 4. 关键词配置：模板渲染 + 意图词
# ----------------------------------------------------------------------------
def _default_intents(market):
    """按市场给默认买方意图词（中文/英文），可被渠道 intents 覆盖。"""
    overseas = bool(re.search(r"[a-zA-Z]{2,}", market or ""))
    if overseas:
        return ["buyer", "purchase", "rfq", "tender", "distributor"]
    return ["采购", "询价", "招标", "求购", "项目"]


def build_channel_query(channel, kw, market, intent):
    """渲染单条检索式：占位符 {kw}/{intent}/{market}/{site}。"""
    site = ""
    if channel.get("site_scope"):
        doms = [d.strip() for d in str(channel["site_scope"]).split(",") if d.strip()]
        site = " ".join("site:" + d for d in doms)
    try:
        q = str(channel.get("query_template", "{kw} {intent} {market}")).format(
            kw=kw or "", intent=intent or "", market=market or "", site=site,
        )
    except Exception:
        q = f"{kw} {intent} {market} {site}"
    return re.sub(r"\s+", " ", q).strip()


def build_channel_queries(channel, keywords, markets, max_per_channel=12):
    """为某渠道生成全部检索式（关键词 × 市场 × 意图），按归一化去重并截断。"""
    intents = channel.get("intents") or None
    out, seen_norm = [], set()
    for kw in (keywords or [])[:6]:
        kw = str(kw).strip()
        if not kw:
            continue
        for market in (markets or [""])[:3]:
            mkt = str(market).strip()
            its = intents or _default_intents(mkt)[:4]
            for intent in its:
                q = build_channel_query(channel, kw, mkt, intent)
                if not q:
                    continue
                norm = " ".join(sorted(set(q.split())))
                if norm in seen_norm:
                    continue
                seen_norm.add(norm)
                out.append((q, kw, mkt))
                if len(out) >= max_per_channel:
                    return out
    return out


# ----------------------------------------------------------------------------
# 5. 单渠道搜索（复用 buyer 后端 + site 限定 + 缓存）
# ----------------------------------------------------------------------------
def search_channel(channel, query, count, settings, use_cache=True):
    """用某渠道的后端搜索一次；自动注入 site_scope / freshness。

    返回 [{title,url,snippet}]（已统一字段），失败时抛异常（由调用方降级）。
    """
    from core import buyer  # 惰性：避免导入期触发 lxml

    prov = channel.get("provider")
    eff = dict(settings or {})
    if prov == "map":
        return _search_map(channel, query, eff)
    # provider 名 → buyer.search_web 认识的搜索源（bing→bing_free、so→so_free）
    _PROV_TO_WEB = {"bing": "bing_free", "so": "so_free", "sogou": "sogou", "baidu": "baidu_free",
                    "serpapi": "serpapi", "google_cse": "google_cse", "bocha": "bocha",
                    "toutiao": "toutiao"}
    prov = _PROV_TO_WEB.get(prov, "bing_free" if prov not in _WEB_PROVIDERS else prov)
    # 合并 site 限定：渠道自带 + 全局 site_scope
    scopes = []
    if channel.get("site_scope"):
        scopes.append(str(channel["site_scope"]))
    if eff.get("search_site_filter"):
        scopes.append(str(eff["search_site_filter"]))
    eff["search_site_filter"] = ",".join(scopes)
    if channel.get("freshness"):
        eff["search_freshness"] = channel["freshness"]

    # 已配置商业搜索 API（SerpAPI/博查/Google CSE）时沿用全局 provider：
    # search_web 内部会自动“商业源 → 免费源 → 直连兜底”。
    # 否则 reddit/x/linkedin/facebook 等海外渠道被强制覆盖成免费源后必然搜不到。
    if not _commercial_provider(eff):
        eff["search_provider"] = prov
    if use_cache:
        return buyer.search_web_cached(query, count, eff)
    return buyer.search_web(query, count, eff)


def _commercial_provider(settings):
    """settings 中已配置且可用的商业搜索 API 名；无则返回空串。"""
    sp = settings.get("search_provider")
    key = settings.get("search_api_key") or ""
    if sp == "serpapi" and key:
        return "serpapi"
    if sp == "bocha" and key:
        return "bocha"
    if sp == "google_cse" and key and settings.get("search_engine_id"):
        return "google_cse"
    return ""


# 高德地图 API 全局节流：多查询/多城市同时并发会触发 QPS 限流导致整批失败
_MAP_LOCK = threading.Lock()
import time as _time


def _search_map(channel, query, settings):
    """地图 POI 渠道：委托 core.mapsearch 拉取厂商（返回统一字段）。"""
    try:
        from core import mapsearch
    except Exception:
        return []
    kw = query
    # 渠道 query 形如 “光缆 深圳”，取第一个词作关键词，末段作城市（尽力而为）
    parts = [p for p in str(query).split() if p]
    kw = parts[0] if parts else query
    city = parts[-1] if len(parts) > 1 else ""
    # 区域名 → 默认城市（高德需具体城市；海外区域用代表性城市，视 map_provider 而定）
    _REGION_CITY = {
        "中国大陆": "深圳", "亚太": "新加坡", "欧美": "洛杉矶",
        "中东非洲拉美": "迪拜", "广东": "广州", "浙江": "杭州",
        "江苏": "南京", "上海": "上海", "北京": "北京", "四川": "成都",
    }
    # 大区域自动展开为多个主要城市检索，大幅增加地图 POI 线索量（找全客户）
    _CITY_LIST = {
        "中国大陆": ["北京", "上海", "广州", "深圳", "武汉", "成都", "杭州", "南京"],
        "亚太": ["新加坡", "香港", "吉隆坡", "曼谷"],
        "欧美": ["洛杉矶", "纽约", "伦敦", "柏林"],
        "中东非洲拉美": ["迪拜", "开罗", "约翰内斯堡", "圣保罗"],
    }
    if city in _REGION_CITY:
        city = _REGION_CITY[city]
    if city in _CITY_LIST:
        cities = _CITY_LIST[city]
    else:
        cities = [city or "深圳"]
    out = []
    for city2 in cities:
        try:
            with _MAP_LOCK:
                leads = mapsearch.run_map_search(settings, kw, city2, pages=1, max_results=8)
                _time.sleep(0.2)
        except Exception:
            continue
        for ld in leads:
            out.append({
                "title": str(ld.get("name", "")),
                "url": str(ld.get("website") or ld.get("url") or ""),
                "snippet": (str(ld.get("address") or "") + " " + str(ld.get("note") or "")).strip()[:200],
            })
    return out


# ----------------------------------------------------------------------------
# 6. 跨渠道并行搜索 + 聚合
# ----------------------------------------------------------------------------
def run_channel_search(channel_ids, keywords, markets=None, settings=None,
                       progress=None, use_cache=True, max_per_channel=12):
    """对一批渠道并行搜索，聚合原始结果并标注来源。

    返回 (results, stats)：
      results —— [{title,url,snippet,keyword,market,channels:[id,...]}]（已跨渠道按 URL 去重归并）
      stats   —— {channel_id: {status, queries, count, error}}
    """
    settings = settings or {}
    cfg = load_channel_config()
    ch_by_id = {c["id"]: c for c in cfg["channels"]}
    channels = [ch_by_id[i] for i in channel_ids if i in ch_by_id]
    raw = []
    stats = {}

    workers = max(1, min(int(settings.get("max_search_workers", 8) or 8), 16))

    def _run_channel(ch):
        cid = ch["id"]
        if not _channel_reachable(ch, settings):
            return cid, {"status": "skipped", "reason": "缺少密钥或未启用", "queries": 0, "count": 0}, []
        queries = build_channel_queries(ch, keywords, markets, max_per_channel=max_per_channel)
        st = {"status": "run", "queries": len(queries), "count": 0, "category": ch["category"]}
        if not queries:
            return cid, st, []
        # rate_limit 作为 stagger 基准，随机化到 [0.5×, 1.5×] 区间，模拟人类不规则节奏
        try:
            rate_base = float(ch.get("rate_limit") or 0.3)
        except (TypeError, ValueError):
            rate_base = 0.3
        stagger = (rate_base * 0.5, rate_base * 1.5) if rate_base > 0 else 0

        def _worker(item, _ch=ch):
            q, kw, mkt = item
            try:
                res = search_channel(_ch, q, 6, settings, use_cache=use_cache)
                return ("ok", res, kw, mkt)
            except Exception as e:
                return ("err", str(e)[:120], q, mkt)

        res_list = concurrent_search.parallel_map(
            _worker, queries, max_workers=workers, stagger=stagger,
            desc=f"渠道搜索[{ch['name']}]", progress=progress,
        )
        ch_count = 0
        items = []
        for r in res_list:
            if r is None or isinstance(r, Exception):
                continue
            status, payload, kw, mkt = r
            if status == "err":
                st["error"] = payload
                continue
            for item in (payload or []):
                item = dict(item)
                item["keyword"] = kw
                item["market"] = mkt
                item.setdefault("channels", [])
                if cid not in item["channels"]:
                    item["channels"].append(cid)
                items.append(item)
                ch_count += 1
        st["count"] = ch_count
        return cid, st, items

    # 渠道之间并行（最多 4 个同时跑），避免单个慢渠道拖垮整体
    from concurrent.futures import ThreadPoolExecutor
    ch_results = []
    ch_workers = max(1, min(4, len(channels)))
    with ThreadPoolExecutor(max_workers=ch_workers) as ex:
        futures = [ex.submit(_run_channel, ch) for ch in channels]
        for fut in futures:
            ch_results.append(fut.result())
    for cid, st, items in ch_results:
        stats[cid] = st
        raw.extend(items)

    merged = normalize_merge(raw)
    return merged, stats


# ----------------------------------------------------------------------------
# 7. 跨渠道去重与归一化（核心：同 URL 不同渠道 → 归并，渠道归因合并）
# ----------------------------------------------------------------------------
def _norm_url(url):
    u = (url or "").strip()
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u.lower()


def normalize_merge(results):
    """按归一化 URL 跨渠道去重：同一条线索（同一网址）出现在多个渠道时，合并 channels 归因、
    保留最优标题/摘要，返回统一后的列表。"""
    best = {}
    for r in results:
        key = _norm_url(r.get("url"))
        if not key:
            # 无 URL 的线索（如地图 POI）用 name 兜底去重
            key = "n:" + str(r.get("title") or "").strip().lower()
        existing = best.get(key)
        if existing is None:
            merged = {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", ""),
                "keyword": r.get("keyword", ""),
                "market": r.get("market", ""),
                "channels": list(r.get("channels") or []),
            }
            best[key] = merged
            continue
        # 合并渠道归因（去重保序）
        for c in r.get("channels") or []:
            if c not in existing["channels"]:
                existing["channels"].append(c)
        # 选更长的摘要 / 更有信息的标题
        if len(str(r.get("snippet") or "")) > len(str(existing["snippet"] or "")):
            existing["snippet"] = r["snippet"]
        if not existing["title"] and r.get("title"):
            existing["title"] = r["title"]
    out = list(best.values())
    # channels 列表 → 稳定排序（按配置文件顺序）
    order = {c["id"]: i for i, c in enumerate(load_channel_config()["channels"])}
    for o in out:
        o["channels"].sort(key=lambda c: order.get(c, 999))
    return out
