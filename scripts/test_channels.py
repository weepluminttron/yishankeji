# -*- coding: utf-8 -*-
"""多源获客渠道框架离线测试（无需 lxml / 无需联网）。

覆盖：
  1) 配置加载与字段补全
  2) 渠道解析（类别 → 渠道 id；缺密钥自动跳过；--channels 显式列表含类别名）
  3) 关键词模板渲染（{kw}/{intent}/{market}/{site} 与 site 限定注入）
  4) 跨渠道去重与渠道归因归并（normalize_merge）
  5) run_channel_search 编排（注入 fake core.buyer，验证并行/聚合/渠道标记/统计）
  6) acquisition 集成：generate_plan 产出 channel_sources；run_engine(seed) 透传 channels 字段；_dedupe 合并渠道归因
"""
import sys
import os
import types
from urllib.parse import quote

sys.path.insert(0, ".")

from core import channels  # 不依赖 lxml（仅惰性 import buyer）


# ---------------------------------------------------------------------------
# 注入 fake core.buyer：让 run_channel_search 在不装 lxml / 不联网时也能跑
# ---------------------------------------------------------------------------
_fake = types.ModuleType("core.buyer")


def _fake_search(query, count, settings):
    # 根据 query 是否含 site: 返回带域名的结果，便于观察 site_scope 是否注入
    prov = settings.get("search_provider", "bing")
    scope = settings.get("search_site_filter", "")
    return [{
        "title": f"[{prov}] {query}",
        "url": "https://example.com/result/" + quote(query),  # 每个 query 唯一路径（不被去重归一化截断）
        "snippet": f"采购 DWDM {scope}",
    }]


_fake.search_web_cached = _fake_search
_fake.search_web = _fake_search
sys.modules["core.buyer"] = _fake


