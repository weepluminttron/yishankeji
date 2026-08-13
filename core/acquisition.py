# -*- coding: utf-8 -*-
"""AI 获客引擎：根据「用户条件」自动生成 → 多渠道发现 → 按条件筛选 → 迭代优化 获客方案，
并输出结构化策略文档与可执行的目标客户清单。

设计原则
--------
1. 条件驱动：用户只给「我们卖什么 / 想找什么样的客户 / 覆盖哪些市场 / 最低优先级 / 排除谁」，
   引擎据此自动产出搜索方案并反复打磨，无需改代码。
2. 复用本地基础设施：发现阶段惰性调用 core.buyer（联网搜索+抓取+抽取+AI 过滤，需 lxml/网络）；
   评分/分级/导出阶段只依赖 core.scorer（纯标准库），即使无 lxml 也能跑通排序与导出。
3. 宁缺毋滥：严格按用户条件过滤——命中规格、买方类型达标、等级≥阈值、未排除才保留；
   只有“泛泛行业相关、未命中任何规格”的线索会被剔除，确保清单是“真想要的客户”。
4. 迭代优化：每轮跑完后分析覆盖缺口（区域/买方类型/规格缺口），自动扩词、加邻近市场、
   补买方专属检索式，重新发现并去重合并，直到达到目标数量或达到最大轮次。
5. 可审计：每条客户都带 matched_conditions（命中的用户条件）、channel_source、priority，
   后续随时能倒推“为什么进了清单”。

本地运行（需先在 settings 配好 openai_api_key / search_provider 等）：
    from core import acquisition
    plan = acquisition.run_engine(conditions, settings)

无网络/无 lxml 的演示：把已收集的原始线索作为 seed 传入，仍可做排序、筛选与导出：
    targets = acquisition.build_targets(raw_candidates, conditions)
"""
import csv
import json
import os
import re
import datetime

from core import scorer  # 纯标准库，安全顶层导入


# ----------------------------------------------------------------------------
# 1. 用户条件（唯一输入）
# ----------------------------------------------------------------------------
def normalize_conditions(conditions):
    """把用户传入的条件规整为引擎内部统一结构，并做基本校验。

    字段说明：
      industry        行业（用于 AI 提示，如 “光纤通信”）
      products        我方主营产品（用于匹配度与 AI 判断）
      specs           必中规格/品类关键词（命中才保留，如 ["DWDM","WDM","玻璃管"]）
      keywords        额外检索种子词（可空，默认由 specs 衍生）
      regions         目标市场列表（如 ["中国大陆","亚太","欧美","中东非洲拉美"]）
      buyer_types     目标买方类型（如 ["光无源器件厂","光模块厂","系统集成商","近期招标扩容"]）
      min_tier        最低保留等级（S/A/B/C，默认 "B"）
      exclude         排除公司名/域名片段（如竞争对手、自家）
      channels        启用的发现渠道（默认全部：["web_search","company_db","exhibition","procurement"]）
      max_results     目标客户数量（达到即停止迭代，默认 30）
      allow_broad     是否保留“泛行业相关但未命中规格”的线索（默认 False，确保精准）
      search_provider / openai_api_key / ...  透传给 core.buyer / core.ai 的 settings
    """
    c = dict(conditions or {})
    c.setdefault("industry", "光纤通信")
    c.setdefault("products", "")
    c.setdefault("specs", [])
    c.setdefault("keywords", [])
    c.setdefault("regions", ["中国大陆", "亚太", "欧美", "中东非洲拉美"])
    c.setdefault("buyer_types", ["光无源器件厂", "光模块厂", "系统集成商", "近期招标扩容"])
    c.setdefault("min_tier", "B")
    c.setdefault("exclude", [])
    c.setdefault("channels", ["web_search", "company_db", "exhibition", "procurement"])
    c.setdefault("max_results", 30)
    c.setdefault("allow_broad", False)

    def as_list(v):
        if isinstance(v, str):
            return [x.strip() for x in re.split(r"[\n,，;；]", v) if x.strip()]
        return list(v or [])

    for k in ("specs", "keywords", "regions", "buyer_types", "exclude", "channels"):
        c[k] = as_list(c.get(k))

    # specs 同时作为检索种子；keywords 补充额外词
    c["specs"] = [s.strip() for s in c["specs"] if s.strip()]
    if not c["specs"] and not c["keywords"]:
        raise ValueError("至少需要提供一个 specs（必中规格）或 keywords（检索种子）")
    c["min_tier"] = str(c["min_tier"]).upper()
    if c["min_tier"] not in ("S", "A", "B", "C"):
        c["min_tier"] = "B"
    try:
        c["max_results"] = max(1, int(c.get("max_results") or 30))
    except Exception:
        c["max_results"] = 30
    return c


