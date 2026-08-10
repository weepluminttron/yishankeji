# -*- coding: utf-8 -*-
"""光纤行业获客工具 - 本地服务入口。

启动：python server.py   （或双击 启动工具.bat）
浏览器访问：http://127.0.0.1:8017
"""
import hashlib
import hmac
import json
import mimetypes
import os
import random
import re
import secrets
import signal
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import ai, buyer, crawler, db, importer, llm_cache, mailer, notify, scorer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEFAULT_PORT = 8017
SESSION_TTL = 12 * 3600
_sessions = {}
_lp_requests = {}

SOCIAL_COPY = {
    "抖音评论引流": [
        "请问这款光缆支持室外直埋吗？我们最近正好有项目在找供应商，方便聊聊吗？",
        "熔接工艺讲得很清楚！我们做弱电工程的，能私信发一份产品资料吗？",
        "光纤收发器这个型号性价比确实不错，我们是集成商，想了解一下代理政策。",
        "博主您好，我们公司主营{{产品}}，想和您交流一下合作，方便留个联系方式吗？",
        "讲得太实用了，已关注。我们正在找靠谱的光纤配套供应商，可以认识一下吗？",
    ],
    "小红书评论": [
        "收藏了！请问这个方案在旧小区改造里也适用吗？我们做FTTH项目的～",
        "同款需求！正在对比供应商，求一份报价单参考下🙏",
        "写得真好，我们是工程商，经常用到这类产品，能私信交流下合作吗？",
        "求问：这种收发器支持长距离传输吗？我们有个监控项目想用。",
    ],
    "私信开场白": [
        "您好，看到您最近在了解{{产品}}，我这边是厂家直供，可以发一份样品和报价给您参考，不耽误您时间～",
        "您好，我是{{我方公司}}的销售，主营{{产品}}，支持样品和批量供货，方便的话加个微信，资料马上发给您。",
        "您好，刚看到您的需求，我们正好有现货和优惠价格，您方便的话我详细介绍一下？",
    ],
    "追粉话术": [
        "上次聊的{{产品}}资料已经整理好了，今天发您？",
        "您好，上次您问的报价我这边申请到了更优惠的政策，方便再聊两句吗？",
        "跟您同步下：那批货下周就到，到时第一时间通知您～",
    ],
}


def log_error(msg):
    try:
        err_dir = os.path.join(BASE_DIR, "data")
        os.makedirs(err_dir, exist_ok=True)
        with open(os.path.join(err_dir, "error.log"), "a", encoding="utf-8") as f:
            f.write("[" + time.strftime("%Y-%m-%d %H:%M:%S") + "]\n" + msg + "\n")
    except Exception:
        pass


def rate_allow(ip, limit=5, window=3600):
    """落地页提交频率限制：同一 IP 每小时最多 limit 次。"""
    now_t = time.time()
    lst = _lp_requests.setdefault(ip, [])
    lst = [t for t in lst if now_t - t < window]
    _lp_requests[ip] = lst
    if len(lst) >= limit:
        return False
    lst.append(now_t)
    return True


def run_auto_crawl_once(urls):
    """立即执行一次定时采集，返回汇总结果。"""
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.splitlines() if u.strip()]
    urls = [u for u in (urls or []) if str(u).strip()]
    if not urls:
        return {"urls": 0, "found": 0, "added": 0, "skipped": 0, "errors": 0, "logs": []}
    total_added = 0
    logs = []
    for url in urls:
        candidates, err = crawler.crawl(url=url)
        if err:
            db.add_auto_crawl_log(url, 0, 0, 0, err)
            logs.append({"url": url, "found": 0, "added": 0, "skipped": 0, "error": err})
            continue
        result = db.bulk_add(candidates, source="定时采集")
        added = len(result["added"])
        skipped = len(result["duplicates"]) + len(result["errors"])
        total_added += added
        db.add_auto_crawl_log(url, len(candidates), added, skipped, "")
        logs.append({"url": url, "found": len(candidates), "added": added, "skipped": skipped, "error": ""})
    db.save_settings({"last_auto_crawl": db.now()})
    if total_added > 0:
        settings = db.get_settings()
        if settings.get("notify_webhook"):
            notify.send_webhook(
                settings.get("notify_webhook"),
                "🔔 定时采集发现新客户",
                f"本次自动采集新增 {total_added} 条线索，记得去跟进哦～",
            )
    return {
        "urls": len(urls),
        "found": sum(l["found"] for l in logs),
        "added": total_added,
        "skipped": sum(l["skipped"] for l in logs),
        "errors": sum(1 for l in logs if l["error"]),
        "logs": logs,
    }


