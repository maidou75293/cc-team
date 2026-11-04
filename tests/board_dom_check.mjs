// board_dom_check.mjs — MECH-UX1 看板实时页签红绿锁（selftest #69 调用）
// 靶子=cc-team/board/live.js（主页唯一实时渲染层）。病根账：VIS1/VIS2 时代渲染层没锁，
// 业主眼睛成了唯一探测器——本轮新渲染层落地当轮就上常驻断言（教训兑现，不是口号）。
// 桩保真铁律（#68 踩过）：getElementById 查不到必须返回 null，绝不惰性造元素。
// REQ-PMQ-TIER-1（业主 2026-11-02「要你拍板的 27 件…不需要我关注的为什么展示在这」）：
// 裁决台分层（红=真需业主四类／黄=在办·整改中·<卡号>／VERIFIED+CANCELED 告警出局）由本锁第 6 节钉死。
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

// 卡面判据裸跑 `node cc-team/tests/board_dom_check.mjs`（无参）=本锁唯一靶子 live.js，
// 缺省路径按脚本自身位置解析（与 cwd 无关）；selftest #69 显式传参不受影响。
const file = process.argv[2] || fileURLToPath(new URL("../board/live.js", import.meta.url));
const code = fs.readFileSync(file, "utf8");
let n = 0, fails = 0, asyncErr = null;
process.on("unhandledRejection", (e) => { asyncErr = e; });
const ok = (c, m) => { n++; if (!c) { fails++; console.error("FAIL: " + m); } else console.error("ok: " + m); };
const tickW = () => new Promise((r) => setTimeout(r, 40));
// REQ-BOARD-TERMS-1：桩=真实服务 /ux1-live.js 首部注入的同款词表（唯一出处=cc-team/board/terms.py；
// python 锁 scripts/e2e/board-terms-check.py 逐键比对本桩与 terms.STATUS_TERMS，漂移即红）。
const CC_TERMS = { OPEN: '待派', CLAIMED: '在制', DONE: '待验收', VERIFIED: '已验收',
  CANCELED: '已取消', HOLD: '已冻结', DRAFT: '岗位草稿' };
const CC_PHRASES = { TODAY_CLAIM: '今日领取', TODAY_VERIFIED: '今日已验收', NEW_TODAY: '今日新立',
  CLAIM_EVENT: '领取', VERIFY_EVENT: '已验收' };

// ---- 合成账（对抗性：标题带 </script>、& 符号、中文 emoji；坏依赖名；多态卡） ----
const DAG = { gen: "G1", seal: "abc", generated: "2026-10-31T20:00:00+0800",
  counts: { CLAIMED: 1, OPEN: 1, HOLD: 1, VERIFIED: 1, CANCELED: 1 },
  ready: ["UX-B"], cycle: [], canceled_blocked: ["UX-D"],
  colors: { DRAFT: "#9ca3af", OPEN: "#3b82f6", CLAIMED: "#f59e0b", DONE: "#8b5cf6",
            VERIFIED: "#10b981", HOLD: "#64748b", CANCELED: "#ef4444" },
  cards: {
    "UX-A": { id: "UX-A", status: "CLAIMED", owner: "pm", title: '在制标题 </script><b onclick=x> 与 &', lanes: ["pm"], deps: [], updated_at: "2026-10-31T19:59:00+0800", commit: "deadbeef1234", evidence: "all.sh PASS" },
    "UX-B": { id: "UX-B", status: "OPEN", owner: "-", title: "就绪待开工😀", deps: ["UX-A"], updated_at: new Date(Date.now() - 3 * 864e5).toISOString() },
"UX-H": { id: "UX-H", status: "HOLD", title: "被冻住的", hold: "等业主一句话再解冻", deps: ["NOT-IN-LEDGER"], lanes: ["m9"] },
    "UX-V": { id: "UX-V", status: "VERIFIED", title: "已验收", deps: [],
              at: "2026-11-01T10:20:00+0800", updated_at: "2026-11-02T18:00:00+0800" },  // STATS-1 夹具：at=转正戳，updated_at 更晚（set 拖的时间不许冒充完成时刻）
    "UX-D": { id: "UX-D", status: "OPEN", title: "被取消卡死", deps: ["UX-C"] },
    "UX-C": { id: "UX-C", status: "CANCELED", title: "被清理的僵尸卡（折叠区夹具）", deps: [],
              updated_at: "2026-11-03T15:00:00+0800" } },
  events: { "UX-A": ["2026-10-31T19:58:00|UX-A|pm|start|干活", "2026-10-31T19:59:00|UX-A|pm|note|备注"] },
  progress: { "UX-A": ["2026-10-31T19:59:30|UX-A|pm|40%"] } };
