// board_laneword_dom_check.mjs — REQ-BOARD-LANEWORD-1 道栏文案客户端常驻锁（由 board_laneword_check.py 调用）
// 业主 2026-11-02 10:5x 问「为什么一个卡有3个道」：属主与候选白名单连排 m1 / m7,m1 易误读。
// 靶子=cc-team/board/live.js 三处：台账「候补道」列剔除在制道、执行详情弹窗拆 在制道（属主）/
// 候补道（先到先得，领走即出局）两段、无候补不显示空壳。独立 boot——不动 #69 board_dom_check.mjs（别道在跑该文件）。
// 跑法: node board_laneword_dom_check.mjs <live.js路径> → stdout 末行 BOARD_LW_DOM_PASS n=N 即绿；FAIL → exit 1。
import fs from "node:fs";
import vm from "node:vm";

const file = process.argv[2];
if (!file) { console.error("usage: node board_laneword_dom_check.mjs <live.js>"); process.exit(2); }
const code = fs.readFileSync(file, "utf8");
let n = 0, fails = 0, asyncErr = null;
process.on("unhandledRejection", (e) => { asyncErr = e; });
const ok = (c, m) => { n++; if (!c) { fails++; console.error("FAIL: " + m); } else console.error("ok: " + m); };
const tickW = () => new Promise((r) => setTimeout(r, 40));
const NOW = new Date().toISOString();

// ---- 合成账：LW-A 在制 m1 + 候补 m7（连排病样本）；LW-B 独道无候补（空壳样本）；LW-C 未领全候补 ----
const DAG = { gen: "G", seal: "s", counts: {}, ready: [], cycle: [], canceled_blocked: [], colors: {},
  cards: {
    "LW-A": { id: "LW-A", status: "CLAIMED", owner: "m1", title: "带候补 </script>&'", lanes: ["m7", "m1"],
              deps: [], created_at: NOW, first_claimed_at: NOW, updated_at: NOW },
    "LW-B": { id: "LW-B", status: "CLAIMED", owner: "m2", title: "独道无候补", lanes: ["m2"],
              deps: [], created_at: NOW, first_claimed_at: NOW, updated_at: NOW },
    "LW-C": { id: "LW-C", status: "OPEN", owner: "-", title: "待派=全是候补", lanes: ["m5", "m3"], deps: [], updated_at: NOW } },
  events: {}, progress: {} };
const OPS = { updated: NOW, rejects: [], fleet: { heartbeat: "x", age_min: 1 }, laneact: {},
              lanes: [], alerts: { alerts: {} } };

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
function boot(src, fetchImpl) {
  const reg = {};
  ["live-pmq", "ledger-rows", "ledger-n", "stream-ev", "stream-pr", "alerts-live", "rej-live", "liveclock",
   "tab-pmq", "lf", "lfs", "sf"].forEach((id) => { reg[id] = mkEl(id); });
  const body = { children: [], appendChild: (c) => { body.children.push(c); return c; } };
  const doc = { getElementById: (id) => reg[id] || null, createElementNS: (_n, t) => mkEl(t),
                createElement: (t) => mkEl(t), querySelectorAll: () => [], body };
  const win = { addEventListener: () => { } };
  const ctx = vm.createContext({ document: doc, window: win, JSON, Math, Object, Set, String, Number,
    Date, Error, Promise, setTimeout, console, fetch: fetchImpl, setInterval: () => 0 });
  vm.runInContext(src, ctx, { timeout: 5000 });
  return { reg, win, body };
}
const goodFetch = (u) => Promise.resolve({ ok: true, json: () => Promise.resolve(u.indexOf("/api/dag") >= 0 ? DAG : OPS) });
function openModal(b, cid) {
  b.reg["ledger-rows"].handlers.click({ target: { closest: () => ({ getAttribute: () => cid }) } });
  return b.body.children[b.body.children.length - 1].innerHTML;
}