# ----------------------------------------------------------------------------
# 2. 方案自动生成
# ----------------------------------------------------------------------------
# 买方类型 → 专属检索后缀（从“买方/采购”侧切入，避开同行供应商噪音）
_BUYER_SUFFIX = {
    "光无源器件厂": ["采购", "询价", "规格书"],
    "光模块厂": ["采购", "物料", "供应商征集"],
    "系统集成商": ["招标", "采购公告", "项目"],
    "近期招标扩容": ["招标公告", "中标", "扩容", "集采"],
}


def _expand_keywords(kw):
    """规格词同义词扩展；优先复用 buyer，缺失（无 lxml）时退回内置映射。"""
    try:
        from core import buyer
        return [kw] + list(buyer.expand_keywords(kw))
    except Exception:
        pass
    SYN = {
        "玻璃管": ["石英玻璃管", "玻璃毛细管", "fiber capillary"],
        "dwdm": ["密集波分", "波分复用", "wdm"],
        "wdm": ["波分复用", "dwdm", "cwdm"],
    }
    return [kw] + SYN.get(kw.lower(), [])


def _buyer_queries(kw, market):
    """买方意图检索式；优先复用 buyer.build_queries，缺失时退回内置模板。"""
    try:
        from core import buyer
        return list(buyer.build_queries(kw, market))
    except Exception:
        pass
    overseas = bool(re.search(r"[a-zA-Z]{2,}", market or ""))
    variants = (["采购", "询价"], ["招标", "公告"], ["求购", "信息"], ["需要", "报价"]) if not overseas \
        else (["buyer", "purchase"], ["rfq", "tender"], ["distributor", "import"])
    return [f"{kw} {v} {market}".strip() for v in variants]


def generate_plan(conditions):
    """由用户条件派生「多渠道、多检索式」的获客方案（不强制依赖 lxml）。"""
    specs = conditions["specs"]
    keywords = list(conditions["keywords"])
    for s in specs:  # 规格词直接作为检索种子
        if s not in keywords:
            keywords.append(s)

    expanded = []
    for kw in keywords:
        for e in _expand_keywords(kw):
            if e not in expanded:
                expanded.append(e)
    expanded = expanded[:16]

    markets = conditions["regions"]
    queries_by_channel = {"web_search": [], "company_db": [], "exhibition": [], "procurement": []}
    for kw in expanded:
        for m in (markets or [""]):
            for q in _buyer_queries(kw, m):
                queries_by_channel["web_search"].append(q)
        queries_by_channel["company_db"].append(f"{kw} 公司 官网")
        if "exhibition" in conditions["channels"]:
            queries_by_channel["exhibition"].append(f"CIOE 展商 {kw}")
        if "procurement" in conditions["channels"]:
            queries_by_channel["procurement"].append(f"{kw} 招标 采购公告")

    # 买方类型专属查询（提升“买方侧”命中、减少同行供应商）
    for bt in conditions["buyer_types"]:
        for suf in _BUYER_SUFFIX.get(bt, ["采购"]):
            for kw in expanded[:6]:
                for m in (markets or [""]):
                    q = f"{kw} {suf} {m}".strip()
                    queries_by_channel["web_search"].append(q)
                    queries_by_channel["procurement"].append(q)

    # 去重并截断（控制每轮成本）
    for ch in queries_by_channel:
        seen = set()
        uniq = []
        for q in queries_by_channel[ch]:
            if q not in seen:
                seen.add(q)
                uniq.append(q)
        queries_by_channel[ch] = uniq[:40]

    return {
        "seed_keywords": expanded,
        "markets": markets,
        "buyer_types": conditions["buyer_types"],
        "channels": conditions["channels"],
        "queries_by_channel": queries_by_channel,
        "query_total": sum(len(v) for v in queries_by_channel.values()),
        "generated_at": datetime.date.today().strftime("%Y-%m-%d"),
    }


