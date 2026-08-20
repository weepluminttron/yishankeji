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
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    # WAL 模式：读写并发不互相阻塞，显著降低等待
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
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
            CREATE TABLE IF NOT EXISTS scheduled_mails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER,
                subject TEXT DEFAULT '',
                body TEXT DEFAULT '',
                send_at TEXT NOT NULL,
                status TEXT DEFAULT '待发送',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
            CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
            CREATE INDEX IF NOT EXISTS idx_leads_name ON leads(name);
            CREATE INDEX IF NOT EXISTS idx_leads_updated ON leads(updated_at);
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
        if "ai_scored" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN ai_scored INTEGER DEFAULT 0")
        # 意向识别 / 用户画像 / 转化追踪（v2 获客增强）
        for col in ("intent_stage", "intent_json", "first_touch_at", "last_touch_at", "converted_at"):
            if col not in cols:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT DEFAULT ''")
        conn.commit()
        # 索引：提升按意向阶段 / 成交时间的聚合查询速度
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_intent ON leads(intent_stage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_converted ON leads(converted_at)")
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
        where = "(" + " OR ".join(sql) + ")"
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


def list_leads(page=1, size=20, q="", status="", type_="", region="", tag="", source="", sort=""):
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
        order_map = {
            "score_desc": "score DESC, updated_at DESC, id DESC",
            "score_asc": "score ASC, updated_at DESC, id DESC",
            "updated_desc": "updated_at DESC, id DESC",
            "created_desc": "created_at DESC, id DESC",
        }
        order_sql = order_map.get(sort, "updated_at DESC, id DESC")
        rows = conn.execute(
            f"SELECT * FROM leads{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
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
            if raw.get("score") is not None:
                try:
                    set_lead_score(
                        lead["id"],
                        max(0, min(10, int(raw["score"]))),
                        str(raw.get("score_reason") or ""),
                        ai=True,
                    )
                except Exception:
                    pass
    return {"added": added, "duplicates": duplicates, "errors": errors}


def bulk_status(ids, status):
    if status not in STATUSES:
        return {"ok": False, "msg": "无效状态"}
    conn = get_conn()
    try:
        ts = now()
        changed = []
        for lid in ids:
            row = conn.execute("SELECT id, status FROM leads WHERE id = ?", (lid,)).fetchone()
            if not row or row["status"] == status:
                continue
            changed.append((lid, row["status"]))
        if changed:
            conv = now() if status == "已成交" else ""
            conn.executemany(
                "UPDATE leads SET status = ?, updated_at = ?, "
                "converted_at = CASE WHEN ? = '已成交' THEN ? ELSE converted_at END WHERE id = ?",
                [(status, ts, status, conv, lid) for lid, _ in changed],
            )
            conn.commit()
    finally:
        conn.close()
    for lid, old_status in changed:
        add_event(lid, "状态变更", f"{old_status} → {status}")
    return {"ok": True}


def mark_contacted(lead_id, contact_type="", note=""):
    conn = get_conn()
    try:
        lead = get_lead(lead_id)
        if not lead:
            return
        conn.execute(
            "UPDATE leads SET last_contacted = ?, last_touch_at = ?, contact_count = contact_count + 1, updated_at = ? WHERE id = ?",
            (today_str(), today_str(), now(), lead_id),
        )
        conn.commit()
        detail = f"{contact_type}触达" if contact_type else "记录一次触达"
        if note:
            detail += f"：{note}"
        add_event(lead_id, "完成联系", detail)
    finally:
        conn.close()


def set_lead_score(lead_id, score, reason="", ai=False):
    conn = get_conn()
    try:
        if ai:
            conn.execute(
                "UPDATE leads SET score = ?, score_reason = ?, ai_scored = 1, updated_at = ? WHERE id = ?",
                (score, reason, now(), lead_id),
            )
        else:
            conn.execute(
                "UPDATE leads SET score = ?, score_reason = ?, updated_at = ? WHERE id = ?",
                (score, reason, now(), lead_id),
            )
        conn.commit()
        add_event(lead_id, "线索评分", f"{score}分 - {reason}")
    finally:
        conn.close()


def list_unscored_leads(limit=2000):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM leads WHERE ai_scored = 0 ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_intent(lead_id, stage, intent_json=""):
    """写入意向阶段与结构化画像（intent_json 为字符串，调用方负责序列化）。"""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE leads SET intent_stage = ?, intent_json = ?, updated_at = ? WHERE id = ?",
            (stage or "", intent_json or "", now(), lead_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_intent(lead_id):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT intent_stage, intent_json FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        return dict(row) if row else {"intent_stage": "", "intent_json": ""}
    finally:
        conn.close()


def list_intent_pending(limit=200):
    """返回尚未做过意向分级的线索（intent_stage 为空）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM leads WHERE (intent_stage IS NULL OR intent_stage = '') "
            "AND status NOT IN ('已成交', '无效') ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_auto_touch_candidates(limit=200):
    """返回可自动首触的线索：未成交/未无效、还没有首触记录，按评分优先。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM leads WHERE status IN ('新线索', '待联系') "
            "AND (first_touch_at IS NULL OR first_touch_at = '') "
            "ORDER BY score DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def has_pending_scheduled_mail(lead_id):
    """该线索是否已有待发送（排队中）的自动/手动触达邮件。"""
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT 1 FROM scheduled_mails WHERE lead_id = ? AND status = '待发送'",
            (lead_id,),
        ).fetchone() is not None
    finally:
        conn.close()


def mark_first_touch(lead_id, channel=""):
    """记录首次触达时间（自动触达引擎排程发出后调用）。"""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE leads SET first_touch_at = ?, last_touch_at = ?, updated_at = ? WHERE id = ?",
            (today_str(), today_str(), now(), lead_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_converted(lead_id):
    """线索成交：写成交时间，并把状态置为已成交（去重保护）。"""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE leads SET converted_at = ?, status = '已成交', updated_at = ? WHERE id = ?",
            (now(), now(), lead_id),
        )
        conn.commit()
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
        status_counts = {s: 0 for s in STATUSES}
        for s, c in conn.execute("SELECT status, COUNT(*) c FROM leads GROUP BY status"):
            status_counts[s] = c
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
        source_counts = conn.execute(
            "SELECT source, COUNT(*) c FROM leads GROUP BY source ORDER BY c DESC"
        ).fetchall()
        score_dist = {"高（7-10分）": 0, "中（4-6分）": 0, "低（0-3分）": 0}
        bucket_map = {3: "高（7-10分）", 2: "中（4-6分）", 1: "低（0-3分）"}
        for bucket, c in conn.execute(
            "SELECT CASE WHEN score >= 7 THEN 3 WHEN score >= 4 THEN 2 ELSE 1 END AS b, "
            "COUNT(*) c FROM leads GROUP BY b"
        ):
            score_dist[bucket_map[bucket]] = c
        no_contact = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE (phone = '' OR phone IS NULL) AND (email = '' OR email IS NULL)"
        ).fetchone()[0]
        recent = conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT 6"
        ).fetchall()
        return {
            "total": total,
            "status_counts": status_counts,
            "new_week": new_week,
            "due_reminders": [dict(r) for r in due],
            "top_types": [{"type": r["type"], "count": r["c"]} for r in top_types],
            "source_counts": [{"source": r["source"], "count": r["c"]} for r in source_counts],
            "score_dist": score_dist,
            "no_contact": no_contact,
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
        "industry": "光纤通信",
        "product_name": "光纤光缆及配套产品",
        "sender_name": "",
        "from_addr": "",
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
        "search_provider": "so_free",
        "search_api_key": "",
        "serpapi_api_key": "",
        "bocha_api_key": "",
        "search_engine_id": "",
        "search_freshness": "",
        "search_site_filter": "",
        "map_api_key": "",
        "map_provider": "amap",
        "qcc_app_key": "",
        "qcc_secret_key": "",
        "tyc_token": "",
        "lp_enabled": "1",
        "lp_title": "光纤光缆及配套产品 专业供应",
        "lp_subtitle": "免费获取样品与报价，1 个工作日内专人对接",
        "lp_cta": "立即获取报价",
        "lp_phone": "",
        "lp_thanks": "提交成功！我们会尽快联系您，请保持电话畅通。",
        "auto_crawl_urls": "",
        "auto_crawl_interval": "0",
        "last_auto_crawl": "",
        "auto_ai_score": "1",
        # ---- v2 获客增强：意向识别 / 自动触达 ----
        "auto_intent_enabled": "1",   # 新线索入库后自动做意向分级
        "auto_intent_use_ai": "0",    # 意向分级是否调用 AI（默认规则，省费用）
        "auto_touch_enabled": "0",    # 自动首触总开关（默认关闭，需手动开启）
        "auto_touch_score": "7",      # 触发自动首触的最低评分
        "auto_touch_delay": "1",      # 入库后延迟几天再首触
        "auto_touch_channel": "email",  # 首触渠道：email / sms
        # ---- 反爬策略配置（对应"快启精线索"综合反爬体系）----
        "proxy_enabled": "0",      # 是否启用代理（1=启用，0=关闭）
        "proxy_pool": "",          # 代理池：逗号分隔的代理 URL（http://ip:port 或 http://user:pass@ip:port）
        "proxy_url": "",           # 单个代理 URL（与 proxy_pool 二选一，优先级低于 pool）
        "proxy_api_url": "",       # 动态代理 API（有代理/快代理等短效IP接口，池空自动拉取）
        "proxy_api_refresh": "3",  # 动态代理刷新间隔（分钟）
        "delay_search": "0.8",      # 搜索请求前随机延时基准（秒，±50% 抖动）
        "delay_fetch": "0.3",      # 页面抓取前随机延时基准（秒）
        "delay_page": "1.5",      # 翻页/连续抓取延时基准（秒）
        "delay_default": "0.5",    # 默认随机延时基准（秒）
        "retry_max": "2",          # 失败重试次数（指数退避：1s → 2s → 4s）
        "retry_base_delay": "1.0", # 重试退避基准延时（秒）
        # ---- 获客增强：联系页深挖 / 并行搜索兜底 ----
        "probe_contact_pages": "1",   # 首页缺联系方式时自动深挖“联系我们”页
        "contact_probe_limit": "12",  # 单轮最多探测的联系页数量
        "parallel_free_search": "1",  # 免费搜索源并行兜底（更快更全）
        "site_fallback_search": "1",  # site: 全部失败时去掉限定重试
        "crawler_probe_contacts": "1",# AI 智能爬虫自动深挖联系页
        "crawler_probe_pages": "1",   # 智能爬虫每个站点最多抓的联系页数
    }
    merged = dict(defaults)
    merged.update(saved)
    return merged


