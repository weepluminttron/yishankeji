/* 光纤获客助手 - 前端逻辑 */
"use strict";

/* ---------- 工具 ---------- */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(path, init);
  let data;
  try { data = await resp.json(); } catch (e) { data = { ok: false, msg: "响应解析失败" }; }
  if (data.need_login) {
    showLogin();
    throw new Error("请先登录");
  }
  if (!resp.ok && !data.ok) throw new Error(data.msg || "请求失败");
  return data;
}

function toast(msg, type = "") {
  const box = document.createElement("div");
  box.className = "toast " + type;
  box.textContent = msg;
  $("#toast-root").appendChild(box);
  setTimeout(() => box.remove(), 3200);
}

function openModal(html, cls = "") {
  // 拆分弹窗：头部/底部固定，中间内容独立滚动（关闭按钮始终可见）
  let head = "", foot = "", body = html;
  const hm = html.match(/^\s*(<div class="modal-head">[\s\S]*?<\/div>)/);
  if (hm) {
    head = hm[1];
    body = html.slice(hm[1].length);
  }
  const fm = body.match(/(<div class="modal-foot">[\s\S]*?<\/div>)\s*$/);
  if (fm) {
    foot = fm[1];
    body = body.slice(0, fm.index);
  }
  $("#modal-root").innerHTML = `<div class="modal-mask" data-close="1"><div class="modal ${cls}" data-stop="1">${head}<div class="modal-body">${body}</div>${foot}</div></div>`;
  const mask = $(".modal-mask");
  mask.addEventListener("click", (e) => {
    if (e.target.closest("[data-stop]")) return;
    closeModal();
  });
  return closeModal;
}
function closeModal() { $("#modal-root").innerHTML = ""; }

let lastTaskSeen = {};
async function pollTasks() {
  try {
    const d = await api("/api/tasks");
    const tasks = d.tasks || [];
    const nowTs = Date.now();
    // 运行中的任务 + 最近 30 秒内完成的任务都显示，避免“一闪而过”
    const visible = tasks.filter((t) => {
      if (t.status === "运行中") return true;
      return t.finished_ts && nowTs - t.finished_ts * 1000 < 30000;
    });
    const bar = $("#task-bar");
    if (visible.length) {
      bar.innerHTML = visible.map((t) => {
        const pct = t.total ? Math.min(100, Math.round((t.done / t.total) * 100)) : null;
        const running = t.status === "运行中";
        const cls = running ? "" : t.status === "成功" ? "t-done" : "t-fail";
        const icon = running ? "⏳" : t.status === "成功" ? "✅" : "⚠️";
        const stage = running
          ? (t.stage || "运行中") + (t.total ? ` ${t.done}/${t.total}` : "") + ` · ⏱ ${fmtElapsed(t.started)}`
          : (t.message || t.status);
        return `<div class="task-chip ${cls}" data-id="${esc(t.id)}" onclick="taskChipClick(this)">
          <span class="t-icon">${icon}</span>
          <span class="t-label">${esc(t.label)}</span>
          <span class="t-stage">${esc(stage)}</span>
          ${pct !== null ? `<span class="t-prog"><i style="width:${pct}%"></i></span>` : ""}
        </div>`;
      }).join("");
    } else {
      bar.innerHTML = "";
    }
    tasks.filter((t) => t.status !== "运行中" && t.finished).forEach((t) => {
      if (lastTaskSeen[t.id] !== t.finished) {
        lastTaskSeen[t.id] = t.finished;
        toast(`${t.label}：${t.message || t.status}`, t.status === "成功" ? "ok" : "err");
        // AI 方案生成完成后：如果人不在方案页，自动跳过去显示方案
        if (t.id === "strategy" && t.status === "成功" && state.page !== "buyer") {
          setTimeout(() => go("buyer"), 800);
        }
      }
    });
  } catch (e) { /* 忽略轮询错误 */ }
  setTimeout(pollTasks, 2500);
}

function fmtElapsed(started) {
  if (!started) return "—";
  const t = new Date(String(started).replace(/-/g, "/")).getTime();
  if (isNaN(t)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return s + "秒";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "分" + (s % 60) + "秒";
  return Math.floor(m / 60) + "小时" + (m % 60) + "分";
}

function openTaskPanel() {
  api("/api/tasks").then((d) => {
    const tasks = d.tasks || [];
    openModal(`
      <div class="modal-head"><h2>任务中心</h2><button class="close-x" onclick="closeModal()">×</button></div>
      ${tasks.length ? `<div class="table-wrap"><table>
        <thead><tr><th>任务</th><th>状态</th><th>进度</th><th>当前阶段</th><th>开始时间</th></tr></thead>
        <tbody>${tasks.map((t) => `<tr>
          <td><b>${esc(t.label)}</b><div class="sub">${esc(t.message || "")}</div></td>
          <td>${t.status === "运行中" ? '<span style="color:var(--brand);font-weight:700">⏳ 运行中</span>' : t.status === "成功" ? '<span style="color:var(--green)">✅ 成功</span>' : '<span style="color:var(--red)">⚠️ ' + esc(t.status) + '</span>'}</td>
          <td>${t.total ? `${t.done}/${t.total}` : "—"}</td>
          <td class="sub">${esc(t.stage || "")}</td>
          <td class="sub">${esc(t.started || "")}</td>
        </tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">暂无任务</div>`}
      <div class="modal-foot"><button class="btn" onclick="closeModal()">关闭</button></div>`, "wide");
  }).catch(() => {});
}

function taskChipClick(el) {
  if (el.dataset.id === "strategy") { go("buyer"); return; }
  openTaskPanel();
}

function showLogin(msg) {
  $("#login-root").innerHTML = `
    <div class="login-mask">
      <div class="login-box">
        <div class="login-logo">🛰️</div>
        <div class="login-title">AI 获客助手</div>
        <div class="login-sub">请输入访问密码</div>
        ${msg ? `<div class="login-err">${esc(msg)}</div>` : ""}
        <input class="input full" type="password" id="login-pw" placeholder="访问密码" autocomplete="current-password">
        <button class="btn primary" id="login-btn">进入系统</button>
      </div>
    </div>`;
  const doLogin = async () => {
    const pw = $("#login-pw").value;
    if (!pw) return;
    try {
      await api("/api/login", { method: "POST", body: { password: pw } });
      $("#login-root").innerHTML = "";
      $("#nav-logout").style.display = "block";
      pollTasks();
      go("dashboard");
    } catch (e) {
      showLogin("密码不正确，请重试");
      $("#login-pw").focus();
    }
  };
  $("#login-btn").onclick = doLogin;
  $("#login-pw").onkeydown = (e) => { if (e.key === "Enter") doLogin(); };
  $("#login-pw").focus();
}

function confirmBox(msg, onOk) {
  openModal(`
    <div class="modal-head"><h2>确认操作</h2><button class="close-x" onclick="closeModal()">×</button></div>
    <p style="color:#44566e">${esc(msg)}</p>
    <div class="modal-foot">
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="ok-btn">确定</button>
    </div>`);
  $("#ok-btn").onclick = () => { closeModal(); onOk(); };
}

function badge(status) {
  return `<span class="badge st-${esc(status)}">${esc(status)}</span>`;
}

function fmtDate(s) {
  if (!s) return "—";
  return s.slice(5, 10).replace("-", "/");
}

/* ---------- 状态 ---------- */
const state = {
  page: "dashboard",
  meta: { statuses: [], types: [], tags: [] },
  leads: {
    page: 1, size: 15, total: 0,
    filters: { q: "", status: "", type: "", region: "", tag: "", source: "", sort: "score_desc" },
    selected: new Set(),
  },
  collectTab: "import",
  outreachTab: "email",
  recipients: [], // 主动触达选择的收件人
  buyerContext: "", // 最近一次 AI 业务描述（用于 AI 智能筛选）
};

/* ---------- 导航 ---------- */
function go(page) {
  state.page = page;
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + page));
  const renderer = { dashboard: renderDashboard, leads: renderLeads, collect: renderCollect, buyer: renderBuyer, outreach: renderOutreach, logs: renderLogs, settings: renderSettings }[page];
  if (renderer) renderer();
}
$$(".nav-item").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.page) go(b.dataset.page);
  else if (b.dataset.href) location.href = b.dataset.href;
}));

/* ---------- 工作台 ---------- */
async function renderDashboard() {
  const el = $("#page-dashboard");
  el.innerHTML = `<div class="page-title">工作台</div><div class="page-sub">今天看一眼进度，客户和线索都在掌握中</div><div class="empty"><div class="ico">⏳</div>加载中…</div>`;
  const s = await api("/api/summary");
  const sc = s.status_counts;
  const pipe = state.meta.statuses.map((st) => `
    <div class="pipe-item" onclick="go('leads');setFilter('status','${esc(st)}')">
      <div class="n">${sc[st] || 0}</div><div class="t">${esc(st)}</div>
    </div>`).join("");
  const due = s.due_reminders.map((r) => `
    <div class="mini-row clickable" onclick="openLeadDetail(${r.id})">
      <div><span class="who">${esc(r.name)}</span> <span class="tag-chip">${esc(r.type)}</span></div>
      <div class="when">${badge(r.status)}</div>
    </div>`).join("") || `<div class="empty">今天没有到期跟进，保持领先 🎉</div>`;
  const recent = s.recent.map((r) => {
    const tag = r.score >= 7 ? '<span class="tag-chip" style="background:#ffe9ec;color:#c62828">🔥高意向</span>'
      : r.score >= 4 ? '<span class="tag-chip" style="background:#fff8e1;color:#b26a00">🟡中意向</span>'
      : '<span class="tag-chip">⚪低意向</span>';
    const contact = (r.phone || r.email)
      ? '<span class="tag-chip" style="background:#e8f5e9;color:#2e7d32">📞已获取</span>'
      : '<span class="tag-chip" style="background:#fdecea;color:#c62828">⚠️无联系方式</span>';
    return `
      <div class="mini-row">
        <div class="clickable" style="flex:1;min-width:0" onclick="openLeadDetail(${r.id})">
          <span class="who">${esc(r.name)}</span> ${tag} ${contact}
          <div class="when">${esc(r.phone || r.email || "无联系方式")}</div>
        </div>
        <div style="display:flex;gap:4px">
          <button class="btn sm" title="发邮件" onclick="sendMailTo(${r.id})">📧</button>
          <button class="btn sm" title="添加备注/查看" onclick="openLeadDetail(${r.id})">📝</button>
        </div>
      </div>`;
  }).join("") || `<div class="empty">还没有线索，去“线索采集”添加第一批客户吧</div>`;
  const types = s.top_types.map((t) => `${esc(t.type)} ${t.count}`).join(" · ") || "—";
  const maxSrc = Math.max(1, ...(s.source_counts || []).map((x) => x.count));
  const sourceBars = (s.source_counts || []).map((x) => `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="min-width:90px;font-size:12px">${esc(x.source)}</span>
      <div style="flex:1;height:8px;background:var(--line);border-radius:4px;overflow:hidden">
        <i style="display:block;height:100%;width:${Math.round(x.count / maxSrc * 100)}%;background:var(--brand-grad)"></i>
      </div>
      <b style="font-size:12px;min-width:24px">${x.count}</b>
    </div>`).join("") || `<div class="empty">暂无数据</div>`;
  const totalScore = Math.max(1, s.total);
  const scoreBars = Object.entries(s.score_dist || {}).map(([k, v]) => `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="min-width:90px;font-size:12px">${esc(k)}</span>
      <div style="flex:1;height:8px;background:var(--line);border-radius:4px;overflow:hidden">
        <i style="display:block;height:100%;width:${Math.round(v / totalScore * 100)}%;background:var(--brand-2)"></i>
      </div>
      <b style="font-size:12px;min-width:24px">${v}</b>
    </div>`).join("");
  const onboarding = s.total === 0 ? `
    <div class="card">
      <h3>🚀 三步开始获客</h3>
      <div class="onboard-steps">
        <div class="onboard-step">
          <div class="n">第 1 步</div><div class="t">🤖 让 AI 出获客方案</div>
          <div class="d">一句话描述业务，AI 生成多套方案，选一套就能开搜</div>
          <button class="btn primary sm" onclick="go('buyer')">去生成方案</button>
        </div>
        <div class="onboard-step">
          <div class="n">第 2 步</div><div class="t">📥 导入已有名单</div>
          <div class="d">Excel、地图采集、微信/社媒记录都能直接导入</div>
          <button class="btn sm" onclick="setCollectTab('import');go('collect')">去导入</button>
        </div>
        <div class="onboard-step">
          <div class="n">第 3 步</div><div class="t">✍️ 手动添加客户</div>
          <div class="d">先录入第一批客户，从今天开始跟进</div>
          <button class="btn sm" onclick="openLeadForm()">去添加</button>
        </div>
      </div>
    </div>` : "";
  const highIntent = (s.score_dist && s.score_dist["高（7-10分）"]) || 0;
  const funnelBars = state.meta.statuses.map((st) => {
    const c = sc[st] || 0;
    const pct = Math.round(c / Math.max(1, s.total) * 100);
    return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span style="min-width:64px;font-size:12px">${esc(st)}</span>
      <div style="flex:1;height:10px;background:var(--line);border-radius:5px;overflow:hidden">
        <i style="display:block;height:100%;width:${pct}%;background:var(--brand-grad)"></i>
      </div>
      <b style="min-width:24px;font-size:12px">${c}</b>
      ${st === "跟进中" && c === 0 ? `<button class="btn sm" onclick="openLeadForm('跟进中')">＋ 一键创建跟进</button>` : ""}
    </div>`;
  }).join("");
  el.innerHTML = `
    <div class="page-title">工作台</div>
    <div class="page-sub">高频操作都在上面：找客户 → 管客户 → 触达客户${s.total ? ` · 数据截至 ${esc(s.today)}` : ""}</div>
    <div class="card" style="background:linear-gradient(135deg,#0e7dd6,#00b4d8);color:#fff;border:none;padding:20px 22px">
      <h3 style="color:#fff;margin-bottom:6px">🤖 AI 获客向导</h3>
      <p style="opacity:.92;margin-bottom:10px;font-size:13px">输入一句话描述您的业务（不限行业），AI 为您生成高利润、高转化的获客组合方案</p>
      <textarea class="textarea" id="home-strategy" style="width:100%;background:rgba(255,255,255,.95);color:#1f2d3d" placeholder="例如：我们做高端光纤设备，想找欧洲数据中心总包商，利润空间大、对方有一定知名度…"></textarea>
      <button class="btn" id="home-strategy-btn" style="margin-top:10px;background:#fff;color:#0e7dd6;font-weight:700">🤖 AI 生成获客方案</button>
    </div>
    <div class="action-group">
      <div class="action-group-title">📥 客户获取</div>
      <div class="action-grid">
        <div class="action-btn" onclick="setCollectTab('map');go('collect')"><span class="ico">🗺️</span>地图获客</div>
        <div class="action-btn" onclick="setCollectTab('import');go('collect')"><span class="ico">📥</span>批量导入</div>
      </div>
    </div>
    <div class="action-group">
      <div class="action-group-title">📇 客户管理</div>
      <div class="action-grid">
        <div class="action-btn primary" onclick="openLeadForm()"><span class="ico">➕</span>新增客户</div>
        <div class="action-btn" onclick="go('leads')"><span class="ico">📇</span>客户列表</div>
        <div class="action-btn" onclick="scoreAllFromHome()"><span class="ico">✨</span>全量 AI 评分</div>
      </div>
    </div>
    <div class="action-group">
      <div class="action-group-title">📣 主动触达</div>
      <div class="action-grid">
        <div class="action-btn" onclick="setOutreachTab('email');go('outreach')"><span class="ico">📧</span>邮件触达</div>
        <div class="action-btn" onclick="setOutreachTab('sequence');go('outreach')"><span class="ico">⏰</span>跟进序列</div>
      </div>
    </div>
    <div class="action-group">
      <div class="action-group-title">🚀 AI 获客增强</div>
      <div class="action-grid">
        <div class="action-btn" onclick="location.href='/analytics.html'"><span class="ico">📈</span>获客分析</div>
        <div class="action-btn" id="home-intent-btn"><span class="ico">🧭</span>意向分级</div>
        <div class="action-btn" id="home-touch-btn"><span class="ico">🤖</span>自动首触</div>
      </div>
    </div>
    ${onboarding}
    <div class="stat-grid">
      <div class="stat-card"><div class="label">全部线索</div><div class="num accent">${s.total}</div></div>
      <div class="stat-card"><div class="label">本周新增</div><div class="num accent green">${s.new_week}</div></div>
      <div class="stat-card"><div class="label">已成交</div><div class="num accent green">${sc["已成交"] || 0}</div></div>
      <div class="stat-card"><div class="label">今日需跟进</div><div class="num accent orange">${s.due_reminders.length}</div></div>
    </div>
    <div class="card">
      <h3>客户阶段漏斗</h3>
      ${s.total ? `<div style="margin-bottom:10px;padding:10px;background:#e8f5e9;border-radius:8px;font-size:13px">系统评估共有 <b style="color:var(--green)">${highIntent} 个高意向客户</b>${highIntent ? "，建议今日优先跟进" : "，建议先去获取更多线索"}</div>` : ""}
      ${funnelBars}
    </div>
    <div class="two-col">
      <div class="card"><h3>今日到期跟进${s.due_reminders.length ? `（${s.due_reminders.length}）` : ""}</h3><div class="mini-list">${due}</div></div>
      <div class="card"><h3>最新线索</h3><div class="mini-list">${recent}</div></div>
    </div>
    <div class="card"><h3>客户类型占比</h3><div>${types}</div></div>
    <div class="two-col">
      <div class="card"><h3>线索来源分布</h3>${sourceBars}</div>
      <div class="card"><h3>AI 评分分布</h3>${scoreBars}</div>
    </div>`;
  $("#home-strategy-btn").onclick = () => {
    const desc = $("#home-strategy").value.trim();
    if (!desc) return toast("请先描述您的业务", "err");
    state.buyerContext = desc;
    state.pendingStrategy = desc;
    go("buyer");
  };
  $("#home-intent-btn").onclick = async () => {
    const btn = $("#home-intent-btn");
    btn.disabled = true; btn.style.opacity = ".6";
    try {
      const r = await api("/api/automation/intent", { method: "POST", body: {} });
      toast(r.msg || "意向分级完成", "ok");
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; btn.style.opacity = ""; }
  };
  $("#home-touch-btn").onclick = async () => {
    try {
      const st = await api("/api/automation");
      if (st.enabled !== true) {
        toast("自动首触未开启，请先到“设置 → 自动首触”开启", "err");
        go("settings");
        return;
      }
      const r = await api("/api/automation/run", { method: "POST", body: {} });
      toast(r.msg || "自动首触巡检已启动", "ok");
    } catch (e) { toast(e.message, "err"); }
  };
}

async function sendMailTo(id) {
  try {
    const lead = await api("/api/leads/" + id);
    if (!lead.email) return toast("该客户没有邮箱", "err");
    state.recipients = state.recipients.filter((x) => x.id !== id).concat({ id: lead.id, name: lead.name, email: lead.email, phone: lead.phone });
    setOutreachTab("email");
    go("outreach");
    toast("已加入邮件收件人", "ok");
  } catch (e) { toast(e.message, "err"); }
}

function setCollectTab(tab) { state.collectTab = tab; }
function setOutreachTab(tab) { state.outreachTab = tab; }
async function scoreAllFromHome() {
  try {
    await api("/api/leads/score_all", { method: "POST", body: {} });
    toast("全量 AI 评分任务已启动", "ok");
  } catch (e) { toast(e.message, "err"); }
  go("leads");
}

function setFilter(key, value) {
  state.leads.filters[key] = value;
  state.leads.page = 1;
  go("leads");
}

/* ---------- 客户线索 ---------- */
async function renderLeads() {
  const el = $("#page-leads");
  const f = state.leads.filters;
  el.innerHTML = `
    <div class="page-title">客户线索</div>
    <div class="page-sub">搜索、筛选、跟进你的光纤行业客户</div>
    <div class="toolbar">
      <input class="input grow" id="lead-q" placeholder="搜索公司 / 联系人 / 电话 / 邮箱" value="${esc(f.q)}">
      <select class="select" id="lead-status">
        <option value="">全部状态</option>
        ${state.meta.statuses.map((s) => `<option ${f.status === s ? "selected" : ""}>${esc(s)}</option>`).join("")}
      </select>
      <select class="select" id="lead-type">
        <option value="">全部类型</option>
        ${state.meta.types.map((t) => `<option ${f.type === t ? "selected" : ""}>${esc(t)}</option>`).join("")}
      </select>
      <input class="input" id="lead-region" placeholder="地区" value="${esc(f.region)}" style="width:110px">
      <button class="btn primary" id="btn-search">筛选</button>
      <button class="btn" id="btn-reset">重置</button>
    </div>
    <div class="toolbar">
      <button class="btn primary" id="btn-add-lead">＋ 新增线索</button>
      <button class="btn" id="btn-import">📥 批量导入</button>
      <button class="btn" id="btn-export-x">⬇ 导出 Excel</button>
      <button class="btn" id="btn-export-c">⬇ 导出 CSV</button>
      <button class="btn" id="btn-score-all">✨ 全量 AI 评分</button>
      <button class="btn" id="btn-clean">🧹 数据清洗</button>
      <span style="flex:1"></span>
      <button class="btn" id="btn-mail-sel" disabled>📧 邮件触达选中</button>
      <button class="btn" id="btn-del-sel" disabled>删除选中</button>
    </div>
    <div class="hint" id="score-all-status" style="margin:-8px 0 12px"></div>
    <div style="display:flex;gap:8px;margin-bottom:10px">
      <button class="btn sm primary" id="view-list">☰ 列表视图</button>
      <button class="btn sm" id="view-kanban">🗂 看板视图</button>
      <span style="flex:1"></span>
      <select class="select" id="lead-sort" title="排序方式">
        <option value="score_desc" ${(f.sort || "score_desc") === "score_desc" ? "selected" : ""}>按评分从高到低</option>
        <option value="score_asc" ${f.sort === "score_asc" ? "selected" : ""}>按评分从低到高</option>
        <option value="updated_desc" ${f.sort === "updated_desc" ? "selected" : ""}>按最近更新</option>
        <option value="created_desc" ${f.sort === "created_desc" ? "selected" : ""}>按最新添加</option>
      </select>
    </div>
    <div class="card"><div class="table-wrap" id="lead-table"></div><div class="pager" id="lead-pager"></div></div>
    <div class="card" id="lead-kanban" style="display:none"></div>`;

  const qInput = $("#lead-q");
  let timer;
  qInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { f.q = qInput.value.trim(); f.page = 1; loadLeadList(); }, 350);
  });
  $("#btn-search").onclick = () => {
    f.status = $("#lead-status").value;
    f.type = $("#lead-type").value;
    f.region = $("#lead-region").value.trim();
    f.page = 1;
    loadLeadList();
  };
  $("#btn-reset").onclick = () => {
    Object.assign(f, { q: "", status: "", type: "", region: "", tag: "", source: "" });
    state.leads.selected.clear();
    renderLeads();
  };
  $("#btn-add-lead").onclick = openLeadForm;
  $("#btn-import").onclick = () => { go("collect"); activateSubTab("collect", "import"); };
  $("#btn-export-x").onclick = () => exportLeads("xlsx");
  $("#btn-export-c").onclick = () => exportLeads("csv");
  const pollScore = async () => {
    try {
      const d = await api("/api/leads/score_all");
      const job = d.job;
      const box = $("#score-all-status");
      if (job.running) {
        box.textContent = `⏳ 正在AI评分：${job.done}/${job.total || "计算中"}${job.current ? "（" + job.current + "）" : ""}`;
        setTimeout(pollScore, 2000);
      } else if (job.message) {
        box.textContent = job.message;
        setTimeout(() => { box.textContent = ""; }, 8000);
        loadLeadList();
      }
    } catch (e) { /* 忽略轮询错误 */ }
  };
  $("#btn-score-all").onclick = async () => {
    try {
      const d = await api("/api/leads/score_all", { method: "POST", body: {} });
      toast(d.msg, "ok");
      pollScore();
    } catch (e) { toast(e.message, "err"); }
  };
  $("#btn-clean").onclick = async () => {
    try {
      const d = await api("/api/leads/duplicates");
      const groups = d.groups || [];
      if (!groups.length) return toast("没有发现重复线索", "ok");
      openModal(`
        <div class="modal-head"><h2>数据清洗（发现 ${groups.length} 组重复）</h2><button class="close-x" onclick="closeModal()">×</button></div>
        <div>
        ${groups.map((g, gi) => `
          <div style="border:1px solid var(--line);border-radius:9px;padding:10px;margin-bottom:10px">
            <b>${esc(g.type)}重复：${esc(g.key)}（${g.leads.length} 条）</b>
            ${g.leads.map((l, li) => `
              <div style="display:flex;gap:8px;align-items:center;padding:4px 0">
                <input type="radio" name="keep${gi}" value="${l.id}" ${li === 0 ? "checked" : ""}>
                <span style="flex:1">${esc(l.name)} · ${esc(l.phone || "—")} · ${esc(l.source || "")} · ${esc(l.created_at || "")}</span>
              </div>`).join("")}
            <button class="btn sm primary" data-merge="${gi}" style="margin-top:6px">合并其余到保留项</button>
          </div>`).join("")}
        </div>
        <div class="modal-foot"><button class="btn" onclick="closeModal()">关闭</button></div>`, "wide");
      $$("[data-merge]").forEach((b) => b.onclick = async () => {
        const gi = +b.dataset.merge;
        const g = groups[gi];
        const keepId = +document.querySelector(`input[name="keep${gi}"]:checked`).value;
        const removeIds = g.leads.map((l) => l.id).filter((id) => id !== keepId);
        if (!removeIds.length) return;
        await api("/api/leads/merge", { method: "POST", body: { keep_id: keepId, remove_ids: removeIds } });
        toast(`已合并 ${removeIds.length} 条重复线索`, "ok");
        closeModal();
        loadLeadList();
      });
    } catch (e) { toast(e.message, "err"); }
  };
  $("#btn-mail-sel").onclick = () => {
    const ids = [...state.leads.selected];
    state.recipients = state.recipients.filter((r) => ids.includes(r.id));
    go("outreach");
  };
  $("#btn-del-sel").onclick = () => {
    const ids = [...state.leads.selected];
    if (!ids.length) return;
    confirmBox(`确定删除选中的 ${ids.length} 条线索？此操作不可恢复。`, async () => {
      for (const id of ids) await api("/api/leads/" + id, { method: "DELETE" });
      state.leads.selected.clear();
      toast(`已删除 ${ids.length} 条线索`, "ok");
      loadLeadList();
    });
  };
  loadLeadList();

  // 恢复全量评分任务进度（切走再回来不丢）
  api("/api/leads/score_all").then((d) => {
    if (d.job && d.job.running) {
      $("#score-all-status").textContent = `⏳ 正在AI评分：${d.job.done}/${d.job.total || "计算中"}${d.job.current ? "（" + d.job.current + "）" : ""}`;
      pollScore();
    }
  }).catch(() => {});

  $("#view-list").onclick = () => {
    $("#view-list").classList.add("primary");
    $("#view-kanban").classList.remove("primary");
    $("#lead-kanban").style.display = "none";
    const card = $("#lead-table").closest(".card");
    if (card) card.style.display = "";
  };
  $("#view-kanban").onclick = () => {
    $("#view-kanban").classList.add("primary");
    $("#view-list").classList.remove("primary");
    const card = $("#lead-table").closest(".card");
    if (card) card.style.display = "none";
    $("#lead-kanban").style.display = "";
    renderKanban();
  };
  $("#lead-sort").onchange = () => {
    f.sort = $("#lead-sort").value;
    state.leads.page = 1;
    loadLeadList();
  };
}

