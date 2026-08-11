# -*- coding: utf-8 -*-
"""线索评分：规则引擎快速评分 + 可选 AI 深度评分。"""
import json
import re


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
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", email):
        score += 3
        reasons.append("有邮箱")
    if re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", phone) or re.search(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)", phone):
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
    system = (
        f"你是一名资深{industry}行业销售顾问，擅长判断潜在客户的采购意向和成交可能性。"
        "只输出一行 JSON，格式：{\"score\": 0-10的整数, \"reason\": \"一句话理由\"}，不要输出任何其他内容。"
    )
    user = (
        f"请评估以下客户线索的跟进价值：\n"
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
        reason = str(data.get("reason", "")).strip()
        return score, reason
    except Exception as e:
        return None, f"AI 返回解析失败：{e}"
