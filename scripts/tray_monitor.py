# -*- coding: utf-8 -*-
"""AI 获客引擎任务栏监控（系统托盘 / 控制台状态栏）。

功能
----
1. 实时枚举引擎当前活跃的后台任务（来自 Web 服务 /api/tasks、/api/acquisition）；
2. 可选枚举引擎相关子进程（psutil：server.py / run_acquisition.py 等）；
3. 任务栏显示：名称、状态、运行时间、进度、阶段；
4. 空列表显示“暂无运行中的任务”；异常退出（任务失败 / 进程异常）明确标红提示；
5. 定时刷新（默认 2 秒），保证与实际运行状态一致；
6. 完整错误捕获与日志（data/tray_monitor.log，自动轮转）。

运行方式
--------
  # 系统托盘模式（需安装 pystray + Pillow）
  pip install pystray pillow psutil
  python scripts/tray_monitor.py --base-url http://123.207.58.61:8017 --password 你的后台密码

  # 控制台状态栏模式（无需任何第三方库）
  python scripts/tray_monitor.py --mode console --base-url http://127.0.0.1:8017 --password 你的后台密码

  # 只读模式：不登录、不拉取 Web 任务，仅枚举本机引擎相关子进程
  python scripts/tray_monitor.py --mode console --local-only
"""
import argparse
import datetime
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

LOG = logging.getLogger("tray_monitor")

# 引擎相关进程的关键字（用于 psutil 枚举子进程）
ENGINE_PROC_KEYWORDS = (
    "yishankeji",
    "server.py",
    "run_acquisition.py",
    "acquisition.py",
    "tray_monitor.py",
)
_PSUTIL_CHECKED = [False]


def setup_logging(path=None):
    path = path or os.path.join(DATA_DIR, "tray_monitor.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tray_monitor.log")
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.info("监控程序启动，PID=%s", os.getpid())


def fmt_elapsed(started_ts, now_ts=None):
    """把开始时间（ISO 字符串或时间戳）格式化为“X小时X分X秒”。"""
    now_ts = now_ts or time.time()
    if isinstance(started_ts, (int, float)):
        secs = max(0, int(now_ts - started_ts))
    else:
        try:
            dt = datetime.datetime.strptime(str(started_ts).strip(), "%Y-%m-%d %H:%M:%S")
            secs = max(0, int(now_ts - dt.timestamp()))
        except Exception:
            return "?"
    if secs < 60:
        return f"{secs}秒"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}分{secs}秒"
    hours, mins = divmod(mins, 60)
    return f"{hours}小时{mins}分"


class MonitorAPI:
    """拉取 Web 服务任务状态（带登录与自动重登）。"""

    def __init__(self, base_url, password=""):
        self.base = (base_url or "http://127.0.0.1:8017").rstrip("/")
        self.password = password
        self.cookie = ""
        self._lock = threading.Lock()

    def _request(self, path, method="GET", payload=None):
        url = self.base + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "User-Agent": "tray-monitor/1.0"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            set_cookie = resp.headers.get("Set-Cookie", "")
            if set_cookie:
                self.cookie = set_cookie.split(";")[0]
            return json.loads(raw)

    def login(self):
        if not self.password:
            return False, "未提供 --password，无法登录"
        try:
            data = self._request("/api/login", "POST", {"password": self.password})
            if data.get("ok"):
                return True, ""
            return False, data.get("msg", "登录失败")
        except Exception as e:
            return False, f"登录请求失败：{e}"

    def get_tasks(self):
        """返回 (tasks, err)。tasks 为 {running:[...], finished:[...]}。"""
        try:
            data = self._request("/api/tasks")
            if not data.get("ok"):
                return {}, data.get("msg", "接口异常")
            tasks = data.get("tasks") or []
            running = [t for t in tasks if t.get("status") == "运行中"]
            finished = [t for t in tasks if t.get("status") != "运行中"]
            return {"running": running, "finished": finished}, ""
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {}, "NEED_LOGIN"
            return {}, f"HTTP {e.code}"
        except Exception as e:
            return {}, f"请求失败：{e}"

    def get_acquisition(self):
        try:
            data = self._request("/api/acquisition")
            if data.get("ok"):
                return data.get("job") or {}, ""
            return {}, data.get("msg", "接口异常")
        except Exception as e:
            return {}, f"请求失败：{e}"