const REJTS = new Date().toISOString();   // 动态时刻：断言「近 24h」永不随日期变红
const OPS = { updated: "20:00:01", rejects: [REJTS + "|Bash|抖窗超时（夹具：近 24h 一笔）"],
  laneact: { pm: 0.5, m1: 41 },  // MECH-FLEET-VIS-1 夹具：道转录龄（分）
  fleet: { heartbeat: "2026-10-30 04:49:01 v2 live", age_min: 2600, note: "派发机最后心跳" },
  alerts: { alerts: {
    "ALARM UX-X: 静默超阈": { count: 3, first: "19:0", last: "19:40" },
    "已ack事项": { count: 1, first: "8:0", last: "8:1", ack: "2026-10-31T08:02:00+0800" } } },
  lanes: [{ lane: "pm", card: "UX-A", kind: "lane", pid: 4242, pid_ns: "win", age_min: 2.1,
            session: "e6841135-06e2-43fb" }],  // LOGBTAIL-1：道锁 meta 里的会话锚（ops 现读带出）
  recycle_tail: ["recycle: stale=0 zombie=0 alive=1 warn=0 mode=APPLY run=7"] };
const OPS2 = JSON.parse(JSON.stringify(OPS)); OPS2.alerts.alerts["ALARM UX-X: 静默超阈"].ack = "x";
// MECH-BOARD-LOGBTAIL-1 夹具：/api/logtail 响应体（对抗性：转录尾摘要带裸标签，必须被转义）
const LT = { id: "UX-A", status: "CLAIMED", owner: "pm", session: "e6841135-06e2-43fb-a236-b491997f7688",
  jsonl_found: true, age_min: 0.4,
  entries: [
    { k: "use", ts: "02:31:05", tool: "Bash", sum: "实施 dispatch bind 回填" },
    { k: "res", ts: "02:31:09", err: false, sum: "SYNTAX_OK <img src=x onerror=alert(1)>" },
    { k: "use", ts: "02:32:00", tool: "Edit", sum: "改 live.js" },
    { k: "res", ts: "02:32:01", err: true, sum: "旧串未命中" }],
  events: ["2026-11-02T02:20:14+0800|UX-A|OPEN->CLAIMED|pm|claim",
           "2026-11-02T02:22:00+0800|UX-A|CLAIMED->CLAIMED|e6841135|bind session_id"],
  progress: ["2026-11-02T02:29:33+0800|UX-A|-|方案定版"],
  verdict: null, brief: "brief-m5-022014.txt", claimed_at: "2026-11-02T02:20:14+08:00" };
const route = (u) => Promise.resolve({ ok: true,
  json: () => Promise.resolve(u.indexOf("/api/dag") >= 0 ? DAG : (u.indexOf("/api/logtail") >= 0 ? LT : OPS)) });

function mkEl(id) {
  const e = { id, attrs: {}, children: [], handlers: {}, textContent: "", value: "", style: {} };
  let h = "";
  Object.defineProperty(e, "innerHTML", { get() { return h; }, set(v) { h = String(v); } });
  e.setAttribute = (k, v) => { e.attrs[k] = String(v); };
  e.appendChild = (c) => { e.children.push(c); return c; };
  e.addEventListener = (ev, fn) => { e.handlers[ev] = fn; };
  e.remove = () => { };
  return e;
}
function boot(fetchImpl) {
  const reg = {};
  ["live-pmq", "ledger-rows", "ledger-n", "stream-ev", "stream-pr", "alerts-live", "rej-live", "liveclock",
   "tab-pmq", "lf", "lfs", "sf", "lt-box",
   // UX-BOARD-COLLAPSE-1 扩展（业主 2026-11-03 23:x「已经取消的放在不显眼地方」）：
   // 生产壳=ledger-canceled 三件入桩，与 status-web 实壳同名；done 壳仍不入=旧「壳不在不藏账」路径继续受测。
   "ledger-rows-canceled", "ledger-canceled", "ledger-canceled-n"].forEach((id) => { reg[id] = mkEl(id); });
  const doc = {
    getElementById: (id) => reg[id] || null,           // 桩保真：查不到=null（#68 血泪规矩）
    createElementNS: (_ns, t) => mkEl(t),
    createElement: (t) => mkEl(t),
    querySelectorAll: () => [],
    body: mkEl("body"),                                // LOGBTAIL-1：详情弹框挂 body（此前没人点弹框所以桩缺这层）
  };
  const win = { addEventListener: () => {} };
  const ctx = vm.createContext({ CC_TERMS, CC_PHRASES, document: doc, window: win, JSON, Math, Object, Set, String, Number,
    Date, Error, Promise, console, fetch: fetchImpl, setInterval: () => 0, encodeURIComponent });
  vm.runInContext(code, ctx, { timeout: 5000 });
  return { reg, win };
}

