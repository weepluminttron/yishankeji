# -*- coding: utf-8 -*-
"""线索评分：规则引擎快速评分 + 可选 AI 深度评分。"""
import json
import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")

# ---- WorkBuddy 式双维度评分：匹配度 fit（0-50）+ 实力 comp（0-50）----
TIER_RULES = [("S", 85), ("A", 75), ("B", 60), ("C", 0)]

_FIT_STRONG = [
    "玻璃管", "毛细管", "准直器", "z-block", "zblock", "滤光片", "隔离器", "环形器",
    "透镜", "套管", "镀膜", "陶瓷插芯", "ferrule", "玻璃", "毛管", "光引擎", "cpo",
]
_FIT_MID = [
    "dwdm", "cwdm", "fwdm", "oadm", "波分", "wdm", "光模块", "光器件", "无源",
    "mux", "demux", "光缆", "跳线", "尾纤", "配线", "odf", "收发器", "硅光", "光芯片",
]
_FIT_WEAK = ["光通信", "通信", "光学", "光电", "光纤"]
_SPEC_WORDS = [
    "通道", "芯数", "波长", "nm", "尺寸", "公差", "型号", "规格", "产能",
    "万件", "万个", "db", "1550", "1310", "850", "96芯", "48芯", "1.6t", "800g", "400g",
    "定制", "图纸",
]
_CUSTOM_WORDS = ["定制", "认证", "样品", "送样", "测试", "准入", "图纸", "验厂", "方案"]
_COMP_SCALE = [
    "营收", "净利", "资本开支", "注册资本", "亿元", "万元", "亿", "规模", "头部",
    "行业领先", "龙头", "上市", "市值", "预增", "增长", "业绩",
]
_COMP_LEVEL = [
    "运营商", "云厂", "英伟达", "nvidia", "lumentum", "coherent", "旭创", "光迅",
    "华为", "中兴", "tier-1", "tier1", "头部客户", "头部厂商", "上市公司", "证券", "董秘",
]
_COMP_ACTIVE = [
    "扩产", "新建", "基地", "招标", "中标", "环评", "定增", "投产", "在建",
    "项目", "订单", "公告", "产能扩张", "海外基地", "泰国", "同步", "新产线",
    "规划", "下半年",
]
_COMP_KEEP = ["长期", "框架", "复购", "持续", "年度", "批量", "多基地", "集采", "框架协议"]


def tier_of(total):
    """0-100 综合分 → S/A/B/C 等级。"""
    for name, minimum in TIER_RULES:
        if total >= minimum:
            return name
    return "C"


def fit_comp_score(lead):
    """WorkBuddy 式双维度评分：匹配度 + 实力，返回 {fit, comp, total, tier, reasons}。"""
    note = str(lead.get("note") or "").lower()
    tags = str(lead.get("tags") or "").lower()
    name = str(lead.get("name") or "").lower()
    text = note + " " + tags + " " + name

    fit = 4
    fit_reasons = []
    strong = sum(text.count(w) for w in _FIT_STRONG)
    if strong:
        fit += min(28, strong * 4)
        fit_reasons.append(f"品类直接命中×{strong}")
    mid = sum(text.count(w) for w in _FIT_MID)
    if mid:
        fit += min(12, mid * 2)
        fit_reasons.append(f"相关品类×{mid}")
    weak = sum(text.count(w) for w in _FIT_WEAK)
    if weak:
        fit += min(8, weak * 2)
        fit_reasons.append(f"行业相关×{weak}")
    spec = sum(text.count(w) for w in _SPEC_WORDS)
    if spec:
        fit += min(15, spec * 4)
        fit_reasons.append(f"规格/数量明确×{spec}")
    custom = sum(text.count(w) for w in _CUSTOM_WORDS)
    if custom:
        fit += min(15, custom * 4)
        fit_reasons.append(f"定制/认证/送样信号×{custom}")
    fit = min(50, fit)

    comp = 0
    comp_reasons = []
    scale = sum(text.count(w) for w in _COMP_SCALE)
    if scale:
        comp += min(15, scale * 5)
        comp_reasons.append(f"体量/财务信号×{scale}")
    level = sum(text.count(w) for w in _COMP_LEVEL)
    if level:
        comp += min(15, level * 4)
        comp_reasons.append(f"客户层级信号×{level}")
    active = sum(text.count(w) for w in _COMP_ACTIVE)
    if active:
        comp += min(15, active * 3)
        comp_reasons.append(f"扩产/项目活跃×{active}")
    keep = sum(text.count(w) for w in _COMP_KEEP)
    if keep:
        comp += min(10, keep * 4)
        comp_reasons.append(f"采购可持续×{keep}")
    comp = min(50, comp)

    total = fit + comp
    return {
        "fit": fit,
        "comp": comp,
        "total": total,
        "tier": tier_of(total),
        "reasons": {
            "fit": fit_reasons,
            "comp": comp_reasons,
        },
    }


