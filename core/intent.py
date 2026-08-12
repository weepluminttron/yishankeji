# -*- coding: utf-8 -*-
"""意向识别与用户画像：把线索原始信息转成可行动的「购买意向」信号。

参考主流获客 AI 的「用户画像分析 / 意向识别」能力，提供两层实现：
- rule_intent：纯规则，零成本、永远可用，作为默认与 AI 失败时的降级；
- ai_intent：调用 OpenAI 兼容接口做结构化意图分类，失败自动回退规则。

设计原则（与项目一致）：
- 纯函数，输入 lead dict + settings，不抛异常、不阻塞主流程；
- 不泄露我方密钥；不编造信息；
- 输出结构化 dict，方便写入 DB、评分加权、前端展示与自动触达决策。
"""
import json
import re

# 购买意向阶段（由弱到强）
STAGES = ["未明确", "初步了解", "明确需求", "对比选型", "决策采购"]
STAGE_WEIGHT = {s: i for i, s in enumerate(STAGES)}  # 0..4

# 各阶段特征词
_STAGE_KEYWORDS = {
    "决策采购": ("下单", "采购中", "已定", "确定合作", "合同", "招标中", "中标", "签约", "付款", "订货", "马上要", "急需", "立即采购"),
    "对比选型": ("对比", "比价", "选型", "几家", "样品测试", "测试通过", "招投标", "方案比", "考察", "验厂", "报价单对比"),
    "明确需求": ("询价", "报价", "咨询", "了解", "需要", "求购", "采购", "买", "订购", "合作", "找", "供应", "inquiry", "purchase", "buy", "rfq"),
    "初步了解": ("看看", "关注", "资料", "官网", "简介", "介绍", "什么", "怎么", "是否", "可以吗", "了解下", "打听"),
}
# 紧迫度特征词
_URGENT_HIGH = ("急", "马上", "尽快", "立即", "本周", "今天", "urgent", "asap", "now")
_URGENT_LOW = ("考虑", "计划", "后续", "年底", "明年", "看看再说", "暂时", "不急", "later")
# 预算 / 规模信号
_BUDGET_WORDS = ("预算", "万元", "万", "批量", "大单", "长期", "框架", "集采", "年度", "项目总额", "招投标", "招标")
# 常见产品兴趣词（与 DEFAULT_TAGS 互补，覆盖口语表达）
_INTEREST_WORDS = {
    "光缆": ("光缆", "光电缆", "室外光缆", "铠装"),
    "光纤跳线": ("跳线", "尾纤", "patch cord", "pigtail"),
    "光纤收发器": ("收发器", "光模块", "光电转换", "sfp", "transceiver"),
    "熔接服务": ("熔接", "熔纤", "熔接机", "splice"),
    "FTTH": ("ftth", "光纤到户", "光纤入户", "皮线"),
    "机房布线": ("机房", "配线架", "odf", "机柜", "综合布线", "mpos", "mpo"),
    "弱电工程": ("弱电", "安防", "监控", "综合布线"),
}

_NEXT_ACTION = {
    "决策采购": "确认规格与交期，直接发合同/报价单",
    "对比选型": "寄样品+测试报告，提供方案对比表",
    "明确需求": "1 个工作日内发针对性报价与资料",
    "初步了解": "先发产品画册与案例，培育信任",
    "未明确": "轻量触达，引导明确需求场景",
}


def rule_intent(lead):
    """规则意向识别。返回结构化 dict，不依赖任何外部服务。"""
    note = str(lead.get("note") or "").lower()
    source = str(lead.get("source") or "")
    ltype = str(lead.get("type") or "")
    tags = str(lead.get("tags") or "").lower()
    text = note + " " + tags
    name = str(lead.get("name") or "")

    # 1) 阶段判定：高阶段特征词优先
    stage = "未明确"
    stage_signals = []
    for stg, kws in _STAGE_KEYWORDS.items():
        hit = [k for k in kws if k in text]
        if hit:
            stage = stg
            stage_signals = hit
            break  # 字典从高到低，命中即止

    # 2) 紧迫度
    urgency = "中"
    if any(w in text for w in _URGENT_HIGH):
        urgency = "高"
    elif any(w in text for w in _URGENT_LOW):
        urgency = "低"

    # 3) 预算 / 规模信号
    budget_signal = any(w in text for w in _BUDGET_WORDS)

    # 4) 产品兴趣（合并 tags + note + 公司名里的关键词）
    interests = []
    full = text + " " + name.lower()
    for prod, kws in _INTEREST_WORDS.items():
        if any(k in full for k in kws):
            interests.append(prod)
    # 公司类型也能暗示兴趣
    if "工程商" in ltype or "集成商" in ltype:
        if "弱电工程" not in interests:
            interests.append("弱电工程")
    if "终端客户" in ltype and not interests:
        interests.append("（待确认产品）")

    # 5) 来源加权：主动留资 > 被动采集
    source_boost = source in ("落地页表单", "买家发现", "社媒评论")

    hot = (STAGE_WEIGHT[stage] >= 3) or (budget_signal and urgency == "高")

    return {
        "stage": stage,
        "stage_weight": STAGE_WEIGHT[stage],
        "urgency": urgency,
        "budget_signal": budget_signal,
        "interests": interests[:5],
        "source_boost": source_boost,
        "signals": stage_signals[:5],
        "next_action": _NEXT_ACTION[stage],
        "hot": hot,
        "method": "rule",
    }