# ----------------------------------------------------------------------------
# 3. 发现：多渠道检索 + 抓取 + 抽取（惰性调用 core.buyer）
# ----------------------------------------------------------------------------
def _discover_one_round(conditions, settings, progress=None):
    """调用 core.buyer.run 完成一轮“搜索→去噪→抓取→抽取→评分→AI过滤”。"""
    from core import buyer

    keywords = conditions["specs"] + conditions["keywords"]
    markets = conditions["regions"]
    use_ai = bool(settings and settings.get("openai_api_key"))
    context = (
        f"我方主营：{conditions.get('products','')}。理想客户："
        f"{'、'.join(conditions.get('buyer_types', []))}。"
        f"必须命中品类：{'、'.join(conditions.get('specs', []))}。"
        "请只保留有真实采购意向的买方，剔除同行供应商与平台噪音。"
    )
    res = buyer.run(
        keywords=keywords,
        markets=markets,
        max_results=8,
        use_ai=use_ai,
        settings=settings,
        progress=progress,
        context=context,
    )
    return res.get("candidates", []), res.get("errors", [])


def _discover_manual(conditions, settings, progress=None):
    """手动搜索（高级）：调用 core.mapsearch 按关键词+城市拉取 POI 线索。

    无地图 Key 或导入失败时返回空列表（不阻断主流程）。返回与 engine 候选同形的列表。
    """
    try:
        from core import mapsearch
    except Exception:
        return []
    if not settings:
        return []
    provider = settings.get("map_provider", "amap")
    key = settings.get("map_api_key") if provider == "amap" else settings.get("search_api_key")
    if not key:
        return []

    # 区域名 → 真实城市（手动搜索需要具体城市）
    CITY_MAP = {
        "中国大陆": ["深圳", "武汉", "苏州", "成都", "上海"],
        "亚太": ["新加坡", "吉隆坡", "雅加达"],
        "欧美": ["Frankfurt", "San Jose"],
        "中东非洲拉美": ["迪拜", "圣保罗"],
    }
    cities = []
    for r in conditions["regions"]:
        cities += CITY_MAP.get(r, [])
    if not cities:
        cities = ["深圳"]

    seeds = list(conditions["specs"]) + list(conditions["keywords"])
    out = []
    seen = set()
    for kw in seeds[:6]:
        for city in cities[:4]:
            try:
                leads = mapsearch.run_map_search(settings, kw, city, pages=1, max_results=20)
                for ld in leads:
                    key_ = (str(ld.get("name", "")).strip().lower(), str(ld.get("phone", "")))
                    if key_ in seen:
                        continue
                    seen.add(key_)
                    out.append(ld)
            except Exception as e:
                if progress:
                    progress({"stage": f"手动搜索跳过({city}/{kw})", "note": str(e)[:80]})
    if progress:
        progress({"stage": f"手动搜索完成", "manual": len(out)})
    return out


# ----------------------------------------------------------------------------
# 4. 按用户条件筛选 / 分级 / 标注匹配条件
# ----------------------------------------------------------------------------
_BUYER_TYPE_RULES = [
    # 买方动作词优先（运营商/集成商在招标、集采、扩容）→ 近期招标扩容，优先级高于品类词，
    # 避免“中国电信 DWDM 扩容集采”被同笔记里的玻璃管/滤光片误判为器件厂。
    ("近期招标扩容", ["招标", "中标", "采购公告", "扩容", "集采", "tender", "procurement", "rfp", "rfq", "询价公告"]),
    ("系统集成商", ["系统集成", "集成商", "总包", "工程公司", "施工", "布线", "contractor", "integrator", "installer"]),
    ("光模块厂", ["光模块", "optical module", "transceiver", "硅光", "cpo", "800g", "1.6t", "400g"]),
    ("光无源器件厂", ["无源", "器件", "准直器", "隔离器", "环形器", "滤光片", "玻璃管", "毛细管", "ferrule",
                   "z-block", "zblock", "透镜", "套管", "插芯", "passive", "pigtail", "fiber"]),
]