def save_settings(values):
    allowed = [
        "company_name", "industry", "product_name", "sender_name", "from_addr", "smtp_host", "smtp_port", "smtp_ssl",
        "smtp_user", "smtp_password", "openai_api_key", "openai_model", "openai_api_base", "sms_notice",
        "notify_webhook",
        "auto_login_trusted",
        "search_provider", "search_api_key", "serpapi_api_key", "bocha_api_key", "search_engine_id",
        "search_freshness", "search_site_filter",
        "map_api_key",
        "map_provider",
        "qcc_app_key", "qcc_secret_key", "tyc_token",
        "auto_ai_score",
        "access_password", "lp_enabled", "lp_title", "lp_subtitle", "lp_cta",
        "lp_phone", "lp_thanks", "auto_crawl_urls", "auto_crawl_interval",
        "last_auto_crawl",
        "auto_intent_enabled", "auto_intent_use_ai",
        "auto_touch_enabled", "auto_touch_score", "auto_touch_delay", "auto_touch_channel",
        # 反爬策略配置
        "proxy_pool", "proxy_url", "proxy_api_url", "proxy_api_refresh", "proxy_enabled",
        "delay_search", "delay_fetch", "delay_page", "delay_default",
        "retry_max", "retry_base_delay",
        "probe_contact_pages", "contact_probe_limit",
        "parallel_free_search", "site_fallback_search",
        "crawler_probe_contacts", "crawler_probe_pages",
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


def add_scheduled_mail(lead_id, subject, body, send_at):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO scheduled_mails (lead_id, subject, body, send_at, status, created_at) VALUES (?,?,?,?,?,?)",
            (lead_id, subject, body, send_at, "待发送", now()),
        )
        conn.commit()
    finally:
        conn.close()


def list_due_scheduled_mails(limit=50):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_mails WHERE status = '待发送' AND send_at <= ? "
            "ORDER BY send_at LIMIT ?",
            (now(), limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_scheduled_mail(mail_id, status, error=""):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE scheduled_mails SET status = ?, error = ? WHERE id = ?",
            (status, error, mail_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_scheduled_mails(limit=50):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_mails ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def merge_leads(keep_id, remove_ids):
    """合并线索：把待删线索的备注挪到保留线索上，然后删除。"""
    conn = get_conn()
    try:
        for rid in remove_ids:
            notes = conn.execute(
                "SELECT content, created_at FROM notes WHERE lead_id = ? ORDER BY id", (rid,)
            ).fetchall()
            for n in notes:
                conn.execute(
                    "INSERT INTO notes (lead_id, content, created_at) VALUES (?,?,?)",
                    (keep_id, f"[合并自线索{rid}] {n['content']}", n["created_at"]),
                )
            conn.execute("DELETE FROM leads WHERE id = ?", (rid,))
        conn.commit()
        add_event(keep_id, "合并线索", f"合并了 {len(remove_ids)} 条重复线索")
        return True
    finally:
        conn.close()
