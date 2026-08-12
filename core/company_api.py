# -*- coding: utf-8 -*-
"""工商信息查询：企查查 + 天眼查 开放平台 API。

- 企查查：https://openapi.qcc.com/data/api/ent_search/getSearchByKey?key=关键词
  请求头 AppKey / Timespan / Token=MD5(AppKey+Timespan+SecretKey) 大写
- 天眼查：https://open.api.tianyancha.com/services/open/ic/baseinfo/normal.json
  请求头 Authorization=Token，JSON body {"keyword": "关键词"}
"""
import hashlib
import json
import time
import urllib.parse
import urllib.request

QCC_URL = "https://openapi.qcc.com/data/api/ent_search/getSearchByKey"
TYC_URL = "https://open.api.tianyancha.com/services/open/ic/baseinfo/normal.json"


def _md5_upper(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest().upper()


def _fetch_json(url, method, headers, payload=None, timeout=20):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={**headers, "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def qcc_query(app_key, secret_key, keyword, base_url=QCC_URL):
    if not app_key or not secret_key:
        raise ValueError("未配置企查查 AppKey / SecretKey（设置 → 工商信息查询）")
    timespan = str(int(time.time() * 1000))
    token = _md5_upper(app_key + timespan + secret_key)
    url = base_url + "?" + urllib.parse.urlencode({"key": keyword})
    data = _fetch_json(url, "POST", {"AppKey": app_key, "Timespan": timespan, "Token": token})
    if str(data.get("Status")) != "200":
        raise ValueError("企查查：" + str(data.get("Message") or data.get("Msg") or data)[:200])
    rows = data.get("Result") or []
    if not rows:
        raise ValueError("企查查：未找到该公司，请检查公司名称")
    r = rows[0]
    return {
        "source": "企查查",
        "company": str(r.get("Name", "") or ""),
        "credit_code": str(r.get("CreditCode", "") or ""),
        "legal_person": str(r.get("OperName", "") or ""),
        "reg_capital": str(r.get("RegCapital", "") or ""),
        "estiblish_time": str(r.get("StartDate", "") or ""),
        "reg_status": str(r.get("Status", "") or ""),
        "address": str(r.get("Address", "") or ""),
        "phone": str(r.get("PhoneNumber", "") or ""),
        "email": str(r.get("Email", "") or ""),
    }


def tyc_query(token, keyword, base_url=TYC_URL):
    if not token:
        raise ValueError("未配置天眼查 Token（设置 → 工商信息查询）")
    data = _fetch_json(base_url, "POST", {"Authorization": token}, payload={"keyword": keyword})
    if data.get("error_code") not in (0, None):
        raise ValueError("天眼查：" + str(data.get("reason") or data.get("error_code"))[:200])
    r = data.get("result") or {}
    if not r:
        raise ValueError("天眼查：未找到该公司，请检查公司名称")
    return {
        "source": "天眼查",
        "company": str(r.get("name", "") or ""),
        "credit_code": str(r.get("creditCode", "") or ""),
        "legal_person": str(r.get("legalPersonName", "") or ""),
        "reg_capital": str(r.get("regCapital", "") or ""),
        "estiblish_time": str(r.get("estiblishTime", "") or ""),
        "reg_status": str(r.get("regStatus", "") or ""),
        "address": str(r.get("regLocation", "") or r.get("address", "") or ""),
        "phone": str(r.get("phoneNumber", "") or ""),
        "email": str(r.get("email", "") or ""),
    }


def query_company(settings, keyword, provider="auto", base_urls=None):
    """按设置查询工商信息。provider: auto / qcc / tyc。"""
    base_urls = base_urls or {}
    if provider == "auto":
        providers = []
        if settings.get("tyc_token"):
            providers.append("tyc")
        if settings.get("qcc_app_key"):
            providers.append("qcc")
        if not providers:
            raise ValueError("未配置企查查/天眼查密钥（设置 → 工商信息查询）")
    else:
        providers = [provider]
    errs = []
    for p in providers:
        try:
            if p == "qcc":
                return qcc_query(
                    settings.get("qcc_app_key", ""),
                    settings.get("qcc_secret_key", ""),
                    keyword,
                    base_urls.get("qcc", QCC_URL),
                )
            return tyc_query(settings.get("tyc_token", ""), keyword, base_urls.get("tyc", TYC_URL))
        except Exception as e:
            errs.append(str(e))
    raise ValueError("；".join(errs))