def classify_buyer_type(text):
    t = (text or "").lower()
    for bt, words in _BUYER_TYPE_RULES:
        if any(w in t for w in words):
            return bt
    return "其他潜在买方"


_REGION_MAP = [
    ("中国大陆", ["中国", "大陆", "广东", "深圳", "江苏", "浙江", "武汉", "上海", "成都", "北京"]),
    ("亚太", ["印度", "印尼", "越南", "泰国", "马来西亚", "日本", "韩国", "台湾", "新加坡", "亚太", "asia", "india", "indonesia"]),
    ("欧美", ["美国", "德国", "英国", "法国", "荷兰", "欧洲", "usa", "us ", "germany", "europe", "america", "lumentum", "coherent"]),
    ("中东非洲拉美", ["中东", "非洲", "拉美", "沙特", "阿联酋", "巴西", "尼日利亚", "middle east", "uae", "saudi", "brazil", "africa"]),
]


def classify_region(text, fallback="其他"):
    t = (text or "").lower()
    for r, words in _REGION_MAP:
        if any(w in t for w in words):
            return r
    return fallback


def _match_specs(text, specs):
    t = (text or "").lower()
    hit = [s for s in specs if s.lower() in t]
    return hit


def build_targets(candidates, conditions, seq_start=1):
    """把原始线索（core.buyer 产出或外部 seed）按用户条件筛选、分级、标注。

    返回 (targets, dropped)：
      targets  —— 通过筛选的目标客户（可执行清单）
      dropped  —— 被剔除的线索及原因（便于审计/迭代）
    """
    specs = conditions["specs"]
    min_tier = conditions["min_tier"]
    exclude = [e.lower() for e in conditions.get("exclude", [])]
    allow_broad = conditions.get("allow_broad", False)
    tier_rank = {"S": 0, "A": 1, "B": 2, "C": 3}
    min_rank = tier_rank.get(min_tier, 2)

    targets, dropped = [], []
    for i, c in enumerate(candidates):
        name = str(c.get("name") or "").strip()
        note = str(c.get("note") or "")
        tags = str(c.get("tags") or "")
        website = str(c.get("website") or "")
        text = " ".join([name, tags, note, c.get("type", ""), c.get("region", ""), c.get("source", "")])

        # 1) 排除名单（公司名/域名/片段）
        if any(e and (e in name.lower() or e in website.lower()) for e in exclude):
            dropped.append((name or "未命名", "命中排除名单"))
            continue

        # 2) 评分/分级（以 note+name+tags 为文本，统一口径）
        fc = scorer.fit_comp_score({"note": note, "tags": tags, "name": name})
        fit, comp, total, tier = fc["fit"], fc["comp"], fc["total"], fc["tier"]

        # 3) 命中规格（用户真正想要的品类）
        matched = _match_specs(text, specs)

        # 4) 买方类型 & 区域归类
        buyer_type = classify_buyer_type(text)
        region = classify_region(text + " " + c.get("region", ""), fallback=classify_region(text))

        # 5) 过滤：等级、规格命中
        reasons = []
        if tier_rank.get(tier, 9) > min_rank:
            dropped.append((name or "未命名", f"等级低于阈值({tier}<{min_tier})"))
            continue
        if not matched:
            if allow_broad:
                reasons.append("泛行业相关（未命中具体规格）")
            else:
                dropped.append((name or "未命名", "未命中任何必中规格（泛泛线索）"))
                continue

        # 6) 组装目标客户（可执行清单字段）
        contact_name, contact_role = _split_contact(c.get("contact", ""))
        target = {
            "id": "ACQ%03d" % (seq_start + len(targets)),
            "company": name or "未命名",
            "company_en": str(c.get("name_en") or _guess_en(name)),
            "contact_name": contact_name,
            "contact_role": contact_role or "采购/决策人(待核验)",
            "phone": str(c.get("phone") or ""),
            "email": str(c.get("email") or ""),
            "website": website,
            "region": region,
            "buyer_type": buyer_type,
            "matched_conditions": matched,
            "channel_source": str(c.get("source") or "买家发现"),
            "priority": tier,
            "fit": fit,
            "comp": comp,
            "total": total,
            "next_action": str(c.get("next_action")
                               or _default_next_action(tier, buyer_type, bool(c.get("email")))),
            "note": note[:300],
            "verified": bool(c.get("verified", False)) or _looks_verified(c),
            "path": str(c.get("path") or _default_path(website, buyer_type)),
            # 策略助手（不限行业）衍生的意向信号，失败自动留空
            "intent_stage": "",
            "intent_urgency": "",
            "intent_next_action": "",
            "profile": "",
        }
        _enrich_intent(target)
        targets.append(target)

    # 优先级排序：等级 → 综合分 → 买方类型权重
    bt_weight = {bt: i for i, bt in enumerate(conditions.get("buyer_types", []))}
    targets.sort(key=lambda t: (tier_rank.get(t["priority"], 9),
                                -t["total"],
                                bt_weight.get(t["buyer_type"], 99)))
    for idx, t in enumerate(targets, 1):
        t["id"] = "ACQ%03d" % idx
    return targets, dropped


