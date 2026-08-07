# -*- coding: utf-8 -*-
"""SQLite 数据层：线索、备注、事件、发送记录、设置。"""
import hashlib
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")

STATUSES = ["新线索", "待联系", "已联系", "跟进中", "已成交", "无效"]
TYPES = ["运营商", "工程商", "集成商", "分销商", "代工厂", "终端客户", "其他"]
DEFAULT_TAGS = ["光缆", "光纤收发器", "熔接服务", "FTTH", "机房改造", "弱电工程", "代工", "出口"]

_lock = threading.Lock()


def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                contact TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                region TEXT DEFAULT '',
                type TEXT DEFAULT '其他',
                status TEXT DEFAULT '新线索',
                source TEXT DEFAULT '手动录入',
                tags TEXT DEFAULT '',
                address TEXT DEFAULT '',
                note TEXT DEFAULT '',
                reminder_date TEXT DEFAULT '',
                last_contacted TEXT DEFAULT '',
                contact_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mail_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER,
                name TEXT DEFAULT '',
                email TEXT DEFAULT '',
                subject TEXT DEFAULT '',
                body TEXT DEFAULT '',
                status TEXT DEFAULT '成功',
                error TEXT DEFAULT '',
                sent_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auto_crawl_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                url TEXT DEFAULT '',
                found INTEGER DEFAULT 0,
                added INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                error TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ai_cache (
                key TEXT PRIMARY KEY,
                result TEXT,
                ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS trusted_ips (
                ip TEXT PRIMARY KEY,
                ua TEXT DEFAULT '',
                last_seen TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT DEFAULT '',
                ua TEXT DEFAULT '',
                action TEXT DEFAULT '',
                status TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
            CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
            CREATE INDEX IF NOT EXISTS idx_notes_lead ON notes(lead_id);
            """
        )
        conn.commit()
        # 兼容旧数据库：补充新增字段
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(leads)")]
        if "score" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN score INTEGER DEFAULT 0")
        if "score_reason" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN score_reason TEXT DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def norm_phone(p):
    if not p:
        return ""
    digits = re.sub(r"\D", "", str(p))
    return digits


def norm_name(n):
    if not n:
        return ""
    return re.sub(r"\s+", "", str(n)).lower()


def is_duplicate(name=None, phone=None, exclude_id=None):
    """按手机号（11 位）或公司名判重，返回已存在记录。"""
    if not name and not phone:
        return None
    conn = get_conn()
    try:
        sql = []
        params = []
        if phone and len(norm_phone(phone)) >= 7:
            sql.append("phone LIKE ?")
            params.append("%" + norm_phone(phone) + "%")
        if name:
            sql.append("name = ?")
            params.append(name.strip())
        if not sql:
            return None
        where = " OR ".join(sql)
        if exclude_id:
            where += " AND id != ?"
            params.append(exclude_id)
        row = conn.execute(
            f"SELECT id, name, phone FROM leads WHERE {where} ORDER BY id LIMIT 1",
            params,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_lead(data):
    fields = {
        "name": str(data.get("name", "")).strip(),
        "contact": str(data.get("contact", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "region": str(data.get("region", "")).strip(),
        "type": str(data.get("type", "其他")).strip() or "其他",
        "status": str(data.get("status", "新线索")).strip() or "新线索",
        "source": str(data.get("source", "手动录入")).strip() or "手动录入",
        "tags": str(data.get("tags", "")).strip(),
        "address": str(data.get("address", "")).strip(),
        "note": str(data.get("note", "")).strip(),
        "reminder_date": str(data.get("reminder_date", "")).strip(),
    }
    if not fields["name"]:
        return None, "公司名称不能为空"
    dup = is_duplicate(fields["name"], fields["phone"])
    if dup:
        return None, f"已存在重复线索（ID {dup['id']}：{dup['name']}）"
    fields["type"] = fields["type"] if fields["type"] in TYPES else "其他"
    fields["status"] = fields["status"] if fields["status"] in STATUSES else "新线索"
    ts = now()
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO leads (name, contact, phone, email, region, type, status,
               source, tags, address, note, reminder_date, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fields["name"], fields["contact"], fields["phone"], fields["email"],
                fields["region"], fields["type"], fields["status"], fields["source"],
                fields["tags"], fields["address"], fields["note"],
                fields["reminder_date"], ts, ts,
            ),
        )
        lead_id = cur.lastrowid
        conn.commit()
        add_event(lead_id, "创建线索", f"来源：{fields['source']}")
        if fields["note"]:
            add_note(lead_id, fields["note"])
        return get_lead(lead_id), None
    finally:
        conn.close()


def update_lead(lead_id, data):
    lead = get_lead(lead_id)
    if not lead:
        return None, "线索不存在"
    allowed = [
        "name", "contact", "phone", "email", "region", "type", "status",
        "source", "tags", "address", "note", "reminder_date",
    ]
    fields = {}
    old_status = lead["status"]
    old_reminder = lead["reminder_date"]
    for k in allowed:
        if k in data:
            fields[k] = str(data[k]).strip() if data[k] is not None else ""
    if "name" in fields and not fields["name"]:
        return None, "公司名称不能为空"
    dup = is_duplicate(fields.get("name", lead["name"]), fields.get("phone", lead["phone"]), exclude_id=lead_id)
    if dup:
        return None, f"已存在重复线索（ID {dup['id']}：{dup['name']}）"
    if "status" in fields and fields["status"] not in STATUSES:
        fields["status"] = old_status
    if "type" in fields and fields["type"] not in TYPES:
        fields["type"] = lead["type"]
    fields["updated_at"] = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [lead_id]
    conn = get_conn()
    try:
        conn.execute(f"UPDATE leads SET {sets} WHERE id = ?", params)
        conn.commit()
        if "status" in fields and fields["status"] != old_status:
            add_event(lead_id, "状态变更", f"{old_status} → {fields['status']}")
        if "reminder_date" in fields and fields["reminder_date"] != old_reminder:
            add_event(lead_id, "设置提醒", f"跟进日期：{fields['reminder_date'] or '无'}")
        return get_lead(lead_id), None
    finally:
        conn.close()


def delete_lead(lead_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        conn.commit()
    finally:
        conn.close()


def get_lead(lead_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_leads(page=1, size=20, q="", status="", type_="", region="", tag="", source=""):
    conn = get_conn()
    try:
        where = []
        params = []
        if q:
            like = f"%{q}%"
            where.append("(name LIKE ? OR contact LIKE ? OR phone LIKE ? OR email LIKE ? OR region LIKE ? OR note LIKE ?)")
            params += [like] * 6
        if status:
            where.append("status = ?")
            params.append(status)
        if type_:
            where.append("type = ?")
            params.append(type_)
        if region:
            where.append("region LIKE ?")
            params.append(f"%{region}%")
        if source:
            where.append("source = ?")
            params.append(source)
        if tag:
            where.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{tag},%")
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) FROM leads{where_sql}", params).fetchone()[0]
        offset = max(0, (page - 1) * size)
        rows = conn.execute(
            f"SELECT * FROM leads{where_sql} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def all_leads(filters=None):
    filters = filters or {}
    return list_leads(
        page=1, size=10 ** 9,
        q=filters.get("q", ""), status=filters.get("status", ""),
        type_=filters.get("type_", ""), region=filters.get("region", ""),
        tag=filters.get("tag", ""), source=filters.get("source", ""),
    )[0]


def bulk_add(leads, source="批量导入"):
    added, duplicates, errors = [], [], []
    for i, raw in enumerate(leads, start=1):
        if not isinstance(raw, dict) or not raw.get("name"):
            errors.append({"row": i, "msg": "缺少公司名称"})
            continue
        lead, err = create_lead({**raw, "source": raw.get("source") or source})
        if err:
            if "重复" in err:
                duplicates.append({"row": i, "msg": err, "name": raw.get("name")})
            else:
                errors.append({"row": i, "msg": err, "name": raw.get("name")})
        else:
            added.append(lead)
    return {"added": added, "duplicates": duplicates, "errors": errors}


def bulk_status(ids, status):
    if status not in STATUSES:
        return {"ok": False, "msg": "无效状态"}
    conn = get_conn()
    try:
        ts = now()
        for lid in ids:
            lead = get_lead(lid)
            if not lead or lead["status"] == status:
                continue
            conn.execute("UPDATE leads SET status = ?, updated_at = ? WHERE id = ?", (status, ts, lid))
        conn.commit()
        for lid in ids:
            lead = get_lead(lid)
            if not lead:
                continue
            add_event(lid, "状态变更", f"{lead['status']} → {status}")
        return {"ok": True}
    finally:
        conn.close()


def mark_contacted(lead_id):
    conn = get_conn()
    try:
        lead = get_lead(lead_id)
        if not lead:
            return
        conn.execute(
            "UPDATE leads SET last_contacted = ?, contact_count = contact_count + 1, updated_at = ? WHERE id = ?",
            (today_str(), now(), lead_id),
        )
        conn.commit()
        add_event(lead_id, "完成联系", "记录一次触达")
    finally:
        conn.close()


def set_lead_score(lead_id, score, reason=""):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE leads SET score = ?, score_reason = ?, updated_at = ? WHERE id = ?",
            (score, reason, now(), lead_id),
        )
        conn.commit()
        add_event(lead_id, "线索评分", f"{score}分 - {reason}")
    finally:
        conn.close()


def add_note(lead_id, content):
    if not content:
        return
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO notes (lead_id, content, created_at) VALUES (?,?,?)",
            (lead_id, content, now()),
        )
        conn.commit()
    finally:
        conn.close()


def add_event(lead_id, action, detail=""):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO events (lead_id, action, detail, created_at) VALUES (?,?,?,?)",
            (lead_id, action, detail, now()),
        )
        conn.commit()
    finally:
        conn.close()


def lead_history(lead_id):
    conn = get_conn()
    try:
        notes = conn.execute(
            "SELECT id, content, created_at FROM notes WHERE lead_id = ? ORDER BY id DESC",
            (lead_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT id, action, detail, created_at FROM events WHERE lead_id = ? ORDER BY id DESC",
            (lead_id,),
        ).fetchall()
        return [dict(r) for r in notes], [dict(r) for r in events]
    finally:
        conn.close()


def summary():
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        status_counts = {}
        for s in STATUSES:
            status_counts[s] = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status = ?", (s,)
            ).fetchone()[0]
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        new_week = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ?", (week_ago,)
        ).fetchone()[0]
        today = today_str()
        due = conn.execute(
            """SELECT * FROM leads WHERE reminder_date != '' AND reminder_date <= ?
               AND status NOT IN ('已成交','无效') ORDER BY reminder_date""",
            (today,),
        ).fetchall()
        top_types = conn.execute(
            "SELECT type, COUNT(*) c FROM leads GROUP BY type ORDER BY c DESC LIMIT 5"
        ).fetchall()
        recent = conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT 6"
        ).fetchall()
        return {
            "total": total,
            "status_counts": status_counts,
            "new_week": new_week,
            "due_reminders": [dict(r) for r in due],
            "top_types": [{"type": r["type"], "count": r["c"]} for r in top_types],
            "recent": [dict(r) for r in recent],
            "today": today,
        }
    finally:
        conn.close()


def get_settings():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        saved = {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()
    defaults = {
        "company_name": "一善科技",
        "product_name": "光纤光缆及配套产品",
        "sender_name": "",
        "smtp_host": "",
        "smtp_port": "465",
        "smtp_ssl": "1",
        "smtp_user": "",
        "smtp_password": "",
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        "openai_api_base": "https://api.openai.com/v1",
        "sms_notice": "",
        "notify_webhook": "",
        "auto_login_trusted": "1",
        "lp_enabled": "1",
        "lp_title": "光纤光缆及配套产品 专业供应",
        "lp_subtitle": "免费获取样品与报价，1 个工作日内专人对接",
        "lp_cta": "立即获取报价",
        "lp_phone": "",
        "lp_thanks": "提交成功！我们会尽快联系您，请保持电话畅通。",
        "auto_crawl_urls": "",
        "auto_crawl_interval": "0",
        "last_auto_crawl": "",
    }
    merged = dict(defaults)
    merged.update(saved)
    return merged


def save_settings(values):
    allowed = [
        "company_name", "product_name", "sender_name", "smtp_host", "smtp_port", "smtp_ssl",
        "smtp_user", "smtp_password", "openai_api_key", "openai_model", "openai_api_base", "sms_notice",
        "notify_webhook",
        "auto_login_trusted",
        "access_password", "lp_enabled", "lp_title", "lp_subtitle", "lp_cta",
        "lp_phone", "lp_thanks", "auto_crawl_urls", "auto_crawl_interval",
        "last_auto_crawl",
    ]
    conn = get_conn()
    try:
        for k in allowed:
            if k in values:
                if k == "access_password":
                    if str(values[k]):
                        conn.execute(
                            "INSERT INTO settings (key, value) VALUES (?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            ("access_password_hash", hashlib.sha256(str(values[k]).encode("utf-8")).hexdigest()),
                        )
                    continue
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, str(values[k])),
                )
        conn.commit()
        return get_settings()
    finally:
        conn.close()


def add_mail_log(lead_id, name, email, subject, body, status, error=""):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO mail_logs (lead_id, name, email, subject, body, status, error, sent_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (lead_id, name, email, subject, body, status, error, now()),
        )
        conn.commit()
    finally:
        conn.close()


def list_mail_logs(limit=100):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mail_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_auto_crawl_log(url, found, added, skipped, error=""):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO auto_crawl_logs (run_at, url, found, added, skipped, error)
               VALUES (?,?,?,?,?,?)""",
            (now(), url, found, added, skipped, error),
        )
        conn.commit()
    finally:
        conn.close()


def list_auto_crawl_logs(limit=50):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM auto_crawl_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_login_log(ip, ua, action, status):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO login_logs (ip, ua, action, status, created_at) VALUES (?,?,?,?,?)",
            (ip or "", ua or "", action, status, now()),
        )
        conn.commit()
    finally:
        conn.close()


def list_login_logs(limit=30):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM login_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def trust_ip(ip, ua=""):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO trusted_ips (ip, ua, last_seen, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(ip) DO UPDATE SET ua = excluded.ua, last_seen = excluded.last_seen",
            (ip or "", (ua or "")[:200], now(), now()),
        )
        conn.commit()
    finally:
        conn.close()


def is_trusted_ip(ip):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM trusted_ips WHERE ip = ?", (ip or "",)
        ).fetchone() is not None
    finally:
        conn.close()


def list_trusted_ips():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM trusted_ips ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def untrust_ip(ip):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM trusted_ips WHERE ip = ?", (ip or "",))
        conn.commit()
    finally:
        conn.close()
