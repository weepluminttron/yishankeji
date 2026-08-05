# -*- coding: utf-8 -*-
"""光纤行业获客工具 - 本地服务入口。

启动：python server.py   （或双击 启动工具.bat）
浏览器访问：http://127.0.0.1:8017
"""
import json
import mimetypes
import os
import re
import sys
import tempfile
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import ai, crawler, db, importer, mailer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEFAULT_PORT = 8017


def send_json(handler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
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
        "type": qs.get("type", [""])[0],
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

    # ---------- 路由 ----------
    def do_GET(self):
        try:
            self._route_get()
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
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
            traceback.print_exc()
            try:
                send_json(self, {"ok": False, "msg": "服务器内部错误"}, 500)
            except Exception:
                pass

    def do_PUT(self):
        try:
            self._route_put()
        except Exception:
            traceback.print_exc()
            try:
                send_json(self, {"ok": False, "msg": "服务器内部错误"}, 500)
            except Exception:
                pass

    def do_DELETE(self):
        try:
            self._route_delete()
        except Exception:
            traceback.print_exc()
            try:
                send_json(self, {"ok": False, "msg": "服务器内部错误"}, 500)
            except Exception:
                pass

    def _route_get(self):
        parts, parsed = self._path_parts()
        qs = urllib.parse.parse_qs(parsed.query)
        if not parts:
            return self._serve_static("index.html")
        if parts[0] == "static":
            return self._serve_static("/".join(parts[1:]))
        if parts[0] != "api":
            return self._serve_static("index.html")
        api = parts[1] if len(parts) > 1 else ""
        if api == "summary":
            return send_json(self, db.summary())
        if api == "leads":
            page = int(qs.get("page", ["1"])[0] or 1)
            size = min(int(qs.get("size", ["20"])[0] or 20), 200)
            items, total = db.list_leads(page=page, size=size, **lead_filters(qs))
            return send_json(self, {"items": items, "total": total, "page": page, "size": size})
        if api == "leads" and len(parts) > 2 and parts[2] == "export":
            return self._export(qs)
        if api == "leads" and len(parts) > 2 and parts[2] == "template":
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
        if api == "settings":
            return send_json(self, {"ok": True, "settings": db.get_settings()})
        if api == "mail":
            return send_json(self, {"ok": True, "logs": db.list_mail_logs()})
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
        if len(parts) < 2 or parts[0] != "api":
            return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)
        api = parts[1]
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
                leads, err = importer.parse_file(tmp_path, filename)
                if err:
                    return send_json(self, {"ok": False, "msg": err}, 400)
                result = db.bulk_add(leads, source="Excel导入")
                return send_json(self, {"ok": True, "total": len(leads), **result})
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
        if api == "crawl":
            data = read_json_body(self)
            candidates, err = crawler.crawl(
                url=data.get("url", ""), html_text=data.get("html", "")
            )
            return send_json(self, {"ok": not err, "candidates": candidates, "error": err})
        if api == "crawl" and len(parts) > 2 and parts[2] == "import":
            data = read_json_body(self)
            result = db.bulk_add(data.get("candidates", []), source="网页采集")
            return send_json(self, {"ok": True, **result})
        if api == "settings":
            data = read_json_body(self)
            settings = db.save_settings(data.get("settings", {}))
            return send_json(self, {"ok": True, "settings": settings})
        if api == "ai":
            data = read_json_body(self)
            settings = db.get_settings()
            if not settings.get("openai_api_key"):
                return send_json(self, {"ok": False, "msg": "还没有配置 AI 密钥（在“设置”里填写 OpenAI API Key）"}, 400)
            text, err = ai.generate_copy(
                settings.get("openai_api_key"),
                settings.get("openai_model"),
                data.get("system", ""),
                data.get("user", ""),
            )
            if err:
                return send_json(self, {"ok": False, "msg": err}, 400)
            return send_json(self, {"ok": True, "text": text})
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
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "leads":
            db.delete_lead(int(parts[2]))
            return send_json(self, {"ok": True})
        return send_json(self, {"ok": False, "msg": "接口不存在"}, 404)

    # ---------- 业务辅助 ----------
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


def main():
    db.init_db()
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 56)
    print("  光纤行业获客工具已启动")
    print(f"  请在浏览器打开： http://127.0.0.1:{port}")
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