// ---- 1) 正常链路：boot 自动 tick → 四个页签全渲染 ----
const good = boot(route);
await tickW();
ok(!asyncErr, "无未捕获异常");
const pmq = good.reg["live-pmq"].innerHTML;
ok(pmq.indexOf("待业主拍板") >= 0, "裁决判决条=红档新文案（合成账含业主点名 HOLD+canceled_blocked+停摆积压）");
ok(pmq.indexOf("ALARM UX-X") < 0, "ghost 告警（属主卡不在账）退出裁决台——红条不再无差别收告警（REQ-PMQ-TIER-1）");
ok(good.reg["alerts-live"].innerHTML.indexOf("ALARM UX-X") >= 0, "ghost 告警仍在「告警」页签全量可查（裁决面降级不漏账）");
ok(pmq.indexOf("⏸ 已冻结卡 UX-H") >= 0, "HOLD 卡进拍板清单（标准词=已冻结）");
ok(pmq.indexOf("被 CANCELED 卡死") >= 0, "canceled_blocked 进拍板清单");
ok(pmq.indexOf("在制") >= 0 && pmq.indexOf("UX-A") >= 0, "在制节列出 CLAIMED");
ok(pmq.indexOf("ready 1") >= 0 || pmq.indexOf("UX-B") >= 0, "队列节含 ready");
const rows = (good.reg["ledger-rows"].innerHTML.match(/<tr[ >]/g) || []).length;
ok(rows === 5, "台账直显行数=6 卡减 1 张已取消（CANCELED 入折叠区，不漏不重）");
ok(good.reg["ledger-rows"].innerHTML.indexOf("UX-C") < 0, "已取消卡不占直显区（业主 2026-11-03 令「不显眼」）");
ok(good.reg["ledger-rows-canceled"].innerHTML.indexOf("UX-C") >= 0, "折叠区在册可查（不藏账：账随折叠走）");
ok(good.reg["ledger-canceled-n"].textContent === "1 张", "折叠壳计数由前端填（壳零自造）");
ok(good.reg["ledger-rows"].innerHTML.indexOf("&lt;/script&gt;") >= 0, "对抗标题在台账已转义");
ok(good.reg["ledger-rows"].innerHTML.indexOf("<b onclick") < 0, "台账无裸注入面");
ok(good.reg["ledger-rows"].innerHTML.indexOf("chip y") >= 0, "状态色片在位");
ok(good.reg["ledger-n"].textContent === "6 / 6 张", "计数行=6/6（折叠不减账，总口径不变）");
ok(good.reg["stream-ev"].innerHTML.indexOf("start") >= 0, "events 流渲染");
ok(good.reg["stream-ev"].innerHTML.indexOf("19:59:00") < good.reg["stream-ev"].innerHTML.indexOf("19:58:00") ||
   good.reg["stream-ev"].innerHTML.indexOf("note") >= 0, "events 新→旧（note 在 start 前或同行序可见）");