def _auto_crawl_loop():
    """后台线程：按设置的时间间隔自动采集线索。"""
    while True:
        try:
            s = db.get_settings()
            interval = int(s.get("auto_crawl_interval") or 0)
            if interval > 0 and s.get("auto_crawl_urls", "").strip():
                last = s.get("last_auto_crawl") or ""
                due = False
                if not last:
                    due = True
                else:
                    try:
                        from datetime import datetime
                        lt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                        due = (datetime.now() - lt).total_seconds() >= interval * 3600
                    except Exception:
                        due = True
                if due:
                    run_auto_crawl_once(s.get("auto_crawl_urls", ""))
        except Exception:
            pass
        time.sleep(60)


def _load_env_file():
    """读取 server.env（可选），支持 HOST / PORT / ACCESS_PASSWORD。
    server.env 的配置优先于 systemd 环境变量，方便只改文件不碰服务。"""
    env_path = os.path.join(BASE_DIR, "server.env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()


def _pw_hash(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def auth_enabled():
    if os.environ.get("ACCESS_PASSWORD"):
        return True
    s = db.get_settings()
    return bool(s.get("access_password_hash") or s.get("access_password"))


def check_password(pw):
    env_pw = os.environ.get("ACCESS_PASSWORD", "")
    if env_pw and hmac.compare_digest(pw, env_pw):
        return True
    s = db.get_settings()
    if s.get("access_password_hash") and hmac.compare_digest(_pw_hash(pw), s["access_password_hash"]):
        return True
    if s.get("access_password") and hmac.compare_digest(pw, s["access_password"]):
        return True
    return False


def send_json(handler, obj, code=200, headers=None):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    for k, v in (headers or {}).items():
        handler.send_header(k, v)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def parse_multipart(handler, field_name):
    """极简 multipart/form-data 解析，返回 (字段dict, 文件名, 临时文件路径)。"""
    ctype = handler.headers.get("Content-Type", "")
    m = re.search(r"boundary=(.+)", ctype)
    if not m:
        return {}, "", None
    boundary = m.group(1).strip('"')
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length)
    sep = b"--" + boundary.encode()
    parts = raw.split(sep)
    fields = {}
    filename = ""
    tmp_path = None
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        head_text = head.decode("utf-8", errors="replace")
        name_m = re.search(r'name="([^"]+)"', head_text)
        if not name_m:
            continue
        name = name_m.group(1)
        file_m = re.search(r'filename="([^"]*)"', head_text)
        if file_m:
            filename = file_m.group(1)
            fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(filename)[1] or ".xlsx")
            with os.fdopen(fd, "wb") as f:
                f.write(body)
        else:
            fields[name] = body.decode("utf-8", errors="replace")
    return fields, filename, tmp_path


