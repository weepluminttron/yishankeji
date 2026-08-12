# -*- coding: utf-8 -*-
"""AI 跟进内容生成：按线索画像生成个性化首触邮件 / 短信 / 开场话术。

设计：纯函数，全部走 core.ai（已带缓存+重试）。无 AI key 或调用失败时返回
(None, err)，由调用方决定是否降级到模板，不抛异常、不阻塞主流程。
"""
from core import ai


def _lead_brief(lead, settings):
    """拼装给 AI 的线索画像摘要（不泄露我方密钥）。"""
    name = (lead.get("name") or "").strip()
    region = (lead.get("region") or "").strip()
    ltype = (lead.get("type") or "").strip()
    source = (lead.get("source") or "").strip()
    tags = (lead.get("tags") or "").strip()
    note = (lead.get("note") or "").strip()
    contact = (lead.get("contact") or "").strip()
    return (
        f"客户公司：{name}\n"
        f"联系人：{contact}\n"
        f"地区：{region}\n"
        f"客户类型：{ltype}\n"
        f"来源：{source}\n"
        f"标签：{tags}\n"
        f"备注/历史：{note[:200]}\n"
    )


def gen_email(lead, settings):
    """生成个性化首触邮件，返回 ({subject, body}, err)。"""
    api_key = settings.get("openai_api_key")
    if not api_key:
        return None, "未配置 AI 密钥"
    industry = settings.get("industry", "") or "通用"
    company = settings.get("company_name", "") or "我方公司"
    product = settings.get("product_name", "") or "我们的产品"
    brief = _lead_brief(lead, settings)
    system = (
        f"你是{industry}行业的资深销售，代表{company}。请基于【客户线索】写一封"
        "专业的首触开发邮件。要求：标题简洁有吸引力；正文 80-160 字、口语化、"
        "突出与客户业务相关的价值点；不要虚构客户案例、电话、邮箱或数据；"
        "不确定的信息用“可进一步沟通”带过。只输出一行 JSON："
        '{"subject":"邮件标题","body":"邮件正文"}，不要输出其他内容。'
    )
    user = (
        f"我方公司：{company}\n主营产品：{product}\n\n"
        f"【客户线索】\n{brief}\n\n请生成首触邮件。"
    )
    obj, err = ai.generate_json(api_key, settings.get("openai_model"), system, user,
                                settings.get("openai_api_base", ""))
    if err:
        return None, err
    if not isinstance(obj, dict) or not obj.get("body"):
        return None, "AI 返回格式异常"
    return {
        "subject": str(obj.get("subject", "")).strip(),
        "body": str(obj.get("body", "")).strip(),
    }, None


def gen_sms(lead, settings):
    """生成≤70字首触短信，返回 (text, err)。"""
    api_key = settings.get("openai_api_key")
    if not api_key:
        return None, "未配置 AI 密钥"
    industry = settings.get("industry", "") or "通用"
    company = settings.get("company_name", "") or "我方公司"
    product = settings.get("product_name", "") or "我们的产品"
    brief = _lead_brief(lead, settings)
    system = (
        f"你是{industry}行业销售，代表{company}。写一条首触短信：≤70字、含称呼与"
        "一句价值点、口语化、可留咨询入口；不要编造电话/微信/案例。只输出短信正文本身，不要引号。"
    )
    user = (
        f"我方主营：{product}\n\n【客户线索】\n{brief}\n\n请生成短信。"
    )
    text, err = ai.generate_copy(api_key, settings.get("openai_model"), system, user,
                                 settings.get("openai_api_base", ""))
    if err:
        return None, err
    text = (text or "").strip().strip('"').strip()
    if not text:
        return None, "AI 返回为空"
    return text[:70], None


def gen_opening(lead, settings):
    """生成一句话开场话术（电话/微信场景），返回 (text, err)。"""
    api_key = settings.get("openai_api_key")
    if not api_key:
        return None, "未配置 AI 密钥"
    industry = settings.get("industry", "") or "通用"
    company = settings.get("company_name", "") or "我方公司"
    product = settings.get("product_name", "") or "我们的产品"
    brief = _lead_brief(lead, settings)
    system = (
        f"你是{industry}行业销售，代表{company}。写一句自然的开场话术（电话或微信首句），"
        "≤40字，能自然引出" + product + "相关业务、不突兀、不硬广。只输出话术本身。"
    )
    user = (
        f"我方主营：{product}\n\n【客户线索】\n{brief}\n\n请生成开场话术。"
    )
    text, err = ai.generate_copy(api_key, settings.get("openai_model"), system, user,
                                 settings.get("openai_api_base", ""))
    if err:
        return None, err
    text = (text or "").strip().strip('"').strip()
    if not text:
        return None, "AI 返回为空"
    return text[:60], None