ok(good.reg["stream-pr"].innerHTML.indexOf("40%") >= 0, "progress 流渲染");
ok(good.reg["alerts-live"].innerHTML.indexOf("开放 <b>1</b>") >= 0, "告警分组：开放 1 已ack 1");
ok(good.reg["alerts-live"].innerHTML.indexOf("×3") >= 0, "同题聚合 count 展示");
ok(good.reg["liveclock"].textContent.length >= 8, "数据时戳已盖（及时性可被看见）");
// MECH-REJT-1（业主令 2026-11-01「被拒历史别黑盒」）+ 陈年灰标（「陈旧不许冒充在动」）
ok(good.reg["rej-live"].innerHTML.indexOf("近 24 小时被拒 <b>1</b>") >= 0, "被拒记录页签渲染（1 笔/24h）");
ok(good.reg["rej-live"].innerHTML.indexOf("抖窗超时") >= 0, "拒绝事由上看板（守卫原话摘录）");
// MECH-PMQ-DEAD-1（业主问「待裁决页都没提车队死了」）：心跳龄>30min 必须占拍板位并点名积压
ok(pmq.indexOf("派发机停摆 2 天") >= 0 && pmq.indexOf("ready 1 张无人派单") >= 0, "车队停摆红灯在待裁决页（含积压数）");
ok(good.reg["ledger-rows"].innerHTML.indexOf("停 3 天") >= 0, "陈年未验收卡带灰标「停 N 天」（UX-B 夹具=3 天前）");
// MECH-FLEET-VIS-1（业主 2026-11-01「也看不到任务是否在执行」）：在制行=道转录活性标
ok(good.reg["ledger-rows"].innerHTML.indexOf("⚡ 活跃") >= 0, "在制行带「⚡ 活跃」（laneact 新写）");
{ const O2 = Object.assign({}, OPS, { laneact: {} });
  good.win.__ux1.setData(DAG, O2); good.win.__ux1.draw();
  ok(good.reg["ledger-rows"].innerHTML.indexOf("无转录") >= 0 && good.reg["ledger-rows"].innerHTML.indexOf("⚡") < 0,
     "活性标红得起来：laneact 断供→活跃字消失");
  good.win.__ux1.setData(DAG, OPS); good.win.__ux1.draw(); }

// ---- 2) 过滤器：lf 输入改变行数（交互不是死的） ----
good.reg["lf"].value = "UX-H";
good.win.__ux1.setData(DAG, OPS); good.win.__ux1.draw();
ok((good.reg["ledger-rows"].innerHTML.match(/<tr[ >]/g) || []).length === 1, "关键词过滤=1 行");
ok(good.reg["ledger-n"].textContent === "1 / 6 张", "过滤计数同步（分母=全账含折叠区，不藏账）");
good.reg["lf"].value = ""; good.reg["lfs"].value = "CLAIMED";
good.win.__ux1.draw();
ok((good.reg["ledger-rows"].innerHTML.match(/<tr[ >]/g) || []).length === 1, "状态筛选=1 行");
good.reg["lfs"].value = "";

// ---- 3) 断链可见性：fetch 炸 → liveerr 横幅必须现（白屏=又拿眼睛当探测器，禁止） ----
const bad = boot(() => Promise.reject(new Error("账不可读")));
await tickW();
ok(String(bad.reg["tab-pmq"].children.map((c) => c.textContent).join("")).indexOf("实时数据读失败") >= 0,
   "断链横幅明报（含原因）");

// ---- 4) 告警全 ack 时裁决转绿 ----
const g2 = boot((u) => Promise.resolve({ ok: true, json: () => Promise.resolve(u.indexOf("/api/dag") >= 0 ? DAG : (u.indexOf("/api/logtail") >= 0 ? LT : OPS2)) }));
await tickW();
const pmq2 = g2.reg["live-pmq"].innerHTML;
ok(pmq2.indexOf("ALARM") < 0, "全 ack 后 ALARM 不再占据拍板位（HOLD/卡死仍应在）");
ok(pmq2.indexOf("⏸ 已冻结卡") >= 0, "HOLD 与 ack 无关，仍在拍板位");

// ---- 5) 防红自证：断掉 draw 内部的台账调用 → tick 走 catch 挂横幅（锁不是空枪）。
// 首版误攻 window 导出映射（ledgerRender: undefined）——live.js 内部闭包直调函数不经导出，
// 假红=空枪，被「替换未命中必报错」自逮，2026-10-31 定案：红探针必须打内部调用点。
const brk = code.replace("pmqRender(_D, _O); ledgerRender(_D); streamRender(_D); alertRender(_O);",
  "pmqRender(_D, _O); ledgerRender_redprobe_removed(_D); streamRender(_D); alertRender(_O);");