// ---- 绿链：真 boot 真渲染 ----
{
  const b = boot(code, goodFetch);
  await tickW();
  ok(!asyncErr, "无未捕获异常");
  const rows = b.reg["ledger-rows"].innerHTML;
  // REQ-BOARD-RUNCOLS-1 锁同步：属主列可挂「无转录/活性/未绑定会话/停N天」挂道标（原精确整串匹配
  // 系徽章混排时代的锚），语义仍=「属主/候补分列」——改按 td 取列：属主列以道名开头、
  // 候补列独立整格；列序（3=属主 4=候补）由 board-runcols-test 的列数对齐闸常驻盯防。
  const tdsOf = (all, cid) => { const seg = String(all).split('<tr data-cid="' + cid + '"')[1] || ""; return (seg.split("</tr>")[0].match(/<td[^>]*>[\s\S]*?<\/td>/g) || []).map((s) => s.replace(/^<td[^>]*>|<\/td>$/g, "")); };
  const laneOnly = (x) => String(x || "").replace(/ <span[\s\S]*$/, "");
  const la = tdsOf(rows, "LW-A"), lb = tdsOf(rows, "LW-B"), lc = tdsOf(rows, "LW-C");
  ok(laneOnly(la[3]) === "m1" && la[4] === "m7", "台账 LW-A：属主 m1 与候补 m7 分列，候补道列不含在制道");
  ok(rows.indexOf("m7,m1") < 0 && !/m1\s*\/\s*m7/.test(rows), "台账无 m7,m1 含主连排（旧样式死透）");
  ok(laneOnly(lb[3]) === "m2" && lb[4] === "—", "台账 LW-B 无候补=占位破折号，不显示假列表");
  ok(laneOnly(lc[3]) === "-" && lc[4] === "m5,m3", "台账 LW-C 未领卡=白名单全为候补");
  const mA = openModal(b, "LW-A");
  ok(mA.indexOf("在制道（属主）") >= 0 && mA.indexOf(">m1<") >= 0, "弹窗 LW-A 在制道段=m1");
  ok(mA.indexOf("候补道（先到先得，领走即出局）") >= 0 && mA.indexOf(">m7<") >= 0, "弹窗 LW-A 候补道段=m7+出局语义");
  ok(mA.indexOf("m7、m1") < 0 && !/m1\s*\/\s*m7/.test(mA) && mA.indexOf("属主 / 道") < 0, "弹窗连排 m1 / m7,m1 不再出现");
  const mB = openModal(b, "LW-B");
  ok(mB.indexOf("在制道（属主）") >= 0 && mB.indexOf("候补道") < 0, "弹窗 LW-B 无候补不显示空壳");
  const mC = openModal(b, "LW-C");
  ok(mC.indexOf("候补道（先到先得，领走即出局）") >= 0 && mC.indexOf("m5、m3") >= 0, "弹窗 LW-C 候补=m5、m3（顿号连排非斜杠）");
}

// ---- 红自证 A：弹窗退回旧「属主 / 道」连排 → 上面对拆段的断言必须红 ----
{
  const OLD = "fld('在制道（属主）', esc(c.owner || '-')) +\n" +
              "      (lanesCand(c).length ? fld('候补道（先到先得，领走即出局）', lanesCand(c).map(esc).join('、')) : '') +";
  const brk = code.replace(OLD, "fld('属主 / 道', esc(c.owner || '-') + ' / ' + esc((c.lanes || []).join(',') || '-')) +");
  if (brk === code) { fails++; console.error("FAIL: 红自证A 替换未命中（弹窗结构漂移，锁需同步）"); }
  else {
    const b = boot(brk, goodFetch);
    await tickW();
    const mA = openModal(b, "LW-A");
    ok(mA.indexOf("在制道（属主）") < 0 && mA.indexOf("属主 / 道") >= 0, "红自证A：拆段撤掉即判红（本锁会红）");
  }
}
// ---- 红自证 B：候补列不剔在制道（filter 被摘）→ 连排病复发即判红 ----
{
  const brk = code.replace("function lanesCand(c) { return (c.lanes || []).filter(function (l) { return l !== c.owner; }); }",
                          "function lanesCand(c) { return (c.lanes || []); }");
  if (brk === code) { fails++; console.error("FAIL: 红自证B 替换未命中（lanesCand 结构漂移，锁需同步）"); }
  else {
    const b = boot(brk, goodFetch);
    await tickW();
    ok(b.reg["ledger-rows"].innerHTML.indexOf("m7,m1") >= 0, "红自证B：不剔在制道时连排复发可被本锁抓到");
  }
}

console.error("board_laneword_dom_check: asserts=" + n + " fails=" + fails);
if (fails) { console.log("BOARD_LW_DOM_FAIL"); process.exit(1); }
console.log("BOARD_LW_DOM_PASS n=" + n);
