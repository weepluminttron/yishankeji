# -*- coding: utf-8 -*-
"""回归测试：买方分层检索、新兴市场专项、线索核验分级、工商自动补全。"""
import sys

sys.path.insert(0, ".")
from core import acquisition, buyer  # noqa: E402


def main():
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
