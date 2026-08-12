# -*- coding: utf-8 -*-
"""本地测试：企查查/天眼查 接入逻辑（使用本地模拟接口，不访问真实 API）。"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, ".")
from core import company_api


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode("utf-8")

    def do_POST(self):
        raw = self._body()
        if self.path.startswith("/qcc"):
            key = self.headers.get("AppKey", "")
            ts = self.headers.get("Timespan", "")
            tok = self.headers.get("Token", "")
            expect = company_api._md5_upper("KEYA" + ts + "SECA")
            if key == "KEYA" and tok == expect:
                body = {
                    "Status": "200",
                    "Result": [{
                        "Name": "深圳一善科技有限公司",
                        "OperName": "张三",
                        "CreditCode": "91440300MA5TEST",
                        "RegCapital": "500万元人民币",
                        "StartDate": "2015-06-01",
                        "Status": "存续",
                        "Address": "深圳市南山区科技园",
                        "PhoneNumber": "0755-88888888",
                        "Email": "a@example.com",
                    }],
                }
            else:
                body = {"Status": "401", "Message": "Token 错误"}
        elif self.path.startswith("/tyc"):
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
            if self.headers.get("Authorization") == "TOKENT" and payload.get("keyword") == "深圳一善科技有限公司":
                body = {
                    "error_code": 0,
                    "result": {
                        "name": "深圳一善科技有限公司",
                        "legalPersonName": "李四",
                        "creditCode": "91440300MA5TYC",
                        "regCapital": "1000万元人民币",
                        "estiblishTime": "2018-03-12",
                        "regStatus": "存续",
                        "regLocation": "深圳市宝安区",
                        "phoneNumber": "0755-66666666",
                        "email": "b@example.com",
                    },
                }
            else:
                body = {"error_code": 40004, "reason": "Token 无效"}
        else:
            body = {"error": "not found"}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = HTTPServer(("127.0.0.1", 0), MockHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"

settings = {"qcc_app_key": "KEYA", "qcc_secret_key": "SECA", "tyc_token": "TOKENT"}

# 1) 企查查
r = company_api.query_company(settings, "深圳一善科技有限公司", "qcc", {"qcc": base + "/qcc"})
assert r["source"] == "企查查" and r["company"] == "深圳一善科技有限公司" and r["legal_person"] == "张三"

# 2) 天眼查
r = company_api.query_company(settings, "深圳一善科技有限公司", "tyc", {"tyc": base + "/tyc"})
assert r["source"] == "天眼查" and r["credit_code"] == "91440300MA5TYC" and r["reg_capital"] == "1000万元人民币"

# 3) auto：企查查失败（改错密钥）自动切换天眼查
bad = {"qcc_app_key": "KEYA", "qcc_secret_key": "WRONG", "tyc_token": "TOKENT"}
r = company_api.query_company(bad, "深圳一善科技有限公司", "auto", {"qcc": base + "/qcc", "tyc": base + "/tyc"})
assert r["source"] == "天眼查"

# 4) auto：两家都失败 → 报错信息包含两家原因
empty = {"qcc_app_key": "KEYA", "qcc_secret_key": "WRONG", "tyc_token": "BAD"}
try:
    company_api.query_company(empty, "深圳一善科技有限公司", "auto", {"qcc": base + "/qcc", "tyc": base + "/tyc"})
    raise AssertionError("应当抛错")
except ValueError as e:
    assert "企查查" in str(e) and "天眼查" in str(e)

# 5) 未配置密钥 → 友好提示
try:
    company_api.query_company({}, "某某公司", "auto")
    raise AssertionError("应当抛错")
except ValueError as e:
    assert "未配置" in str(e)

print("company_api tests: ALL OK")