def rule_score(lead):
    """规则评分（0-10 分），快速过滤低质量线索。"""
    score = 0
    reasons = []
    name = str(lead.get("name") or "")
    email = str(lead.get("email") or "")
    phone = str(lead.get("phone") or "")
    website = str(lead.get("website") or "")
    note = str(lead.get("note") or "")
    source = str(lead.get("source") or "")

    if name and len(name) >= 4:
        score += 2
        reasons.append("有公司/主体名称")
    if _EMAIL_RE.search(email):
        score += 3
        reasons.append("有邮箱")
    if _MOBILE_RE.search(phone) or _LANDLINE_RE.search(phone):
        score += 2
        reasons.append("有电话")
    if website and not any(
        b in website for b in ("alibaba.com", "made-in-china.com", "1688.com", "taobao.com",
                               "baidu.com", "zhihu.com", "xiaohongshu.com", "douyin.com",
                               "bilibili.com", "weibo.com", "jianshu.com", "sohu.com",
                               "163.com", "qq.com", "sina.com")
    ):
        score += 1
        reasons.append("有独立网站")
    if source in ("落地页表单", "买家发现", "社媒评论"):
        score += 1
        reasons.append(f"来源{source}")
    for kw in ("采购", "需要", "报价", "询价", "项目", "合作", "批发", "经销商", "工程", "订单", "buy", "purchase", "import"):
        if kw in note.lower():
            score += 1
            reasons.append("备注含需求关键词")
            break
    score = min(10, score)
    return score, "；".join(reasons) or "信息较少，建议补充后跟进"


def ai_score(settings, lead):
    """AI 深度评分（需要配置 API Key），返回 (score, reason) 或 (None, 错误信息)。"""
    from core import ai
    api_key = settings.get("openai_api_key", "")
    if not api_key:
        return None, "未配置 AI 密钥"
    industry = settings.get("industry", "") or "通用"
    company = settings.get("company_name", "") or "我方公司"
    products = settings.get("product_name", "") or "我们的产品"
    system = (
        f"你是{industry}行业的资深销售分析师，服务于{company}，擅长判断潜在客户的采购意向和成交可能性。"
        "结合我方主营产品，输出一行 JSON，格式："
        '{"score": 0到10的整数, "reason": "一句话结论", "points": ["2到3条具体判断依据"]}，'
        "points 必须基于线索真实信息（如采购意向、联系方式完整度、规模/需求信号、匹配度），禁止编造。"
        "不要输出任何其他内容。"
    )
    user = (
        f"我方主营产品：{products}\n"
        f"请评估以下客户线索的跟进价值，并给出具体依据：\n"
        f"公司：{lead.get('name','')}\n"
        f"联系人：{lead.get('contact','')}\n"
        f"电话：{lead.get('phone','')}\n"
        f"邮箱：{lead.get('email','')}\n"
        f"地区：{lead.get('region','')}\n"
        f"客户类型：{lead.get('type','')}\n"
        f"来源：{lead.get('source','')}\n"
        f"标签：{lead.get('tags','')}\n"
        f"备注：{lead.get('note','')}\n"
        f"地址：{lead.get('address','')}"
    )
    text, err = ai.generate_copy(
        api_key,
        settings.get("openai_model", "gpt-4o-mini"),
        system,
        user,
        settings.get("openai_api_base", ""),
    )
    if err:
        return None, err
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None, "AI 返回格式无法解析"
    try:
        data = json.loads(m.group(0))
        score = max(0, min(10, int(data.get("score", 0))))
        points = [str(p).strip() for p in (data.get("points") or []) if str(p).strip()]
        reason = "；".join(points) if points else str(data.get("reason", "")).strip()
        return score, reason
    except Exception as e:
        return None, f"AI 返回解析失败：{e}"
