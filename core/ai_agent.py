# -*- coding: utf-8 -*-
"""AI 获客智能体：DeepSeek 大脑 + 爬虫手脚。

把三个能力暴露给大模型调用：
- search_web：联网搜索（复用 core.buyer 多源降级 + 缓存）
- scrape_urls：批量智能抓取（Playwright 渲染 + urllib 兜底，自动提取联系方式）
- company_discover：按公司名核验官网/联系方式（core.public_company，企查查思路）

用法：
    result = run_lead_agent("我想找东南亚有数据中心项目的系统集成商", settings)
"""
import json
import re

from core import ai, buyer
from core.smart_crawler import SmartCrawler

SYSTEM_PROMPT = """你是“商机探针”AI 获客智能体，擅长 B2B 客户调研（不限行业）。

你的工作方法：
1. 先用 search_web 做多轮检索。关键词必须带买方意图（采购/招标/询价/RFQ/sourcing/procurement/tender/project）。
   找海外客户时用英文关键词；需要限定站点时用 site:（例如 site:linkedin.com、site:reddit.com）。
2. 找到候选公司后，用 scrape_urls 抓取官网及联系页，提取邮箱/电话/主营业务/采购信号。
3. 对不确定的公司用 company_discover 核验官网与公开联系方式。
4. 最后只输出一个 JSON（不要 Markdown 代码块，不要解释）：
   {"leads":[{"name":"公司名","website":"官网","email":"","phone":"","region":"地区","buyer_type":"客户类型","reason":"为什么是潜在客户（50字内）","priority":1-5}],"summary":"整体结论与建议（80字内）"}

纪律：
- 只写有公开依据的信息，查不到的字段留空，禁止编造邮箱/电话；
- 每家公司最多抓 3 个页面，抓取总数控制在 10 个以内；
- priority 5=高意向（正在招标/采购/扩产），3=中意向，1=低意向。"""


def _tool_defs():
    return [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "联网搜索找潜在客户、招标采购公告、公司官网。返回标题/链接/摘要。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索词，必须带采购/招标/RFQ 等买方意图词，可带 site: 限定"},
                        "count": {"type": "integer", "description": "返回条数，默认 8，最大 10"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scrape_urls",
                "description": "批量抓取网页，提取页面标题、邮箱、电话和正文摘要。适合抓公司官网/联系页。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "要抓取的网址列表，最多 10 个",
                        },
                    },
                    "required": ["urls"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "company_discover",
                "description": "输入公司名，返回官网、公开联系方式、主营业务简介。用于核验一家公司。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string", "description": "公司全称"},
                        "region": {"type": "string", "description": "地区（可选）"},
                    },
                    "required": ["company"],
                },
            },
        },
    ]


def _execute_tool(name, args, settings, crawler):
    if name == "search_web":
        q = str(args.get("query") or "").strip()
        if not q:
            return {"error": "缺少 query"}
        count = min(int(args.get("count") or 8), 10)
        try:
            results = buyer.search_web_cached(q, count, settings)
        except Exception as e:
            return {"error": str(e)[:300]}
        return [
            {
                "title": (r.get("title") or "")[:120],
                "url": r.get("url") or "",
                "snippet": (r.get("snippet") or "")[:220],
            }
            for r in results[:count]
        ]
    if name == "scrape_urls":
        urls = [u for u in (args.get("urls") or [])[:10] if isinstance(u, str) and u.startswith(("http://", "https://"))]
        if not urls:
            return {"error": "缺少 urls 或网址不合法"}
        out = crawler.batch_scrape_sync(urls, settings)
        return [
            {
                "url": r.get("url"),
                "success": r.get("success"),
                "title": (r.get("title") or "")[:120],
                "emails": r.get("emails") or [],
                "phones": r.get("phones") or [],
                "content": (r.get("content") or "")[:600],
                "error": r.get("error"),
            }
            for r in out
        ]
    if name == "company_discover":
        company = str(args.get("company") or "").strip()
        if not company:
            return {"error": "缺少 company"}
        region = str(args.get("region") or "").strip()
        try:
            from core import public_company
            profile = public_company.discover(company, region=region, settings=settings, max_results=6, max_fetch=3)
            return {
                "name": profile.get("name") or company,
                "website": profile.get("website") or "",
                "contacts": profile.get("contacts") or {},
                "summary": public_company.summary_text(profile, 1500),
            }
        except Exception as e:
            return {"error": str(e)[:300]}
    return {"error": "未知工具 " + str(name)}


def _extract_leads(answer):
    """从 AI 最终回答中提取 leads 列表（容错解析）。"""
    if not answer:
        return []
    # 去掉 Markdown 代码块围栏
    answer = answer.strip()
    if answer.startswith("```"):
        answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer, flags=re.S)
    try:
        obj = json.loads(answer)
    except Exception:
        m = __import__("re").search(r"\{.*\}", answer, __import__("re").S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return []
    if isinstance(obj, list):
        return obj
    return obj.get("leads") or []


def run_lead_agent(user_request, settings=None, max_steps=8, progress=None):
    """运行 AI 获客智能体。返回 {ok, answer, steps, leads, msg}。"""
    settings = settings or {}
    key = settings.get("openai_api_key") or ""
    if not key:
        return {"ok": False, "msg": "未配置 AI 密钥（设置 → AI 接口）", "leads": []}
    model = settings.get("openai_model") or "deepseek-chat"
    api_base = settings.get("openai_api_base") or ""
    try:
        concurrency = int(settings.get("crawler_concurrency") or 3)
    except Exception:
        concurrency = 3
    crawler = SmartCrawler(max_concurrent=concurrency)

    def exec_tool(name, args):
        if progress:
            try:
                progress("调用工具：" + name)
            except Exception:
                pass
        return _execute_tool(name, args, settings, crawler)

    answer, steps, err = ai.run_tool_loop(
        key, model, SYSTEM_PROMPT, user_request, _tool_defs(),
        execute_tool=exec_tool, api_base=api_base, max_steps=max_steps,
    )
    if err:
        return {"ok": False, "msg": err, "answer": answer, "steps": steps, "leads": []}
    leads = _extract_leads(answer)
    return {"ok": True, "answer": answer, "steps": steps, "leads": leads, "msg": ""}