if (brk === code) { fails++; console.error("FAIL: 红自证替换未命中（draw 结构漂移，锁需同步）"); }
else {
  const b3 = (() => {
    const reg = {};
    ["live-pmq", "ledger-rows", "ledger-n", "stream-ev", "stream-pr", "alerts-live", "rej-live", "liveclock", "tab-pmq", "lf", "lfs", "sf"].forEach((id) => { reg[id] = mkEl(id); });
    const doc = { getElementById: (id) => reg[id] || null, createElementNS: (_n, t) => mkEl(t), createElement: (t) => mkEl(t), querySelectorAll: () => [] };
    const win = {};
    const ctx = vm.createContext({ CC_TERMS, CC_PHRASES, document: doc, window: win, JSON, Math, Object, Set, String, Number, Date, Error, Promise, console, fetch: (u) => Promise.resolve({ ok: true, json: () => Promise.resolve(u.indexOf("/api/dag") >= 0 ? DAG : OPS) }), setInterval: () => 0 });
    vm.runInContext(brk, ctx, { timeout: 5000 });
    return { reg, win };
  })();
  await tickW();
  const broke = b3.reg["ledger-rows"].innerHTML === "" || String(b3.reg["tab-pmq"].children.map((c) => c.textContent).join("")).indexOf("读失败") >= 0;
  ok(broke, "红自证：撤渲染器台账变空/报警（本锁会红）");
}

// ---- 6) MECH-BOARD-LOGBTAIL-1：台账「未绑定会话」黄字 + 详情弹框运行尾面板（业主令「点开卡见本卡运行轨迹」）----
{ // 规格③红绿翻转：同道 CLAIMED 卡，道锁 meta 无会话绑定=黄字「未绑定会话」；bind 回填后=消失（同源数据两态，锁红得起来）
  const NA = JSON.parse(JSON.stringify(OPS)); NA.lanes[0].session = "-";
  good.win.__ux1.setData(DAG, NA); good.win.__ux1.draw();
  ok(good.reg["ledger-rows"].innerHTML.indexOf("未绑定会话") >= 0, "CLAIMED 未见 bind→台账行黄字「未绑定会话」");
  const B9 = JSON.parse(JSON.stringify(OPS)); B9.lanes[0].session = "live-uuid";
  good.win.__ux1.setData(DAG, B9); good.win.__ux1.draw();
  ok(good.reg["ledger-rows"].innerHTML.indexOf("未绑定会话") < 0, "会话已绑定→黄字「未绑定会话」消失（红绿同源翻转）");
  const NOCARD = JSON.parse(JSON.stringify(OPS)); NOCARD.lanes = [];
  good.win.__ux1.setData(DAG, NOCARD); good.win.__ux1.draw();
  ok(good.reg["ledger-rows"].innerHTML.indexOf("未绑定会话") >= 0, "道锁整把不见（手工认领残渣）同样=未绑定会话，不装安心");
}
{ // ltRender 纯函数直测：三态如实 + 对抗转义
  const R = good.win.__ux1.ltRender;
  ok(R(Object.assign({}, LT, { session: "-", status: "CLAIMED" })).indexOf("未绑定会话") >= 0,
     "ltRender: CLAIMED 未绑定会话明说「写闸 bind 未回填 session_id」");
  ok(R({ status: "VERIFIED", owner: "pm", session: "-", events: [], progress: [], entries: [], brief: "" }).indexOf("未绑定会话") >= 0,
     "ltRender: 终态卡未绑定如实说（不当在制冒充）");
  ok(R({ error: "卡不在账（graph.json 查无此 id）" }).indexOf("卡不在账") >= 0, "ltRender: error 体可读");
  const one = R(LT);
  ok(one.indexOf("&lt;img") >= 0 && one.indexOf("<img") < 0, "ltRender: 转录尾摘要裸标签已转义");
  ok(one.indexOf("▸ Bash 实施 dispatch bind 回填") >= 0 && one.indexOf("✖ 旧串未命中") >= 0,
     "ltRender: tool_use ▸ / 报错 ✖ / 成功 ✔ 口径");
  ok(one.indexOf("bind session_id") >= 0 && one.indexOf("方案定版") >= 0 && one.indexOf("brief-m5-022014.txt") >= 0,
     "ltRender: events verdict 痕+progress 痕+brief 链接三块齐");
}
{ // 详情弹框链路：点台账行 → /api/logtail 异步填运行尾（真弹框路径=业主点的那条）
  good.win.__ux1.setData(DAG, OPS); good.win.__ux1.draw();
  good.reg["lt-box"].innerHTML = "";
  good.reg["ledger-rows"].handlers.click({ target: { closest: () => ({ getAttribute: () => "UX-A" }) } });
  await tickW();
  const lt = good.reg["lt-box"].innerHTML;
  ok(lt.indexOf("会话绑定") >= 0 && lt.indexOf("e6841135") >= 0, "弹框运行尾=按卡上 session 现读（绑定上行）");
  ok(lt.indexOf("最后写入") >= 0, "转录写入龄随绑定展示（心跳同口径）");
  ok(lt.indexOf("派发 brief") >= 0 && lt.indexOf("/api/brief?name=brief-m5-022014.txt") >= 0,
     "brief 链接指 /api/brief 白名单端点");
}
{ // 红自证：摘掉锚查找 → 弹框运行尾永不填充（本锁不是空枪，#5 同法打内部调用点）
  const brk4 = code.replace("var ltb = document.getElementById('lt-box');",
    "var ltb = null; /* redprobe-LOGBTAIL */");
  if (brk4 === code) { fails++; console.error("FAIL: 红自证替换未命中（live.js 运行尾结构漂移，锁需同步）"); }
  else {
    const reg = {};
    ["live-pmq", "ledger-rows", "ledger-n", "stream-ev", "stream-pr", "alerts-live", "rej-live",
     "liveclock", "tab-pmq", "lf", "lfs", "sf", "lt-box"].forEach((id) => { reg[id] = mkEl(id); });
    const doc = { getElementById: (id) => reg[id] || null, createElementNS: (_n, t) => mkEl(t),
      createElement: (t) => mkEl(t), querySelectorAll: () => [], body: mkEl("body") };
    const win = { addEventListener: () => {} };
    const ctx = vm.createContext({ CC_TERMS, CC_PHRASES, document: doc, window: win, JSON, Math, Object, Set, String, Number,
      Date, Error, Promise, console, encodeURIComponent, fetch: route, setInterval: () => 0 });
    vm.runInContext(brk4, ctx, { timeout: 5000 });
    win.__ux1.setData(DAG, OPS); win.__ux1.draw();
    reg["ledger-rows"].handlers.click({ target: { closest: () => ({ getAttribute: () => "UX-A" }) } });
    await tickW();
    ok(reg["lt-box"].innerHTML === "", "红自证：撤锚查找后运行尾永不填（断言会红）");
  }
}

