# -*- coding: utf-8 -*-
"""地图获客：高德地图 POI 搜索（弱电工程/安防监控/机房建设等公司）。

需要免费申请高德开放平台 Web 服务 Key：https://lbs.amap.com/
内置请求延迟，避免高频访问被限流。
"""
import json
import time
import urllib.parse
import urllib.request

API_URL = "https://restapi.amap.com/v3/place/text"

# 关键词 → 客户类型映射
TYPE_MAP = [
    (("弱电", "安防", "监控", "智能楼宇", "综合布线"), "工程商"),
    (("通信", "网络", "数据", "机房", "集成", "工程", "建设", "施工"), "工程商"),
]


def _map_type(category):
    for kws, t in TYPE_MAP:
        if any(k in category for k in kws):
            return t
    return "其他"


def amap_search(keyword, city, api_key, offset=25, max_pages=4):
    """按关键词+城市搜索，返回 [{name, phone, address, region, type, source, tags, note}]。"""
    if not api_key:
        raise ValueError("未配置高德地图 API Key，请到“设置 → 地图接口”填写（免费申请：lbs.amap.com）")
    results = []
    seen = set()
    for page in range(1, max_pages + 1):
        params = urllib.parse.urlencode({
            "key": api_key,
            "keywords": keyword,
            "city": city,
            "citylimit": "true",
            "offset": str(offset),
            "page": str(page),
            "extensions": "base",
        })
        req = urllib.request.Request(API_URL + "?" + params, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        if data.get("status") != "1":
            raise ValueError("高德接口返回：" + str(data.get("info") or data))
        pois = data.get("pois") or []
        for p in pois:
            name = str(p.get("name", "")).strip()
            tel = str(p.get("tel", "") or "").strip()
            address = str(p.get("address", "") or "").strip()
            category = str(p.get("type", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            phone = tel.split(";")[0].strip() if tel else ""
            results.append({
                "name": name,
                "contact": "",
                "phone": phone,
                "email": "",
                "region": city,
                "type": _map_type(category),
                "status": "新线索",
                "source": "地图获客",
                "tags": keyword,
                "address": address,
                "note": f"地图类别：{category}",
            })
        if len(pois) < offset:
            break
        time.sleep(1.2)  # 防限流延迟
    return results