async function renderKanban() {
  const f = state.leads.filters;
  const params = new URLSearchParams({ size: "300", q: f.q, type: f.type, region: f.region, tag: f.tag, source: f.source, sort: f.sort || "score_desc" });
  const data = await api("/api/leads?" + params.toString());
  const byStatus = {};
  state.meta.statuses.forEach((s) => byStatus[s] = []);
  data.items.forEach((l) => { (byStatus[l.status] || byStatus["新线索"] || []).push(l); });
  $("#lead-kanban").innerHTML = `<div class="kanban">${state.meta.statuses.map((s) => `
    <div class="kanban-col" data-status="${esc(s)}">
      <div class="kanban-head">${badge(s)} <span class="sub">${byStatus[s].length}</span></div>
      <div class="kanban-body">
        ${byStatus[s].map((l) => `
          <div class="kanban-card" draggable="true" data-id="${l.id}">
            <div><b>${esc(l.name)}</b></div>
            <div class="sub">${esc(l.phone || l.email || "无联系方式")}</div>
            <div style="margin-top:4px">${scoreBadge(l.score)} ${(l.tags || "").split(",").filter(Boolean).slice(0, 2).map((t) => `<span class="tag-chip">${esc(t)}</span>`).join("")}</div>
          </div>`).join("") || `<div class="empty" style="padding:14px">空</div>`}
      </div>
    </div>`).join("")}</div>`;
  $$(".kanban-card", $("#lead-kanban")).forEach((c) => {
    c.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", c.dataset.id));
    c.addEventListener("click", () => openLeadDetail(+c.dataset.id));
  });
  $$(".kanban-col", $("#lead-kanban")).forEach((col) => {
    col.addEventListener("dragover", (e) => e.preventDefault());
    col.addEventListener("drop", async (e) => {
      e.preventDefault();
      const id = +e.dataTransfer.getData("text/plain");
      const status = col.dataset.status;
      if (!id || !status) return;
      await api("/api/leads/" + id, { method: "PUT", body: { status } });
      toast(`已移动到“${status}”`, "ok");
      renderKanban();
    });
  });
}

async function loadLeadList() {
  const f = state.leads.filters;
  const params = new URLSearchParams({ page: state.leads.page, size: state.leads.size, q: f.q, status: f.status, type: f.type, region: f.region, tag: f.tag, source: f.source, sort: f.sort || "score_desc" });
  const data = await api("/api/leads?" + params.toString());
  state.leads.total = data.total;
  const sel = state.leads.selected;
  const rows = data.items.map((r) => `
    <tr>
      <td><input type="checkbox" class="row-check" data-id="${r.id}" ${sel.has(r.id) ? "checked" : ""}></td>
      <td class="name-cell clickable" onclick="openLeadDetail(${r.id})">${esc(r.name)}</td>
      <td>${esc(r.contact || "—")}</td>
      <td>${r.phone ? `<a href="tel:${esc(r.phone)}">${esc(r.phone)}</a>` : "—"}<div class="sub">${r.email ? `<a href="mailto:${esc(r.email)}">${esc(r.email)}</a>` : ""}</div></td>
      <td>${esc(r.region || "—")}</td>
      <td><span class="type-chip">${esc(r.type)}</span></td>
      <td>${badge(r.status)}</td>
      <td>${(r.tags || "").split(",").filter(Boolean).map((t) => `<span class="tag-chip">${esc(t)}</span>`).join("")}</td>
      <td>${scoreBadge(r.score)}${r.score_reason ? `<div class="sub" style="max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.score_reason)}">${esc(r.score_reason)}</div>` : ""}</td>
      <td>${fmtDate(r.updated_at)}</td>
      <td><div class="row-actions">
        <button class="btn sm" onclick="openLeadDetail(${r.id})">查看</button>
        <button class="btn sm" onclick="openLeadForm(${r.id})">编辑</button>
        <button class="btn sm danger" onclick="deleteLead(${r.id})">删除</button>
      </div></td>
    </tr>`).join("");
  $("#lead-table").innerHTML = rows
    ? `<table><thead><tr>
        <th style="width:30px"></th><th>公司名称</th><th>联系人</th><th>联系方式</th><th>地区</th>
        <th>类型</th><th>状态</th><th>标签</th><th>评分</th><th>更新时间</th><th>操作</th>
      </tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty"><div class="ico">🗂️</div>没有找到线索，试试调整筛选条件，或去“线索采集”添加</div>`;
  const totalPages = Math.max(1, Math.ceil(data.total / state.leads.size));
  $("#lead-pager").innerHTML = `
    <span class="info">共 ${data.total} 条 · 第 ${data.page}/${totalPages} 页</span>
    <button class="btn sm" id="pg-prev" ${data.page <= 1 ? "disabled" : ""}>上一页</button>
    <button class="btn sm" id="pg-next" ${data.page >= totalPages ? "disabled" : ""}>下一页</button>`;
  $("#pg-prev").onclick = () => { state.leads.page--; loadLeadList(); };
  $("#pg-next").onclick = () => { state.leads.page++; loadLeadList(); };
  $$(".row-check", $("#lead-table")).forEach((c) => {
    c.onchange = () => {
      const id = +c.dataset.id;
      if (c.checked) sel.add(id); else sel.delete(id);
      $("#btn-mail-sel").disabled = sel.size === 0;
      $("#btn-del-sel").disabled = sel.size === 0;
    };
  });
}

function exportLeads(fmt) {
  const f = state.leads.filters;
  const params = new URLSearchParams({ fmt, q: f.q, status: f.status, type: f.type, region: f.region, tag: f.tag, source: f.source });
  window.location.href = "/api/leads/export?" + params.toString();
}

function deleteLead(id) {
  confirmBox("确定删除这条线索？", async () => {
    await api("/api/leads/" + id, { method: "DELETE" });
    toast("已删除", "ok");
    loadLeadList();
  });
}

/* ---------- 线索表单 / 详情 ---------- */
function leadFormHtml(lead = {}) {
  const l = lead.id ? lead : {};
  return `
    <div class="modal-head"><h2>${lead.id ? "编辑线索" : "新增线索"}</h2><button class="close-x" onclick="closeModal()">×</button></div>
    <div class="form-grid">
      <div class="field"><label>公司名称 *</label><input class="input full" id="f-name" value="${esc(l.name || "")}"></div>
      <div class="field"><label>联系人</label><input class="input full" id="f-contact" value="${esc(l.contact || "")}"></div>
      <div class="field"><label>电话</label><input class="input full" id="f-phone" value="${esc(l.phone || "")}"></div>
      <div class="field"><label>邮箱</label><input class="input full" id="f-email" value="${esc(l.email || "")}"></div>
      <div class="field"><label>地区</label><input class="input full" id="f-region" placeholder="如：广东深圳" value="${esc(l.region || "")}"></div>
      <div class="field"><label>客户类型</label><select class="select full" id="f-type">
        ${state.meta.types.map((t) => `<option ${(l.type || "其他") === t ? "selected" : ""}>${esc(t)}</option>`).join("")}
      </select></div>
      <div class="field"><label>状态</label><select class="select full" id="f-status">
        ${state.meta.statuses.map((s) => `<option ${(l.status || "新线索") === s ? "selected" : ""}>${esc(s)}</option>`).join("")}
      </select></div>
      <div class="field"><label>线索来源</label><select class="select full" id="f-source">
        ${["手动录入", "网页采集", "Excel导入", "老客户转介绍", "展会/活动", "其他"].map((s) => `<option ${(l.source || "手动录入") === s ? "selected" : ""}>${esc(s)}</option>`).join("")}
      </select></div>
      <div class="field"><label>标签（逗号分隔）</label><input class="input full" id="f-tags" placeholder="光缆,FTTH" value="${esc(l.tags || "")}"></div>
      <div class="field"><label>下次跟进日期</label><input class="input full" type="date" id="f-reminder" value="${esc(l.reminder_date || "")}"></div>
      <div class="field" style="grid-column:1/-1"><label>公司地址</label><input class="input full" id="f-address" value="${esc(l.address || "")}"></div>
      <div class="field" style="grid-column:1/-1"><label>备注</label><textarea class="textarea full" id="f-note">${esc(l.note || "")}</textarea></div>
    </div>
    <div class="modal-foot">
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="save-lead">保存</button>
    </div>`;
}

function openLeadForm(id, presetStatus) {
  if (id) {
    api("/api/leads/" + id).then((lead) => {
      openModal(leadFormHtml(lead));
      $("#save-lead").onclick = () => saveLead(id);
    });
  } else {
    openModal(leadFormHtml());
    if (presetStatus) $("#f-status").value = presetStatus;
    $("#save-lead").onclick = () => saveLead();
  }
}

async function saveLead(id) {
  const data = {
    name: $("#f-name").value.trim(),
    contact: $("#f-contact").value.trim(),
    phone: $("#f-phone").value.trim(),
    email: $("#f-email").value.trim(),
    region: $("#f-region").value.trim(),
    type: $("#f-type").value,
    status: $("#f-status").value,
    source: $("#f-source").value,
    tags: $("#f-tags").value.trim(),
    address: $("#f-address").value.trim(),
    reminder_date: $("#f-reminder").value,
    note: $("#f-note").value.trim(),
  };
  if (!data.name) return toast("公司名称不能为空", "err");
  try {
    await api(id ? "/api/leads/" + id : "/api/leads", { method: id ? "PUT" : "POST", body: data });
    closeModal();
    toast("已保存", "ok");
    loadLeadList();
  } catch (e) { toast(e.message, "err"); }
}

