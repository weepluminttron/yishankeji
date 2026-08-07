# -*- coding: utf-8 -*-
"""群机器人通知：飞书 / 企业微信自定义机器人 Webhook。

在群里添加“自定义机器人”获得 Webhook 地址，填入工具设置后，
新客户留资 / 定时采集到新线索时自动推送到群。
"""
import json
import urllib.request


def send_webhook(url, title, text):
    """发送文本消息，返回 (ok, error)。自动识别飞书 / 企业微信格式。"""
    if not url or not str(url).strip():
        return False, "未配置通知地址"
    url = str(url).strip()
    is_wecom = "qyapi.weixin.qq.com" in url or "qyapi.qq.com" in url
    if is_wecom:
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    else:
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        err = data.get("errcode")
        code = data.get("code")
        if err not in (None, 0) or code not in (None, 0):
            return False, body[:200]
        return True, ""
    except Exception as e:
        return False, str(e)


def lead_notice_text(lead):
    """把一条线索转成通知文本。"""
    lines = [
        f"🏢 {lead.get('name') or '未知客户'}",
    ]
    if lead.get("contact"):
        lines.append(f"👤 联系人：{lead['contact']}")
    if lead.get("phone"):
        lines.append(f"📞 电话：{lead['phone']}")
    if lead.get("email"):
        lines.append(f"📧 邮箱：{lead['email']}")
    if lead.get("region"):
        lines.append(f"📍 地区：{lead['region']}")
    if lead.get("note"):
        lines.append(f"📝 需求：{lead['note'][:120]}")
    if lead.get("source"):
        lines.append(f"🔍 来源：{lead['source']}")
    return "\n".join(lines)