def enum_processes():
    """枚举本机引擎相关子进程（psutil 可用时）。返回列表或 None（不可用）。"""
    if not _PSUTIL_CHECKED[0]:
        _PSUTIL_CHECKED[0] = True
        try:
            import psutil  # noqa: F401
        except Exception as e:
            LOG.warning("psutil 不可用，跳过子进程枚举：%s", e)
            return None
    try:
        import psutil
    except Exception:
        return None
    out = []
    try:
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "status"]):
            try:
                if p.info["pid"] == me:
                    continue
                cmd = " ".join(p.info.get("cmdline") or [])
                if not cmd or not any(k.lower() in cmd.lower() for k in ENGINE_PROC_KEYWORDS):
                    continue
                status = p.info.get("status") or "?"
                elapsed = ""
                ct = p.info.get("create_time")
                if ct:
                    elapsed = fmt_elapsed(ct)
                out.append({
                    "name": p.info.get("name") or "python",
                    "pid": p.info["pid"],
                    "status": "异常" if status in ("zombie", "stopped", "dead") else status,
                    "elapsed": elapsed,
                })
            except Exception:
                continue
    except Exception as e:
        LOG.error("枚举进程失败：%s", e)
    return out


def build_lines(api_result, proc_list, acq_job):
    """把任务/进程/引擎状态拼成状态栏文本行。"""
    lines = []
    now = time.time()

    if acq_job and acq_job.get("running"):
        stage = acq_job.get("stage") or "运行中"
        started = acq_job.get("started") or ""
        lines.append(f"🧠 AI获客引擎：运行中 · {stage} · 已运行{fmt_elapsed(started, now)}")
    elif acq_job and acq_job.get("message"):
        lines.append(f"🧠 AI获客引擎：已停止（{acq_job.get('message')}）")

    running = (api_result or {}).get("running") or []
    finished = (api_result or {}).get("finished") or []
    if not running and not finished and not proc_list:
        lines.append("📋 暂无运行中的任务")
    for t in running:
        stage = t.get("stage") or "运行中"
        prog = f" {t.get('done', 0)}/{t.get('total', 0)}" if t.get("total") else ""
        lines.append(
            f"⏳ {t.get('label', '任务')}：运行中{prog} · {stage}"
            f" · 已运行{fmt_elapsed(t.get('started', ''), now)}"
        )
    for t in finished[-5:]:
        if t.get("status") == "成功":
            lines.append(f"✅ {t.get('label', '任务')}：成功 · {t.get('message') or ''}")
        else:
            lines.append(f"⚠️ {t.get('label', '任务')}：{t.get('status')}（异常退出）· {t.get('message') or ''}")

    if proc_list:
        for p in proc_list:
            mark = "⚠️" if p["status"] == "异常" else "🖥️"
            lines.append(f"{mark} 进程 {p['name']} (PID {p['pid']})：{p['status']} · 已运行{p['elapsed']}")
    elif proc_list is None:
        lines.append("🖥️ 子进程枚举：不可用（未安装 psutil）")
    return lines


def console_render(lines):
    """控制台状态栏：原地刷新，避免刷屏。"""
    text = " | ".join(lines)
    sys.stdout.write("\r\033[K" + text)
    sys.stdout.flush()


def tray_render(icon, lines):
    """更新系统托盘图标提示。"""
    try:
        text = "\n".join(lines)
        icon.title = text[:127]  # 托盘 tooltip 长度限制
    except Exception as e:
        LOG.error("更新托盘提示失败：%s", e)