def _split_contact(contact):
    if not contact:
        return "", ""
    # 形如 “王经理|采购部” 或 “采购经理 李工”
    parts = re.split(r"[|｜,，:：\s]+", str(contact).strip(), maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return contact, ""


def _guess_en(name):
    # 简单占位：中文名无法自动翻英文，留空由人工补
    return ""


def _looks_verified(c):
    email = str(c.get("email") or "")
    phone = str(c.get("phone") or "")
    web = str(c.get("website") or "")
    # 企业邮箱 + 非 webmail + 独立网站 + 电话 → 视为可核验
    webmail = ("qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "hotmail.com",
               "foxmail.com", "sina.com", "139.com", "aliyun.com", "icloud.com", "yahoo.com")
    has_corp_email = email and not any(email.endswith("@" + w) for w in webmail)
    return bool(has_corp_email and phone and web)


def _default_next_action(tier, buyer_type, has_email):
    if buyer_type == "近期招标扩容":
        return "查招标文件，确认品类与参数，发对应方案/报价"
    if not has_email:
        return "先核验官网/年报公开联系人，再触达"
    if tier in ("S", "A"):
        return "发定制方案+样品报价单，约展前/线上述职"
    return "发产品资料+案例，培育并跟进"


def _default_path(website, buyer_type):
    if buyer_type == "近期招标扩容":
        return "政府/企业 e-procurement 平台公开公告；招标文件合规获取"
    if website:
        return f"公司官网公开信息栏 / 年报；{website}"
    return "公司官网公开信息栏 / 年报 / 行业展商名录（合法公开渠道）"


def _enrich_intent(target):
    """策略助手（不限行业）：用 core.intent 给目标客户打购买意向标签。

    纯规则、零成本、永远可用；导入或评分失败则留空，不影响主流程。
    """
    try:
        from core import intent
    except Exception:
        return
    lead = {
        "name": target["company"],
        "note": target["note"],
        "tags": target["buyer_type"],
        "type": target["buyer_type"],
        "region": target["region"],
        "source": target["channel_source"],
    }
    try:
        it = intent.classify(lead, use_ai=False)
        target["intent_stage"] = it.get("stage", "")
        target["intent_urgency"] = it.get("urgency", "")
        target["intent_next_action"] = it.get("next_action", "")
        target["profile"] = intent.enrich_profile(lead, it)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 5. 迭代优化：缺口分析 + 自动扩词重跑
# ----------------------------------------------------------------------------
def analyze_gaps(targets, conditions):
    """分析当前清单覆盖缺口，返回需要补强的方向。"""
    regions = conditions["regions"]
    buyer_types = conditions["buyer_types"]
    specs = conditions["specs"]
    by_region = {}
    by_type = {}
    by_spec = {}
    for t in targets:
        by_region[t["region"]] = by_region.get(t["region"], 0) + 1
        by_type[t["buyer_type"]] = by_type.get(t["buyer_type"], 0) + 1
        for s in t["matched_conditions"]:
            by_spec[s] = by_spec.get(s, 0) + 1
    gaps = []
    for r in regions:
        if by_region.get(r, 0) == 0:
            gaps.append(("region", r))
    for bt in buyer_types:
        if by_type.get(bt, 0) == 0:
            gaps.append(("buyer_type", bt))
    for s in specs:
        if by_spec.get(s, 0) == 0:
            gaps.append(("spec", s))
    return gaps


def _expand_for_gaps(conditions, gaps, round_no):
    """根据缺口自动产生补充检索词（不修改原始条件，仅本轮增强）。"""
    add_kw = []
    add_regions = list(conditions["regions"])
    for kind, val in gaps:
        if kind == "spec":
            add_kw.append(val)
            # 规格的近义/组合
            if val.lower() in ("dwdm", "wdm"):
                add_kw += ["波分复用", "OADM", "光传输", "DCI"]
            if "玻璃管" in val or "glass" in val.lower():
                add_kw += ["石英毛细管", "玻璃毛细管", "fiber capillary"]
        elif kind == "buyer_type":
            if "招标" in val or "扩容" in val:
                add_kw += ["中标公告", "集采", "扩容项目"]
            elif "模块" in val:
                add_kw += ["800G 光模块", "1.6T 光模块"]
            elif "无源" in val:
                add_kw += ["无源器件", "准直器", "隔离器"]
            elif "集成" in val:
                add_kw += ["系统集成", "总包", "FTTH 工程"]
        elif kind == "region":
            # 邻近市场补充
            if val == "欧美":
                add_regions += ["德国", "荷兰"]
            elif val == "亚太":
                add_regions += ["印度", "印尼"]
    # 去重
    seen = set()
    out = []
    for k in add_kw:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:10], add_regions


