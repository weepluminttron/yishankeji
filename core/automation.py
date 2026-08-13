# -*- coding: utf-8 -*-
"""自动触达引擎：把高意向新线索自动转化为首触动作（复用既有邮件序列机制）。

对应主流获客 AI 的「自动化营销 / 自动触达」能力：
- 新线索入库并完成意向分级后，若评分 >= 阈值且尚未排程，则自动生成个性化首触内容
  （复用 core.followup 的 AI 文案）并写入 scheduled_mails 队列；
- 真正的发送仍由 server 的 _mail_sequence_loop 按 send_at 执行（与“跟进序列”同一可信模型）；
- 全开关默认关闭（auto_touch_enabled=0），开启后才生效，避免无预警群发。

设计：纯后台轮询 + 幂等（has_pending_scheduled_mail 防重复排程），不阻塞主流程。
"""
import time
import traceback

from core import db, followup, intent as _intent, log_helper


def _safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def should_enroll(lead, settings):
    """判断一条线索是否应被自动首触排程。"""
    if settings.get("auto_touch_enabled") != "1":
        return False
    if not lead.get("email"):
        return False
    if lead.get("status") not in ("新线索", "待联系"):
        return False
    try:
        score = _safe_int(lead.get("score"))
    except Exception:
        score = 0
    threshold = _safe_int(settings.get("auto_touch_score", 7), 7)
    return score >= threshold


def enroll_lead(lead, settings):
    """为单条线索生成首触内容并排程；成功返回 (True, 说明)，否则 (False, 原因)。"""
    if not lead.get("email"):
        return False, "缺少邮箱"
    if db.has_pending_scheduled_mail(lead["id"]):
        return False, "已有待发送排程"
    channel = str(settings.get("auto_touch_channel", "email") or "email").strip().lower()
    try:
        delay = max(0, _safe_int(settings.get("auto_touch_delay", 1), 1))
    except Exception:
        delay = 1

    subject, body = "", ""
    if channel == "sms":
        text, err = followup.gen_sms(lead, settings)
        if err:
            return False, "生成短信失败：" + err
        subject = "来自" + (settings.get("company_name", "") or "我们")
        body = text
    else:
        res, err = followup.gen_email(lead, settings)
        if err:
            return False, "生成邮件失败：" + err
        subject, body = res.get("subject", ""), res.get("body", "")

    send_at = (time.strftime("%Y-%m-%d", time.localtime(time.time() + delay * 86400))
               + " 09:00:00")
    db.add_scheduled_mail(lead["id"], subject, body, send_at)
    return True, f"已排程{channel}首触（{send_at}）"


def process_pending(settings, limit=30):
    """扫描待自动首触的线索并排程，返回处理统计。"""
    if settings.get("auto_touch_enabled") != "1":
        return {"enabled": False, "enrolled": 0, "skipped": 0, "errors": []}
    # 取未做过首触的新线索（不依赖意向分级是否完成，评分达标即可排程）
    candidates = db.list_auto_touch_candidates(limit=limit * 4)
    enrolled = 0
    skipped = 0
    errors = []
    for lead in candidates:
        if not should_enroll(lead, settings):
            continue
        ok, msg = enroll_lead(lead, settings)
        if ok:
            enrolled += 1
            db.add_event(lead["id"], "自动首触", msg)
        else:
            if "已有待发送" not in msg and "缺少邮箱" not in msg:
                errors.append({"id": lead["id"], "name": lead["name"], "msg": msg})
            skipped += 1
        if enrolled >= limit:
            break
    return {"enabled": True, "enrolled": enrolled, "skipped": skipped, "errors": errors}


def ensure_intent(limit=50):
    """为尚未分级的线索补做规则意向识别（便宜，无 API 消耗）。返回处理条数。"""
    leads = db.list_intent_pending(limit=limit)
    done = 0
    for lead in leads:
        it = _intent.rule_intent(lead)
        db.set_intent(lead["id"], it["stage"], "")
        done += 1
    return done


def auto_touch_loop():
    """后台线程：周期性做意向分级 + 自动首触排程。"""
    while True:
        try:
            settings = db.get_settings()
            if settings.get("auto_intent_enabled") == "1":
                ensure_intent(limit=50)
            if settings.get("auto_touch_enabled") == "1":
                process_pending(settings, limit=30)
        except Exception:
            log_helper.log_error("自动触达循环异常：" + traceback.format_exc())
        time.sleep(300)  # 每 5 分钟巡检一次