def build_icon():
    """生成托盘图标（Pillow 可用时），失败返回 None。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGB", (64, 64), "#0e7dd6")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 22, 56, 42], radius=8, fill="#00b4d8")
    d.rectangle([14, 8, 20, 56], fill="#ffffff")
    d.rectangle([44, 8, 50, 56], fill="#ffffff")
    return img


def run_console(args, monitor, proc_enabled):
    LOG.info("控制台状态栏模式启动")
    while True:
        try:
            tasks, terr = monitor.get_tasks()
            if terr == "NEED_LOGIN":
                ok, msg = monitor.login()
                if ok:
                    tasks, terr = monitor.get_tasks()
                else:
                    LOG.error("登录失败：%s", msg)
                    console_render(["🔐 登录失败：" + msg])
                    time.sleep(max(2, args.refresh))
                    continue
            elif terr:
                LOG.error("拉取任务失败：%s", terr)
                console_render(["⚠️ 拉取任务失败：" + terr])
                time.sleep(max(2, args.refresh))
                continue
            acq, aerr = monitor.get_acquisition()
            if aerr:
                LOG.error("拉取引擎状态失败：%s", aerr)
            procs = enum_processes() if proc_enabled else None
            lines = build_lines(tasks, procs, acq)
            console_render(lines)
        except Exception as e:
            LOG.exception("刷新循环异常")
            console_render(["⚠️ 监控异常：" + str(e)])
        time.sleep(max(1, args.refresh))


def run_tray(args, monitor, proc_enabled):
    try:
        import pystray
        from PIL import Image
    except Exception as e:
        LOG.error("系统托盘模式需要 pystray + Pillow：%s，已回退到控制台模式", e)
        run_console(args, monitor, proc_enabled)
        return
    LOG.info("系统托盘模式启动")
    img = build_icon() or Image.new("RGB", (64, 64), "#0e7dd6")
    state = {"lines": ["正在连接…"], "stop": False}

    def on_refresh(icon, item):
        icon.notify("正在刷新任务状态…", "AI 获客引擎监控")
        try:
            tasks, terr = monitor.get_tasks()
            if terr == "NEED_LOGIN":
                monitor.login()
                tasks, terr = monitor.get_tasks()
            acq, aerr = monitor.get_acquisition()
            procs = enum_processes() if proc_enabled else None
            state["lines"] = build_lines(tasks, procs, acq)
            if terr or aerr:
                state["lines"].append(f"（拉取异常：{terr or aerr}）")
            tray_render(icon, state["lines"])
            icon.notify("\n".join(state["lines"][:4]), "任务状态已刷新")
        except Exception as e:
            LOG.exception("手动刷新失败")
            icon.notify("刷新失败：" + str(e), "AI 获客引擎监控")

    def on_logs(icon, item):
        try:
            os.startfile(os.path.join(DATA_DIR, "tray_monitor.log"))
        except Exception as e:
            LOG.error("打开日志失败：%s", e)

    def on_quit(icon, item):
        state["stop"] = True
        icon.stop()

    def background_refresh():
        while not state["stop"]:
            try:
                tasks, terr = monitor.get_tasks()
                if terr == "NEED_LOGIN":
                    monitor.login()
                    tasks, terr = monitor.get_tasks()
                acq, aerr = monitor.get_acquisition()
                procs = enum_processes() if proc_enabled else None
                lines = build_lines(tasks, procs, acq)
                if terr or aerr:
                    lines.append(f"（拉取异常：{terr or aerr}）")
                state["lines"] = lines
                tray_render(icon, lines)
            except Exception as e:
                LOG.exception("托盘后台刷新异常")
                state["lines"] = ["⚠️ 监控异常：" + str(e)]
                tray_render(icon, state["lines"])
            time.sleep(max(1, args.refresh))

    menu = pystray.Menu(
        pystray.MenuItem("🔄 刷新", on_refresh),
        pystray.MenuItem("📄 打开日志", on_logs),
        pystray.MenuItem("🚪 退出", on_quit),
    )
    icon = pystray.Icon("yishankeji_tray", img, "AI 获客引擎监控", menu)
    threading.Thread(target=background_refresh, daemon=True).start()
    icon.run()


def main():
    ap = argparse.ArgumentParser(description="AI 获客引擎任务栏监控（系统托盘/控制台状态栏）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8017", help="Web 服务地址")
    ap.add_argument("--password", default=os.environ.get("YSK_PASSWORD", ""), help="后台访问密码")
    ap.add_argument("--mode", default="auto", choices=["auto", "tray", "console"], help="显示模式")
    ap.add_argument("--refresh", type=float, default=2.0, help="刷新间隔（秒）")
    ap.add_argument("--local-only", action="store_true", help="不连 Web 服务，只枚举本机引擎进程")
    ap.add_argument("--no-procs", action="store_true", help="关闭子进程枚举")
    args = ap.parse_args()

    setup_logging()
    if args.local_only:
        LOG.info("本地只读模式启动")
        while True:
            try:
                procs = None if args.no_procs else enum_processes()
                lines = ["📋 暂无运行中的任务"] if not procs else []
                if procs:
                    for p in procs:
                        mark = "⚠️" if p["status"] == "异常" else "🖥️"
                        lines.append(f"{mark} {p['name']} (PID {p['pid']})：{p['status']} · 已运行{p['elapsed']}")
                elif procs is None:
                    lines = ["🖥️ 子进程枚举：不可用（未安装 psutil）"]
                console_render(lines)
            except Exception as e:
                LOG.exception("本地模式异常")
                console_render(["⚠️ " + str(e)])
            time.sleep(max(1, args.refresh))
        return

    monitor = MonitorAPI(args.base_url, args.password)
    proc_enabled = not args.no_procs
    if args.mode == "tray":
        run_tray(args, monitor, proc_enabled)
    elif args.mode == "console":
        run_console(args, monitor, proc_enabled)
    else:
        try:
            import pystray  # noqa: F401
            run_tray(args, monitor, proc_enabled)
        except Exception:
            LOG.info("pystray 未安装，使用控制台状态栏")
            run_console(args, monitor, proc_enabled)


if __name__ == "__main__":
    main()
