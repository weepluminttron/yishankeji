# -*- coding: utf-8 -*-
"""邮件发送：SMTP 支持 SSL / STARTTLS，带占位符个性化。"""
import re
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def personalize(template, lead, settings):
    vals = {
        "公司": lead.get("name", ""),
        "公司名": lead.get("name", ""),
        "联系人": lead.get("contact", "") or "客户",
        "称呼": lead.get("contact", "").split("先生")[0].split("女士")[0] or "客户",
        "地区": lead.get("region", ""),
        "产品": settings.get("product_name", "光纤产品"),
        "自己": settings.get("sender_name", "") or settings.get("company_name", ""),
        "我方公司": settings.get("company_name", ""),
    }
    out = template
    for k, v in vals.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def send_one(settings, to_addr, subject, body):
    """发送单封邮件，返回 (ok, error)。"""
    host = (settings.get("smtp_host") or "").strip()
    if not host:
        return False, "未配置 SMTP 服务器地址（请在“设置”里填写）"
    user = (settings.get("smtp_user") or "").strip()
    password = (settings.get("smtp_password") or "").strip()
    try:
        port = int(settings.get("smtp_port") or 465)
    except ValueError:
        port = 465
    ssl_mode = settings.get("smtp_ssl", "1") == "1"
    sender_name = (settings.get("sender_name") or settings.get("company_name") or "").strip()
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(sender_name, "utf-8")), user)) if sender_name else user
    msg["To"] = to_addr
    try:
        # local_hostname 固定为 localhost：避免 Windows 中文主机名导致 EHLO 编码失败
        if ssl_mode:
            server = smtplib.SMTP_SSL(host, port, timeout=20, local_hostname="localhost")
        else:
            server = smtplib.SMTP(host, port, timeout=20, local_hostname="localhost")
            server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
        server.quit()
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "邮箱账号或密码不正确"
    except Exception as e:
        return False, str(e)


def validate_settings(settings):
    missing = []
    if not settings.get("smtp_host"):
        missing.append("SMTP 服务器")
    if not settings.get("smtp_user"):
        missing.append("发信邮箱")
    if not settings.get("smtp_password"):
        missing.append("邮箱授权码/密码")
    return missing