// ---- 7) MECH-BOARD-STATS-1：完成时刻取值链 at 优先（台账「完成」列 ↔ 顶栏「今日已验收」同源同链，
// 对表法才立得住——两边各读各的字段=业主看到的两个数字永远差一口）----
{ good.win.__ux1.setData(DAG, OPS); good.win.__ux1.draw();
  const rows7 = good.reg["ledger-rows"].innerHTML;
  ok(rows7.indexOf("11-01 10:20") >= 0, "STATS: VERIFIED 完成列=转正戳 at（UX-V 行）");
  ok(rows7.indexOf("11-02 18:00") < 0, "STATS: at 在场时 updated_at 不得冒充完成时刻");
  const brk5 = code.replace("if (c.at) return c.at;", "if (0) return c.at; /* redprobe-STATS */");
  if (brk5 === code) { fails++; console.error("FAIL: 红自证替换未命中（live.js doneTs 结构漂移，锁需同步）"); }
  else {
    const reg = {};
    ["live-pmq", "ledger-rows", "ledger-n", "stream-ev", "stream-pr", "alerts-live", "rej-live",
     "liveclock", "tab-pmq", "lf", "lfs", "sf", "lt-box"].forEach((id) => { reg[id] = mkEl(id); });
    const doc = { getElementById: (id) => reg[id] || null, createElementNS: (_n, t) => mkEl(t),
      createElement: (t) => mkEl(t), querySelectorAll: () => [], body: mkEl("body") };
    const win = { addEventListener: () => {} };
    const ctx = vm.createContext({ CC_TERMS, CC_PHRASES, document: doc, window: win, JSON, Math, Object, Set, String, Number,
      Date, Error, Promise, console, encodeURIComponent, fetch: route, setInterval: () => 0 });
    vm.runInContext(brk5, ctx, { timeout: 5000 });
    win.__ux1.setData(DAG, OPS); win.__ux1.draw();
    ok(reg["ledger-rows"].innerHTML.indexOf("11-01 10:20") < 0 && reg["ledger-rows"].innerHTML.indexOf("11-02 18:00") >= 0,
       "红自证：撤 at 优先后完成列塌回 updated_at（本锁红得起来）");
  } }
