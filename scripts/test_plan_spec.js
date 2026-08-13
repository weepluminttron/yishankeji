// 临时验证：方案关键词 → 必中规格 清洗逻辑
function cleanPlanSpec(kw) {
  const kwText = String(kw || "").trim();
  let s = kwText.split(/\s+(?:采购经理|扩容项目|项目方|采购公告|招标公告|询价公告|中标公告|采购|招标|询价|求购|公告|中标|项目|需求|供应商|procurement|tender|rfq|rfp|purchase|buyer|sourcing|inquiry|distributor|dealer|supplier)\b/i)[0] || "";
  s = s.trim();
  if (s === kwText) {
    s = kwText.replace(/(?:采购经理|扩容项目|项目方|采购公告|招标公告|询价公告|中标公告|采购|招标|询价|求购|公告|中标|项目|需求|供应商|procurement|tender|rfq|rfp|purchase|buyer|sourcing|inquiry|distributor|dealer|supplier)\s*$/i, "").trim();
  }
  if (!s || /(采购|招标|询价|求购|公告|中标|项目|需求|供应商|procurement|tender|rfq|rfp|purchase|buyer)/i.test(s)) {
    s = kwText.split(/\s+/)[0] || "";
  }
  return s.trim();
}

const cases = [
  ["DWDM 采购公告", "DWDM"],
  ["data center fiber procurement", "data center fiber"],
  ["光传输扩容 项目方", "光传输扩容"],
  ["电信运营商 光模块需求", "电信运营商 光模块"],
  ["MPO MTP 预端接光缆", "MPO MTP 预端接光缆"],
  ["WDM 采购公告 电信", "WDM"],
];
let ok = true;
for (const [input, expect] of cases) {
  const got = cleanPlanSpec(input);
  const pass = got === expect;
  if (!pass) ok = false;
  console.log((pass ? "OK  " : "FAIL") + " " + input + " -> " + got + (pass ? "" : "（期望 " + expect + "）"));
}
process.exit(ok ? 0 : 1);
