// 验证 renderPlans 模板在真实方案数据下不会抛错（Node 环境模拟 DOM）
const fs = require("fs");
const src = fs.readFileSync("static/app.js", "utf-8");

// 只提取 renderPlans 函数体做运行验证
const start = src.indexOf("function renderPlans(");
const end = src.indexOf("/* ---------- 主动触达 ---------- */", start);
const fnText = src.slice(start, end > 0 ? end : src.length);

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const fakeEl = {
  innerHTML: "",
  style: {},
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const $ = () => fakeEl;
const $$ = () => [];

// 直接执行函数文本（不依赖 window）
eval(fnText);

const plans = [
  {
    title: "方案A：海外数据中心总包商", buyer_role: "系统集成商",
    target_customers: "欧洲数据中心总包商", keywords: ["DWDM 采购公告", "波分项目 招标", "fiber procurement", "扩容 项目"],
    markets: ["欧美"], profit: 5, brand: 4, demand: 3, effort: 4,
    timeline: "1周建名单+首触，2周送样", pitch: "您好，我们是波分器件供应商，方便发份资料？",
    why: "扩产期窗口明确", decision: "技术+采购双确认，2-3个月", moat: "现货+定制",
    strategy: "先送样", cooperation: "长期框架", channels: ["展会", "邮件"], risks: ["认证门槛"],
  },
  {
    title: "方案B：运营商扩容", buyer_role: "运营商", target_customers: "东南亚运营商",
    keywords: ["WDM 扩容 招标", "metro 集采", "光传输 项目"], markets: ["印度", "印尼"],
    profit: 4, brand: 3, demand: 5, effort: 3, timeline: "", pitch: "", why: "",
    decision: "", moat: "", strategy: "", cooperation: "", channels: [], risks: [],
  },
];

renderPlans(plans, ["方案之间的市场趋同，建议覆盖不同地区"]);
if (!fakeEl.innerHTML.includes("AI 获客方案（2 套）")) {
  throw new Error("未渲染标题: " + fakeEl.innerHTML.slice(0, 200));
}
if (!fakeEl.innerHTML.includes("难度 ★★★★") || !fakeEl.innerHTML.includes("决策链/周期")) {
  throw new Error("新字段未渲染");
}
console.log("renderPlans 运行时 OK，输出长度:", fakeEl.innerHTML.length);