// ---- 8) REQ-PMQ-TIER-1 裁决台分层（业主 2026-11-02 11:0x「要你拍板的 27 件…不需要我关注的为什么展示在这」）
// 假数据按现账配比复刻：25 条未 ack ALARM——13 涉 VERIFIED、1 涉 CANCELED、7 涉 DONE、4 涉 CLAIMED；
// 外加 1 张 hold 文案点名义主的 HOLD 卡。承诺：红条只剩这 1 张；25 条各归其位；旧句式绝迹。
const RECC = new Date(Date.now() - 30 * 6e4).toISOString();
const TCARDS = { "T-HOLD": { id: "T-HOLD", status: "HOLD", title: "需业主定夺", hold: "业主拍板后才动", deps: [] },
  "T-CAN": { id: "T-CAN", status: "CANCELED", title: "已撤卡", deps: [] } };
for (let i = 1; i <= 13; i++) TCARDS["T-V" + i] = { id: "T-V" + i, status: "VERIFIED", title: "已验收" + i, deps: [] };
for (let i = 1; i <= 7; i++) TCARDS["T-D" + i] = { id: "T-D" + i, status: "DONE", title: "待验批" + i, deps: [] };
for (let i = 1; i <= 4; i++) TCARDS["T-K" + i] = { id: "T-K" + i, status: "CLAIMED", owner: "m" + i, title: "在制" + i, claimed_at: RECC, updated_at: RECC, deps: [] };
const TDAG = { cards: TCARDS, ready: [], cycle: [], canceled_blocked: [], counts: {}, colors: {}, events: {}, progress: {} };
const TALK = {};
for (let i = 1; i <= 13; i++) TALK["ALARM T-V" + i + ": session 执行空壳(无幻觉锚) 静默超阈 → 连续两轮 WARN 仍未收口"] = { count: 2, first: "09:0", last: "10:40" };
TALK["ALARM T-CAN: lane 锚失踪 无 pid(claim 未带 --pid) 换机锚"] = { count: 1, first: "08:0", last: "08:30" };
for (let i = 1; i <= 7; i++) TALK["ALARM: T-D" + i + " 状态=VERIFIED 但事件流水尾行=DONE(补验/漏改)"] = { count: 3, first: "07:0", last: "10:50" };
for (let i = 1; i <= 4; i++) TALK["ALARM T-K" + i + ": owner=m" + i + " session 执行空壳 静默超阈"] = { count: 1, first: "10:0", last: "11:0" };
const OPS_T = { alerts: { alerts: TALK }, fleet: { heartbeat: "fresh", age_min: 5 }, lanes: [], rejects: [] };
const bootWith = (dag, ops) => boot((u) => Promise.resolve({ ok: true, json: () => Promise.resolve(u.indexOf("/api/dag") >= 0 ? dag : ops) }));
const sect = (h, a, b) => h.slice(Math.max(0, h.indexOf(a)), Math.max(0, h.indexOf(b)));
const lis = (s) => (s.match(/<li>/g) || []).length;

