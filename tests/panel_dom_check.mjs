// panel_dom_check.mjs — MECH-VIS2/VIS3 渲染器红绿锁（selftest #68 调用）
// 为什么存在：此前渲染器（panel.py 内嵌 JS）零自动断言——VIS1 的 DOM 实证是一次性手工 node 桩，
// 改一行 JS 没有任何东西会红。业主「图操作为什么 BUG 这么多」的病根之一就在这：无守卫的活体代码。
// VIS3 后双模式必须同图：①文件模式（panel 导出快照，内嵌账）②壳模式（看板 /dag，fetch /api/dag
// 现读真账）——两条路共用同一个 main(D)，桩都跑一遍；断链要喊「读账失败」不许白屏。
// 自带【防红自证】：删掉 detail 函数后点击必须炸——不炸=守卫是空的=比没有更糟（全局纪律）。
import fs from "node:fs";
import vm from "node:vm";

const file = process.argv[2];
if (!file) { console.error("usage: node panel_dom_check.mjs <panel.html>"); process.exit(2); }
const html = fs.readFileSync(file, "utf8");
let n = 0, fails = 0, asyncErr = null;
process.on("unhandledRejection", (e) => { asyncErr = e; });
const ok = (cond, msg) => { n++; if (!cond) { fails++; console.error("FAIL: " + msg); } else console.error("ok: " + msg); };
const tick = () => new Promise((r) => setTimeout(r, 30));   // 放线 vm 里的 promise 链

// ---- 1) 数据块与转义往返 ----
const mj = html.match(/<script type="application\/json" id="ledger">([\s\S]*?)<\/script>/);
ok(!!mj, "ledger JSON 段可提取（数据里的 </ 已转义成正则不被截断的形态）");
if (!mj) { console.error("PANEL_DOM_FAIL(no-json)"); process.exit(1); }
const raw = mj[1];
ok(raw.indexOf("<\\/") >= 0, "blob 层 </ 已转义为 <\\\\/（防账本数据提前闭合 script 标签）");
const data = JSON.parse(raw);
ok(data.cards["V2-A"].title.indexOf("</script>") >= 0, "对抗标题 </script> 穿过转义往返仍等值");
ok(data.cards["V2-A"].title === "标题含 </script><img src=x onerror=alert(1)> 与 & 符号", "标题逐字节等值");

// ---- 2) DOM 桩 + 执行器（embed=内嵌账；live=fetch 现读） ----
function mk(tag) {
  const e = { tag: tag, attrs: {}, children: [], handlers: {}, textContent: "",
    setAttribute(k, v) { e.attrs[k] = String(v); },
    getAttribute(k) { return e.attrs[k]; },
    appendChild(c) { e.children.push(c); return c; },
    append() { for (const c of arguments) e.children.push(c); return e; },
    addEventListener(ev, fn) { e.handlers[ev] = fn; },
    classList: { s: new Set(), add(x) { this.s.add(x); }, remove(x) { this.s.delete(x); } } };
  let h = "";
  Object.defineProperty(e, "innerHTML", { get() { return h; }, set(v) { h = String(v); } });
  return e;
}
// 桩保真铁律（#68 首次上线就抓到本桩自己的失真）：getElementById 查不到必须返回 null——
// 浏览器语义如此。桩若" lazily 造元素"，壳模式永不会走 fetch 路，锁就瞎了。
function makeDoc() {
  const ids = { hd: mk("hd"), svg: mk("svg"), dt: mk("dt") };  // 模板静态三件套（真页面里写死存在）
  return { ids, doc: {
    getElementById(id) { return ids[id] || null; },
    createElementNS(_ns, t) { return mk(t); },
    querySelectorAll() { return []; } } };
}
function run(src, embed, fetchImpl) {
  const { ids, doc } = makeDoc();
  if (embed) { ids.ledger = mk("ledger"); ids.ledger.textContent = raw; }
  const box = { calls: [], ivArmed: 0, ivCleared: 0 };
  const fetch = fetchImpl || function (u, o) {
    box.calls.push([u, o]);
    return Promise.resolve({ json: () => Promise.resolve(JSON.parse(raw)) });
  };
  const ctx = vm.createContext({ document: doc, JSON, Math, Object, Set, String, Number, console, Promise, fetch,
    setInterval: () => { box.ivArmed++; return 1; }, clearInterval: () => { box.ivCleared++; } });
  vm.runInContext(src, ctx, { timeout: 5000 });
  return { ids, box };
}
const ms = html.match(/<script>([\s\S]*?)<\/script>/);
ok(!!ms, "主脚本段可提取");
const code = ms[1];

// ---- 3) 文件模式（快照/导出路） ----
const { ids, box } = run(code, true);
ok(box.calls.length === 0, "文件模式不发 fetch（内嵌账即数据源，离线可用）");
const svg = ids.svg;
ok(/^0 0 [0-9.]+ [0-9.]+$/.test(svg.attrs.viewBox || ""), "viewBox 为正数视口：" + svg.attrs.viewBox);
ok(svg.attrs.preserveAspectRatio === "xMidYMid meet", "等比缩放一屏见全（业主圆点档定版）");
// MECH-BOARD-UI-1 新契约：左列卡列表在页、画布 1:1 显式宽高（圆点直径=屏幕像素，缩略看不见退场）
ok(html.indexOf('id="side"') >= 0, "左列 DAG 卡历史列表容器在页（业主「左列表右图」令）");
ok(/^0 0 [0-9]+ [0-9]+$/.test(svg.attrs.viewBox || "") && Number(svg.attrs.width) > 0 && Number(svg.attrs.height) > 0,
   "画布 1:1 像素档：viewBox 与显式宽高同源（节点放大不靠缩放拟合）");
