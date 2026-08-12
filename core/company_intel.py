# -*- coding: utf-8 -*-
"""AI 公司背景速览：输入公司名（+地区），联网抓取或纯推断，输出结构化速览。

设计：
- 优先用 core.crawler 抓取公司官网/搜索结果摘要作为依据；
- 抓取失败则降级为“仅基于公司名让 AI 推断”，并在结果里标注“待核实”；
- 全部走 core.ai（带缓存+重试）；失败返回 (None, err)，不抛异常、不阻塞主流程。
"""
import re
import urllib.parse

from core import ai, crawler


def _fetch_context(company, region=""):
    """尝试联网拿到公司简介文本；失败返回 ("", 原因)。"""
    query = (company + " " + (region or "")).strip() + " 公司 官网 简介"
    try:
        html_text, _ = crawler.fetch_page(
            "https://www.bing.com/search?q=" + urllib.parse.quote(query),
            timeout=12,
        )
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        # 取前 1500 字作为上下文（搜索结果摘要足够）
        return text[:1500], ""
    except Exception as e:
        return "", f"联网抓取失败：{e}"


def brief(company, settings, region=""):
    """返回 (info_dict, err)。

    info_dict: {scale, business, match(高/中/低), match_reason, signal, summary, inferred(bool)}
    """
    company = (company or "").strip()
    if not company:
        return None, "请填写公司名称"
    api_key = settings.get("openai_api_key")
    if not api_key:
        return None, "未配置 AI 密钥"
    industry = settings.get("industry", "") or "通用"
    product = settings.get("product_name", "") or "我们的产品"

    ctx, ctx_err = _fetch_context(company, region)
    inferred = not ctx
    ctx_block = (
        f"【联网获取的公司相关信息】\n{ctx}\n" if ctx
        else "（未能联网获取该公司信息，请仅基于公司名称推断，并明确标注“待核实”）\n"
    )

    system = (
        f"你是{industry}行业的资深销售分析师。请基于给定信息分析一家公司的"
        "客户价值，只输出一行 JSON："
        '{"scale":"规模估计(如:小型/中型/大型/未知)","business":"主营业务一句话",'
        '"match":"与我方产品匹配度(高/中/低)","match_reason":"匹配度依据",'
        '"signal":"采购意向信号(强/中/弱/未知)","summary":"一句话简介"}，'
        "不要输出其他内容。所有判断必须基于给定信息，无法确认的一律写“未知/待核实”，禁止编造。"
    )
    user = (
        f"我方主营产品：{product}\n"
        f"待分析公司：{company}（地区：{region or '未知'}）\n\n"
        f"{ctx_block}\n请输出分析。"
    )
    obj, err = ai.generate_json(api_key, settings.get("openai_model"), system, user,
                                settings.get("openai_api_base", ""))
    if err:
        return None, err
    if not isinstance(obj, dict):
        return None, "AI 返回格式异常"
    return {
        "scale": str(obj.get("scale", "未知")).strip(),
        "business": str(obj.get("business", "")).strip(),
        "match": str(obj.get("match", "未知")).strip(),
        "match_reason": str(obj.get("match_reason", "")).strip(),
        "signal": str(obj.get("signal", "未知")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "inferred": inferred,
    }, None