const t1 = bootWith(TDAG, OPS_T); await tickW();
const pt = t1.reg["live-pmq"].innerHTML;
const secR = sect(pt, "🔴 待业主拍板", "🟡 在办");
const secY = sect(pt, "🟡 在办", "⚪ 队列");
ok(pt.indexOf("要你拍板的") < 0 && pt.indexOf("等你拍板") < 0, "旧「要你拍板的/等你拍板」句式绝迹（业主病灶④）");
ok(pt.indexOf("待业主拍板 1 件 · 在办 11 件 · 其余机器自转") >= 0, "头部文案④「待业主拍板 N 件 · 在办 M 件 · 其余机器自转」");
ok(lis(secR) === 1, "红条项数=1（25 条混合告警零进红，仅业主点名 HOLD）");
ok(secR.indexOf("⏸ 已冻结卡 T-HOLD") >= 0, "红条唯一点=hold 文案含业主/拍板的 HOLD 卡（四类之①a）");
ok(lis(secY) === 11, "黄条项数=11（7 张 DONE + 4 张 CLAIMED 属主告警并条）");
ok(secY.indexOf("整改中·T-D1") >= 0 && secY.indexOf("整改中·T-K1") >= 0, "黄条格式=整改中·<卡号>（四类之②）");
ok(pt.indexOf("T-V3") < 0 && pt.indexOf("T-CAN") < 0, "VERIFIED/CANCELED 卡的告警不进任何条（③展示层第二道防线）");
ok(pt.indexOf("派发机停摆") < 0, "车队健康（心跳 5 分）不占任何条");
// ①b IM 信箱 [待拍板] 条：契约=O.im 数组（后端投影缺口登记 evidence，归 SCOPE-1/主控；本卡边界不动 status-web.py）
{ const O2 = JSON.parse(JSON.stringify(OPS_T));
  O2.im = ["[待拍板] seq=9 旧看板下线与合并否", "[例行] seq=8 心跳正常勿念"];
  const t2 = bootWith(TDAG, O2); await tickW();
  const p2 = t2.reg["live-pmq"].innerHTML;
  ok(lis(sect(p2, "🔴 待业主拍板", "🟡 在办")) === 2 && p2.indexOf("seq=9") >= 0 && p2.indexOf("seq=8") < 0,
     "IM 信箱 [待拍板] 条进红、非待拍板条不进（四类之①b）"); }
// ①d 派发机停摆必须「且有积压」才算业主账：停摆无积压→黄条知情，不占红
{ const O3 = JSON.parse(JSON.stringify(OPS_T)); O3.fleet = { heartbeat: "old", age_min: 2600 };
  const t3 = bootWith(TDAG, O3); await tickW();
  const p3 = t3.reg["live-pmq"].innerHTML;
  ok(lis(sect(p3, "🔴 待业主拍板", "🟡 在办")) === 1 && sect(p3, "🟡 在办", "⚪ 队列").indexOf("派发机停摆") >= 0,
     "停摆无积压降黄条——红条四类之外一概不进（①d 反面）"); }
// ④ N=0：业主点名 HOLD 不存在（hold=机器文案）时整台转绿
{ const D4 = JSON.parse(JSON.stringify(TDAG)); D4.cards["T-HOLD"].hold = "PM 解冻 N1 后发出";
  const t4 = bootWith(D4, OPS_T); await tickW();
  const p4 = t4.reg["live-pmq"].innerHTML;
  ok(p4.indexOf("没有待业主拍板的") >= 0 && p4.indexOf("verdict g") >= 0 &&
     lis(sect(p4, "🔴 待业主拍板", "🟡 在办")) === 0, "N=0 显绿条「没有待业主拍板的」（④后半）");
  ok(sect(p4, "🟡 在办", "⚪ 队列").indexOf("已冻结·T-HOLD") >= 0, "机器/PM 管解冻的 HOLD 不冒充业主账，转黄条知情（REQ-BOARD-TERMS-1：黄条走词表 TT('HOLD')）"); }
// UX-BOARD-COLLAPSE-1 扩展红自证（同 DONE 折叠 ③ 层纪律）：摘掉折叠壳=账必须回直显区，
// 「藏账」结构性不可能——壳不在就不分桶。
{ const before = good.reg["ledger-rows"].innerHTML;
  delete good.reg["ledger-rows-canceled"]; delete good.reg["ledger-canceled"]; delete good.reg["ledger-canceled-n"];
  good.win.__ux1.setData(DAG, OPS); good.win.__ux1.draw();
  const after = good.reg["ledger-rows"].innerHTML;
  ok(before.indexOf("UX-C") < 0 && after.indexOf("UX-C") >= 0,
     "红自证：摘壳后已取消卡回灌直显区（摘拆桶条件必红，锁得住）"); }
console.error("board_dom_check: asserts=" + n + " fails=" + fails);
if (fails) { console.log("BOARD_DOM_FAIL"); process.exit(1); }
console.log("BOARD_DOM_PASS n=" + n);