def ai_intent(lead, settings, fallback=None):
    """AI 结构化意图分类；失败返回 fallback（默认规则结果）。

    返回与 rule_intent 同形状的 dict，并附带 method 字段说明来源。
    """
    from core import ai

    api_key = settings.get("openai_api_key")
    if not api_key:
        return fallback if fallback is not None else rule_intent(lead)
    industry = settings.get("industry", "") or "通用"
    product = settings.get("product_name", "") or "我们的产品"
    name = (lead.get("name") or "").strip()
    contact = (lead.get("contact") or "").strip()
    note = (lead.get("note") or "").strip()
    source = (lead.get("source") or "").strip()
    tags = (lead.get("tags") or "").strip()
    brief = (
        f"公司：{name}\n联系人：{contact}\n客户类型：{lead.get('type','')}\n"
        f"地区：{lead.get('region','')}\n来源：{source}\n标签：{tags}\n备注：{note[:300]}"
    )
    system = (
        f"你是{industry}行业的资深销售分析师。根据客户线索判断其购买意向，"
        "只输出一行 JSON："
        '{"stage":"未明确/初步了解/明确需求/对比选型/决策采购",'
        '"urgency":"高/中/低","budget_signal":true或false,'
        '"interests":["1到3个相关产品词"],"next_action":"下一步动作建议(20字内)",'
        '"hot":true或false}。所有判断必须基于线索真实信息；无法确认的写默认值，禁止编造。'
        "不要输出其他内容。"
    )
    user = f"我方主营产品：{product}\n\n【客户线索】\n{brief}\n\n请判断购买意向。"
    obj, err = ai.generate_json(api_key, settings.get("openai_model"), system, user,
                                settings.get("openai_api_base", ""))
    if err or not isinstance(obj, dict):
        return fallback if fallback is not None else rule_intent(lead)
    stage = str(obj.get("stage", "")).strip()
    if stage not in STAGES:
        stage = "未明确"
    urgency = str(obj.get("urgency", "中")).strip()
    if urgency not in ("高", "中", "低"):
        urgency = "中"
    interests = [str(x).strip() for x in (obj.get("interests") or []) if str(x).strip()][:5]
    try:
        budget_signal = bool(obj.get("budget_signal", False))
    except Exception:
        budget_signal = False
    try:
        hot = bool(obj.get("hot", False))
    except Exception:
        hot = False
    result = {
        "stage": stage,
        "stage_weight": STAGE_WEIGHT.get(stage, 0),
        "urgency": urgency,
        "budget_signal": budget_signal,
        "interests": interests,
        "signals": [],
        "next_action": str(obj.get("next_action", _NEXT_ACTION[stage]) or _NEXT_ACTION[stage])[:60],
        "hot": hot,
        "method": "ai",
    }
    return result


def classify(lead, settings=None, use_ai=False):
    """统一入口：默认规则；use_ai=True 且配置了密钥时走 AI（失败自动降级）。"""
    settings = settings or {}
    rule = rule_intent(lead)
    if use_ai and settings.get("openai_api_key"):
        return ai_intent(lead, settings, fallback=rule)
    return rule


def enrich_profile(lead, intent=None):
    """生成一句话用户画像摘要（用于跟进记录 / 前端展示）。"""
    intent = intent or rule_intent(lead)
    name = (lead.get("name") or "").strip() or "该客户"
    parts = [f"【{intent['stage']}】"]
    if intent.get("urgency") and intent["urgency"] != "中":
        parts.append(f"紧迫度{intent['urgency']}")
    if intent.get("budget_signal"):
        parts.append("有预算/规模信号")
    interests = intent.get("interests") or []
    if interests:
        parts.append("关注：" + "/".join(interests))
    parts.append("建议：" + (intent.get("next_action") or _NEXT_ACTION.get(intent["stage"], "")))
    return name + " " + "；".join(p for p in parts if p)
