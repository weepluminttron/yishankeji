# -*- coding: utf-8 -*-
"""回归测试：买方分层检索、新兴市场专项、线索核验分级、工商自动补全。"""
import sys
import os
import tempfile

sys.path.insert(0, ".")
from core import acquisition, buyer  # noqa: E402
from core import db  # noqa: E402


def main():
    db.DB_PATH = os.path.join(tempfile.gettempdir(), "yskt_acq_logic_test.db")
    db.init_db()

    c = acquisition.normalize_conditions({
        "specs": "DWDM,玻璃管",
        "buyer_types": "光模块厂,近期招标扩容",
        "regions": "中国大陆,印度",
        "min_tier": "B",
    })
    p = acquisition.generate_plan(c)
    qs = p["queries_by_channel"]
    allq = " | ".join(" ".join(v) for v in qs.values())
    assert "供应商征集" in allq, "缺供应商征集"
    assert "扩产 募投" in allq, "缺扩产募投"
    assert "骨干网 项目" in allq or "metro DWDM" in allq, "缺骨干网"
    assert "DWDM import 印度" in allq and "DWDM tender 印度" in allq, "缺印度专项"
    print("买方分层检索式 + 新兴市场专项 OK, 查询数:", p["query_total"])

    qs_india = buyer.build_queries("DWDM", "印度")
    assert any("import" in q for q in qs_india) and any("tender" in q for q in qs_india), qs_india
    print("实际发现阶段海外买方查询 OK:", qs_india[:2])

    # 时间范围 + 站点过滤
    q = buyer._apply_search_filters("DWDM 采购", {"search_site_filter": "gov.cn, in"})
    assert "site:gov.cn" in q and "site:in" in q, q
    fp = buyer._freshness_params("month")
    assert fp["bocha"] == "oneMonth" and fp["tbs"] == "qdr:m" and fp["qdr"] == "m"
    print("时间范围/站点过滤 OK:", q, fp)

    cand = buyer._to_candidate(
        {"emails": ["buy@corp.com"], "phones": ["13800138000"], "whatsapp": [], "wechat": [],
         "company": "某公司", "website": "http://corp.com"},
        "某公司 - 采购", "采购 DWDM", "DWDM 采购", "广东", "DWDM 采购 招标",
    )
    assert cand["verified"] is True and cand["path"]
    cand2 = buyer._to_candidate(
        {"emails": [], "phones": [], "whatsapp": [], "wechat": [], "company": "某公司", "website": ""},
        "某公司", "", "DWDM", "", "",
    )
    assert cand2["verified"] is False
    print("线索核验分级 OK")

    t = [{"company": "腾景科技", "phone": "", "email": "", "address": "", "verified": False, "path": "x"}]
    r = acquisition.enrich_with_company_api(t, {}, limit=5)
    assert r["done"] == 0
    import core.company_api as ca

    ca.query_company = lambda settings, kw, provider="auto": {
        "phone": "0591-38178242", "email": "sales@optowide.com", "address": "福州", "source": "企查查",
    }
    r = acquisition.enrich_with_company_api(t, {"qcc_app_key": "k", "qcc_secret_key": "s"}, limit=5)
    assert r["updated"] == 1 and t[0]["verified"] is True and t[0]["phone"] == "0591-38178242"
    assert "企查查" in t[0]["path"]
    print("工商自动补全 OK:", r)

    # 官网定向抓取核验（WebFetch）
    import core.crawler as crawler
    import core.buyer as buyer_mod

    crawler.fetch_page = lambda url, timeout=15, use_jina=True, jina_timeout=12: (
        "<html><title>公司</title><body>buy@corp.com 010-88886666</body></html>", url,
    )
    buyer_mod.extract_contacts = lambda html, url="": {
        "company": "公司", "emails": ["buy@corp.com"], "phones": ["010-88886666"],
        "whatsapp": [], "wechat": [], "website": url,
    }
    t2 = [{"company": "某公司", "website": "http://corp.com", "phone": "", "email": "",
           "address": "", "verified": False, "path": "x"}]
    vr = acquisition.verify_contacts(t2, {}, limit=5)
    assert vr["updated"] == 1 and t2[0]["verified"] is True and t2[0]["email"] == "buy@corp.com"
    assert "WebFetch" in t2[0]["path"]
    print("官网定向抓取核验 OK:", vr)

    s = db.get_settings()
    assert "search_freshness" in s and "search_site_filter" in s
    print("设置默认值 OK")

    seed = [{
        "name": "某光模块厂", "note": "采购DWDM准直器玻璃毛细管 1550nm 定制送样 营收10亿 扩产 头部厂商",
        "tags": "DWDM,玻璃管", "website": "http://x.com", "email": "a@b.com", "phone": "13800138000",
        "region": "广东", "type": "代工厂", "source": "手动", "next_action": "报价",
    }]
    res = acquisition.run_engine(c, max_rounds=1, seed_candidates=seed)
    assert not res["company_enrich"] or res["company_enrich"] == {"done": 0, "updated": 0, "errors": []}
    print("引擎集成 OK")
    print("ALL OK")


if __name__ == "__main__":
    main()