async function openLeadDetail(id) {
  const lead = await api("/api/leads/" + id);
  const hist = await api(`/api/leads/${id}/history`);
  const timeline = [...hist.events.map((e) => ({ ...e, isEvent: true })), ...hist.notes.map((n) => ({ ...n, isNote: true }))]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .map((x) => x.isNote
      ? `<div class="tl-item"><div class="act">📝 备注</div><div class="dt">${esc(x.created_at)}</div><div class="det">${esc(x.content)}</div></div>`
      : `<div class="tl-item"><div class="act">${esc(x.action)}</div><div class="dt">${esc(x.created_at)}</div><div class="det">${esc(x.detail || "")}</div></div>`)
    .join("") || `<div class="empty">暂无记录</div>`;
  const notesHtml = hist.notes.map((n) => `
    <div class="mini-row"><div>${esc(n.content)}</div><div class="when">${esc(n.created_at)}</div></div>`).join("");
  openModal(`
    <div class="modal-head">
      <h2>${esc(lead.name)} ${badge(lead.status)}</h2>
      <button class="close-x" onclick="closeModal()">×</button>
    </div>
    <div class="detail-grid">
      <div class="item"><div class="k">联系人</div><div class="v">${esc(lead.contact || "—")}</div></div>
      <div class="item"><div class="k">电话</div><div class="v">${esc(lead.phone || "—")}</div></div>
      <div class="item"><div class="k">邮箱</div><div class="v">${esc(lead.email || "—")}</div></div>
      <div class="item"><div class="k">地区</div><div class="v">${esc(lead.region || "—")}</div></div>
      <div class="item"><div class="k">客户类型</div><div class="v">${esc(lead.type)}</div></div>
      <div class="item"><div class="k">线索来源</div><div class="v">${esc(lead.source)}</div></div>
      <div class="item"><div class="k">标签</div><div class="v">${(lead.tags || "").split(",").filter(Boolean).map((t) => `<span class="tag-chip">${esc(t)}</span>`).join("") || "—"}</div></div>
      <div class="item"><div class="k">下次跟进</div><div class="v">${esc(lead.reminder_date || "—")}</div></div>
      <div class="item"><div class="k">最近联系</div><div class="v">${esc(lead.last_contacted || "—")}（${lead.contact_count || 0} 次）</div></div>
      <div class="item"><div class="k">线索评分</div><div class="v">${scoreBadge(lead.score)} <span class="sub">${esc(lead.score_reason || "")}</span></div></div>
      <div class="item" style="grid-column:1/-1"><div class="k">地址</div><div class="v">${esc(lead.address || "—")}</div></div>
      <div class="item" style="grid-column:1/-1"><div class="k">备注</div><div class="v">${esc(lead.note || "—")}</div></div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
      <button class="btn sm" id="d-edit">编辑</button>
      <button class="btn sm" id="d-mail">发邮件</button>
      <button class="btn sm" id="d-sample">📦 寄样品</button>
      <button class="btn sm" id="d-analyze">🤖 AI 客户分析</button>
      <button class="btn sm" id="d-company">🏢 查工商</button>
      <button class="btn sm" id="d-score">🎯 AI 评分</button>
      <button class="btn sm" id="d-ai-followup">✍️ AI 跟进</button>
      <button class="btn sm" id="d-ai-intel">🔍 公司背景</button>
      <button class="btn sm danger" id="d-del">删除</button>
    </div>
    <div id="d-ai-box" style="display:none;margin-bottom:14px;background:rgba(59,130,246,.06);border:1px solid rgba(59,130,246,.25);border-radius:10px;padding:12px"></div>
    <h3 style="margin-bottom:8px">记录一次联系</h3>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:14px">
      <select class="select" id="d-contact-type">
        <option>电话</option><option>微信</option><option>邮件</option><option>拜访</option><option>样品</option><option>其他</option>
      </select>
      <input class="input grow" id="d-contact-note" placeholder="这次联系的内容…">
      <button class="btn primary" id="d-contact">✅ 记录联系</button>
    </div>
    <h3 style="margin-bottom:8px">添加跟进备注</h3>
    <div style="display:flex;gap:8px;margin-bottom:14px">
      <input class="input grow" id="d-note-input" placeholder="记录这次沟通的内容…">
      <button class="btn" id="d-note-btn">添加</button>
    </div>
    <h3 style="margin-bottom:8px">跟进时间线</h3>
    <div class="timeline">${timeline}</div>
  `, "wide");
  $("#d-contact").onclick = async () => {
    await api("/api/leads/contacted", { method: "POST", body: { id, type: $("#d-contact-type").value, note: $("#d-contact-note").value.trim() } });
    toast("已记录一次联系", "ok");
    closeModal(); openLeadDetail(id);
  };
  $("#d-edit").onclick = () => { closeModal(); openLeadForm(id); };
  $("#d-mail").onclick = () => {
    if (lead.email) state.recipients = state.recipients.filter((r) => r.id !== id).concat({ id, name: lead.name, email: lead.email, phone: lead.phone });
    closeModal(); go("outreach");
  };
  $("#d-score").onclick = async () => {
    const btn = $("#d-score");
    btn.disabled = true; btn.textContent = "评分中…";
    try {
      await api("/api/leads/score", { method: "POST", body: { id, use_ai: true, fallback: true } });
      toast("评分完成", "ok");
      closeModal(); openLeadDetail(id);
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; btn.textContent = "🎯 AI 评分"; }
  };
  $("#d-sample").onclick = async () => {
    try {
      await api("/api/leads/sample", { method: "POST", body: { id } });
      toast("已记录寄样，7天后自动提醒跟进", "ok");
      closeModal(); openLeadDetail(id);
    } catch (e) { toast(e.message, "err"); }
  };
  $("#d-analyze").onclick = async () => {
    try {
      await api("/api/leads/analyze", { method: "POST", body: { id } });
      toast("AI 客户分析已启动，完成后自动写入跟进记录（进度看右上角任务栏）", "ok");
    } catch (e) { toast(e.message, "err"); }
  };
  $("#d-company").onclick = async () => {
    const btn = $("#d-company");
    btn.disabled = true; btn.textContent = "查询中…";
    try {
      const r = await api("/api/company/lookup", { method: "POST", body: { keyword: lead.name, lead_id: id } });
      toast("工商查询已启动，完成后自动写入跟进记录（进度看右上角任务栏）", "ok");
      pollCompanyTask(r.task_id, () => { closeModal(); openLeadDetail(id); });
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; btn.textContent = "🏢 查工商"; }
  };
  $("#d-ai-followup").onclick = async () => {
    const box = $("#d-ai-box");
    box.style.display = "block";
    box.innerHTML = `<div class="sub">✍️ AI 正在按该线索画像生成跟进内容…</div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn sm" data-k="email">📧 邮件</button>
        <button class="btn sm" data-k="sms">💬 短信</button>
        <button class="btn sm" data-k="opening">💡 开场话术</button>
      </div>
      <div id="d-ai-out" style="margin-top:10px;white-space:pre-wrap"></div>`;
    box.querySelectorAll("button[data-k]").forEach((b) => {
      b.onclick = async () => {
        const out = $("#d-ai-out");
        out.textContent = "生成中…";
        try {
          const r = await api("/api/ai/followup", { method: "POST", body: { lead_id: id, kind: b.dataset.k } });
          if (r.kind === "email") {
            out.innerHTML = `<b>主题：</b>${esc(r.subject)}\n\n${esc(r.body)}\n\n<button class="btn sm" onclick="navigator.clipboard.writeText(${JSON.stringify(r.subject + "\n\n" + r.body)})">复制</button>`;
          } else {
            out.innerHTML = `${esc(r.text)}\n\n<button class="btn sm" onclick="navigator.clipboard.writeText(${JSON.stringify(r.text)})">复制</button>`;
          }
        } catch (e) { out.textContent = "生成失败：" + e.message; }
      };
    });
  };
  $("#d-ai-intel").onclick = async () => {
    const box = $("#d-ai-box");
    box.style.display = "block";
    box.innerHTML = `<div class="sub">🔍 AI 正在联网分析「${esc(lead.name)}」背景…</div><div id="d-ai-out" style="margin-top:10px"></div>`;
    try {
      const r = await api("/api/ai/company_intel", { method: "POST", body: { company: lead.name, region: lead.region } });
      const i = r.intel;
      box.querySelector("#d-ai-out").innerHTML = `
        <div class="mini-row"><span>规模</span><b>${esc(i.scale)}</b></div>
        <div class="mini-row"><span>匹配度</span><b>${esc(i.match)}</b> <span class="sub">${esc(i.match_reason)}</span></div>
        <div class="mini-row"><span>采购信号</span><b>${esc(i.signal)}</b></div>
        <div class="mini-row"><span>主营</span><b>${esc(i.business)}</b></div>
        <div class="mini-row" style="grid-column:1/-1"><span>简介</span><b>${esc(i.summary)}</b></div>
        ${i.inferred ? '<div class="sub" style="color:#f59e0b">⚠ 未能联网核实，以上为基于名称推断，仅供参考</div>' : ''}`;
    } catch (e) { box.querySelector("#d-ai-out").textContent = "分析失败：" + e.message; }
  };
  $("#d-del").onclick = () => { closeModal(); deleteLead(id); };
  $("#d-note-btn").onclick = async () => {
    const v = $("#d-note-input").value.trim();
    if (!v) return;
    await api(`/api/leads/${id}/notes`, { method: "POST", body: { content: v } });
    closeModal(); openLeadDetail(id);
  };
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pollCompanyTask(taskId, onDone) {
  for (let i = 0; i < 60; i++) {
    await sleep(1200);
    try {
      const d = await api("/api/tasks");
      const t = (d.tasks || []).find((x) => x.id === taskId);
      if (!t) return;
      if (t.status === "成功" && t.result) {
        showCompanyInfo(t.result);
        if (onDone) setTimeout(onDone, 600);
        return;
      }
      if (t.status === "失败") {
        toast("工商查询失败：" + (t.message || "请检查密钥和公司名称"), "err");
        return;
      }
    } catch (e) { return; }
  }
}

function showCompanyInfo(info) {
  const rows = [
    ["来源", info.source], ["公司名称", info.company], ["统一社会信用代码", info.credit_code],
    ["法定代表人", info.legal_person], ["注册资本", info.reg_capital], ["成立时间", info.estiblish_time],
    ["经营状态", info.reg_status], ["地址", info.address], ["联系电话", info.phone], ["邮箱", info.email],
  ].filter(([, v]) => v);
  openModal(`
    <div class="modal-head"><h2>🏢 工商信息（${esc(info.source || "")}）</h2><button class="close-x" onclick="closeModal()">×</button></div>
    <div class="detail-grid">${rows.map(([k, v]) => `<div class="item"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("")}</div>
    <div class="hint" style="margin-top:10px">信息已自动写入该客户的跟进记录；客户资料里空缺的电话/邮箱/地址也已自动补充。</div>
    <div class="modal-foot"><button class="btn primary" onclick="closeModal()">好的</button></div>`, "wide");
}

/* ---------- 线索采集 ---------- */
function activateSubTab(group, name) {
  $$(`.sub-tab[data-group="${group}"]`).forEach((t) => t.classList.toggle("active", t.dataset.name === name));
  $$(`.sub-panel[data-group="${group}"]`).forEach((p) => p.style.display = p.dataset.name === name ? "" : "none");
}

async function renderCollect() {
  const el = $("#page-collect");
  el.innerHTML = `
    <div class="page-title">线索采集</div>
    <div class="page-sub">三种方式把客户信息装进你的线索库：Excel 导入、网页采集、手动录入</div>
    <div class="sub-tabs">
      <button class="sub-tab active" data-group="collect" data-name="import">📥 批量导入</button>
      <button class="sub-tab" data-group="collect" data-name="crawl">🕸️ 网页采集</button>
      <button class="sub-tab" data-group="collect" data-name="auto">⏰ 定时自动采集</button>
      <button class="sub-tab" data-group="collect" data-name="social">📱 社媒评论导入</button>
      <button class="sub-tab" data-group="collect" data-name="wechat">💬 微信记录导入</button>
      <button class="sub-tab" data-group="collect" data-name="map">🗺️ 地图获客</button>
      <button class="sub-tab" data-group="collect" data-name="manual">✍️ 手动录入</button>
    </div>
    <div class="sub-panel" data-group="collect" data-name="import">
      <div class="card">
        <h3>从 Excel / CSV 批量导入</h3>
        <div class="drop-zone" id="drop-zone">
          <div class="big">点击选择文件，或拖拽到这里</div>
          <div>支持 .xlsx / .csv，自动识别公司名称、联系人、电话等列</div>
          <div style="margin-top:10px"><a href="/api/leads/template" class="btn primary">⬇ 下载导入模板</a></div>
        </div>
        <input type="file" id="file-input" accept=".xlsx,.csv" style="display:none">
        <div class="field" style="margin-top:14px"><label>提示</label>
          <div class="hint">表头请使用：公司名称、联系人、电话、邮箱、地区、客户类型、状态、来源、标签、备注、地址。导入时会自动按“电话/公司名”去重，重复的不再入库。</div>
        </div>
      </div>
      <div class="card">
        <h3>📦 WorkBuddy 拓客清单导入</h3>
        <div class="drop-zone" id="wb-zone">
          <div class="big">上传 WorkBuddy 导出的 leads.json</div>
          <div>自动映射公司/电话/邮箱/地区/类型/标签，保留匹配度+体量评分与 S/A/B/C 分级</div>
        </div>
        <input type="file" id="wb-file" accept=".json" style="display:none">
        <div class="hint" style="margin-top:10px">支持 WorkBuddy 拓客清单 JSON（含 meta + leads 数组）。导入后评分直接采用 WorkBuddy 结果并写入备注里的“切入产品 / 采购信号 / 最佳窗口 / 联系人”。</div>
      </div>
      <div class="card" id="import-result"></div>
    </div>
    <div class="sub-panel" data-group="collect" data-name="crawl" style="display:none">
      <div class="card">
        <h3>网页采集客户信息</h3>
        <div class="field"><label>网页地址</label>
          <input class="input full" id="crawl-url" placeholder="粘贴企业黄页 / 行业目录 / 公司列表页的网址，例如 https://example.com/company-list">
        </div>
        <div class="field"><label>或者直接粘贴网页源代码（高级用法）</label>
          <textarea class="textarea full" id="crawl-html" placeholder="如果网页无法直接打开，可复制页面源码粘贴到这里"></textarea>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn primary" id="crawl-btn">🕸️ 开始采集</button>
          <button class="btn" id="ai-extract-btn">✨ AI 智能提取</button>
        </div>
        <div class="hint" style="margin-top:8px">采集器会从页面里提取电话号码，并自动匹配附近的公司名称。部分网站有验证码、登录墙或反爬措施，采集不到时请换一个页面，或改用 Excel 导入。</div>
      </div>
      <div class="card" id="crawl-result"><div class="empty"><div class="ico">🕸️</div>还没有采集结果</div></div>
      <div class="card" id="ai-extract-result" style="display:none"></div>
    </div>
    <div class="sub-panel" data-group="collect" data-name="auto" style="display:none">
      <div class="card">
        <h3>⏰ 定时自动采集</h3>
        <div class="field"><label>采集网址列表（每行一个，支持企业黄页 / 目录 / 列表页）</label>
          <textarea class="textarea full" id="auto-urls" placeholder="https://example.com/company-list-1&#10;https://example.com/company-list-2"></textarea>
        </div>
        <div class="field"><label>采集间隔</label>
          <select class="select" id="auto-interval">
            <option value="0">关闭定时采集</option>
            <option value="1">每 1 小时</option>
            <option value="6">每 6 小时</option>
            <option value="12">每 12 小时</option>
            <option value="24">每天 1 次</option>
            <option value="72">每 3 天</option>
          </select>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn primary" id="auto-save">💾 保存配置</button>
          <button class="btn" id="auto-run">▶ 立即采集一次</button>
        </div>
        <div class="hint" style="margin-top:10px" id="auto-status">设置间隔并保存后，工具会在后台定时采集、自动去重入库，你只需定期到“客户线索”里跟进新线索。</div>
      </div>
      <div class="card"><h3>采集记录</h3><div id="auto-logs"><div class="empty">暂无记录</div></div></div>
    </div>
    <div class="sub-panel" data-group="collect" data-name="social" style="display:none">
      <div class="card">
        <h3>📱 抖音 / 小红书 评论线索导入</h3>
        <div class="drop-zone" id="social-zone">
          <div class="big">上传评论导出文件</div>
          <div>支持 .xlsx / .csv，自动按“评论人昵称”创建线索，评论内容写入跟进备注</div>
          <div style="margin-top:10px"><a href="/api/leads/template?kind=social" class="btn primary">⬇ 下载社媒评论模板</a></div>
        </div>
        <input type="file" id="social-file" accept=".xlsx,.csv" style="display:none">
        <div class="hint" style="margin-top:10px">可用抖音评论采集工具（如 douyin_one_spider）或小红书评论导出后，把“评论人昵称、评论内容、作品链接”整理成模板格式导入。</div>
      </div>
      <div class="card" id="social-result"></div>
    </div>
    <div class="sub-panel" data-group="collect" data-name="wechat" style="display:none">
      <div class="card">
        <h3>💬 微信聊天记录导入</h3>
        <div class="drop-zone" id="wechat-zone">
          <div class="big">上传聊天记录文本</div>
          <div>支持 .txt / .csv，按联系人自动创建/匹配线索，聊天内容写入跟进记录</div>
          <div style="margin-top:10px"><a href="/api/leads/template?kind=wechat" class="btn primary">⬇ 下载微信记录格式说明</a></div>
        </div>
        <input type="file" id="wechat-file" accept=".txt,.csv" style="display:none">
        <div class="hint" style="margin-top:10px">可用微信导出工具（如 WeChatMsgDump / WeChatMsg）导出聊天记录文本后导入；也支持“时间 联系人: 内容”格式。请确保你有权处理这些聊天数据。</div>
      </div>
      <div class="card" id="wechat-result"></div>
    </div>
    <div class="sub-panel" data-group="collect" data-name="map" style="display:none">
      <div class="card">
        <h3>🗺️ 地图获客（高德搜索）</h3>
        <div class="form-grid">
          <div class="field"><label>搜索关键词</label><input class="input full" id="map-kw" placeholder="弱电工程 / 安防监控 / 机房建设 / 通信工程"></div>
          <div class="field"><label>城市</label><input class="input full" id="map-city" placeholder="广州 / 上海"></div>
          <div class="field"><label>抓取页数（每页 25 条）</label><select class="select full" id="map-pages">
            <option value="1">1 页</option><option value="2" selected>2 页</option><option value="4">4 页</option>
          </select></div>
        </div>
        <button class="btn primary" id="map-run">🗺️ 开始搜索</button>
        <div class="hint" style="margin-top:8px">地图源在“设置 → 地图接口”选择：高德（国内，需免费 Key）或谷歌地图（复用 SerpAPI Key，可搜海外）。内置请求延迟防封。</div>
      </div>
      <div class="card" id="map-result"><div class="empty">搜索结果显示在这里</div></div>
      <div class="card">
        <h3>或用采集器导出后导入（Web Scraper / 后羿采集器）</h3>
        <div class="drop-zone" id="map-zone">
          <div class="big">上传地图采集导出文件</div>
          <div>支持 .xlsx / .csv，表头：公司名称、地址、电话、分类、城市</div>
          <div style="margin-top:10px"><a href="/api/leads/template?kind=map" class="btn primary">⬇ 下载地图线索模板</a></div>
        </div>
        <input type="file" id="map-file" accept=".xlsx,.csv" style="display:none">
      </div>
      <div class="card" id="map-import-result"></div>
    </div>
    <div class="sub-panel" data-group="collect" data-name="manual" style="display:none">
      <div class="card"><div id="manual-form"></div></div>
    </div>`;
  $$(".sub-tab", el).forEach((t) => t.onclick = () => {
    state.collectTab = t.dataset.name;
    activateSubTab("collect", t.dataset.name);
  });

  // 手动录入
  $("#manual-form").innerHTML = leadFormHtml();
  $("#save-lead").onclick = async () => {
    await saveLead();
    renderCollect();
  };

  // 文件导入
  const dz = $("#drop-zone");
  const fileInput = $("#file-input");
  dz.onclick = () => fileInput.click();
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
  dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0], "", "#import-result"); };
  fileInput.onchange = () => { if (fileInput.files[0]) uploadFile(fileInput.files[0], "", "#import-result"); };

  // WorkBuddy 拓客清单导入
  const wbZone = $("#wb-zone"), wbInput = $("#wb-file");
  wbZone.onclick = () => wbInput.click();
  wbZone.ondragover = (e) => { e.preventDefault(); wbZone.classList.add("over"); };
  wbZone.ondragleave = () => wbZone.classList.remove("over");
  wbZone.ondrop = (e) => { e.preventDefault(); wbZone.classList.remove("over"); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0], "workbuddy", "#import-result"); };
  wbInput.onchange = () => { if (wbInput.files[0]) uploadFile(wbInput.files[0], "workbuddy", "#import-result"); };

  // 社媒评论导入
  const sZone = $("#social-zone"), sInput = $("#social-file");
  sZone.onclick = () => sInput.click();
  sZone.ondragover = (e) => { e.preventDefault(); sZone.classList.add("over"); };
  sZone.ondragleave = () => sZone.classList.remove("over");
  sZone.ondrop = (e) => { e.preventDefault(); sZone.classList.remove("over"); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0], "social", "#social-result"); };
  sInput.onchange = () => { if (sInput.files[0]) uploadFile(sInput.files[0], "social", "#social-result"); };

  // 微信记录导入
  const wZone = $("#wechat-zone"), wInput = $("#wechat-file");
  wZone.onclick = () => wInput.click();
  wZone.ondragover = (e) => { e.preventDefault(); wZone.classList.add("over"); };
  wZone.ondragleave = () => wZone.classList.remove("over");
  wZone.ondrop = (e) => { e.preventDefault(); wZone.classList.remove("over"); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0], "wechat", "#wechat-result"); };
  wInput.onchange = () => { if (wInput.files[0]) uploadFile(wInput.files[0], "wechat", "#wechat-result"); };

  // 地图获客
  const mZone = $("#map-zone"), mInput = $("#map-file");
  mZone.onclick = () => mInput.click();
  mZone.ondragover = (e) => { e.preventDefault(); mZone.classList.add("over"); };
  mZone.ondragleave = () => mZone.classList.remove("over");
  mZone.ondrop = (e) => { e.preventDefault(); mZone.classList.remove("over"); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0], "map", "#map-import-result"); };
  mInput.onchange = () => { if (mInput.files[0]) uploadFile(mInput.files[0], "map", "#map-import-result"); };
  $("#map-run").onclick = async () => {
    const btn = $("#map-run");
    btn.disabled = true;
    try {
      await api("/api/map", { method: "POST", body: { keyword: $("#map-kw").value.trim(), city: $("#map-city").value.trim(), pages: $("#map-pages").value } });
      $("#map-result").innerHTML = `<div class="empty"><div class="ico">⏳</div>地图搜索已启动，正在后台进行…</div>`;
      pollMapJob();
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; }
  };

  // 网页采集
  $("#crawl-btn").onclick = async () => {
    const btn = $("#crawl-btn");
    btn.disabled = true; btn.textContent = "采集中…";
    try {
      const data = await api("/api/crawl", { method: "POST", body: { url: $("#crawl-url").value.trim(), html: $("#crawl-html").value.trim() } });
      if (!data.ok) {
        $("#crawl-result").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(data.error || "采集失败")}</div>`;
        return;
      }
      state.candidates = data.candidates || [];
      renderCandidates();
    } catch (e) {
      $("#crawl-result").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(e.message)}</div>`;
    } finally {
      btn.disabled = false; btn.textContent = "🕸️ 开始采集";
    }
  };

  $("#ai-extract-btn").onclick = async () => {
    const btn = $("#ai-extract-btn");
    btn.disabled = true; btn.textContent = "AI 提取中…";
    try {
      const res = await api("/api/crawl/ai_extract", { method: "POST", body: { url: $("#crawl-url").value.trim(), html: $("#crawl-html").value.trim() } });
      const box = $("#ai-extract-result");
      if (!res.ai) {
        state.candidates = res.extracted || [];
        renderCandidates();
        box.style.display = "none";
        toast(res.msg || "规则提取完成", "ok");
        return;
      }
      const e = res.extracted || {};
      const signal = e.buyer_signal === "高" ? '<span class="badge sc-high">采购意向：高</span>'
        : e.buyer_signal === "中" ? '<span class="badge sc-mid">采购意向：中</span>'
        : '<span class="badge sc-low">采购意向：低</span>';
      box.style.display = "";
      box.innerHTML = `
        <h3>AI 智能提取结果 ${signal}</h3>
        <div class="detail-grid" style="margin-top:10px">
          <div class="item"><div class="k">公司/主体</div><div class="v">${esc(e.company || "—")}</div></div>
          <div class="item"><div class="k">联系人</div><div class="v">${esc(e.contact || "—")}</div></div>
          <div class="item"><div class="k">电话</div><div class="v">${esc(e.phone || "—")}</div></div>
          <div class="item"><div class="k">邮箱</div><div class="v">${esc(e.email || "—")}</div></div>
          <div class="item"><div class="k">地址</div><div class="v">${esc(e.address || "—")}</div></div>
          <div class="item"><div class="k">标签</div><div class="v">${esc(e.tags || "—")}</div></div>
          <div class="item" style="grid-column:1/-1"><div class="k">简介</div><div class="v">${esc(e.summary || "—")}</div></div>
        </div>
        <button class="btn primary" id="ai-extract-add">＋ 添加为线索</button>`;
      $("#ai-extract-add").onclick = async () => {
        const lead = {
          name: e.company || "未命名",
          contact: e.contact, phone: e.phone, email: e.email,
          address: e.address, tags: e.tags || "网页采集",
          note: (e.summary ? "简介：" + e.summary + "\n" : "") + "采购意向：" + (e.buyer_signal || "待定") + "；来源网页：" + $("#crawl-url").value,
          source: "网页采集",
        };
        const res = await api("/api/leads/bulk", { method: "POST", body: { leads: [lead], source: "网页采集" } });
        toast(res.added.length ? "已添加线索" : "已存在重复，未重复添加", res.added.length ? "ok" : "err");
      };
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; btn.textContent = "✨ AI 智能提取"; }
  };

  // 定时自动采集
  const loadAuto = async () => {
    const s = (await api("/api/settings")).settings;
    $("#auto-urls").value = s.auto_crawl_urls || "";
    $("#auto-interval").value = s.auto_crawl_interval || "0";
    $("#auto-status").textContent = s.auto_crawl_interval && s.auto_crawl_interval !== "0"
      ? `定时采集已开启：每 ${s.auto_crawl_interval} 小时执行一次${s.last_auto_crawl ? "（上次执行：" + s.last_auto_crawl + "）" : ""}`
      : "定时采集已关闭。设置间隔并保存后自动开启。";
    const d = await api("/api/crawl/auto");
    const rows = d.logs.map((l) => `
      <tr>
        <td>${esc(l.run_at)}</td>
        <td style="max-width:280px;word-break:break-all">${esc(l.url)}</td>
        <td>${l.found}</td>
        <td style="color:var(--green);font-weight:600">+${l.added}</td>
        <td>${l.skipped}</td>
        <td class="sub">${esc(l.error || "—")}</td>
      </tr>`).join("");
    $("#auto-logs").innerHTML = rows
      ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>网址</th><th>发现</th><th>新增</th><th>跳过</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="empty">暂无采集记录</div>`;
  };
  $("#auto-save").onclick = async () => {
    await api("/api/settings", { method: "POST", body: { settings: {
      auto_crawl_urls: $("#auto-urls").value.trim(),
      auto_crawl_interval: $("#auto-interval").value,
    } } });
    toast("配置已保存", "ok");
    loadAuto();
  };
  $("#auto-run").onclick = async () => {
    const btn = $("#auto-run");
    btn.disabled = true; btn.textContent = "采集中…";
    try {
      const res = await api("/api/crawl/auto", { method: "POST", body: { urls: $("#auto-urls").value.trim() } });
      toast(`采集完成：新增 ${res.added} 条线索`, "ok");
      loadAuto();
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; btn.textContent = "▶ 立即采集一次"; }
  };
  loadAuto();
  activateSubTab("collect", state.collectTab || "import");
  if (state.collectTab === "map") {
    api("/api/map").then((d) => {
      const job = d.job || {};
      if (job.running) {
        $("#map-result").innerHTML = `<div class="empty"><div class="ico">🗺️</div>${esc(job.stage || "正在搜索地图客户")}…（进度看右上角任务栏）</div>`;
        pollMapJob();
      } else if (job.result && job.result.length) {
        renderMapResult(job.result);
      } else if (job.message) {
        $("#map-result").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(job.message)}</div>`;
      }
    }).catch(() => {});
  }
}

function renderCandidates() {
  const list = state.candidates || [];
  const box = $("#crawl-result");
  if (!list.length) return;
  box.innerHTML = `
    <h3>采集到 ${list.length} 条线索</h3>
    <div style="margin:10px 0;display:flex;gap:8px;align-items:center">
      <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="cand-all"> 全选</label>
      <button class="btn primary sm" id="cand-add">＋ 添加选中（自动去重）</button>
      <span class="hint" id="cand-hint"></span>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th style="width:30px"></th><th>公司名称</th><th>电话</th><th>地址</th><th>来源</th></tr></thead>
      <tbody>${list.map((c, i) => `
        <tr>
          <td><input type="checkbox" class="cand-check" data-i="${i}" checked></td>
          <td><input class="input" data-i="${i}" data-k="name" value="${esc(c.name)}" style="width:100%"></td>
          <td><input class="input" data-i="${i}" data-k="phone" value="${esc(c.phone)}" style="width:130px"></td>
          <td><input class="input" data-i="${i}" data-k="address" value="${esc(c.address || "")}" style="width:100%"></td>
          <td class="sub">网页采集</td>
        </tr>`).join("")}
      </tbody></table></div>`;
  $("#cand-all").onchange = (e) => $$(".cand-check").forEach((c) => c.checked = e.target.checked);
  $$(".cand-check").forEach((c) => c.onchange = updateCandHint);
  $$(".cand-input").forEach(() => {});
  $$("input[data-k]", box).forEach((inp) => inp.onchange = () => {
    list[+inp.dataset.i][inp.dataset.k] = inp.value.trim();
  });
  updateCandHint();
  $("#cand-add").onclick = async () => {
    const picks = list.filter((_, i) => $$(".cand-check")[i] && $$(".cand-check")[i].checked);
    if (!picks.length) return toast("请先勾选要添加的线索", "err");
    const res = await api("/api/leads/bulk", { method: "POST", body: { leads: picks, source: "网页采集" } });
    const kept = picks.filter((p) => !res.duplicates.some((d) => d.name === p.name));
    state.candidates = state.candidates.filter((c) => !kept.includes(c));
    toast(`已添加 ${res.added.length} 条，跳过重复 ${res.duplicates.length} 条`, "ok");
    if (state.candidates.length) renderCandidates();
    else $("#crawl-result").innerHTML = `<div class="empty"><div class="ico">✅</div>全部处理完成，去“客户线索”查看吧</div>`;
  };
}

function updateCandHint() {
  const n = $$(".cand-check").filter((c) => c.checked).length;
  $("#cand-hint").textContent = `已选 ${n} 条`;
}

function uploadFile(file, kind, resultSel) {
  const fd = new FormData();
  fd.append("file", file);
  if (kind) fd.append("kind", kind);
  const resultBox = $(resultSel || "#import-result");
  resultBox.innerHTML = `<div class="empty"><div class="ico">⏳</div>正在导入 ${esc(file.name)}…</div>`;
  fetch("/api/import", { method: "POST", body: fd })
    .then((r) => r.json())
    .then((d) => {
      if (!d.ok) throw new Error(d.msg);
      const dupRows = d.duplicates.map((x) => `<li>第 ${x.row} 行：${esc(x.name)}（${esc(x.msg)}）</li>`).join("");
      const errRows = d.errors.map((x) => `<li>第 ${x.row} 行：${esc(x.name || "")} ${esc(x.msg)}</li>`).join("");
      resultBox.innerHTML = `
        <h3>导入结果</h3>
        <p style="margin-top:8px">共读取 <b>${d.total}</b> 行，成功添加 <b style="color:var(--green)">${d.added.length}</b> 条，重复跳过 <b>${d.duplicates.length}</b> 条，出错 <b style="color:var(--red)">${d.errors.length}</b> 条。</p>
        ${dupRows ? `<div style="margin-top:8px"><b>重复明细：</b><ul style="margin:6px 0 0 18px;color:var(--muted)">${dupRows}</ul></div>` : ""}
        ${errRows ? `<div style="margin-top:8px"><b>错误明细：</b><ul style="margin:6px 0 0 18px;color:var(--red)">${errRows}</ul></div>` : ""}
        <div style="margin-top:12px"><button class="btn" onclick="go('leads')">去客户线索查看</button></div>`;
    })
    .catch((e) => { resultBox.innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(e.message)}</div>`; });
}