def run_engine(conditions, settings=None, max_rounds=3, progress=None, seed_candidates=None, use_manual=False):
    """编排：发现 → 筛选 → 迭代补强 → 导出就绪。

    返回 dict: {targets, dropped, plan, gaps_history, rounds, stats}
    use_manual=True 时，会额外调用 core.mapsearch（手动搜索/高级）补充 POI 线索。
    """
    conditions = normalize_conditions(conditions)
    plan = generate_plan(conditions)
    if progress:
        progress({"stage": "方案已生成", "query_total": plan["query_total"]})

    all_candidates = []
    if seed_candidates:
        all_candidates = list(seed_candidates)

    rounds = 0
    gaps_history = []
    if not seed_candidates:
        # 手动搜索（高级）一次性补充，避免每轮重复调用地图接口
        if use_manual:
            all_candidates = _discover_manual(conditions, settings, progress) + all_candidates
        for rnd in range(1, max_rounds + 1):
            rounds = rnd
            cands, errors = _discover_one_round(conditions, settings, progress)
            all_candidates.extend(cands)
            targets, _ = build_targets(_dedupe(all_candidates), conditions)
            gaps = analyze_gaps(targets, conditions)
            gaps_history.append({"round": rnd, "targets": len(targets), "gaps": [list(g) for g in gaps]})
            if progress:
                progress({"stage": f"第{rnd}轮完成", "targets": len(targets), "gaps": len(gaps)})
            if len(targets) >= conditions["max_results"]:
                break
            if not gaps:
                break
            # 根据缺口扩词，注入下一轮条件
            add_kw, add_regions = _expand_for_gaps(conditions, gaps, rnd)
            conditions["keywords"] = list(conditions["keywords"]) + add_kw
            conditions["regions"] = add_regions
    else:
        # 仅排序/筛选（无网络演示路径）
        rounds = 1

    targets, dropped = build_targets(_dedupe(all_candidates), conditions)
    final_gaps = analyze_gaps(targets, conditions)
    stats = _stats(targets, conditions)
    return {
        "targets": targets,
        "dropped": dropped,
        "plan": plan,
        "gaps_history": gaps_history,
        "final_gaps": [list(g) for g in final_gaps],
        "rounds": rounds,
        "stats": stats,
    }