def lead_filters(qs):
    return {
        "q": qs.get("q", [""])[0],
        "status": qs.get("status", [""])[0],
        "type_": qs.get("type", [""])[0],
        "region": qs.get("region", [""])[0],
        "tag": qs.get("tag", [""])[0],
        "source": qs.get("source", [""])[0],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LeadTool/1.0"

    def log_message(self, fmt, *args):
        pass

    def _path_parts(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        parts = [p for p in path.split("/") if p]
        return parts, parsed

    def _cookie_token(self):
        m = re.search(r"yskt_session=([0-9a-f]+)", self.headers.get("Cookie", ""))
        return m.group(1) if m else ""

    def _client_ip(self):
        return self.client_address[0] if self.client_address else ""

    def _ua(self):
        return self.headers.get("User-Agent", "")[:200]

    def _issue_session(self):
        tok = secrets.token_hex(24)
        _sessions[tok] = time.time() + SESSION_TTL
        return f"yskt_session={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"

    def _check_auth(self):
        if not auth_enabled():
            return True
        tok = self._cookie_token()
        return bool(tok) and _sessions.get(tok, 0) > time.time()

    def _require_auth(self, parts):
        """API 鉴权：登录/会话/健康检查/落地页/元数据除外。"""
        if parts and parts[0] == "api":
            api_name = parts[1] if len(parts) > 1 else ""
            if api_name not in ("login", "session", "health", "meta", "lp"):
                if not self._check_auth():
                    return send_json(self, {"ok": False, "msg": "请先登录", "need_login": True}, 401)
        return None

    # ---------- 路由 ----------
    def do_GET(self):
        try:
            self._route_get()
        except BrokenPipeError:
            pass
        except Exception:
            log_error(traceback.format_exc())
            try:
                send_json(self, {"ok": False, "msg": "服务器内部错误"}, 500)
            except Exception:
                pass

    def do_POST(self):
        try:
            self._route_post()
        except BrokenPipeError:
            pass
        except Exception:
            log_error(traceback.format_exc())
            try:
                send_json(self, {"ok": False, "msg": "服务器内部错误"}, 500)
            except Exception:
                pass

    def do_PUT(self):
        try:
            self._route_put()
        except Exception:
            log_error(traceback.format_exc())
            try:
                send_json(self, {"ok": False, "msg": "服务器内部错误"}, 500)
            except Exception:
                pass

    def do_DELETE(self):
        try:
            self._route_delete()
        except Exception:
            log_error(traceback.format_exc())
            try:
                send_json(self, {"ok": False, "msg": "服务器内部错误"}, 500)
            except Exception:
                pass

    def _route_get(self):
        parts, parsed = self._path_parts()
        qs = urllib.parse.parse_qs(parsed.query)
        denied = self._require_auth(parts)
        if denied:
            return denied
        if not parts:
            return self._serve_static("index.html")
        if parts[0] == "static":
            return self._serve_static("/".join(parts[1:]))
        if parts[0] == "manifest.json":
            return self._serve_static("manifest.json")
        if parts[0] == "sw.js":
            self.send_response(200)
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            with open(os.path.join(STATIC_DIR, "sw.js"), "r", encoding="utf-8") as f:
                body = f.read().encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parts[0] != "api":
            if parts[0] == "lp":
                return self._serve_landing()
            return self._serve_static("index.html")
        api = parts[1] if len(parts) > 1 else ""
        if api == "summary":
            return send_json(self, db.summary())
        if api == "session":
            authed = self._check_auth()
            headers = None
            if not authed and auth_enabled():
                s = db.get_settings()
                if s.get("auto_login_trusted", "1") == "1" and db.is_trusted_ip(self._client_ip()):
                    headers = {"Set-Cookie": self._issue_session()}
                    authed = True
                    db.add_login_log(self._client_ip(), self._ua(), "自动登录（信任IP）", "成功")
            return send_json(self, {
                "ok": True,
                "authed": authed,
                "password_set": auth_enabled(),
            }, headers=headers)
        if api == "health":
            return send_json(self, {"ok": True, "status": "up"})
        if api == "lp":
            return send_json(self, {"ok": True, "config": self._lp_config()})
        if api == "leads" and len(parts) > 2 and parts[2] == "export":
            return self._export(qs)
        if api == "leads" and len(parts) > 2 and parts[2] == "template":
            kind = qs.get("kind", [""])[0]
            if kind == "social":
                buf = importer.build_social_template_xlsx()
                self._send_bytes(
                    buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "社媒评论导入模板.xlsx",
                )
                return
            if kind == "wechat":
                self._send_bytes(
                    importer.build_wechat_template_txt(),
                    "text/plain; charset=utf-8",
                    "微信记录导入模板.txt",
                )
                return
            buf = importer.build_template_xlsx()
            self._send_bytes(
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "线索导入模板.xlsx",
            )
            return
        if api == "leads" and len(parts) > 2:
            lead_id = int(parts[2])
            if len(parts) > 3 and parts[3] == "history":
                notes, events = db.lead_history(lead_id)
                return send_json(self, {"notes": notes, "events": events})
            lead = db.get_lead(lead_id)
            if not lead:
                return send_json(self, {"ok": False, "msg": "线索不存在"}, 404)
            return send_json(self, lead)
        if api == "leads":
            page = int(qs.get("page", ["1"])[0] or 1)
            size = min(int(qs.get("size", ["20"])[0] or 20), 200)
            items, total = db.list_leads(page=page, size=size, **lead_filters(qs))
            return send_json(self, {"items": items, "total": total, "page": page, "size": size})
        if api == "settings":
            return send_json(self, {"ok": True, "settings": db.get_settings()})
        if api == "mail":
            return send_json(self, {"ok": True, "logs": db.list_mail_logs()})
        if api == "crawl" and len(parts) > 2 and parts[2] == "auto":
            settings = db.get_settings()
            return send_json(self, {
                "ok": True,
                "logs": db.list_auto_crawl_logs(),
                "last_run": settings.get("last_auto_crawl", ""),
            })
        if api == "copy" and len(parts) > 2 and parts[2] == "social":
            return send_json(self, {"ok": True, "list": SOCIAL_COPY})
        if api == "trusted":
            return send_json(self, {
                "ok": True,
                "trusted": db.list_trusted_ips(),
                "logs": db.list_login_logs(),
                "auto_login_trusted": db.get_settings().get("auto_login_trusted", "1"),
            })
        if api == "meta":
            return send_json(self, {
                "statuses": db.STATUSES,
                "types": db.TYPES,
                "tags": db.DEFAULT_TAGS,
            })
        return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)

    def _route_post(self):
        parts, parsed = self._path_parts()
        qs = urllib.parse.parse_qs(parsed.query)
        denied = self._require_auth(parts)
        if denied:
            return denied
        if len(parts) < 2 or parts[0] != "api":
            return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)
        api = parts[1]
        if api == "login":
            data = read_json_body(self)
            if not auth_enabled():
                return send_json(self, {"ok": True, "msg": "未启用密码保护"})
            if check_password(data.get("password", "")):
                db.add_login_log(self._client_ip(), self._ua(), "密码登录", "成功")
                db.trust_ip(self._client_ip(), self._ua())
                return send_json(
                    self, {"ok": True}, headers={"Set-Cookie": self._issue_session()},
                )
            db.add_login_log(self._client_ip(), self._ua(), "密码错误", "失败")
            return send_json(self, {"ok": False, "msg": "密码不正确"}, 401)
        if api == "logout":
            tok = self._cookie_token()
            if tok:
                _sessions.pop(tok, None)
            return send_json(
                self, {"ok": True}, headers={
                    "Set-Cookie": "yskt_session=; Path=/; HttpOnly; Max-Age=0",
                },
            )
        if api == "lp" and len(parts) > 2 and parts[2] == "submit":
            return self._lp_submit()
        if api == "buyer":
            data = read_json_body(self)
            result = buyer.run(
                keywords=data.get("keywords", ""),
                markets=data.get("markets", ""),
                max_results=int(data.get("max_results", 6) or 6),
                urls=data.get("urls", ""),
                use_ai=bool(data.get("use_ai")),
                settings=db.get_settings(),
            )
            return send_json(self, {"ok": True, **result})
        if api == "copy" and len(parts) > 2 and parts[2] == "social":
            data = read_json_body(self)
            return self._social_copy(data)
        if api == "leads" and len(parts) == 2:
            data = read_json_body(self)
            lead, err = db.create_lead(data)
            if err:
                return send_json(self, {"ok": False, "msg": err}, 400)
            return send_json(self, {"ok": True, "lead": lead})
        if api == "leads" and len(parts) > 2 and parts[2] == "bulk":
            data = read_json_body(self)
            result = db.bulk_add(data.get("leads", []), source=data.get("source", "批量导入"))
            return send_json(self, {"ok": True, **result})
        if api == "leads" and len(parts) > 2 and parts[2] == "bulk_status":
            data = read_json_body(self)
            return send_json(self, db.bulk_status(data.get("ids", []), data.get("status", "")))
        if api == "leads" and len(parts) > 2 and parts[2] == "contacted":
            data = read_json_body(self)
            db.mark_contacted(int(data.get("id", 0)))
            return send_json(self, {"ok": True})
        if api == "leads" and len(parts) > 2 and parts[2] == "score":
            data = read_json_body(self)
            lead_id = int(data.get("id", 0))
            lead = db.get_lead(lead_id)
            if not lead:
                return send_json(self, {"ok": False, "msg": "线索不存在"}, 404)
            use_ai = bool(data.get("use_ai"))
            score, reason = scorer.rule_score(lead)
            if use_ai:
                settings = db.get_settings()
                ai_score, ai_reason = scorer.ai_score(settings, lead)
                if ai_score is not None:
                    score, reason = ai_score, ai_reason
                elif data.get("fallback", True):
                    reason = reason + "（AI：" + ai_reason + "）"
                else:
                    return send_json(self, {"ok": False, "msg": ai_reason}, 400)
            db.set_lead_score(lead_id, score, reason)
            return send_json(self, {"ok": True, "score": score, "reason": reason})
        if api == "leads" and len(parts) > 2:
            lead_id = int(parts[2])
            if len(parts) > 3 and parts[3] == "notes":
                data = read_json_body(self)
                db.add_note(lead_id, data.get("content", ""))
                return send_json(self, {"ok": True})
            return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)
        if api == "import":
            fields, filename, tmp_path = parse_multipart(self, "file")
            if not tmp_path:
                return send_json(self, {"ok": False, "msg": "没有收到文件"}, 400)
            try:
                kind = fields.get("kind", "")
                if kind == "social":
                    leads, err = importer.parse_social(tmp_path)
                elif kind == "wechat":
                    leads, err = importer.parse_wechat(tmp_path)
                else:
                    leads, err = importer.parse_file(tmp_path, filename)
                if err:
                    return send_json(self, {"ok": False, "msg": err}, 400)
                source = {"social": "社媒评论", "wechat": "微信记录"}.get(kind, "Excel导入")
                result = db.bulk_add(leads, source=source)
                return send_json(self, {"ok": True, "total": len(leads), **result})
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
        if api == "crawl" and len(parts) > 2 and parts[2] == "auto":
            data = read_json_body(self)
            result = run_auto_crawl_once(data.get("urls"))
            return send_json(self, {"ok": True, **result})
        if api == "crawl" and len(parts) > 2 and parts[2] == "import":
            data = read_json_body(self)
            result = db.bulk_add(data.get("candidates", []), source="网页采集")
            return send_json(self, {"ok": True, **result})
        if api == "crawl":
            data = read_json_body(self)
            candidates, err = crawler.crawl(
                url=data.get("url", ""), html_text=data.get("html", "")
            )
            return send_json(self, {"ok": not err, "candidates": candidates, "error": err})
        if api == "settings":
            data = read_json_body(self)
            settings = db.save_settings(data.get("settings", {}))
            return send_json(self, {"ok": True, "settings": settings})
        if api == "ai":
            data = read_json_body(self)
            settings = db.get_settings()
            if not settings.get("openai_api_key"):
                return send_json(self, {"ok": False, "msg": "还没有配置 AI 密钥（在“设置”里填写 OpenAI API Key）"}, 400)
            cache_key = llm_cache.make_key(
                settings.get("openai_model"),
                settings.get("openai_api_base"),
                data.get("system", ""),
                data.get("user", ""),
            )
            cached = llm_cache.cache_get(cache_key)
            if cached:
                return send_json(self, {"ok": True, "text": cached, "from_cache": True})
            text, err = ai.generate_copy(
                settings.get("openai_api_key"),
                settings.get("openai_model"),
                data.get("system", ""),
                data.get("user", ""),
                settings.get("openai_api_base", ""),
            )
            if err:
                return send_json(self, {"ok": False, "msg": err}, 400)
            llm_cache.cache_set(cache_key, text)
            return send_json(self, {"ok": True, "text": text})
        if api == "notify" and len(parts) > 2 and parts[2] == "test":
            settings = db.get_settings()
            ok, err = notify.send_webhook(
                settings.get("notify_webhook", ""),
                "🔔 获客助手通知测试",
                "如果收到这条消息，说明通知配置正常 ✅",
            )
            if not ok:
                return send_json(self, {"ok": False, "msg": "发送失败：" + err}, 400)
            return send_json(self, {"ok": True})
        if api == "mail" and len(parts) > 2 and parts[2] == "test":
            settings = db.get_settings()
            missing = mailer.validate_settings(settings)
            if missing:
                return send_json(self, {"ok": False, "msg": "请先填写：" + "、".join(missing)}, 400)
            ok, err = mailer.send_one(
                settings, settings.get("smtp_user"),
                "获客工具测试邮件",
                "这是一封测试邮件，说明 SMTP 配置可用。",
            )
            if not ok:
                return send_json(self, {"ok": False, "msg": err}, 400)
            return send_json(self, {"ok": True})
        if api == "mail":
            data = read_json_body(self)
            return self._send_mails(data)
        return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)

    def _route_put(self):
        parts, parsed = self._path_parts()
        denied = self._require_auth(parts)
        if denied:
            return denied
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "leads":
            lead_id = int(parts[2])
            data = read_json_body(self)
            lead, err = db.update_lead(lead_id, data)
            if err:
                return send_json(self, {"ok": False, "msg": err}, 400)
            return send_json(self, {"ok": True, "lead": lead})
        return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)

    def _route_delete(self):
        parts, parsed = self._path_parts()
        denied = self._require_auth(parts)
        if denied:
            return denied
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "trusted":
            db.untrust_ip(urllib.parse.unquote(parts[2]))
            return send_json(self, {"ok": True})
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "leads":
            db.delete_lead(int(parts[2]))
            return send_json(self, {"ok": True})
        return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)

    # ---------- 业务辅助 ----------
    def _social_copy(self, data):
        scenario = data.get("scenario", "抖音评论引流")
        count = max(1, min(5, int(data.get("count", 3) or 3)))
        if scenario not in SOCIAL_COPY:
            return send_json(self, {"ok": False, "msg": "话术场景不存在"}, 400)
        if data.get("use_ai"):
            settings = db.get_settings()
            if not settings.get("openai_api_key"):
                return send_json(self, {"ok": False, "msg": "未配置 AI 密钥，请先到“设置”填写"}, 400)
            system = (
                "你是一名光纤通信行业的资深运营，擅长写自然、不硬广的社媒引流话术。"
                "话术要简短口语化，符合平台语境，不要虚构电话和微信号。"
            )
            user = f"场景：{scenario}\n主营产品：{settings.get('product_name','')}\n公司：{settings.get('company_name','')}\n请生成 {count} 条不同的话术，每行一条。"
            text, err = ai.generate_copy(
                settings.get("openai_api_key"),
                settings.get("openai_model"),
                system,
                user,
                settings.get("openai_api_base", ""),
            )
            if err:
                return send_json(self, {"ok": False, "msg": err}, 400)
            texts = [t.strip() for t in text.splitlines() if t.strip()]
            return send_json(self, {"ok": True, "texts": texts, "ai": True})
        s = db.get_settings()
        pool = [c.replace("{{产品}}", s.get("product_name", "")).replace("{{我方公司}}", s.get("company_name", "")) for c in SOCIAL_COPY[scenario]]
        random.shuffle(pool)
        return send_json(self, {"ok": True, "texts": pool[:count], "ai": False})

    def _export(self, qs):
        filters = lead_filters(qs)
        rows = db.all_leads(filters)
        fmt = qs.get("fmt", ["xlsx"])[0]
        if fmt == "csv":
            self._send_bytes(
                importer.export_csv(rows).encode("utf-8-sig"),
                "text/csv; charset=utf-8",
                "客户线索.csv",
            )
        else:
            self._send_bytes(
                importer.export_xlsx(rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "客户线索.xlsx",
            )

    def _send_mails(self, data):
        settings = db.get_settings()
        missing = mailer.validate_settings(settings)
        if missing:
            return send_json(self, {"ok": False, "msg": "请先在“设置”里填写：" + "、".join(missing)}, 400)
        lead_ids = data.get("lead_ids", [])
        if not lead_ids:
            return send_json(self, {"ok": False, "msg": "请选择要发送的线索"}, 400)
        subject_tpl = data.get("subject", "")
        body_tpl = data.get("body", "")
        if not subject_tpl or not body_tpl:
            return send_json(self, {"ok": False, "msg": "请填写邮件主题和正文"}, 400)
        results = {"ok": True, "sent": 0, "failed": 0, "errors": []}
        for lid in lead_ids:
            lead = db.get_lead(lid)
            if not lead or not lead.get("email"):
                results["failed"] += 1
                results["errors"].append({"id": lid, "name": lead["name"] if lead else str(lid), "msg": "缺少邮箱"})
                continue
            subject = mailer.personalize(subject_tpl, lead, settings)
            body = mailer.personalize(body_tpl, lead, settings)
            ok, err = mailer.send_one(settings, lead["email"], subject, body)
            if ok:
                results["sent"] += 1
                db.add_mail_log(lid, lead["name"], lead["email"], subject, body, "成功")
                db.mark_contacted(lid)
            else:
                results["failed"] += 1
                db.add_mail_log(lid, lead["name"], lead["email"], subject, body, "失败", err)
                results["errors"].append({"id": lid, "name": lead["name"], "msg": err})
        return send_json(self, results)

    # ---------- 静态文件 ----------
    def _serve_static(self, rel):
        safe = os.path.normpath(rel)
        if safe.startswith("..") or os.path.isabs(safe):
            return send_json(self, {"ok": False, "msg": "禁止访问"}, 403)
        path = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(path):
            return send_json(self, {"ok": False, "msg": "文件不存在"}, 404)
        ctype, _ = mimetypes.guess_type(path)
        ctype = ctype or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body, ctype, filename):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(filename)}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- 落地页 ----------
    def _lp_config(self):
        s = db.get_settings()
        return {
            "enabled": s.get("lp_enabled", "1") == "1",
            "title": s.get("lp_title", ""),
            "subtitle": s.get("lp_subtitle", ""),
            "cta": s.get("lp_cta", "立即获取报价"),
            "company": s.get("company_name", ""),
            "product": s.get("product_name", ""),
            "phone": s.get("lp_phone", ""),
            "thanks": s.get("lp_thanks", ""),
        }

    def _serve_landing(self):
        cfg = self._lp_config()
        if not cfg["enabled"]:
            body = "落地页已停用，请在后台“设置”中开启。".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = os.path.join(STATIC_DIR, "landing.html")
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        repl = {
            "__TITLE__": cfg["title"],
            "__SUBTITLE__": cfg["subtitle"],
            "__CTA__": cfg["cta"],
            "__COMPANY__": cfg["company"],
            "__PRODUCT__": cfg["product"],
            "__PHONE__": cfg["phone"],
            "__THANKS__": cfg["thanks"],
        }
        for k, v in repl.items():
            html = html.replace(k, v)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _lp_submit(self):
        data = read_json_body(self)
        if data.get("website"):  # 蜜罐：机器人填写的隐藏字段
            return send_json(self, {"ok": True})
        s = db.get_settings()
        if s.get("lp_enabled", "1") != "1":
            return send_json(self, {"ok": False, "msg": "表单暂未开放"}, 400)
        ip = self.client_address[0] if self.client_address else "unknown"
        if not rate_allow(ip):
            return send_json(self, {"ok": False, "msg": "提交过于频繁，请稍后再试"}, 429)
        name = str(data.get("name", "")).strip()
        company = str(data.get("company", "")).strip()
        phone = str(data.get("phone", "")).strip()
        email = str(data.get("email", "")).strip()
        region = str(data.get("region", "")).strip()
        need = str(data.get("need", "")).strip()
        if not name:
            return send_json(self, {"ok": False, "msg": "请填写称呼/姓名"}, 400)
        if not re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", phone) and not re.search(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)", phone):
            return send_json(self, {"ok": False, "msg": "请填写正确的联系电话"}, 400)
        lead, err = db.create_lead({
            "name": company or name + "（个人）",
            "contact": name,
            "phone": phone,
            "email": email,
            "region": region,
            "type": "终端客户" if not company else "其他",
            "source": "落地页表单",
            "note": ("需求：" + need) if need else "来自获客落地页表单",
            "tags": "落地页",
        })
        if err:
            if "重复" in err:
                return send_json(self, {"ok": True})
            return send_json(self, {"ok": False, "msg": err}, 400)
        s = db.get_settings()
        if s.get("notify_webhook"):
            notify.send_webhook(
                s.get("notify_webhook"),
                "🎉 官网收到新客户留资",
                notify.lead_notice_text(lead),
            )
        return send_json(self, {"ok": True, "thanks": s.get("lp_thanks", "")})


def main():
    _load_env_file()
    db.init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=_auto_crawl_loop, daemon=True).start()

    def _handle_stop(sig, frame):
        print("\n收到停止信号，正在安全关闭...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    print("=" * 56)
    print("  光纤行业获客工具已启动")
    print(f"  管理后台： http://{host}:{port}")
    print(f"  获客落地页： http://{host}:{port}/lp")
    print("  访问密码：" + ("已启用" if auth_enabled() else "未启用（仅本机建议）"))
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