/* ---------- 买家发现 ---------- */
async function renderBuyer() {
  const el = $("#page-buyer");
  el.innerHTML = `
    <div class="page-title">买家发现</div>
    <div class="page-sub">一条流水线：① 描述业务让 AI 出方案 → ② 选“手动搜索”或“🧠 用引擎获客” → ③ 结果可再“送引擎筛选分级” → ④ 统一导入客户库</div>
    <div class="card">
      <h3>🤖 AI 获客策略助手（不限行业）</h3>
      <div class="field"><label>描述你的业务和理想客户（用大白话就行）</label>
        <textarea class="textarea full" id="strategy-desc" style="min-height:100px" placeholder="例如：我们做光纤通信设备，WDM和EDFA是拳头产品，想找海外有大型数据中心建设需求的集成商，最好利润空间大、对方有一定行业知名度。&#10;或者：我们做3C电子代工，想找北美有品牌的小家电客户。"></textarea>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn primary" id="strategy-gen">🤖 AI 生成获客方案</button>
        <span class="hint">AI 会生成 3-5 套方案（目标角色、关键词、地区、利润/需求/知名度、渠道、风险），关键词自动补齐“采购/招标/RFP”等买方意图词并排除百科/论文/同行页面，选一套即可一键开搜。需要先配置 AI 密钥（设置 → AI 文案）。</span>
      </div>
      <div id="strategy-plans" style="margin-top:14px"></div>
    </div>
    <div class="card">
      <h3>🧠 AI 获客引擎（条件驱动，自动迭代）</h3>
      <div class="form-grid">
        <div class="field" style="grid-column:1/-1"><label>主营产品（给 AI 判断用）</label><input class="input full" id="acq-products" placeholder="石英玻璃毛细管 / 光无源器件（准直器、滤光片、隔离器、透镜、套管）"></div>
        <div class="field"><label>必中规格/品类（逗号分隔）*</label><input class="input full" id="acq-specs" placeholder="DWDM,WDM,玻璃管"></div>
        <div class="field"><label>检索种子词（可选，逗号分隔）</label><input class="input full" id="acq-seeds" placeholder="DWDM 招标公告,光传输设备 采购商"></div>
        <div class="field"><label>目标市场（逗号分隔）</label><input class="input full" id="acq-regions" placeholder="中国大陆,亚太,欧美,中东非洲拉美"></div>
        <div class="field"><label>目标买方类型（逗号分隔）</label><input class="input full" id="acq-types" placeholder="光无源器件厂,光模块厂,系统集成商,近期招标扩容"></div>
        <div class="field"><label>最低等级</label><select class="select full" id="acq-tier">
          <option value="B">B 级及以上</option>
          <option value="A">A 级及以上</option>
          <option value="S">仅 S 级</option>
          <option value="C">全部</option>
        </select></div>
        <div class="field"><label>目标客户数</label><input class="input full" type="number" min="1" max="100" id="acq-max" value="30"></div>
        <div class="field" style="grid-column:1/-1"><label>排除词（逗号分隔）</label><input class="input full" id="acq-exclude" placeholder="自家企业,同行平台"></div>
        <div class="field"><label>搜索时间范围（可选）</label>
          <select class="select full" id="acq-recency">
            <option value="">不限</option>
            <option value="week">近 7 天</option>
            <option value="month">近 1 个月</option>
            <option value="year">近 1 年</option>
          </select>
        </div>
        <div class="field"><label>限定站点/域名（可选）</label><input class="input full" id="acq-site" placeholder="gov.cn,in 或具体官网"></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <button class="btn primary" id="acq-run">🚀 运行获客引擎</button>
        <button class="btn" id="acq-import" disabled>📥 导入客户库</button>
        <label style="display:flex;gap:6px;align-items:center;font-size:13px"><input type="checkbox" id="acq-manual"> 🗺️ 同时用地图POI补充（需地图Key）</label>
        <span class="hint">引擎按你的条件自动生成多渠道检索方案、发现并筛选买家、迭代补齐缺口；也可接收“AI 方案”或“手动搜索”的结果做离线筛选分级；完成后可一键导入客户库（保留评分与等级）。</span>
      </div>
      <div id="acq-plan-tag" style="display:none;margin-top:8px;padding:8px 10px;background:#eaf3fd;border:1px solid #bcd9f5;border-radius:8px;font-size:12px"></div>
      <div id="acq-result" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <h3>🔍 手动搜索（高级）</h3>
      <div class="hint" style="margin-bottom:10px">💡 技巧：关键词 = 产品词 + 买方动作（采购/招标/询价/项目），例如 “WDM 采购”、“波分复用设备 项目方”，比只写产品名精准得多。</div>
      <div class="form-grid">
        <div class="field" style="grid-column:1/-1"><label>搜索关键词（每行一个，建议 1-3 个）</label>
          <textarea class="textarea full" id="buyer-kws" placeholder="WDM 采购&#10;波分复用设备 招标&#10;光传输扩容 项目方"></textarea>
        </div>
        <div class="field" style="grid-column:1/-1"><label>行业获客词模板（一键套用）</label>
          <div id="buyer-presets" style="display:flex;gap:8px;flex-wrap:wrap"><span class="hint">加载中…</span></div>
        </div>
        <div class="field"><label>目标地区/市场（每行一个，可留空）</label>
          <textarea class="textarea full" id="buyer-markets" style="min-height:80px" placeholder="广东&#10;浙江&#10;海外：Peru"></textarea>
        </div>
        <div class="field"><label>每个关键词抓取数量</label>
          <select class="select full" id="buyer-max">
            <option value="3">3 条</option>
            <option value="5">5 条</option>
            <option value="8" selected>8 条</option>
            <option value="10">10 条</option>
          </select>
        </div>
      </div>
      <div class="field"><label>或直接指定网址抓取（每行一个，优先于关键词搜索）</label>
        <textarea class="textarea full" id="buyer-urls" style="min-height:60px" placeholder="https://example.com/company-a&#10;https://example.com/company-b"></textarea>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" id="buyer-run">🔍 开始发现买家</button>
        <label style="display:flex;gap:6px;align-items:center;font-size:13px"><input type="checkbox" id="buyer-ai"> AI 智能筛选（需要 API Key）</label>
        <span class="hint">AI 会结合上方的业务描述判断每条线索像不像你的买家（不限行业）并给出依据；未填描述时按“设置 → 行业”判断</span>
      </div>
    </div>
    <div class="card" id="buyer-result"><div class="empty"><div class="ico">🎯</div>搜索结果显示在这里</div></div>`;

  $("#buyer-run").onclick = async () => {
    const btn = $("#buyer-run");
    btn.disabled = true; btn.textContent = "启动中…";
    try {
      const res = await api("/api/buyer", { method: "POST", body: {
        keywords: $("#buyer-kws").value.trim(),
        markets: $("#buyer-markets").value.trim(),
        max_results: $("#buyer-max").value,
        urls: $("#buyer-urls").value.trim(),
        use_ai: $("#buyer-ai").checked,
        context: state.buyerContext || $("#strategy-desc").value.trim(),
      } });
      $("#buyer-result").innerHTML = `<div class="empty"><div class="ico">⏳</div>搜索任务已启动，正在后台进行…（页面可继续操作）</div>`;
      pollBuyerJob();
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; btn.textContent = "🔍 开始发现买家"; }
  };
  const loadPresets = async () => {
    try {
      const d = await api("/api/buyer/presets");
      const presets = d.presets || {};
      const box = $("#buyer-presets");
      box.innerHTML = Object.entries(presets).map(([name]) => `<button class="btn sm" data-p="${esc(name)}">${esc(name)}</button>`).join("");
      $$("#buyer-presets [data-p]").forEach((b) => b.onclick = () => {
        const p = presets[b.dataset.p];
        $("#buyer-kws").value = (p.keywords || []).join("\n");
        $("#buyer-markets").value = (p.markets || []).join("\n");
        toast("已套用模板：" + b.dataset.p, "ok");
      });
    } catch (e) { /* 忽略 */ }
  };
  loadPresets();

  $("#strategy-gen").onclick = async () => {
    const desc = $("#strategy-desc").value.trim();
    if (!desc) return toast("请先描述你的业务和客户需求", "err");
    state.buyerContext = desc;
    // 立即清掉上一次的方案，避免残留/闪烁
    $("#strategy-plans").innerHTML = `<div class="empty"><div class="ico">🤖</div>AI 正在生成新方案…（进度看右上角任务栏）</div>`;
    const btn = $("#strategy-gen");
    btn.disabled = true; btn.textContent = "启动中…";
    try {
      await api("/api/buyer/strategy", { method: "POST", body: { description: desc } });
      pollStrategy();
    } catch (e) {
      toast(e.message, "err");
      $("#strategy-plans").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(e.message)}</div>`;
    }
    finally { btn.disabled = false; btn.textContent = "🤖 AI 生成获客方案"; }
  };
  if (state.pendingStrategy) {
    $("#strategy-desc").value = state.pendingStrategy;
    state.pendingStrategy = "";
    $("#strategy-gen").click();
  }

  // AI 获客引擎
  $("#acq-run").onclick = async () => {
    const specs = $("#acq-specs").value.trim();
    if (!specs) return toast("请至少填写必中规格/品类", "err");
    const conditions = {
      industry: "光纤通信 / 光器件",
      products: $("#acq-products").value.trim(),
      specs,
      keywords: $("#acq-seeds").value.trim(),
      regions: $("#acq-regions").value.trim(),
      buyer_types: $("#acq-types").value.trim(),
      min_tier: $("#acq-tier").value,
      max_results: $("#acq-max").value || 30,
      exclude: $("#acq-exclude").value.trim(),
      recency: $("#acq-recency").value,
      site_scope: $("#acq-site").value.trim(),
    };
    if (state.acqPlan) conditions.ai_plan = state.acqPlan;
    try {
      await api("/api/acquisition/run", { method: "POST", body: { conditions, use_manual: $("#acq-manual").checked } });
      $("#acq-result").innerHTML = `<div class="empty"><div class="ico">🧠</div>获客引擎已启动，正在发现并筛选买家…（进度看右上角任务栏）</div>`;
      $("#acq-import").disabled = true;
      pollAcquisition();
    } catch (e) { toast(e.message, "err"); }
  };
  $("#acq-import").onclick = async () => {
    try {
      const r = await api("/api/acquisition/import", { method: "POST", body: {} });
      toast(`已导入 ${r.added} 家，跳过重复 ${r.duplicates} 家`, "ok");
      const box = $("#acq-result");
      box.innerHTML += `<div class="hint" style="margin-top:8px">✅ 已导入 ${r.added} 家客户（跳过重复 ${r.duplicates} 家），去“客户线索”查看</div>`;
    } catch (e) { toast(e.message, "err"); }
  };
  // 手动修改引擎条件后，自动解除“AI 方案带入”状态，避免旧方案残留
  ["acq-products", "acq-specs", "acq-seeds", "acq-regions", "acq-types", "acq-tier", "acq-max", "acq-exclude", "acq-recency", "acq-site"].forEach((id) => {
    const el = $("#" + id);
    if (el) el.addEventListener("input", () => {
      state.acqPlan = null;
      const tag = $("#acq-plan-tag");
      if (tag) tag.style.display = "none";
    });
  });
  // 恢复获客引擎任务状态（切走再回来不丢）
  api("/api/acquisition").then((d) => {
    const job = d.job || {};
    if (job.running) {
      $("#acq-result").innerHTML = `<div class="empty"><div class="ico">🧠</div>${esc(job.stage || "正在运行")}…（进度看右上角任务栏）</div>`;
      pollAcquisition();
    } else if (job.result) {
      renderAcqResult(job.result);
    }
  }).catch(() => {});

  // 恢复 AI 方案任务状态（切走再回来不丢）
  api("/api/buyer/strategy").then((d) => {
    const t = d.task || {};
    // 如果是刚发起的新一轮生成，跳过旧方案渲染，避免闪烁
    if (state.pendingStrategy) return;
    if (t.status === "运行中") {
      $("#strategy-plans").innerHTML = `<div class="empty"><div class="ico">⏳</div>${esc(t.stage || "AI 生成中")}…（进度看右上角任务栏）</div>`;
      if (!strategyPolling) pollStrategy();
    } else if (t.status === "成功" && t.result && t.result.plans) {
      renderPlans(t.result.plans, t.result.warnings || []);
    } else if (t.status === "失败") {
      $("#strategy-plans").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(t.message || "生成失败，请重试")}</div>`;
    }
  }).catch(() => {});

  // 恢复买家发现搜索任务状态
  api("/api/buyer").then((d) => {
    const job = d.job || {};
    if (job.running) {
      $("#buyer-result").innerHTML = `<div class="empty"><div class="ico">⏳</div>${esc(job.stage || "正在搜索并分析买家线索")}…（进度看右上角任务栏）</div>`;
      pollBuyerJob();
    } else if (job.result) {
      renderBuyerResult(job.result);
    }
  }).catch(() => {});
}