def _dedupe(candidates):
    seen = set()
    out = []
    for c in candidates:
        key = (str(c.get("website") or "").lower(),
               str(c.get("email") or "").lower(),
               str(c.get("name") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _stats(targets, conditions):
    from collections import Counter
    by_tier = Counter(t["priority"] for t in targets)
    by_region = Counter(t["region"] for t in targets)
    by_type = Counter(t["buyer_type"] for t in targets)
    by_spec = Counter()
    for t in targets:
        for s in t["matched_conditions"]:
            by_spec[s] += 1
    verified = sum(1 for t in targets if t["verified"])
    return {
        "total": len(targets),
        "by_tier": dict(by_tier),
        "by_region": dict(by_region),
        "by_type": dict(by_type),
        "by_spec": dict(by_spec),
        "verified": verified,
        "target_count": conditions["max_results"],
    }


# ----------------------------------------------------------------------------
# 6. 输出：策略文档 + 目标清单（JSON/CSV）
# ----------------------------------------------------------------------------
def build_strategy_doc(conditions, engine_result, out_dir):
    """生成结构化的「获客策略文档」markdown。"""
    stats = engine_result["stats"]
    plan = engine_result["plan"]
    targets = engine_result["targets"]
    today = datetime.date.today().strftime("%Y-%m-%d")

    lines = []
    lines.append(f"# 获客策略文档 · {conditions.get('industry','')}行业")
    lines.append("")
    lines.append(f"> 生成日期：{today} ｜ 迭代轮次：{engine_result['rounds']} ｜ "
                 f"目标客户数：{stats['total']}（目标 ≥ {stats['target_count']}）")
    lines.append("")
    lines.append("## 一、目标客户画像（我们想找谁）")
    lines.append("")
    lines.append(f"- **我方主营**：{conditions.get('products','（未填）')}")
    lines.append(f"- **必中规格/品类**：{', '.join(conditions['specs']) or '（无）'}")
    lines.append(f"- **目标买方类型**：{', '.join(conditions['buyer_types'])}")
    lines.append(f"- **覆盖市场**：{', '.join(conditions['regions'])}")
    lines.append(f"- **最低优先级**：{conditions['min_tier']} 级及以上")
    if conditions.get("exclude"):
        lines.append(f"- **排除**：{', '.join(conditions['exclude'])}")
    lines.append("")
    lines.append("## 二、多渠道发现策略")
    lines.append("")
    lines.append(f"- **检索方案规模**：共派生 {plan['query_total']} 条检索式，覆盖 "
                 f"{', '.join(plan['channels'])} 等渠道。")
    lines.append(f"- **搜索种子词**：{', '.join(plan['seed_keywords'][:12])}")
    lines.append("- **买方侧切入**：检索式统一带「采购/招标/询价/RFQ」等买方意图词，"
                 "自动剔除同行供应商与平台黄页噪音，确保拿到的是真买家。")
    if plan["queries_by_channel"].get("procurement"):
        lines.append(f"- **招投标渠道**：示例检索式 `{plan['queries_by_channel']['procurement'][0]}`")
    if plan["queries_by_channel"].get("exhibition"):
        lines.append(f"- **展会渠道**：示例检索式 `{plan['queries_by_channel']['exhibition'][0]}`")
    lines.append("")
    lines.append("## 三、评分与优先级模型")
    lines.append("")
    lines.append("- **综合分 = 匹配度 fit(0-50) + 实力 comp(0-50)**，由 `core.scorer` 统一计算。")
    lines.append("- **等级阈值**：S≥85 / A≥75 / B≥60 / C≥0（与系统全局一致，可倒推审计）。")
    lines.append("- **匹配条件标注**：每条客户都标出命中的必中规格（matched_conditions），"
                 "未命中任何规格的泛泛线索已被自动剔除。")
    lines.append("")
    lines.append("## 四、覆盖与缺口分析")
    lines.append("")
    def _fmt(d):
        return "、".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])) or "（无）"
    lines.append(f"- **区域分布**：{_fmt(stats['by_region'])}")
    lines.append(f"- **买方类型分布**：{_fmt(stats['by_type'])}")
    lines.append(f"- **规格命中分布**：{_fmt(stats['by_spec'])}")
    if engine_result["final_gaps"]:
        lines.append("- **剩余缺口**：" + "；".join(f"{k}:{v}" for k, v in engine_result["final_gaps"]))
    else:
        lines.append("- **剩余缺口**：无（目标市场/类型/规格均已覆盖）。")
    lines.append("")
    lines.append("## 五、目标客户清单（按优先级，前 30 条）")
    lines.append("")
    lines.append("| 优先级 | 公司 | 区域 | 买方类型 | 命中规格 | 渠道来源 | 联系人/下一步 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for t in targets[:30]:
        ca = f"{t['contact_name']}（{t['contact_role']}）" if t["contact_name"] else t["contact_role"]
        na = t["next_action"]
        lines.append(f"| {t['priority']} | {t['company']} | {t['region']} | {t['buyer_type']} | "
                     f"{','.join(t['matched_conditions'])} | {t['channel_source']} | {ca}：{na} |")
    lines.append("")
    lines.append("## 六、执行建议")
    lines.append("")
    lines.append("1. **S/A 级优先**：先触达综合分最高、联系方式已核验（verified=true）的客户。")
    lines.append("2. **展前窗口**：若清单含展会渠道来源，赶在展前完成首轮触达，现场约见效率最高。")
    lines.append("3. **待核验客户**：verified=false 者按 path 字段的合法公开渠道补全联系方式后再触达，"
                 "禁止编造。")
    lines.append("4. **月度刷新**：更新 conditions 与已发现线索，重跑 `run_engine` 即可迭代更新本清单。")
    lines.append("")
    lines.append("> 说明：本清单由 `core.acquisition` 依用户条件自动生成与迭代优化；"
                 "联系方式来自公开渠道，部分待核验，请按 path 字段合规补全。")
    return "\n".join(lines)


def export_outputs(conditions, engine_result, out_dir, base_name="acquisition"):
    """写出 strategy.md / targets.json / targets.csv，返回各文件路径。"""
    os.makedirs(out_dir, exist_ok=True)
    conditions = normalize_conditions(conditions)
    targets = engine_result["targets"]

    md = build_strategy_doc(conditions, engine_result, out_dir)
    md_path = os.path.join(out_dir, f"{base_name}_strategy.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    json_path = os.path.join(out_dir, f"{base_name}_targets.json")
    payload = {
        "meta": {
            "industry": conditions["industry"],
            "products": conditions["products"],
            "specs": conditions["specs"],
            "regions": conditions["regions"],
            "buyer_types": conditions["buyer_types"],
            "min_tier": conditions["min_tier"],
            "generated": datetime.date.today().strftime("%Y-%m-%d"),
            "rounds": engine_result["rounds"],
            "target_count": conditions["max_results"],
            "stats": engine_result["stats"],
        },
        "targets": targets,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    csv_path = os.path.join(out_dir, f"{base_name}_targets.csv")
    fields = ["id", "company", "company_en", "contact_name", "contact_role", "phone", "email",
              "website", "region", "buyer_type", "matched_conditions", "channel_source",
              "priority", "fit", "comp", "total", "next_action", "verified", "path", "note",
              "intent_stage", "intent_urgency", "intent_next_action", "profile"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in targets:
            row = dict(t)
            row["matched_conditions"] = "|".join(t.get("matched_conditions", []))
            w.writerow(row)

    return {"strategy_md": md_path, "targets_json": json_path, "targets_csv": csv_path}
