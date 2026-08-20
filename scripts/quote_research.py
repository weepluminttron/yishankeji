# -*- coding: utf-8 -*-
"""供应商报价调研：按公司名单逐个搜索“产品+报价”，抓取页面提取价格与联系方式。

用法：
    python scripts/quote_research.py
    python scripts/quote_research.py --companies "深圳市嘉富光通信有限公司,深圳市飞宇光纤系统有限公司" --product "光纤回路器" --limit 4

输出：
    outputs/quote_research_report.md   人工可读报价报告
    outputs/quote_research.csv         结构化报价表
"""
import argparse
import csv
import json
import os
import re
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core import db, buyer, crawler  # noqa: E402

DEFAULT_COMPANIES = [
    "深圳市嘉富光通信有限公司", "深圳市飞宇光纤系统有限公司", "深圳市嘉万光通信有限公司",
    "深圳市盛杰通讯技术有限公司", "深圳市毅宏光通信有限公司", "深圳市纳鑫达通讯设备有限公司",
    "深圳市科海光器件有限公司", "宁波莱塔思光学科技有限公司", "慈溪正佳通信科技有限公司",
    "福建硅光通讯科技有限公司", "爱普迪", "深圳前海荟创科技有限公司",
    "深圳市视海通电子有限公司", "深圳市凯达光通信科技有限公司",
    "BGB", "MOOG", "Schleifring", "Princetel", "Rojone", "Hitachi-cable",
]

PRICE_LINE_RE = re.compile(
    r"[^\n。；;|]{0,45}(?:¥|￥|RMB|USD|US\$|价格|单价|售价|批发价|出厂价|报价|起订量|MOQ|min\.? order|price)"
    r"[^\n。；;|]{0,70}", re.I,
)
PRICE_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
NOISE_URL_RE = re.compile(r"(baike|zhihu|weixin|juejin|csdn|sohu|163\.com|sina|qq\.com)", re.I)


def load_settings(provider=""):
    try:
        db.init_db()
    except Exception:
        pass
    s = db.get_settings()
    if provider:
        s["search_provider"] = provider
    return s


def clean_text(html_text):
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt[:8000]


def extract_price_lines(text):
    lines = []
    for m in PRICE_LINE_RE.finditer(text):
        frag = re.sub(r"\s+", " ", m.group(0)).strip()
        if frag and PRICE_NUM_RE.search(frag) and frag not in lines:
            lines.append(frag[:120])
        if len(lines) >= 6:
            break
    return lines


def search_company(company, product, settings, limit=6):
    queries = [
        f"{company} {product} 报价",
        f"{company} {product} 价格",
        f"{company} {product} price",
        f"{company} 官网",
    ]
    results = []
    seen = set()
    errors = []
    for q in queries:
        try:
            rs = buyer.search_web_cached(q, 6, settings) or []
        except Exception as e:
            errors.append(f"{q}: {e}")
            continue
        for r in rs:
            url = str(r.get("url") or "").strip()
            title = str(r.get("title") or "").strip()
            if not url or url in seen or NOISE_URL_RE.search(url):
                continue
            seen.add(url)
            results.append({"url": url, "title": title, "query": q})
        if len(results) >= limit:
            break
    return results[:limit], errors


def probe_url(url, title, settings):
    info = {"url": url, "title": title, "price_lines": [], "phones": [], "emails": [], "error": ""}
    try:
        html_text, final_url = crawler.fetch_page(url, timeout=12, use_jina=True, settings=settings)
    except Exception as e:
        info["error"] = str(e)[:100]
        return info
    text = clean_text(html_text)
    info["price_lines"] = extract_price_lines(text)
    try:
        contact = buyer.extract_contacts(html_text, final_url or url)
        info["phones"] = (contact.get("phones") or [])[:3]
        info["emails"] = (contact.get("emails") or [])[:3]
    except Exception:
        pass
    return info


def research_company(company, product, settings, limit=6, probe=4):
    item = {"company": company, "results": [], "errors": [], "probes": []}
    results, errors = search_company(company, product, settings, limit=limit)
    item["results"] = results
    item["errors"] = errors
    for r in results[:probe]:
        p = probe_url(r["url"], r["title"], settings)
        item["probes"].append(p)
        time.sleep(0.3)
    return item


def summarize(item):
    company = item["company"]
    all_prices = []
    phones = []
    emails = []
    sites = []
    for p in item["probes"]:
        if p["url"] and p["url"] not in sites:
            sites.append(p["url"])
        for ph in p["phones"]:
            if ph not in phones:
                phones.append(ph)
        for em in p["emails"]:
            if em not in emails:
                emails.append(em)
        for pl in p["price_lines"]:
            if pl not in all_prices:
                all_prices.append(pl)
    return {
        "公司": company,
        "官网/来源": "; ".join(sites)[:300],
        "电话": "; ".join(phones)[:120],
        "邮箱": "; ".join(emails)[:120],
        "报价信息": " | ".join(all_prices)[:400],
        "搜索错误": "; ".join(item["errors"])[:200],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", default="")
    ap.add_argument("--product", default="光纤回路器")
    ap.add_argument("--provider", default="", help="搜索源：bing_free/so_free/baidu_free/serpapi/bocha")
    ap.add_argument("--limit", type=int, default=6, help="每家公司最多搜索条数")
    ap.add_argument("--probe", type=int, default=4, help="每家公司最多抓取页数")
    args = ap.parse_args()

    companies = [c.strip() for c in (args.companies or ",".join(DEFAULT_COMPANIES)).split(",") if c.strip()]
    settings = load_settings(args.provider)
    print(f"搜索源: {settings.get('search_provider')} | 公司数: {len(companies)} | 产品: {args.product}", flush=True)

    rows = []
    md = [f"# {args.product} 供应商报价调研\n", f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')} ｜ 搜索源：{settings.get('search_provider')}", ""]
    for i, company in enumerate(companies, 1):
        print(f"[{i}/{len(companies)}] {company} ...", flush=True)
        item = research_company(company, args.product, settings, limit=args.limit, probe=args.probe)
        row = summarize(item)
        rows.append(row)
        md.append(f"## {i}. {company}")
        md.append(f"- 官网/来源：{row['官网/来源'] or '未找到'}")
        md.append(f"- 电话：{row['电话'] or '未找到'} ｜ 邮箱：{row['邮箱'] or '未找到'}")
        md.append(f"- 报价信息：{row['报价信息'] or '未公开报价，需询价'}")
        if row["搜索错误"]:
            md.append(f"- 搜索提示：{row['搜索错误']}")
        md.append("")
        # 每 3 家保存一次中间结果，避免长任务中断丢数据
        if i % 3 == 0:
            write_outputs(rows, md, args.product)

    write_outputs(rows, md, args.product)
    print(f"完成：{len(rows)} 家公司，报告见 outputs/quote_research_report.md", flush=True)


def write_outputs(rows, md_lines, product):
    out_dir = os.path.join(BASE_DIR, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "quote_research_report.md")
    csv_path = os.path.join(out_dir, "quote_research.csv")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["公司", "官网/来源", "电话", "邮箱", "报价信息", "搜索错误"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    main()