def main():
    # 1) 配置加载
    cfg = channels.load_channel_config()
    assert cfg["channels"], "渠道配置为空"
    by_id = {c["id"]: c for c in cfg["channels"]}
    assert "linkedin" in by_id and "c114" in by_id and "zhihu" in by_id
    print("1) 配置加载 OK，渠道数:", len(cfg["channels"]))

    # 2) 渠道解析：无密钥 → 仅免费可达；serpapi/bocha 应被跳过
    ids = channels.get_enabled_channel_ids(conditions={"channels": ["search_engine", "social_media", "industry_site", "forum"]}, settings={})
    assert "linkedin" in ids and "c114" in ids and "zhihu" in ids
    assert "serpapi" not in ids and "bocha" not in ids, "无密钥不应启用 serpapi/bocha"
    print("2) 无密钥解析 OK（免费源启用，付费源跳过）:", ids)

    # 2b) 显式指定付费源 + 有密钥 → 可达（付费源默认关闭，需显式启用）
    ids2 = channels.get_enabled_channel_ids(explicit=["serpapi", "bocha"], settings={"search_api_key": "x", "search_engine_id": "y"})
    assert "serpapi" in ids2 and "bocha" in ids2, "显式+密钥应启用 serpapi/bocha"
    # 2c) 显式指定但无密钥 → 可达性过滤掉付费源
    ids2b = channels.get_enabled_channel_ids(explicit=["serpapi", "bocha"], settings={})
    assert "serpapi" not in ids2b and "bocha" not in ids2b, "无密钥应跳过付费源"
    print("2b) 显式付费源解析 OK（有密钥启用/无密钥跳过）:", ids2)

    # 2c) 显式列表含类别名（中文展示名 / 英文 key 均可）
    ids3 = channels.get_enabled_channel_ids(explicit=["社交媒体", "c114"], settings={})
    assert "linkedin" in ids3 and "c114" in ids3, ids3
    ids3b = channels.get_enabled_channel_ids(explicit=["social_media", "c114"], settings={})
    assert "linkedin" in ids3b and "c114" in ids3b
    print("2c) 显式+类别名解析 OK:", ids3)

    # 3) 关键词模板渲染
    q_linkedin = channels.build_channel_query(by_id["linkedin"], "DWDM", "广东", "采购")
    assert "site:linkedin.com" in q_linkedin, q_linkedin
    assert "DWDM" in q_linkedin and "采购" in q_linkedin
    q_c114 = channels.build_channel_query(by_id["c114"], "DWDM", "广东", "招标")
    assert "site:c114.com.cn" in q_c114
    qs = channels.build_channel_queries(by_id["linkedin"], ["DWDM", "光缆"], ["广东", "印度"], max_per_channel=12)
    assert qs and all(len(x) == 3 for x in qs)  # (q, kw, mkt)
    assert len(qs) <= 12
    print("3) 关键词渲染 OK:", q_linkedin, "|", q_c114, "| 生成检索式数:", len(qs))

    # 4) 跨渠道去重与归因归并
    merged = channels.normalize_merge([
        {"title": "A", "url": "https://example.com/x", "snippet": "s1", "keyword": "DWDM", "market": "广东", "channels": ["bing"]},
        {"title": "A 更全", "url": "https://example.com/x", "snippet": "s1 更详细摘要", "keyword": "DWDM", "market": "广东", "channels": ["linkedin"]},
        {"title": "B", "url": "https://other.com/y", "snippet": "s2", "keyword": "光缆", "market": "印度", "channels": ["c114"]},
    ])
    assert len(merged) == 2, merged
    a = [m for m in merged if "example.com" in m["url"]][0]
    assert set(a["channels"]) == {"bing", "linkedin"}, a["channels"]
    assert "更详细" in a["snippet"], "应保留更长的摘要"
    print("4) 跨渠道去重归并 OK，合并渠道:", a["channels"])

    # 5) run_channel_search 编排（fake buyer）
    res, stats = channels.run_channel_search(
        ["linkedin", "c114"], keywords=["DWDM"], markets=["广东"], settings={}, use_cache=False,
    )
    assert res, "应有聚合结果"
    assert all(isinstance(r.get("channels"), list) and r["channels"] for r in res), res
    assert stats["linkedin"]["count"] > 0 and stats["c114"]["count"] > 0
    # 注入的 site_scope 应体现在搜索设置里（linkedin → site:linkedin.com）
    assert any("linkedin.com" in (r.get("snippet") or "") for r in res), [r["snippet"] for r in res]
    print("5) run_channel_search 编排 OK，聚合结果数:", len(res),
          "各渠道统计:", {k: v["count"] for k, v in stats.items()})

    # 6) acquisition 集成（无需 buyer：用 seed 路径）
    from core import acquisition
    c = acquisition.normalize_conditions({
        "specs": "DWDM,玻璃管", "buyer_types": "光模块厂,近期招标扩容",
        "regions": "中国大陆,印度", "min_tier": "B",
    })
    assert "social_media" in c["channels"] and "industry_site" in c["channels"], "默认应启用新渠道类别"
    plan = acquisition.generate_plan(c)
    assert plan.get("channel_sources"), "generate_plan 应产出 channel_sources"
    assert any(cs["id"] == "linkedin" for cs in plan["channel_sources"]), plan["channel_sources"]
    print("6) generate_plan.channel_sources OK，启用渠道数:", len(plan["channel_sources"]))

    # 6b) _dedupe 合并渠道归因
    deduped = acquisition._dedupe([
        {"website": "http://a.com", "email": "", "name": "同公司", "channels": "bing"},
        {"website": "http://a.com", "email": "", "name": "同公司", "channels": "linkedin"},
    ])
    assert len(deduped) == 1
    assert set(deduped[0]["channels"].split(",")) == {"bing", "linkedin"}, deduped[0]
    print("6b) _dedupe 渠道合并 OK:", deduped[0]["channels"])

    # 6c) run_engine(seed) 透传 channels 字段（不触网）
    seed = [{
        "name": "某光模块厂", "note": "采购DWDM准直器玻璃毛细管 1550nm 定制送样 营收10亿 扩产 头部厂商",
        "tags": "DWDM,玻璃管", "website": "http://x.com", "email": "a@b.com", "phone": "13800138000",
        "region": "广东", "type": "代工厂", "source": "手动", "next_action": "报价", "channels": "",
    }]
    res_e = acquisition.run_engine(c, max_rounds=1, seed_candidates=seed)
    assert res_e["targets"], "seed 应产出目标"
    t0 = res_e["targets"][0]
    assert "channels" in t0, "目标应含 channels 字段"
    print("6c) run_engine(seed) 透传 channels 字段 OK，字段示例:", t0["channels"])

    # 6d) 引擎默认 conditions.channels 经 get_enabled_channel_ids 解析出具体渠道
    ch_ids = channels.get_enabled_channel_ids(c, {})
    assert "linkedin" in ch_ids and "c114" in ch_ids and "zhihu" in ch_ids
    print("6d) 引擎默认渠道解析 OK:", ch_ids)

    # 9) 非法延时/重试配置不应导致崩溃（自检加固）
    import core.antibot as ab

    assert ab._safe_float("abc", 0.5) == 0.5
    assert ab._safe_int("xyz", 2) == 2
    assert ab.human_delay({"delay_default": "abc"}, key="default") >= 0.1
    print("9) 非法配置安全解析 OK")

    # 10) 仓库默认渠道配置可回退（服务器无 data/channels_config.json 时也能启用全部渠道）
    import pathlib

    default_path = str(pathlib.Path("core/channels_default.json"))
    cfg_default = channels.load_channel_config(default_path)
    assert len(cfg_default["channels"]) >= 20, len(cfg_default["channels"])
    ids_default = {c["id"] for c in cfg_default["channels"]}
    assert "zhaopin" in ids_default and "cninfo" in ids_default
    print("10) 默认渠道配置 OK，渠道数:", len(cfg_default["channels"]))

    print("\nALL CHANNEL TESTS OK")


if __name__ == "__main__":
    main()