const gs = svg.children.filter(e => e.tag === "g");
const cnt = (root, t, cls) => root.children.reduce((acc, e) => acc + (function w(x) {
  return (x.tag === t && (x.attrs.class || "").indexOf(cls) === 0 ? 1 : 0) + x.children.reduce((s, y) => s + w(y), 0);
})(e), 0);
ok(gs.length === 5, "5 节点组");
ok(cnt(svg, "circle", "dot") === 5, "5 个圆点=卡数（节点不漏不重）");
ok(cnt(svg, "circle", "hit") === 5, "5 个大命中圈（点得着才是可操作图）");
ok(cnt(svg, "circle", "pulse") === 1, "1 个呼吸光环=CLAIMED 数");
ok(cnt(svg, "path", "edge") === 3, "3 条边（坏依赖名 NOT-EXIST 被滤，账外依赖不上图）");
ok(cnt(svg, "path", "edge cp") === 1, "关键路径边高亮恰 1 条（A→B）");
ok((ids.hd.innerHTML || "").indexOf("gen G-TEST") >= 0, "页眉带 gen 戳");
ok((ids.hd.innerHTML || "").indexOf("CLAIMED 1") >= 0, "图例合并计数（counts+色点一行）");
ok((ids.hd.innerHTML || "").indexOf("ready V2-B") >= 0, "ready 徽章在位");
const gA = gs.find(g => g.children.some(c => c.tag === "text" && c.textContent === "V2-A"));
ok(!!gA, "V2-A 节点可寻（卡号标签）");
const hitA = gA && gA.children.find(c => c.tag === "circle" && c.attrs.class === "hit");
ok(!!(hitA && hitA.handlers.click), "命中圈挂了 click 处理器");
hitA.handlers.click();
const dt = ids.dt.innerHTML || "";
ok(dt.indexOf("&lt;/script&gt;") >= 0, "详情里对抗标题已转义（&lt;/script&gt;）");
ok(dt.indexOf("<img") < 0, "详情无裸 <img 注入面");
ok(dt.indexOf("V2-B") >= 0 && dt.indexOf("下游") >= 0, "详情含「被谁依赖（下游）」且列出 V2-B");
ok(dt.indexOf("时间账") >= 0 && dt.indexOf("创建") >= 0, "详情带时间账（业主令：开始/完成/耗时上看板）");

// ---- 4) 壳模式（看板 /dag 热路：无内嵌账 → 必须现读 /api/dag） ----
ok(code.indexOf("fetch('/api/dag'") >= 0, "壳模式引导在位（现读真账=唯一热路数据源）");
const shell = html.replace(mj[0], "");
ok(shell !== html && shell.indexOf('id="ledger"') < 0, "壳=剥离内嵌数据块后无 ledger 段");
asyncErr = null;
const L = run(code, false);
ok(L.box.calls.length === 1 && L.box.calls[0][0] === "/api/dag", "壳恰好一次 fetch('/api/dag')");
ok(!!(L.box.calls[0][1] && L.box.calls[0][1].cache === "no-store"), "壳数据请求自带 cache:no-store");
await tick();
ok(!asyncErr, "壳模式异步链无异常");
ok(L.ids.svg.children.filter(e => e.tag === "g").length === 5, "壳模式同图 5 节点（两条渲染路共用同一个 main=零分叉）");
ok((L.ids.hd.innerHTML || "").indexOf("gen G-TEST") >= 0, "壳模式页眉现读到真账 gen");

// ---- 5) 断链可见性+自愈（MECH-RTY1）：fetch 炸了必须喊，且必须装 15s 重试表；成功路不许装表 ----
const B = run(code, false, () => Promise.reject(new Error("账不可读")));
await tick();
ok(String(B.ids.hd.textContent).indexOf("读账失败") >= 0, "数据链断→页眉明报「读账失败」（静默白屏=业主再当一次探测器，禁止）");
ok(B.box.ivArmed === 1, "断链后 15s 自愈重试已装表（RTY1 修复在位：壳页失败不自躺）");
ok(L.box.ivArmed === 0, "成功路不装重试表（不做周期重画=点选高亮/详情栏不被闪断，壳页语义=打开即现读快照）");

// ---- 6) 布局回归锁：VIS2 修的 calc 魔法数不许复活 ----
ok(html.indexOf("calc(100vh - 45px)") < 0, "布局魔法数 calc(100vh-45px) 已根除（页眉换行不再裁底）");
ok(html.indexOf("min-height:0") >= 0, "flex 撑高布局在位");

// ---- 7) 防红自证：删掉 detail 定义 → 点击必须炸。不炸说明这套断言是空枪 ----
const brk = code.replace("function detail(cid,g)", "function detail_redprobe_removed(cid,g)");
if (brk === code) { fails++; console.error("FAIL: 红自证替换未命中（渲染器函数名漂移，锁需同步）"); }
else {
  let threw = false;
  try {
    const r = run(brk, true);
    const g2 = r.ids.svg.children.filter(e => e.tag === "g")
      .find(g => g.children.some(c => c.tag === "text" && c.textContent === "V2-A"));
    g2.children.find(c => c.tag === "circle" && c.attrs.class === "hit").handlers.click();
  } catch (e) { threw = true; }
  ok(threw, "红自证：撤掉修复渲染器会红（本锁非空检查）");
}

console.error("panel_dom_check: asserts=" + n + " fails=" + fails);
if (fails) { console.log("PANEL_DOM_FAIL"); process.exit(1); }
console.log("PANEL_DOM_PASS n=" + n);