let strategyPolling = false;
async function pollStrategy() {
  strategyPolling = true;
  try {
    const d = await api("/api/buyer/strategy");
    const t = d.task || {};
    if (t.status === "运行中") {
      $("#strategy-plans").innerHTML = `<div class="empty"><div class="ico">⏳</div>${esc(t.stage || "AI 生成中")}…（进度看右上角任务栏）</div>`;
      setTimeout(pollStrategy, 1500);
      return;
    }
    if (t.status === "成功" && t.result && t.result.plans) {
      renderPlans(t.result.plans, t.result.warnings || []);
    } else {
      $("#strategy-plans").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(t.message || "生成失败，请重试")}</div>`;
    }
  } catch (e) { /* 忽略 */ }
  finally { strategyPolling = false; }
}

function renderPlans(plans, warnings) {
  const box = $("#strategy-plans");
  if (!box) return;
  try {
  if (!plans.length) {
    box.innerHTML = `<div class="empty">没有生成方案，请调整描述后重试</div>`;
    return;
  }
  const stars = (n) => "★★★★★".slice(0, n) + "☆☆☆☆☆".slice(0, 5 - n);
  box.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <b>🤖 AI 获客方案（${plans.length} 套）</b>
      <button class="btn sm" id="plans-toggle">收起 ▲</button>
    </div>
    ${warnings && warnings.length ? `<div style="margin:0 0 10px;padding:8px 10px;background:#fff8e1;border:1px solid #f0d27a;border-radius:8px;font-size:12px;color:#8a5a00">⚠️ 方案自检：${warnings.map((w) => esc(w)).join("；")}</div>` : ""}
    <div id="plans-body">` + plans.map((p, i) => `
    <div style="border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
        <b>方案 ${"ABCDE"[i] || i + 1}：${esc(p.title)}${i === 0 ? ' <span class="type-chip" style="color:#fff;background:#16a34a">推荐</span>' : ""}</b>
        <span style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn primary sm" data-use="${i}">🔍 手动搜索</button>
          <button class="btn sm" data-acq="${i}">🧠 用引擎获客</button>
        </span>
      </div>
      <div style="margin-top:6px">🎯 目标客户：${esc(p.target_customers)}</div>
      ${p.buyer_role ? `<div class="sub" style="margin-top:2px">👤 目标角色：${esc(p.buyer_role)}</div>` : ""}
      <div class="sub" style="margin-top:4px">关键词：${(p.keywords || []).map((k) => `<span class="tag-chip">${esc(k)}</span>`).join("")}</div>
      <div class="sub">地区：${(p.markets || []).map((m) => `<span class="tag-chip">${esc(m)}</span>`).join("") || "—"}</div>
      <div style="margin-top:6px">
        <span style="color:#e8913a">利润 ${stars(p.profit)}</span>
        <span style="color:#8b5cf6;margin-left:12px">知名度 ${stars(p.brand)}</span>
        <span style="color:#0ea5b7;margin-left:12px">需求量 ${stars(p.demand)}</span>
        ${p.effort ? `<span style="color:#64748b;margin-left:12px">难度 ${stars(p.effort)}</span>` : ""}
        ${p.cooperation ? `<span class="type-chip" style="margin-left:12px">${esc(p.cooperation)}</span>` : ""}
      </div>
      ${p.timeline ? `<div class="hint" style="margin-top:6px">🗓 ${esc(p.timeline)}</div>` : ""}
      ${p.pitch ? `<div style="margin-top:6px;background:#eef6ff;border-radius:8px;padding:8px 10px;font-size:12px">💬 首触：${esc(p.pitch)}</div>` : ""}
      ${p.why ? `<div class="hint" style="margin-top:6px">✅ 推荐理由：${esc(p.why)}</div>` : ""}
      ${p.decision ? `<div class="hint" style="margin-top:6px">🧭 决策链/周期：${esc(p.decision)}</div>` : ""}
      ${p.moat ? `<div class="hint" style="margin-top:6px">🛡 差异化壁垒：${esc(p.moat)}</div>` : ""}
      ${p.strategy ? `<div class="hint" style="margin-top:6px">💡 ${esc(p.strategy)}</div>` : ""}
      ${p.channels && p.channels.length ? `<div class="hint" style="margin-top:6px">📡 渠道：${p.channels.map((c) => `<span class="tag-chip">${esc(c)}</span>`).join("")}</div>` : ""}
      ${p.risks && p.risks.length ? `<div class="hint" style="margin-top:6px">⚠️ 风险：${esc(p.risks.join("；"))}</div>` : ""}
    </div>`).join("") + `</div>`;
  $("#plans-toggle").onclick = () => {
    const body = $("#plans-body");
    const collapsed = body.style.display === "none";
    body.style.display = collapsed ? "" : "none";
    $("#plans-toggle").textContent = collapsed ? "收起 ▲" : "展开 ▼";
  };
  $$("[data-use]", box).forEach((b) => b.onclick = () => {
    const p = plans[+b.dataset.use];
    $("#buyer-kws").value = (p.keywords || []).join("\n");
    $("#buyer-markets").value = (p.markets || []).join("\n");
    toast("已按方案填入关键词，开始手动搜索…", "ok");
    $("#buyer-run").click();
  });
  $$("[data-acq]", box).forEach((b) => b.onclick = () => {
    const p = plans[+b.dataset.acq];
    // 方案 → 引擎条件：规格/市场/买方角色 一键带入并启动
    state.acqPlan = p;  // 完整方案（标题/评级/策略/合作/渠道/风险）随引擎一起传递
    $("#acq-products").value = p.target_customers || "";
    // 从方案关键词提取干净的产品词填“必中规格”（去掉 采购/招标/公告 等意图后缀）
    const specs = [];
    (p.keywords || []).forEach((kw) => {
      const kwText = String(kw || "").trim();
      // 在第一个买方意图词处切分，只保留前面的产品/场景词
      let s = kwText.split(/\s+(?:采购经理|扩容项目|项目方|采购公告|招标公告|询价公告|中标公告|采购|招标|询价|求购|公告|中标|项目|需求|供应商|procurement|tender|rfq|rfp|purchase|buyer|sourcing|inquiry|distributor|dealer|supplier)\b/i)[0] || "";
      s = s.trim();
      // 没有“空格+意图词”时，尝试直接去掉结尾的意图词（如“光模块需求”）
      if (s === kwText) {
        s = kwText.replace(/(?:采购经理|扩容项目|项目方|采购公告|招标公告|询价公告|中标公告|采购|招标|询价|求购|公告|中标|项目|需求|供应商|procurement|tender|rfq|rfp|purchase|buyer|sourcing|inquiry|distributor|dealer|supplier)\s*$/i, "").trim();
      }
      // 首段仍含意图词时（如“采购经理”），退而取关键词第一个词
      if (!s || /(采购|招标|询价|求购|公告|中标|项目|需求|供应商|procurement|tender|rfq|rfp|purchase|buyer)/i.test(s)) {
        s = kwText.split(/\s+/)[0] || "";
      }
      s = s.trim();
      if (s && specs.indexOf(s) < 0) specs.push(s);
    });
    $("#acq-specs").value = specs.slice(0, 5).join(",");
    $("#acq-seeds").value = (p.keywords || []).join(",");
    $("#acq-regions").value = (p.markets || []).join(",");
    $("#acq-types").value = p.buyer_role || "";
    $("#acq-exclude").value = "";
    const tag = $("#acq-plan-tag");
    if (tag) {
      tag.style.display = "block";
      tag.textContent = `📋 已带入 AI 方案：${p.title || ""}${p.profit ? ` ｜ 利润${"★".repeat(Math.max(0, Math.min(5, p.profit)))}` : ""}${p.channels && p.channels.length ? ` ｜ 渠道：${p.channels.join("、")}` : ""}${p.risks && p.risks.length ? ` ｜ 风险：${p.risks.join("；")}` : ""}`;
    }
    toast("已用方案填充引擎条件，开始自动获客…", "ok");
    $("#acq-run").click();
    const card = $("#acq-run").closest(".card");
    if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  } catch (e) {
    // 防御：渲染异常时至少把方案标题/角色/关键词兜底显示出来
    console.error("方案渲染异常", e);
    box.innerHTML = `<div class="empty">⚠️ 方案渲染异常（${esc(e && e.message || e)}），已降级显示</div>` +
      (plans || []).map((p, i) => `
        <div style="border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px">
          <b>方案 ${"ABCDE"[i] || i + 1}：${esc(p && p.title)}</b>
          ${p && p.buyer_role ? `<div class="sub">👤 ${esc(p.buyer_role)}</div>` : ""}
          <div class="sub">关键词：${((p && p.keywords) || []).map((k) => `<span class="tag-chip">${esc(k)}</span>`).join("")}</div>
        </div>`).join("");
  }
}

async function pollBuyerJob() {
  try {
    const d = await api("/api/buyer");
    const job = d.job;
    if (job.running) {
      $("#buyer-result").innerHTML = `<div class="empty"><div class="ico">⏳</div>${esc(job.stage || "正在搜索并分析买家线索")}…（进度看右上角任务栏）</div>`;
      setTimeout(pollBuyerJob, 1500);
      return;
    }
    if (job.message) {
      $("#buyer-result").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(job.message)}</div>`;
      return;
    }
    renderBuyerResult(job.result || { candidates: [] });
  } catch (e) { /* 忽略 */ }
}

let acqPolling = false;
async function pollAcquisition() {
  if (acqPolling) return;
  acqPolling = true;
  try {
    const d = await api("/api/acquisition");
    const job = d.job || {};
    if (job.running) {
      $("#acq-result").innerHTML = `<div class="empty"><div class="ico">🧠</div>${esc(job.stage || "正在运行")}…（进度看右上角任务栏）</div>`;
      setTimeout(pollAcquisition, 2000);
      return;
    }
    acqPolling = false;
    if (job.result) renderAcqResult(job.result);
    else $("#acq-result").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(job.message || "运行失败，请重试")}</div>`;
  } catch (e) { acqPolling = false; }
}

function renderAcqResult(res) {
  const targets = res.targets || [];
  const stats = res.stats || {};
  const warnings = res.warnings || [];
  const enrich = res.company_enrich || {};
  const verify = res.contact_verify || {};
  const box = $("#acq-result");
  if (!box) return;
  const importBtn = $("#acq-import");
  if (importBtn) importBtn.disabled = !targets.length;
  const tiers = Object.entries(stats.by_tier || {}).map(([k, v]) => `${k}级 ${v}`).join(" / ") || "—";
  const warnHtml = warnings.length
    ? `<div style="margin:8px 0;padding:10px 12px;background:#fff8e1;border:1px solid #f0d27a;border-radius:10px;font-size:12px;color:#8a5a00">
        ⚠️ ${targets.length ? "部分搜索源受限：" : "本次没有找到目标客户，原因："}<br>
        ${warnings.slice(0, 6).map((w) => esc(w)).join("<br>")}
        ${targets.length ? "" : "<br>建议：① 到“设置 → 搜索接口”点「🧪 检测搜索源」看哪些源可用/被限流；② 更换或补充配额（SerpAPI/博查）；③ 把方案关键词放进“检索种子词”、只填干净产品词到“必中规格”；④ 配置地图/工商密钥作为补充渠道。"}
      </div>`
    : "";
  box.innerHTML = `
    <h3 style="margin:6px 0 8px">发现 ${targets.length} 家目标客户</h3>
    ${warnHtml}
    <div class="hint" style="margin-bottom:8px">等级分布：${esc(tiers)} ｜ 已核验 ${stats.verified || 0} 家${verify.updated ? ` ｜ 🌐 官网核验 ${verify.updated} 家` : ""}${enrich.updated ? ` ｜ 🏢 工商补全 ${enrich.updated} 家` : ""}${res.final_gaps && res.final_gaps.length ? ` ｜ 剩余缺口：${esc(res.final_gaps.map((g) => g.join(":")).join("；"))}` : ""}</div>
    ${targets.length ? `<div class="table-wrap"><table>
      <thead><tr><th>等级</th><th>公司</th><th>区域</th><th>买方类型</th><th>命中规格</th><th>建议动作</th></tr></thead>
      <tbody>${targets.slice(0, 30).map((t) => `
        <tr><td>${esc(t.priority)}级</td><td><b>${esc(t.company)}</b>${t.email ? `<div class="sub">${esc(t.email)}</div>` : ""}</td>
        <td>${esc(t.region)}</td><td>${esc(t.buyer_type)}</td><td class="sub">${esc((t.matched_conditions || []).join("、"))}</td>
        <td class="sub">${esc(t.next_action || "")}</td></tr>`).join("")}</tbody>
    </table></div>` : `<div class="empty">没有目标客户，请根据上方原因调整后重试</div>`}`;
}

async function pollMapJob() {
  try {
    const d = await api("/api/map");
    const job = d.job;
    if (job.running) {
      $("#map-result").innerHTML = `<div class="empty"><div class="ico">🗺️</div>${esc(job.stage || "正在搜索地图客户")}…（进度看右上角任务栏）</div>`;
      setTimeout(pollMapJob, 1500);
      return;
    }
    if (job.message) {
      $("#map-result").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(job.message)}</div>`;
      return;
    }
    const list = job.result || [];
    state.mapCandidates = list;
    if (!list.length) {
      $("#map-result").innerHTML = `<div class="empty">没有找到结果，试试换个关键词或城市</div>`;
      return;
    }
    renderMapResult(list);
  } catch (e) { /* 忽略 */ }
}

function renderMapResult(list) {
    $("#map-result").innerHTML = `
      <h3>找到 ${list.length} 家公司</h3>
      <div style="margin:10px 0;display:flex;gap:8px;align-items:center">
        <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="map-all" checked> 全选</label>
        <button class="btn primary sm" id="map-add">＋ 添加选中到客户线索（自动去重）</button>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th style="width:30px"></th><th>公司名称</th><th>电话</th><th>地址</th><th>类型</th></tr></thead>
        <tbody>${list.map((c, i) => `
          <tr><td><input type="checkbox" class="map-check" data-i="${i}" checked></td>
          <td><b>${esc(c.name)}</b><div class="sub">${esc(c.tags || "")}</div></td>
          <td>${esc(c.phone || "—")}</td><td class="sub">${esc(c.address || "")}${c.website ? `<div>🌐 ${esc(c.website)}</div>` : ""}</td><td>${esc(c.type)}</td></tr>`).join("")}
        </tbody></table></div>`;
    $("#map-all").onchange = (e) => $$(".map-check").forEach((c) => c.checked = e.target.checked);
    $("#map-add").onclick = async () => {
      const picks = list.filter((_, i) => $$(".map-check")[i] && $$(".map-check")[i].checked);
      if (!picks.length) return toast("请先勾选", "err");
      const res = await api("/api/leads/bulk", { method: "POST", body: { leads: picks, source: "地图获客" } });
      toast(`已添加 ${res.added.length} 条，跳过重复 ${res.duplicates.length} 条`, "ok");
      $("#map-result").innerHTML = `<div class="empty"><div class="ico">✅</div>已处理，去“客户线索”查看</div>`;
    };
}

function renderBuyerResult(res) {
  state.buyerCandidates = res.candidates || [];
  const filteredCount = res.filtered || 0;
  const droppedLow = res.dropped_low || 0;
  const errs = (res.errors || []).map((e) => `<li>${esc(e)}</li>`).join("");
  if (!state.buyerCandidates.length) {
    $("#buyer-result").innerHTML = `<div class="empty"><div class="ico">😕</div>没有发现线索${errs ? `<ul style="margin-top:8px;color:var(--red);text-align:left">${errs}</ul>` : ""}</div>`;
    return;
  }
  $("#buyer-result").innerHTML = `
    <h3>发现 ${state.buyerCandidates.length} 条潜在买家${filteredCount || droppedLow ? `（已过滤噪音/同行 ${filteredCount} 条${droppedLow ? `，低分 ${droppedLow} 条` : ""}）` : ""}</h3>
    <div style="margin:10px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="buyer-all" checked> 全选</label>
      <button class="btn primary sm" id="buyer-add">＋ 添加选中到客户线索（自动去重）</button>
      <button class="btn sm" id="buyer-to-acq">🧠 送引擎筛选分级</button>
      <label style="display:flex;gap:6px;align-items:center;font-size:13px"><input type="checkbox" id="buyer-high"> 只看 ≥6 分</label>
      <span class="hint">评分高 = 有采购意向词 + 企业邮箱/电话；含“厂家直供/批发价”等供应商信号的会扣分</span>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th style="width:30px"></th><th>公司/主体</th><th>邮箱</th><th>电话</th><th>评分</th><th>判断依据</th></tr></thead>
      <tbody>${state.buyerCandidates.map((c, i) => `
        <tr data-score="${c.score}">
          <td><input type="checkbox" class="buyer-check" data-i="${i}" checked></td>
          <td><b>${esc(c.name)}</b><div class="sub">${esc(c.tags || "")}</div></td>
          <td>${esc(c.email || "—")}</td>
          <td>${esc(c.phone || "—")}</td>
          <td>${scoreBadge(c.score)}</td>
          <td class="sub" style="max-width:280px">${c.tier ? `<span class="tag-chip" style="background:${c.tier === "S" ? "#ffe9ec;color:#c62828" : c.tier === "A" ? "#fff8e1;color:#b26a00" : "#f1f5f9;color:#475569"}">${esc(c.tier)}级</span> ` : ""}${esc(c.score_reason || "")}${c.next_action ? `<div style="margin-top:4px;color:var(--blue)">👉 ${esc(c.next_action)}</div>` : ""}${c.signal ? `<div style="margin-top:4px;color:var(--green)">📶 ${esc(c.signal)}</div>` : ""}${c.window ? `<div style="margin-top:4px;color:var(--orange)">⏰ ${esc(c.window)}</div>` : ""}${c.snippet ? `<div style="margin-top:3px;color:var(--muted)">${esc(c.snippet.slice(0, 120))}</div>` : ""}</td>
        </tr>`).join("")}
      </tbody></table></div>
      ${errs ? `<div style="margin-top:10px;color:var(--orange);font-size:12px">部分页面抓取失败：<ul style="margin:4px 0 0 18px">${errs}</ul></div>` : ""}`;
  $("#buyer-all").onchange = (e) => $$(".buyer-check").forEach((c) => c.checked = e.target.checked);
  $("#buyer-high").onchange = (e) => {
    $$("#buyer-result tbody tr").forEach((tr) => {
      tr.style.display = e.target.checked && Number(tr.dataset.score) < 6 ? "none" : "";
    });
  };
  $("#buyer-add").onclick = async () => {
    const picks = state.buyerCandidates.filter((_, i) => $$(".buyer-check")[i] && $$(".buyer-check")[i].checked);
    if (!picks.length) return toast("请先勾选线索", "err");
    const res = await api("/api/leads/bulk", { method: "POST", body: { leads: picks, source: "买家发现" } });
    toast(`已添加 ${res.added.length} 条，跳过重复 ${res.duplicates.length} 条`, "ok");
    state.buyerCandidates = [];
    $("#buyer-result").innerHTML = `<div class="empty"><div class="ico">✅</div>处理完成，去“客户线索”里查看和跟进</div>`;
  };
  $("#buyer-to-acq").onclick = async () => {
    const cands = state.buyerCandidates || [];
    if (!cands.length) return toast("没有可送的搜索结果", "err");
    const conditions = {
      industry: "光纤通信 / 光器件",
      products: $("#acq-products").value.trim() || state.buyerContext || "光通信器件与设备",
      specs: $("#acq-specs").value.trim() || "DWDM,WDM,光模块",
      regions: $("#acq-regions").value.trim(),
      buyer_types: $("#acq-types").value.trim(),
      min_tier: $("#acq-tier").value,
      max_results: 50,
      exclude: $("#acq-exclude").value.trim(),
    };
    try {
      await api("/api/acquisition/run", { method: "POST", body: { conditions, seed: cands } });
      $("#acq-import").disabled = true;
      $("#acq-result").innerHTML = `<div class="empty"><div class="ico">🧠</div>正在用引擎对搜索结果做双维度筛选分级（离线，无需联网）…</div>`;
      pollAcquisition();
      const card = $("#acq-result").closest(".card");
      if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { toast(e.message, "err"); }
  };
}

function scoreBadge(score) {
  const s = Number(score || 0);
  const cls = s >= 7 ? "sc-high" : s >= 4 ? "sc-mid" : "sc-low";
  return `<span class="badge ${cls}">${s}分</span>`;
}

/* ---------- 主动触达 ---------- */
const EMAIL_TPLS = [
  {
    name: "光缆产品供应",
    desc: "向工程商/集成商推广光缆产品",
    subject: "关于{{产品}}的合作咨询",
    body: "{{联系人}}您好：\n\n我是{{我方公司}}的{{自己}}，我们专业供应{{产品}}，长期服务运营商、工程商和集成商客户，质量稳定、价格有竞争力，可提供样品、检测报告和批量供货支持。\n\n贵公司在{{地区}}的业务近期如有相关采购或配套需求，欢迎随时联系，我可以尽快发送详细资料和报价。\n\n期待与贵公司合作！\n{{我方公司}} {{自己}}",
  },
  {
    name: "熔接 / 施工服务",
    desc: "向有施工需求的项目方推广服务",
    subject: "光纤熔接与施工服务合作",
    body: "{{联系人}}您好：\n\n我是{{我方公司}}的{{自己}}，我们提供光纤熔接、线路铺设、机房改造等专业施工服务，团队经验丰富，工期有保障，价格透明，已完成多个FTTH和弱电项目。\n\n如贵公司近期在{{地区}}有相关项目或分包需求，欢迎随时联系，我可以先发送服务方案和案例供参考。\n\n祝项目顺利！\n{{我方公司}} {{自己}}",
  },
  {
    name: "FTTH / 机房改造",
    desc: "面向运营商与集成商的成套方案",
    subject: "FTTH / 机房改造配套方案",
    body: "{{联系人}}您好：\n\n我是{{我方公司}}的{{自己}}，我们专注{{产品}}及FTTH、机房改造成套配套，可提供从设备选型、方案设计到现场支持的一站式服务，帮助项目降本增效。\n\n如果贵公司正在推进相关项目，欢迎随时联系，我可以根据项目情况提供定制方案和报价。\n\n期待交流！\n{{我方公司}} {{自己}}",
  },
  {
    name: "弱电工程配套",
    desc: "面向弱电工程商的全线配套",
    subject: "弱电工程光纤配套合作",
    body: "{{联系人}}您好：\n\n我是{{我方公司}}的{{自己}}，我们为弱电工程商提供光纤光缆、收发器、熔接设备及配件的一站式配套，库存充足、发货快，支持月结和长期合作。\n\n贵公司如果方便，我可以发送一份常备型号和价格表，后续项目询价也欢迎直接找我。\n\n祝生意兴隆！\n{{我方公司}} {{自己}}",
  },
  {
    name: "通用开发信",
    desc: "简洁通用的第一封开发信",
    subject: "您好，{{我方公司}}向您问好",
    body: "{{联系人}}您好：\n\n我是{{我方公司}}的{{自己}}，我们主营{{产品}}。了解到贵公司从事{{地区}}相关业务，想看看有没有合作机会。\n\n如果方便，可以加个微信或留个电话，我发一份资料给您，不耽误您时间。\n\n祝好！\n{{我方公司}} {{自己}}",
  },
  {
    name: "工程商现货开发信",
    desc: "面向弱电/安防工程商：现货+当天发货+同行价",
    subject: "光纤配套现货，工程商当天发货",
    body: "{{联系人}}您好：\n\n我是{{我方公司}}的{{自己}}，厂家直供光纤跳线、配线架、分纤箱等配套产品。针对工程商客户，我们提供：\n\n1. 常备现货：常用型号仓库现货，当天发货，不耽误您工期；\n2. 工程商同行价：量大可谈，长期合作月结；\n3. 免费样品：可先寄样品测试（插损/回损数据随样附上），测试通过再下单。\n\n如果贵公司近期在{{地区}}有光纤配套采购需求，加个微信，我发份电子画册和价格表给您备选。\n\n祝工程顺利！\n{{我方公司}} {{自己}}",
  },
  {
    name: "样品测试跟进",
    desc: "寄样后回访测试结果",
    subject: "样品测试情况跟进（{{公司}}）",
    body: "{{联系人}}您好：\n\n上次寄给贵公司的样品，不知道测试结果如何？如插损、回损或兼容性有任何问题，随时发我，我们技术同事第一时间协助解决。\n\n如果测试通过需要批量采购，我这边可以按工程商价出正式报价，常用型号现货、当天发货。\n\n期待您的反馈！\n{{我方公司}} {{自己}}",
  },
  {
    name: "外贸开发信（英文）",
    desc: "海外 ISP/工程商开发信，含 CE/RoHS/ISO9001",
    subject: "Fiber Optic Patch Cords & Distribution - OEM Supplier with CE/RoHS/ISO9001",
    body: "Dear {{联系人}},\n\nThis is {{自己}} from {{我方公司}}, a professional manufacturer of fiber optic patch cords, patch panels, and FTTH products.\n\nWe supply:\n- Telecom-grade patch cords (SC/LC/FC, UPC/APC)\n- High-density MPO/MTP pre-terminated solutions\n- FTTH distribution boxes and accessories\n\nAll products comply with CE, RoHS, and ISO 9001 standards. Samples are available for testing before your bulk order, and we offer competitive OEM/ODM pricing.\n\nCould you please share your current requirements? I will send our catalog and quotation within 24 hours.\n\nBest regards,\n{{自己}}\n{{我方公司}}",
  },
];

const SMS_TPLS = [
  { name: "光缆推广短信", body: "【{{我方公司}}】{{联系人}}您好，我司长期供应光纤光缆及配套，价格优惠、现货充足，可提供样品。方便时回电或加微信详聊。电话：13800000000" },
  { name: "施工服务短信", body: "【{{我方公司}}】{{联系人}}您好，我司提供光纤熔接、机房改造施工服务，团队专业、价格透明，欢迎洽谈合作。电话：13800000000" },
  { name: "展会跟进短信", body: "【{{我方公司}}】{{联系人}}您好，上次展会咱们聊过{{产品}}合作，现有一批优惠价格政策，欢迎来电了解。电话：13800000000" },
];

async function renderOutreach() {
  const el = $("#page-outreach");
  el.innerHTML = `
    <div class="page-title">主动触达</div>
    <div class="page-sub">写文案、群发邮件、整理短信名单，三步触达客户</div>
    <div class="sub-tabs">
      <button class="sub-tab active" data-group="outreach" data-name="email">📧 邮件群发</button>
      <button class="sub-tab" data-group="outreach" data-name="copy">✍️ 文案生成</button>
      <button class="sub-tab" data-group="outreach" data-name="social">💬 社媒话术</button>
      <button class="sub-tab" data-group="outreach" data-name="sequence">⏰ 跟进序列</button>
      <button class="sub-tab" data-group="outreach" data-name="sms">📱 短信</button>
    </div>
    <div class="sub-panel" data-group="outreach" data-name="email">
      <div class="card">
        <h3>收件人（${state.recipients.length} 人）</h3>
        <div style="display:flex;gap:8px;margin-bottom:10px">
          <button class="btn" id="pick-recipients">👥 选择收件人</button>
          <button class="btn" id="clear-recipients">清空</button>
          <button class="btn" id="use-filter">使用“客户线索”当前筛选</button>
        </div>
        <div id="recipient-list"></div>
      </div>
      <div class="editor-row">
        <div class="editor-main">
          <div class="card">
            <h3>邮件内容</h3>
            <div class="field"><label>主题</label><input class="input full" id="mail-subject" value="关于{{产品}}的合作咨询"></div>
            <div class="field"><label>正文</label><textarea class="textarea full" id="mail-body" style="min-height:240px"></textarea></div>
            <div class="placeholder-hint">可用占位符：<code>{{公司}}</code> <code>{{联系人}}</code> <code>{{称呼}}</code> <code>{{地区}}</code> <code>{{产品}}</code> <code>{{我方公司}}</code> <code>{{自己}}</code></div>
          </div>
        </div>
        <div class="editor-side">
          <div class="card">
            <h3>行业模板</h3>
            <div id="mail-tpls">${EMAIL_TPLS.map((t, i) => `
              <div class="tpl-item" data-i="${i}"><div class="t">${esc(t.name)}</div><div class="d">${esc(t.desc)}</div></div>`).join("")}
            </div>
            <button class="btn sm" id="btn-preview">👁 预览第一封</button>
            <button class="btn primary" id="btn-send-mail" style="margin-top:8px">🚀 发送给选中客户</button>
          </div>
        </div>
      </div>
      <div class="card" id="mail-result"></div>
    </div>
    <div class="sub-panel" data-group="outreach" data-name="copy" style="display:none">
      <div class="card">
        <h3>营销文案生成</h3>
        <div class="form-grid">
          <div class="field"><label>文案场景</label><select class="select full" id="ai-scene">
            <option>开发信（邮件）</option><option>短信</option><option>微信/朋友圈</option><option>报价跟进</option>
            <option>社媒帖子（含话题标签）</option>
            <option>技术科普文章</option><option>B2B平台发布文案</option><option>外贸开发信（英文）</option>
          </select></div>
          <div class="field"><label>目标客户</label><select class="select full" id="ai-audience">
            <option>运营商</option><option>工程商</option><option>集成商</option><option>分销商</option><option>代工厂</option><option>终端客户</option>
          </select></div>
          <div class="field"><label>主营产品</label><input class="input full" id="ai-product" value="光纤光缆及配套产品"></div>
          <div class="field"><label>语气风格</label><select class="select full" id="ai-tone">
            <option>正式专业</option><option>亲切友好</option><option>简洁直接</option><option>突出优惠</option>
          </select></div>
          <div class="field" style="grid-column:1/-1"><label>参考内容（选填：粘贴文章/链接正文，AI 会基于它生成）</label>
            <textarea class="textarea full" id="ai-ref" style="min-height:80px" placeholder="粘贴行业文章、产品介绍或客户留言…"></textarea>
          </div>
        </div>
        <div class="toolbar" style="margin-top:4px">
          <button class="btn primary" id="ai-gen">✨ AI 生成</button>
          <button class="btn" id="ai-to-mail">📥 填入邮件编辑器</button>
          <button class="btn" id="ai-copy">📋 复制结果</button>
        </div>
        <div class="hint" id="ai-hint">AI 生成需要先在“设置”里配置 OpenAI API Key；未配置时可先用下方行业模板。</div>
        <div class="field" style="margin-top:10px"><label>生成结果</label><div class="result-box" id="ai-result">（点击“AI 生成”后显示）</div></div>
      </div>
    </div>
    <div class="sub-panel" data-group="outreach" data-name="sms" style="display:none">
      <div class="card">
        <h3>短信触达</h3>
        <div class="field"><label>短信内容（模板选择或自行填写）</label>
          <div id="sms-tpls" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
            ${SMS_TPLS.map((t, i) => `<button class="btn sm tpl-item" data-i="${i}" style="border-radius:8px">${esc(t.name)}</button>`).join("")}
          </div>
          <textarea class="textarea full" id="sms-body"></textarea>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" id="sms-pick">👥 选择收件人（当前 ${state.recipients.length} 人）</button>
          <button class="btn" id="sms-copy-num">📋 复制号码列表</button>
          <button class="btn" id="sms-copy-body">📋 复制短信内容</button>
          <button class="btn" id="sms-export">⬇ 导出号码文本</button>
        </div>
        <div class="hint" style="margin-top:10px">发送短信需要运营商短信网关（如阿里云短信）。当前版本先把“号码+短信内容”整理好：复制到手机短信 App 或导入第三方群发平台即可。在“设置”里可以填写你的短信服务商备注。</div>
      </div>
    </div>`;
  $$(".sub-tab", el).forEach((t) => t.onclick = () => {
    state.outreachTab = t.dataset.name;
    activateSubTab("outreach", t.dataset.name);
  });
  renderRecipients();

  $("#pick-recipients").onclick = openRecipientPicker;
  $("#sms-pick").onclick = openRecipientPicker;
  $("#clear-recipients").onclick = () => { state.recipients = []; renderRecipients(); };
  $("#use-filter").onclick = async () => {
    const f = state.leads.filters;
    const params = new URLSearchParams({ q: f.q, status: f.status, type: f.type, region: f.region, tag: f.tag, source: f.source });
    const data = await api("/api/leads?" + params.toString() + "&size=200");
    state.recipients = data.items.filter((r) => r.email).map((r) => ({ id: r.id, name: r.name, email: r.email, phone: r.phone }));
    renderRecipients();
    toast(`已加入 ${state.recipients.length} 个有邮箱的客户`, "ok");
  };

  const bodyBox = $("#mail-body");
  const tplItems = $$("#mail-tpls .tpl-item");
  tplItems.forEach((item) => item.onclick = () => {
    tplItems.forEach((x) => x.classList.remove("sel"));
    item.classList.add("sel");
    const t = EMAIL_TPLS[+item.dataset.i];
    $("#mail-subject").value = t.subject;
    bodyBox.value = t.body;
  });
  tplItems[0].click();

  $("#btn-preview").onclick = async () => {
    const lead = state.recipients[0] || (await api("/api/leads?size=1")).items[0];
    if (!lead) return toast("没有可预览的收件人", "err");
    const settings = (await api("/api/settings")).settings;
    const subj = personalize($("#mail-subject").value, lead, settings);
    const body = personalize(bodyBox.value, lead, settings);
    openModal(`<div class="modal-head"><h2>邮件预览（给 ${esc(lead.name)}）</h2><button class="close-x" onclick="closeModal()">×</button></div>
      <div class="field"><label>主题</label><div class="result-box">${esc(subj)}</div></div>
      <div class="field"><label>正文</label><div class="result-box">${esc(body)}</div></div>
      <div class="modal-foot"><button class="btn primary" onclick="closeModal()">好的</button></div>`, "wide");
  };

  $("#btn-send-mail").onclick = async () => {
    const picks = state.recipients.filter((r) => r.email);
    if (!picks.length) return toast("请先选择有邮箱的收件人", "err");
    if (!bodyBox.value.trim()) return toast("请填写邮件正文", "err");
    confirmBox(`确定向 ${picks.length} 位客户发送邮件？发送后不可撤回。`, async () => {
      try {
        const res = await api("/api/mail", { method: "POST", body: { lead_ids: picks.map((p) => p.id), subject: $("#mail-subject").value, body: bodyBox.value } });
        $("#mail-result").innerHTML = `<div class="empty"><div class="ico">⏳</div>发送任务已启动，正在后台逐封发送…（页面可继续操作）</div>`;
        pollMailJob();
      } catch (e) { toast(e.message, "err"); }
    });
  };

  async function pollMailJob() {
    try {
      const d = await api("/api/mail/status");
      const job = d.job;
      if (job.running) {
        const done = (job.result && job.result.done) || 0;
        const total = (job.result && job.result.total) || "…";
        $("#mail-result").innerHTML = `<div class="empty"><div class="ico">📧</div>正在发送 ${done}/${total} 封…（后台进行中）</div>`;
        setTimeout(pollMailJob, 1500);
        return;
      }
      const r = job.result;
      if (!r) {
        $("#mail-result").innerHTML = `<div class="empty"><div class="ico">⚠️</div>${esc(job.message || "发送任务已结束")}</div>`;
        return;
      }
      renderMailResult(r);
    } catch (e) { /* 忽略轮询错误 */ }
  }
  function renderMailResult(r) {
    const errs = (r.errors || []).map((e) => `<li>${esc(e.name)}：${esc(e.msg)}</li>`).join("");
    $("#mail-result").innerHTML = `<h3>发送结果</h3><p style="margin-top:8px">成功 <b style="color:var(--green)">${r.sent}</b> 封，失败 <b style="color:var(--red)">${r.failed}</b> 封。</p>${errs ? `<ul style="margin:8px 0 0 18px;color:var(--red)">${errs}</ul>` : ""}`;
    toast(`已发送 ${r.sent} 封`, r.sent ? "ok" : "err");
  }

  // AI 文案
  $("#ai-gen").onclick = async () => {
    const btn = $("#ai-gen");
    btn.disabled = true;
    try {
      const scene = $("#ai-scene").value, audience = $("#ai-audience").value,
        product = $("#ai-product").value, tone = $("#ai-tone").value,
        ref = $("#ai-ref").value.trim();
      let extra = "";
      if (scene.includes("话题标签")) extra = "文末给出 3-5 个相关话题标签（#开头）。";
      if (ref) extra += "\n请基于以下参考内容创作：\n" + ref.slice(0, 2000);
      const res = await api("/api/ai", { method: "POST", body: {
        system: "你是一名资深的光纤通信行业销售顾问，擅长写简洁、得体、有转化力的中文营销文案。",
        user: `场景：${scene}\n主营产品：${product}\n目标客户：${audience}\n语气：${tone}\n${extra}\n\n请生成一份可直接使用的文案。如果是邮件/开发信，请包含“主题：”和正文两部分；如果是短信，控制在70字以内；如果是朋友圈或社媒帖子，控制在200字以内。用“{{公司}}”“{{联系人}}”“{{称呼}}”“{{地区}}”“{{产品}}”“{{我方公司}}”“{{自己}}”作为占位符，不要虚构具体电话。`,
      }});
      state.aiText = res.text;
      $("#ai-result").textContent = res.text;
      toast("生成完成", "ok");
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; }
  };
  $("#ai-to-mail").onclick = () => {
    if (!state.aiText) return toast("先生成文案", "err");
    const m = state.aiText.match(/主题[：:]\s*(.+)/);
    if (m) $("#mail-subject").value = m[1].trim();
    bodyBox.value = state.aiText.replace(/^主题[：:].*\n?/, "");
    activateSubTab("outreach", "email");
  };
  $("#ai-copy").onclick = () => {
    if (!state.aiText) return toast("先生成文案", "err");
    copyText(state.aiText);
  };

  // 社媒话术
  const socialTab = `
    <div class="card">
      <h3>💬 社媒引流话术库</h3>
      <div class="field"><label>场景</label>
        <select class="select full" id="social-scene">
          <option>抖音评论引流</option>
          <option>小红书评论</option>
          <option>私信开场白</option>
          <option>加微信申请</option>
          <option>群内解答后引流</option>
          <option>样品测试跟进</option>
          <option>追粉话术</option>
        </select>
      </div>
      <div class="field"><label>生成数量</label>
        <select class="select" id="social-count">
          <option value="1">1 条</option>
          <option value="3" selected>3 条</option>
          <option value="5">5 条</option>
        </select>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn primary" id="social-gen">🎲 随机生成</button>
        <button class="btn" id="social-ai">✨ AI 生成</button>
        <button class="btn" id="social-copy-all">📋 复制全部</button>
      </div>
      <div class="hint" style="margin-top:8px">参考抖音评论引流工具（来赞）的思路：话术要像真实用户、不硬广，随机换着发更安全。AI 生成需在“设置”里配置 API Key。</div>
      <div id="social-list" style="margin-top:12px"></div>
    </div>`;
  const socialPanel = document.createElement("div");
  socialPanel.className = "sub-panel";
  socialPanel.dataset.group = "outreach";
  socialPanel.dataset.name = "social";
  socialPanel.style.display = "none";
  socialPanel.innerHTML = socialTab;
  $(".sub-panel[data-group='outreach'][data-name='sms']").after(socialPanel);

  const showSocial = (list) => {
    $("#social-list").innerHTML = list.length
      ? list.map((t, i) => `
        <div class="chk-row">
          <span style="flex:1">${i + 1}. ${esc(t)}</span>
          <button class="btn sm" data-copy="${i}">复制</button>
        </div>`).join("")
      : `<div class="empty">还没有话术</div>`;
    $$("#social-list [data-copy]").forEach((b) => b.onclick = () => copyText(list[+b.dataset.copy]));
    state.socialTexts = list;
  };
  const runSocial = async (useAi) => {
    const btn = useAi ? $("#social-ai") : $("#social-gen");
    btn.disabled = true;
    try {
      const res = await api("/api/copy/social", { method: "POST", body: {
        scenario: $("#social-scene").value,
        count: $("#social-count").value,
        use_ai: useAi,
      } });
      showSocial(res.texts || []);
      if (res.ai) toast("AI 话术生成完成", "ok");
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; }
  };
  $("#social-gen").onclick = () => runSocial(false);
  $("#social-ai").onclick = () => runSocial(true);
  $("#social-copy-all").onclick = () => {
    if (!state.socialTexts || !state.socialTexts.length) return toast("先生成话术", "err");
    copyText(state.socialTexts.join("\n\n"));
  };

  // 短信
  $$("#sms-tpls .tpl-item").forEach((b) => b.onclick = () => { $("#sms-body").value = SMS_TPLS[+b.dataset.i].body; });
  $("#sms-copy-num").onclick = () => {
    const nums = state.recipients.map((r) => r.phone).filter(Boolean);
    if (!nums.length) return toast("收件人里没有电话号码", "err");
    copyText(nums.join("\n"));
  };
  $("#sms-copy-body").onclick = () => {
    if (!$("#sms-body").value.trim()) return toast("请先填写短信内容", "err");
    copyText($("#sms-body").value);
  };
  $("#sms-export").onclick = () => {
    const nums = state.recipients.map((r) => (r.name ? r.name + "\t" : "") + (r.phone || "")).filter((x) => x);
    if (!nums.length) return toast("收件人里没有电话号码", "err");
    downloadText(nums.join("\n"), "短信名单.txt");
  };

  // 跟进序列
  const seqPanel = document.createElement("div");
  seqPanel.className = "sub-panel";
  seqPanel.dataset.group = "outreach";
  seqPanel.dataset.name = "sequence";
  seqPanel.style.display = "none";
  seqPanel.innerHTML = `
    <div class="card">
      <h3>⏰ 跟进序列（自动按节奏发送）</h3>
      <p class="hint" style="margin-bottom:10px">收件人：<b id="seq-count">${state.recipients.length}</b> 人
        <button class="btn sm" id="seq-pick" style="margin-left:8px">👥 选择收件人</button>
      </p>
      <div id="seq-stages"></div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button class="btn primary" id="seq-schedule">📅 安排跟进序列</button>
        <span class="hint">例：第 1 天开发信 → 第 3 天跟进资料 → 第 7 天追单。需要已配置 SMTP 发信邮箱、客户有邮箱。</span>
      </div>
    </div>
    <div class="card"><h3>已安排的序列</h3><div id="seq-list"><div class="empty">加载中…</div></div></div>`;
  $(".sub-panel[data-group='outreach'][data-name='sms']").after(seqPanel);

  const buildStage = (i, tplIdx, days) => {
    const t = EMAIL_TPLS[tplIdx] || EMAIL_TPLS[0];
    const row = document.createElement("div");
    row.style.cssText = "border:1px solid var(--line);border-radius:9px;padding:10px;margin-bottom:10px";
    row.innerHTML = `
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <b>第 ${i + 1} 封</b>
        <select class="select seq-tpl" style="min-width:220px">
          ${EMAIL_TPLS.map((t, k) => `<option value="${k}" ${k === tplIdx ? "selected" : ""}>${esc(t.name)}</option>`).join("")}
        </select>
        <label style="display:flex;gap:6px;align-items:center">间隔
          <input class="input seq-days" type="number" min="0" max="30" value="${days}" style="width:70px"> 天</label>
        <button class="btn sm danger seq-del" ${i === 0 ? "disabled" : ""}>删除</button>
      </div>
      <div class="field" style="margin:8px 0 0"><label>主题</label><input class="input full seq-subject" value="${esc(t.subject)}"></div>
      <div class="field" style="margin-top:6px"><label>正文</label><textarea class="textarea full seq-body" style="min-height:90px">${esc(t.body)}</textarea></div>`;
    const tplSel = row.querySelector(".seq-tpl");
    tplSel.onchange = () => {
      const t2 = EMAIL_TPLS[+tplSel.value];
      row.querySelector(".seq-subject").value = t2.subject;
      row.querySelector(".seq-body").value = t2.body;
    };
    return row;
  };
  const stageBox = $("#seq-stages");
  const renderStages = () => {
    stageBox.innerHTML = "";
    const defaults = [0, 1, 3]; // 模板序号与间隔天数
    defaults.forEach((d, i) => stageBox.appendChild(buildStage(i, i, d)));
    $$("#seq-stages .seq-del").forEach((b) => b.onclick = () => { b.closest("div").remove(); });
  };
  renderStages();

  $("#seq-pick").onclick = openRecipientPicker;
  $("#seq-schedule").onclick = async () => {
    const picks = state.recipients.filter((r) => r.email);
    if (!picks.length) return toast("请先选择有邮箱的收件人", "err");
    const stages = $$("#seq-stages > div").map((row) => ({
      subject: row.querySelector(".seq-subject").value.trim(),
      body: row.querySelector(".seq-body").value,
      days: +row.querySelector(".seq-days").value || 0,
    })).filter((s) => s.subject && s.body);
    if (!stages.length) return toast("请至少保留一个跟进节点", "err");
    confirmBox(`确定为 ${picks.length} 位客户安排 ${stages.length} 个跟进节点（共 ${picks.length * stages.length} 封定时邮件）？`, async () => {
      try {
        const res = await api("/api/sequences/schedule", { method: "POST", body: {
          lead_ids: picks.map((p) => p.id),
          stages,
        } });
        toast(`已安排 ${res.scheduled} 封定时邮件`, "ok");
        loadSeqList();
      } catch (e) { toast(e.message, "err"); }
    });
  };
  const loadSeqList = async () => {
    try {
      const d = await api("/api/sequences");
      const rows = (d.mails || []).map((m) => `
        <tr>
          <td>${esc(m.created_at)}</td><td>${esc(m.subject)}</td>
          <td>${esc(m.send_at)}</td>
          <td>${m.status === "成功" ? `<span style="color:var(--green)">成功</span>` : m.status === "失败" ? `<span style="color:var(--red)" title="${esc(m.error || "")}">失败</span>` : esc(m.status)}</td>
        </tr>`).join("");
      $("#seq-list").innerHTML = rows
        ? `<div class="table-wrap"><table><thead><tr><th>安排时间</th><th>主题</th><th>计划发送</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : `<div class="empty">还没有安排跟进序列</div>`;
    } catch (e) { /* 忽略 */ }
  };
  loadSeqList();
  activateSubTab("outreach", state.outreachTab || "email");
  // 恢复邮件发送任务状态（切走再回来不丢）
  api("/api/mail/status").then((d) => {
    const job = d.job || {};
    if (job.running) {
      $("#mail-result").innerHTML = `<div class="empty"><div class="ico">📧</div>正在发送…（后台进行中，进度看右上角任务栏）</div>`;
      pollMailJob();
    } else if (job.result) {
      renderMailResult(job.result);
    }
  }).catch(() => {});
}

function personalize(tpl, lead, settings) {
  const vals = {
    "公司": lead.name || "", "公司名": lead.name || "",
    "联系人": lead.contact || "客户", "称呼": (lead.contact || "").split(/先生|女士/)[0] || "客户",
    "地区": lead.region || "", "产品": settings.product_name || "光纤产品",
    "自己": settings.sender_name || settings.company_name || "", "我方公司": settings.company_name || "",
  };
  return Object.entries(vals).reduce((s, [k, v]) => s.split("{{" + k + "}}").join(v), tpl);
}

function renderRecipients() {
  const box = $("#recipient-list");
  if (!box) return;
  box.innerHTML = state.recipients.length
    ? `<div class="chk-list">${state.recipients.map((r) => `
        <div class="chk-row">
          <span style="flex:1"><b>${esc(r.name)}</b> <span class="em">${esc(r.email || "无邮箱")}</span></span>
          <button class="btn sm danger" data-rid="${r.id}">移除</button>
        </div>`).join("")}</div>`
    : `<div class="empty"><div class="ico">👥</div>还没有收件人，点击“选择收件人”或“使用客户线索当前筛选”</div>`;
  $$("#recipient-list [data-rid]").forEach((b) => b.onclick = () => {
    state.recipients = state.recipients.filter((r) => r.id !== +b.dataset.rid);
    renderRecipients();
  });
}

async function openRecipientPicker() {
  let rows = [];
  let keyword = "";
  const render = async () => {
    const params = new URLSearchParams({ size: "100", q: keyword });
    const data = await api("/api/leads?" + params.toString());
    rows = data.items;
    $("#picker-list").innerHTML = rows.length
      ? `<div class="chk-list">${rows.map((r) => `
          <div class="chk-row">
            <label><input type="checkbox" class="pick-check" data-id="${r.id}" data-name="${esc(r.name)}" data-email="${esc(r.email || "")}" data-phone="${esc(r.phone || "")}">
              <span><b>${esc(r.name)}</b> <span class="em">${esc(r.email || "无邮箱")} · ${esc(r.phone || "无电话")}</span></span>
            </label>
          </div>`).join("")}</div>`
      : `<div class="empty">没有更多线索了</div>`;
    $$(".pick-check", $("#picker-list")).forEach((c) => {
      const hit = state.recipients.some((r) => r.id === +c.dataset.id);
      c.checked = hit;
    });
  };
  openModal(`
    <div class="modal-head"><h2>选择收件人</h2><button class="close-x" onclick="closeModal()">×</button></div>
    <div class="toolbar"><input class="input grow" id="pick-q" placeholder="搜索公司 / 联系人 / 电话"><span class="hint" id="pick-count"></span></div>
    <div id="picker-list"><div class="empty">加载中…</div></div>
    <div class="modal-foot">
      <button class="btn" id="pick-all">全选当前页</button>
      <button class="btn primary" id="pick-ok">确定</button>
    </div>`, "wide");
  await render();
  let timer;
  $("#pick-q").oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => { keyword = $("#pick-q").value.trim(); await render(); }, 300);
  };
  $("#pick-all").onclick = () => $$(".pick-check").forEach((c) => c.checked = true);
  $("#pick-ok").onclick = () => {
    const picked = $$(".pick-check").filter((c) => c.checked).map((c) => ({
      id: +c.dataset.id, name: c.dataset.name, email: c.dataset.email, phone: c.dataset.phone,
    }));
    state.recipients = state.recipients.filter((r) => !picked.some((p) => p.id === r.id)).concat(picked);
    closeModal();
    renderRecipients();
    toast(`已选择 ${picked.length} 位收件人`, "ok");
  };
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast("已复制到剪贴板", "ok")).catch(() => toast("复制失败，请手动复制", "err"));
}
function downloadText(text, name) {
  const blob = new Blob(["\ufeff" + text], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------- 发送记录 ---------- */
async function renderLogs() {
  const el = $("#page-logs");
  const d = await api("/api/mail");
  const rows = d.logs.map((l) => `
    <tr>
      <td>${esc(l.sent_at)}</td>
      <td>${esc(l.name)}</td>
      <td>${esc(l.email)}</td>
      <td>${esc(l.subject)}</td>
      <td>${l.status === "成功" ? badge("已成交").replace("已成交", "成功") : `<span style="color:var(--red);font-weight:600">失败</span>`}</td>
      <td class="sub">${esc(l.error || "")}</td>
    </tr>`).join("");
  el.innerHTML = `
    <div class="page-title">发送记录</div>
    <div class="page-sub">最近发送的邮件与结果</div>
    <div class="card"><div class="toolbar"><button class="btn" id="log-refresh">🔄 刷新</button></div>
      <div class="table-wrap"><table>
        <thead><tr><th>时间</th><th>公司</th><th>邮箱</th><th>主题</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6"><div class="empty">还没有发送记录</div></td></tr>`}</tbody>
      </table></div>
    </div>`;
  $("#log-refresh").onclick = renderLogs;
}

/* ---------- 设置 ---------- */
async function renderSettings() {
  const el = $("#page-settings");
  const d = await api("/api/settings");
  const s = d.settings;
  el.innerHTML = `
    <div class="page-title">设置</div>
    <div class="page-sub">公司信息、发信邮箱和 AI 密钥都配置在这里</div>
    <div class="card">
      <h3>🏢 公司信息</h3>
      <div class="form-grid">
        <div class="field"><label>公司名称</label><input class="input full" id="s-company" value="${esc(s.company_name)}"></div>
        <div class="field"><label>主营产品</label><input class="input full" id="s-product" value="${esc(s.product_name)}"></div>
        <div class="field"><label>行业（AI 评分与方案按此行业调整）</label><input class="input full" id="s-industry" placeholder="如：光纤通信 / 机械 / 跨境电商" value="${esc(s.industry || "光纤通信")}"></div>
        <div class="field"><label>发件人姓名（显示名）</label><input class="input full" id="s-sender" placeholder="如：张三（销售经理）" value="${esc(s.sender_name)}"></div>
        <div class="field"><label>访问密码（部署到公网后必填）</label><input class="input full" type="password" id="s-accesspw" placeholder="留空表示不修改" autocomplete="new-password"></div>
      </div>
      <div class="hint">工具部署到云服务器后会暴露在公网，请务必设置访问密码，防止客户数据被他人看到。</div>
    </div>
    <div class="card">
      <h3>🌐 获客落地页 / 表单</h3>
      <div class="form-grid">
        <div class="field"><label>落地页状态</label><select class="select full" id="s-lp-enabled">
          <option value="1" ${s.lp_enabled === "1" ? "selected" : ""}>开启（访客可提交）</option>
          <option value="0" ${s.lp_enabled !== "1" ? "selected" : ""}>关闭</option>
        </select></div>
        <div class="field"><label>咨询电话（选填）</label><input class="input full" id="s-lp-phone" placeholder="13800000000" value="${esc(s.lp_phone)}"></div>
        <div class="field" style="grid-column:1/-1"><label>页面主标题</label><input class="input full" id="s-lp-title" value="${esc(s.lp_title)}"></div>
        <div class="field" style="grid-column:1/-1"><label>副标题</label><input class="input full" id="s-lp-subtitle" value="${esc(s.lp_subtitle)}"></div>
        <div class="field"><label>按钮文字</label><input class="input full" id="s-lp-cta" value="${esc(s.lp_cta)}"></div>
        <div class="field"><label>提交成功提示</label><input class="input full" id="s-lp-thanks" value="${esc(s.lp_thanks)}"></div>
      </div>
      <div class="toolbar" style="margin-bottom:0">
        <button class="btn" id="s-lp-open">🔗 打开落地页查看</button>
        <span class="hint">访客提交的信息会自动成为“客户线索”（来源：落地页表单），还可以分享给客户微信/朋友圈获取留资。</span>
      </div>
    </div>
    <div class="card">
      <h3>📧 发信邮箱（SMTP）</h3>
      <div class="form-grid">
        <div class="field"><label>SMTP 服务器</label><input class="input full" id="s-host" placeholder="如 smtp.qq.com / smtp.163.com" value="${esc(s.smtp_host)}"></div>
        <div class="field"><label>端口 / 加密方式</label><select class="select full" id="s-port">
          <option value="465:1" ${s.smtp_ssl === "1" ? "selected" : ""}>465（SSL）</option>
          <option value="587:0" ${s.smtp_ssl === "0" ? "selected" : ""}>587（STARTTLS）</option>
        </select></div>
        <div class="field"><label>发信邮箱</label><input class="input full" id="s-user" placeholder="your@example.com" value="${esc(s.smtp_user)}"></div>
        <div class="field"><label>邮箱授权码 / 密码</label><input class="input full" type="password" id="s-pass" placeholder="授权码，不是登录密码" value="${esc(s.smtp_password)}"></div>
      </div>
      <div class="hint">使用 QQ/163/企业邮等，需要在邮箱设置里开启 SMTP 并生成“授权码”。测试前请先保存。</div>
    </div>
    <div class="card">
      <h3>✨ AI 文案（可选）</h3>
      <div class="form-grid">
        <div class="field"><label>OpenAI API Key</label><input class="input full" type="password" id="s-key" placeholder="sk-..." value="${esc(s.openai_api_key)}"></div>
        <div class="field"><label>模型</label><input class="input full" id="s-model" value="${esc(s.openai_model)}"></div>
        <div class="field"><label>新线索自动 AI 评分</label><select class="select full" id="s-auto-ai">
          <option value="1" ${(s.auto_ai_score || "1") === "1" ? "selected" : ""}>开启（推荐）</option>
          <option value="0" ${s.auto_ai_score === "0" ? "selected" : ""}>关闭</option>
        </select></div>
        <div class="field" style="grid-column:1/-1"><label>接口地址（OpenAI 兼容协议）</label><input class="input full" id="s-api-base" placeholder="https://api.openai.com/v1" value="${esc(s.openai_api_base || "https://api.openai.com/v1")}"></div>
      </div>
      <div class="hint">支持 OpenAI / DeepSeek / FastGPT 等兼容接口。开启“自动 AI 评分”后，新线索（落地页留资、导入、买家发现、定时采集）会后台自动评分，无需手动操作。</div>
    </div>
    <div class="card">
      <h3>🤖 意向分级与自动首触（v2 获客增强）</h3>
      <div class="form-grid">
        <div class="field"><label>新线索自动意向分级</label>
          <select class="select full" id="s-auto-intent">
            <option value="1" ${(s.auto_intent_enabled || "1") === "1" ? "selected" : ""}>开启（推荐，规则判断不花钱）</option>
            <option value="0" ${s.auto_intent_enabled === "0" ? "selected" : ""}>关闭</option>
          </select>
        </div>
        <div class="field"><label>自动首触（新线索自动发首触邮件/短信）</label>
          <select class="select full" id="s-auto-touch">
            <option value="0" ${(s.auto_touch_enabled || "0") !== "1" ? "selected" : ""}>关闭（推荐先手动跟进）</option>
            <option value="1" ${s.auto_touch_enabled === "1" ? "selected" : ""}>开启</option>
          </select>
        </div>
        <div class="field"><label>首触最低评分（0-10）</label><input class="input full" type="number" min="0" max="10" id="s-auto-touch-score" value="${esc(s.auto_touch_score || "7")}"></div>
        <div class="field"><label>入库后延迟天数</label><input class="input full" type="number" min="0" max="30" id="s-auto-touch-delay" value="${esc(s.auto_touch_delay || "1")}"></div>
        <div class="field"><label>首触渠道</label>
          <select class="select full" id="s-auto-touch-channel">
            <option value="email" ${(s.auto_touch_channel || "email") === "email" ? "selected" : ""}>邮件（需配置 SMTP）</option>
            <option value="sms" ${s.auto_touch_channel === "sms" ? "selected" : ""}>短信（需配置短信服务商）</option>
          </select>
        </div>
      </div>
      <div class="hint">自动首触开启后，评分达标、尚未触达的新线索会自动生成个性化邮件/短信并排程发送（走“跟进序列”同一发送机制）。正式开启前请确认 SMTP 已配置、阈值合理，避免打扰客户。</div>
    </div>
    <div class="card">
      <h3>🔎 搜索接口（买家发现用）</h3>
      <div class="form-grid">
        <div class="field"><label>搜索源</label>
          <select class="select full" id="s-search-provider">
            <option value="bocha" ${s.search_provider === "bocha" ? "selected" : ""}>博查 AI 搜索（推荐，国内稳定）</option>
            <option value="so_free" ${(s.search_provider || "so_free") === "so_free" ? "selected" : ""}>免费搜索（360+搜狗自动切换，推荐）</option>
            <option value="bing_free" ${s.search_provider === "bing_free" ? "selected" : ""}>免费 Bing（可能被限制）</option>
            <option value="serpapi" ${s.search_provider === "serpapi" ? "selected" : ""}>SerpAPI（推荐，免费200次/月）</option>
            <option value="google_cse" ${s.search_provider === "google_cse" ? "selected" : ""}>Google 自定义搜索 API</option>
          </select>
        </div>
        <div class="field"><label>API Key</label><input class="input full" type="password" id="s-search-key" placeholder="serpapi 或 google key" value="${esc(s.search_api_key)}"></div>
        <div class="field" style="grid-column:1/-1"><label>Google 搜索引擎 ID（cx，仅 Google 源需要）</label><input class="input full" id="s-search-cx" placeholder="0123456789abcdef" value="${esc(s.search_engine_id)}"></div>
        <div class="field"><label>搜索时间范围（WebSearch 收窄，可选）</label>
          <select class="select full" id="s-search-fresh">
            <option value="" ${!s.search_freshness ? "selected" : ""}>不限</option>
            <option value="day" ${s.search_freshness === "day" ? "selected" : ""}>近 24 小时</option>
            <option value="week" ${s.search_freshness === "week" ? "selected" : ""}>近 7 天</option>
            <option value="month" ${s.search_freshness === "month" ? "selected" : ""}>近 1 个月</option>
            <option value="year" ${s.search_freshness === "year" ? "selected" : ""}>近 1 年</option>
          </select>
        </div>
        <div class="field" style="grid-column:1/-1"><label>限定站点/域名（可选，逗号分隔，如 gov.cn,in 或 具体官网）</label><input class="input full" id="s-search-site" placeholder="gov.cn,in,example.com" value="${esc(s.search_site_filter)}"></div>
      </div>
      <div class="hint">推荐用博查 AI 搜索（国内稳定、结果精准，平台：open.bochaai.com）；也可用免费 360/搜狗，或 SerpAPI 走 Google（serpapi.com）。</div>
      <div class="toolbar" style="margin-top:10px;margin-bottom:0">
        <button class="btn" id="s-search-test">🧪 检测搜索源</button>
        <span class="hint">找不到客户时先点这里；每个源最多等 18 秒，超时自动跳过</span>
      </div>
      <div id="s-search-test-result" style="margin-top:8px"></div>
    </div>
    <div class="card">
      <h3>🛡️ 反爬策略（参考"快启精线索"综合反爬体系）</h3>
      <div class="form-grid">
        <div class="field" style="grid-column:1/-1"><label>代理池（逗号分隔，如 http://1.2.3.4:8080,http://user:pass@5.6.7.8:3128）</label><input class="input full" id="s-proxy-pool" placeholder="留空走直连；配置后自动轮换 IP 规避封禁" value="${esc(s.proxy_pool)}"></div>
        <div class="field"><label>搜索请求延时基准（秒，±50% 随机抖动）</label><input class="input full" type="number" step="0.1" min="0" id="s-delay-search" value="${esc(s.delay_search || 0.8)}"></div>
        <div class="field"><label>页面抓取延时基准（秒）</label><input class="input full" type="number" step="0.1" min="0" id="s-delay-fetch" value="${esc(s.delay_fetch || 0.3)}"></div>
        <div class="field"><label>失败重试次数（指数退避）</label><input class="input full" type="number" min="0" max="5" id="s-retry-max" value="${esc(s.retry_max || 2)}"></div>
        <div class="field"><label>重试退避基准延时（秒）</label><input class="input full" type="number" step="0.5" min="0" id="s-retry-base" value="${esc(s.retry_base_delay || 1.0)}"></div>
      </div>
      <div class="hint">反爬体系：① 请求伪装（UA 轮换+Referer）② IP 轮换（代理池）③ 行为模拟（随机延时）④ 动态渲染（Jina 兜底）⑤ 重试退避。未配代理时仍启用 UA 轮换+延时+重试，比单一 UA 更难被识别。</div>
    </div>
    <div class="card">
      <h3>🗺️ 地图接口（地图获客用）</h3>
      <div class="form-grid">
        <div class="field"><label>地图源</label>
          <select class="select full" id="s-map-provider">
            <option value="amap" ${(s.map_provider || "amap") === "amap" ? "selected" : ""}>高德地图（需高德 Key）</option>
            <option value="google_maps" ${s.map_provider === "google_maps" ? "selected" : ""}>谷歌地图（复用 SerpAPI Key）</option>
          </select>
        </div>
        <div class="field"><label>高德地图 API Key</label><input class="input full" type="password" id="s-map-key" placeholder="高德 Web 服务 Key" value="${esc(s.map_api_key)}"></div>
      </div>
      <div class="hint">高德 Key 免费申请：lbs.amap.com → 应用管理 → 创建应用（Web 服务）。选“谷歌地图”则复用你已填的 SerpAPI Key，可搜海外公司；谷歌地图接口每次最多 20 条。</div>
    </div>
    <div class="card">
      <h3>🏢 工商信息查询（企查查 + 天眼查）</h3>
      <div class="form-grid">
        <div class="field"><label>企查查 AppKey</label><input class="input full" type="password" id="s-qcc-app" placeholder="openapi.qcc.com 申请的 AppKey" value="${esc(s.qcc_app_key)}"></div>
        <div class="field"><label>企查查 SecretKey</label><input class="input full" type="password" id="s-qcc-secret" placeholder="openapi.qcc.com 申请的 SecretKey" value="${esc(s.qcc_secret_key)}"></div>
        <div class="field" style="grid-column:1/-1"><label>天眼查 Token</label><input class="input full" type="password" id="s-tyc-token" placeholder="open.tianyancha.com 申请的 Token" value="${esc(s.tyc_token)}"></div>
      </div>
      <div class="hint">至少配置一家（推荐两家都配）：客户详情页点“🏢 查工商”会优先用已配置的一家，失败自动切换另一家。申请地址：企查查开放平台 openapi.qcc.com、天眼查开放平台 open.tianyancha.com。密钥只保存在你自己的服务器。</div>
    </div>
    <div class="card">
      <h3>📱 短信服务商备注</h3>
      <div class="field"><input class="input full" id="s-sms" placeholder="如：阿里云短信，群发平台：xxx" value="${esc(s.sms_notice)}"></div>
    </div>
    <div class="card">
      <h3>🔔 群机器人通知（飞书 / 企业微信）</h3>
      <div class="field"><label>机器人 Webhook 地址</label>
        <input class="input full" id="s-webhook" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxx 或 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" value="${esc(s.notify_webhook)}">
      </div>
      <div class="hint">在飞书/企业微信群添加“自定义机器人”即可获得地址。配置后，落地页收到新客户留资、定时采集到新线索时，会自动推送到群里提醒销售跟进。</div>
      <div style="margin-top:10px"><button class="btn" id="test-webhook">🧪 发送测试通知</button></div>
    </div>
    <div class="card">
      <h3>🔐 自动登录与登录记录</h3>
      <div class="form-grid">
        <div class="field"><label>记住本机 IP，下次自动进入</label>
          <select class="select full" id="s-auto-login">
            <option value="1" ${s.auto_login_trusted === "1" ? "selected" : ""}>开启（推荐）</option>
            <option value="0" ${s.auto_login_trusted !== "1" ? "selected" : ""}>关闭（每次输密码）</option>
          </select>
        </div>
      </div>
      <div class="hint" style="margin-bottom:10px">开启后，登录成功会记录这台设备的 IP；下次从同一网络访问时自动进入后台，不用再输密码。办公网同事共用同一公网 IP 也会自动进入。担心安全可随时关闭或删除某个 IP。</div>
      <div class="field"><label>信任的设备（IP）</label><div id="trusted-list" style="margin-top:6px"><div class="empty">加载中…</div></div></div>
      <div class="field"><label>最近登录记录</label><div id="login-log-list" style="margin-top:6px"><div class="empty">加载中…</div></div></div>
    </div>
    <div style="display:flex;gap:10px">
      <button class="btn primary" id="save-settings">💾 保存设置</button>
      <button class="btn" id="test-smtp">🧪 发送测试邮件</button>
    </div>`;
  $("#save-settings").onclick = async () => {
    const [port, ssl] = $("#s-port").value.split(":");
    try {
      await api("/api/settings", { method: "POST", body: { settings: {
        company_name: $("#s-company").value.trim(),
        product_name: $("#s-product").value.trim(),
        industry: $("#s-industry").value.trim(),
        sender_name: $("#s-sender").value.trim(),
        smtp_host: $("#s-host").value.trim(),
        smtp_port: port,
        smtp_ssl: ssl,
        smtp_user: $("#s-user").value.trim(),
        smtp_password: $("#s-pass").value,
        access_password: $("#s-accesspw").value,
        lp_enabled: $("#s-lp-enabled").value,
        lp_title: $("#s-lp-title").value.trim(),
        lp_subtitle: $("#s-lp-subtitle").value.trim(),
        lp_cta: $("#s-lp-cta").value.trim(),
        lp_phone: $("#s-lp-phone").value.trim(),
        lp_thanks: $("#s-lp-thanks").value.trim(),
        openai_api_key: $("#s-key").value.trim(),
        openai_model: $("#s-model").value.trim() || "gpt-4o-mini",
        openai_api_base: $("#s-api-base").value.trim() || "https://api.openai.com/v1",
        auto_ai_score: $("#s-auto-ai").value,
        sms_notice: $("#s-sms").value.trim(),
        notify_webhook: $("#s-webhook").value.trim(),
        auto_login_trusted: $("#s-auto-login").value,
        search_provider: $("#s-search-provider").value,
        search_api_key: $("#s-search-key").value.trim(),
        search_engine_id: $("#s-search-cx").value.trim(),
        search_freshness: $("#s-search-fresh").value,
        search_site_filter: $("#s-search-site").value.trim(),
        proxy_pool: $("#s-proxy-pool").value.trim(),
        delay_search: $("#s-delay-search").value,
        delay_fetch: $("#s-delay-fetch").value,
        retry_max: $("#s-retry-max").value,
        retry_base_delay: $("#s-retry-base").value,
        map_api_key: $("#s-map-key").value.trim(),
        map_provider: $("#s-map-provider").value,
        qcc_app_key: $("#s-qcc-app").value.trim(),
        qcc_secret_key: $("#s-qcc-secret").value.trim(),
        tyc_token: $("#s-tyc-token").value.trim(),
        auto_intent_enabled: $("#s-auto-intent").value,
        auto_touch_enabled: $("#s-auto-touch").value,
        auto_touch_score: $("#s-auto-touch-score").value.trim() || "7",
        auto_touch_delay: $("#s-auto-touch-delay").value.trim() || "1",
        auto_touch_channel: $("#s-auto-touch-channel").value,
      } } });
      toast("设置已保存", "ok");
    } catch (e) { toast(e.message, "err"); }
  };
  $("#s-lp-open").onclick = () => window.open("/lp", "_blank");
  let searchTestPolling = false;
  $("#s-search-test").onclick = async () => {
    const box = $("#s-search-test-result");
    box.innerHTML = `<div class="empty">搜索自检已启动，正在逐个检测搜索源…（进度看右上角任务栏）</div>`;
    try {
      await api("/api/search/test", { method: "POST", body: { query: "DWDM 采购" } });
      pollSearchTest();
    } catch (e) { box.innerHTML = `<div class="empty">启动失败：${esc(e.message)}</div>`; }
  };
  async function pollSearchTest() {
    if (searchTestPolling) return;
    searchTestPolling = true;
    try {
      const d = await api("/api/search/test");
      const job = d.job || {};
      if (job.running) {
        searchTestPolling = false;
        setTimeout(pollSearchTest, 2000);
        return;
      }
      searchTestPolling = false;
      const box = $("#s-search-test-result");
      if (job.result) renderSearchTest(job.result);
      else box.innerHTML = `<div class="empty">自检失败：${esc(job.message || "未知错误")}</div>`;
    } catch (e) { searchTestPolling = false; }
  }
  function renderSearchTest(r) {
    const box = $("#s-search-test-result");
    if (!box) return;
    const rows = (r.sources || []).map((s) => `
      <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px dashed var(--line);font-size:12px">
        <span style="min-width:150px">${esc(s.name)}</span>
        ${s.status === "ok" ? `<span style="color:var(--green);font-weight:600">✅ ${s.count} 条</span>` : `<span style="color:var(--red);font-weight:600">⚠️ 失败</span>`}
        <span class="sub">${s.seconds}s${s.error ? ` ｜ ${esc(s.error)}` : ""}</span>
      </div>`).join("");
    box.innerHTML = `
      <div class="hint" style="margin-bottom:6px">查询：${esc(r.query)} ｜ 可用源：${r.usable && r.usable.length ? esc(r.usable.join("、")) : "无"}</div>
      ${rows}
      <div class="hint" style="margin-top:6px">${esc(r.advice || "")}</div>`;
  }
  $("#test-webhook").onclick = async () => {
    try {
      await api("/api/settings", { method: "POST", body: { settings: { notify_webhook: $("#s-webhook").value.trim() } } });
      await api("/api/notify/test", { method: "POST", body: {} });
      toast("测试通知已发送，请查看群消息", "ok");
    } catch (e) { toast(e.message, "err"); }
  };
  const loadTrusted = async () => {
    const d = await api("/api/trusted");
    const trusted = d.trusted || [];
    $("#trusted-list").innerHTML = trusted.length
      ? `<div class="chk-list">${trusted.map((t) => `
          <div class="chk-row">
            <span style="flex:1"><b>${esc(t.ip)}</b> <span class="em">${esc(t.ua.slice(0, 40) || "")} · 最近 ${esc(t.last_seen)}</span></span>
            <button class="btn sm danger" data-ip="${esc(t.ip)}">删除</button>
          </div>`).join("")}</div>`
      : `<div class="empty">还没有信任设备：登录一次后会自动记录</div>`;
    $$("#trusted-list [data-ip]").forEach((b) => b.onclick = async () => {
      await api("/api/trusted/" + encodeURIComponent(b.dataset.ip), { method: "DELETE" });
      toast("已移除该设备的自动登录", "ok");
      loadTrusted();
    });
    const logs = d.logs || [];
    $("#login-log-list").innerHTML = logs.length
      ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>IP</th><th>方式</th><th>结果</th></tr></thead><tbody>${logs.map((l) => `
          <tr><td>${esc(l.created_at)}</td><td>${esc(l.ip)}</td><td>${esc(l.action)}</td>
          <td>${l.status === "成功" ? `<span style="color:var(--green)">成功</span>` : `<span style="color:var(--red)">${esc(l.status)}</span>`}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">暂无登录记录</div>`;
  };
  loadTrusted();
  $("#test-smtp").onclick = async () => {
    const btn = $("#test-smtp");
    btn.disabled = true;
    try {
      const [port, ssl] = $("#s-port").value.split(":");
      await api("/api/settings", { method: "POST", body: { settings: {
        company_name: $("#s-company").value.trim(), product_name: $("#s-product").value.trim(),
        sender_name: $("#s-sender").value.trim(), smtp_host: $("#s-host").value.trim(),
        smtp_port: port, smtp_ssl: ssl, smtp_user: $("#s-user").value.trim(),
        smtp_password: $("#s-pass").value, openai_api_key: $("#s-key").value.trim(),
        openai_model: $("#s-model").value.trim() || "gpt-4o-mini", sms_notice: $("#s-sms").value.trim(),
      } } });
      await api("/api/mail/test", { method: "POST", body: {} });
      toast("测试邮件发送成功，请检查收件箱", "ok");
    } catch (e) { toast(e.message, "err"); }
    finally { btn.disabled = false; }
  };
}

/* ---------- 启动 ---------- */
(async function init() {
  try {
    state.meta = await api("/api/meta");
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
    const sess = await api("/api/session");
    if (sess.password_set && !sess.authed) {
      showLogin();
      return;
    }
    if (sess.password_set) {
      $("#nav-logout").style.display = "block";
    }
    pollTasks();
  } catch (e) {
    toast("无法连接本地服务：" + e.message, "err");
  }
  go("dashboard");
})();

$("#nav-logout").onclick = async () => {
  try {
    await api("/api/logout", { method: "POST", body: {} });
    $("#nav-logout").style.display = "none";
    showLogin();
  } catch (e) { toast(e.message, "err"); }
};
