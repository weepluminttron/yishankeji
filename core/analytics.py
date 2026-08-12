# -*- coding: utf-8 -*-
"""数据追踪与转化分析（只读）：把已有的 leads / events / mail_logs 变成可决策的获客指标。

对应主流获客 AI 的「数据追踪 / 转化分析」能力：
- 转化漏斗：总线索 → 已联系 → 跟进中 → 已成交（含流失）；
- 分来源转化率：哪类渠道带来的线索更容易成交；
- 分意向阶段转化率：意向越强的线索成交率是否越高（验证意向识别有效性）；
- 触达效果：是否有过首触 / 多次触达，与成交率的相关性。

全部为只读查询，不修改任何数据、不改变既有业务写入逻辑。
"""
from datetime import datetime, timedelta

from core import db, intent as _intent


def _count(conn, where="", params=None):
    params = params or []
    sql = "SELECT COUNT(*) FROM leads" + ((" WHERE " + where) if where else "")
    return conn.execute(sql, params).fetchone()[0]


def funnel():
    """转化漏斗（基于状态快照，不依赖事件表，稳定可读）。"""
    conn = db.get_conn()
    try:
        total = _count(conn)
        contacted = _count(conn, "status IN ('已联系','跟进中','已成交')")
        following = _count(conn, "status IN ('跟进中','已成交')")
        won = _count(conn, "status = '已成交'")
        lost = _count(conn, "status = '无效'")
        rate = round(won / total * 100, 1) if total else 0.0
        return {
            "total": total,
            "contacted": contacted,
            "following": following,
            "won": won,
            "lost": lost,
            "conversion_rate": rate,  # 成交 / 总
            "contact_to_win_rate": round(won / contacted * 100, 1) if contacted else 0.0,
        }
    finally:
        conn.close()


def by_source():
    """分来源：线索量、成交量、转化率，按转化率降序。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT source, COUNT(*) c, "
            "SUM(CASE WHEN status='已成交' THEN 1 ELSE 0 END) w FROM leads "
            "GROUP BY source ORDER BY c DESC"
        ).fetchall()
        out = []
        for r in rows:
            c = r["c"] or 0
            w = r["w"] or 0
            out.append({
                "source": r["source"] or "未标注",
                "total": c,
                "won": w,
                "rate": round(w / c * 100, 1) if c else 0.0,
            })
        return out
    finally:
        conn.close()


def by_intent():
    """分意向阶段：线索量、成交量、转化率，验证意向分级是否有区分度。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT intent_stage, COUNT(*) c, "
            "SUM(CASE WHEN status='已成交' THEN 1 ELSE 0 END) w FROM leads "
            "GROUP BY intent_stage ORDER BY c DESC"
        ).fetchall()
        out = []
        for r in rows:
            stage = r["intent_stage"] or "未分级"
            c = r["c"] or 0
            w = r["w"] or 0
            out.append({
                "stage": stage,
                "total": c,
                "won": w,
                "rate": round(w / c * 100, 1) if c else 0.0,
            })
        return out
    finally:
        conn.close()


def touch_effect():
    """触达效果：是否首触 / 触达次数与成交率的关系。"""
    conn = db.get_conn()
    try:
        touched = _count(conn, "first_touch_at != '' AND first_touch_at IS NOT NULL")
        touched_won = _count(conn, "first_touch_at != '' AND first_touch_at IS NOT NULL AND status='已成交'")
        untouch = _count(conn, "(first_touch_at = '' OR first_touch_at IS NULL) AND status != '已成交'")
        untouch_won = _count(conn, "(first_touch_at = '' OR first_touch_at IS NULL) AND status='已成交'")
        multi = _count(conn, "contact_count >= 3")
        multi_won = _count(conn, "contact_count >= 3 AND status='已成交'")
        return {
            "touched": {
                "total": touched, "won": touched_won,
                "rate": round(touched_won / touched * 100, 1) if touched else 0.0,
            },
            "untouched": {
                "total": untouch, "won": untouch_won,
                "rate": round(untouch_won / untouch * 100, 1) if untouch else 0.0,
            },
            "multi_touch": {
                "total": multi, "won": multi_won,
                "rate": round(multi_won / multi * 100, 1) if multi else 0.0,
            },
        }
    finally:
        conn.close()


def hot_leads(limit=20):
    """当前高意向 / 高评分待跟进线索（用于首页智能待办）。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM leads WHERE status NOT IN ('已成交','无效') "
            "ORDER BY CASE WHEN intent_stage='决策采购' THEN 5 WHEN intent_stage='对比选型' THEN 4 "
            "WHEN intent_stage='明确需求' THEN 3 WHEN intent_stage='初步了解' THEN 2 ELSE 1 END DESC, "
            "score DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            lead = dict(r)
            intent = _intent.rule_intent(lead)
            out.append({
                "id": lead["id"],
                "name": lead["name"],
                "score": lead["score"],
                "intent_stage": lead.get("intent_stage") or intent["stage"],
                "next_action": intent["next_action"],
                "status": lead["status"],
            })
        return out
    finally:
        conn.close()


def overview():
    """汇总：供 /api/analytics 一次性返回。"""
    return {
        "funnel": funnel(),
        "by_source": by_source(),
        "by_intent": by_intent(),
        "touch_effect": touch_effect(),
        "hot_leads": hot_leads(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
