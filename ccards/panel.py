# ccards.panel — DAG 可视化面板（MECH-VIS1，业主需求「每次生成的发布用 DAG 图展示，点节点看详细记录」）。
# 定版：零新数据源、零服务端、零依赖——面板是 graph.json 的**派生视图**（自包含单文件 HTML，
# 内嵌事实+events/progress 流水，前端 JS 画 SVG），随每个写命令收尾自动重生成（STD1 物化视图同款）；
# 渲染失败只 WARN 永不拦写账（旁证层纪律）。视图非事实：手改 panel.html=下次写覆盖，别编辑它。
import json, os
from .config import EVENTS, GRAPH, PROGRESS, STATE, now

# 状态配色（色盲友好偏冷色系；虚线=冻结、红描边=环/卡死）
COLORS = {"DRAFT": "#9ca3af", "OPEN": "#3b82f6", "CLAIMED": "#f59e0b", "DONE": "#8b5cf6",
          "VERIFIED": "#10b981", "HOLD": "#64748b", "CANCELED": "#ef4444"}
EV_CAP, PR_CAP, PER_CARD_CAP = 800, 400, 60  # 流水嵌入上限（面板是便览不是仓库）


def _layers(cards):
    """最长路径分层（Kahn 依赖深度）；环成员不再下钻（防递归爆栈），由视图 cycle 字段负责点名。"""
    memo = {}

    def d(cid, stack):
        if cid in memo:
            return memo[cid]
        if cid in stack:
            return 0
        deps = [x for x in cards.get(cid, {}).get("deps", []) if x in cards]
        v = 1 + max([d(x, stack | {cid}) for x in deps], default=-1)
        memo[cid] = v
        return v

    return {cid: d(cid, set()) for cid in cards}


def _tail_lines(path, cap):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.strip() for ln in f if ln.strip()][-cap:]
    except OSError:
        return []


def build_data(doc=None):
    """面板内嵌数据：图（视图字段+全字段卡）+ 该卡 events + 该卡 progress + 布局坐标。"""
    if doc is None:
        try:
            with open(GRAPH, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
    cards = doc.get("cards") or {}
    evs, prs = {}, {}
    for ln in _tail_lines(EVENTS, EV_CAP):
        p = ln.split("|")
        if len(p) > 2 and p[1]:
            evs.setdefault(p[1], []).append(ln)
    for ln in _tail_lines(PROGRESS, PR_CAP):
        p = ln.split("|")
        if len(p) > 1 and p[1]:
            prs.setdefault(p[1], []).append(ln)
    for m in (evs, prs):
        for k in m:
            m[k] = m[k][-PER_CARD_CAP:]
    lay = _layers(cards)
    cols = {}
    for cid in sorted(cards):
        cols.setdefault(lay[cid], []).append(cid)
    nodes = {}
    for depth in sorted(cols):        # x/y=圆心（MECH-VIS1 圆点档 + viewBox 缩放到全幅=一屏见全图）
        for i, cid in enumerate(cols[depth]):
            nodes[cid] = {"x": 48 + depth * 124, "y": 40 + i * 52,
                          "status": cards[cid].get("status", "?"),
                          "title": cards[cid].get("title", "")}
    cyc = set(doc.get("cycle") or [])
    cp = doc.get("critical_path") or []
    return {"generated": now(), "gen": doc.get("generation"), "seal": doc.get("seal"),
            "digest": (doc.get("digest") or "")[:16], "counts": doc.get("counts"),
            "ready": doc.get("ready"), "cycle": sorted(cyc),
            "canceled_blocked": doc.get("canceled_blocked"), "critical_path": cp,
            "remaining": sorted(cid for cid, c in cards.items()
                                if c.get("status") not in ("DONE", "VERIFIED", "CANCELED")),
            "nodes": nodes, "cards": cards, "events": evs, "progress": prs,
            "colors": COLORS}


HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>cc-team DAG 面板</title><style>
:root{--ink:#1f2a3d;--mut:#5b6b82;--line:#dbe4f0;--bg:#f4f7fc;--blue:#2f6fed;--vio:#6d5bd0}
*{box-sizing:border-box}
body{margin:0;height:100vh;display:flex;flex-direction:column;
 font:13px/1.55 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:var(--ink);
 background:radial-gradient(#e0e8f4 1px,transparent 1.6px) 0 0/24px 24px,var(--bg)}
/* MECH-VIS2 布局定版：body 纵向 flex、main 吃剩余高度。**不再用 calc(100vh-45px) 魔法数**
   ——头栏带徽章会换行，一旦两行(约66px)内容就被顶出视口裁底(业主报的图操作难受之一)。 */
header{position:sticky;top:0;z-index:5;display:flex;gap:12px;flex-wrap:wrap;align-items:center;padding:9px 16px;
 background:rgba(255,255,255,.9);backdrop-filter:blur(5px);border-bottom:1px solid var(--line);font-size:12.5px}
header b{font-size:15px;background:linear-gradient(90deg,var(--blue),var(--vio));-webkit-background-clip:text;background-clip:text;color:transparent}
.meta{color:var(--mut)}
.badge{padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;border:1px solid}
main{flex:1;min-height:0;display:flex}
/* MECH-BOARD-UI-1（业主 2026-11-01 三条原话之二）：左列=DAG 卡历史列表（点击才在右边出图）；
   画布改 1:1 像素+滚动（svg 不再被 viewBox 等比缩没——「节点圆太小看不见」的病根=缩放拟合，
   不是圆本身）。全图档直径 28px 起步，聚焦链路档 52px。 */
nav#side{width:264px;flex:none;border-right:1px solid var(--line);background:#fff;overflow:auto;padding:8px;font-size:12px}
nav#side .sh{padding:4px 8px 8px;color:var(--mut)}
nav#side button{margin-top:4px;padding:4px 10px;border:1px solid var(--line);border-radius:8px;background:#eef4fd;cursor:pointer;font-size:12px}
.side-row{display:flex;gap:6px;align-items:center;padding:6px 8px;border-radius:8px;cursor:pointer;border:1px solid transparent}
.side-row:hover{background:#eef4fd}
.side-row i{width:10px;height:10px;border-radius:99px;flex:none;border:1px solid #fff;box-shadow:0 0 0 1px #d3ddec}
.side-row code{font-size:11px;flex:none}
.side-row span.tt{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mut)}
.side-row span.ts{color:#93a1b5;font-size:10.5px;flex:none}
#wrap{flex:1;overflow:auto} svg{display:block}
aside{width:400px;border-left:1px solid var(--line);background:#fff;box-shadow:-8px 0 24px rgba(30,50,90,.06);overflow:auto;padding:14px;font-size:13px}
.edge{stroke:#b8c5d8;stroke-width:1.3;fill:none}
.edge.cp{stroke:var(--blue);stroke-width:2.2;filter:drop-shadow(0 0 3px rgba(47,111,237,.45))}
.node circle.dot{stroke:#fff;stroke-width:2.2;filter:drop-shadow(0 1px 2.5px rgba(30,50,90,.28));cursor:pointer}
.node:hover circle.dot{stroke:var(--blue);stroke-width:2.8} .node.sel circle.dot{stroke:var(--blue);stroke-width:2.8}
.node .hit{fill:transparent;cursor:pointer}
.node .lab{pointer-events:none;fill:var(--ink);font-size:11.5px;font-weight:600;text-anchor:middle}
.node:hover .lab,.node.sel .lab{fill:var(--blue)}
.pulse{fill:none;stroke:#f59e0b;stroke-width:2;animation:p 1.6s ease-out infinite;transform-box:fill-box;transform-origin:center}
@keyframes p{0%{transform:scale(1);opacity:.85}100%{transform:scale(2);opacity:0}}
.legend{display:flex;gap:10px;font-size:11px;color:var(--mut);align-items:center}
.legend i{width:9px;height:9px;border-radius:99px;display:inline-block;margin-right:3px;border:1px solid #fff;box-shadow:0 0 0 1px #d3ddec}
h3{margin:2px 0 8px;font-size:15px} dl{margin:0} dt{color:#8a97ab;margin-top:9px;font-size:10.5px;letter-spacing:.6px;text-transform:uppercase}
dd{margin:2px 0;word-break:break-all}
pre{background:#f2f6fc;border:1px solid var(--line);border-radius:8px;padding:8px;font-size:11px;white-space:pre-wrap;max-height:200px;overflow:auto}
.warn{color:#c2410c;font-weight:600} code{background:#eef2f9;padding:1px 5px;border-radius:5px;font-size:11.5px}
footer{position:fixed;right:420px;bottom:6px;font-size:10.5px;color:#93a1b5}
</style></head><body>
<header id="hd"></header><main><nav id="side"></nav><div id="wrap"><svg id="svg"></svg></div><aside id="dt">点击左侧卡片列表或图上圆点，查看该卡详细记录（悬停看标题）</aside></main>
__LEDGER__
<script>
function main(D){
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function badge(t,c){return '<span class="badge" style="background:'+c+'14;color:'+c+';border-color:'+c+'44">'+t+'</span>'}
const hd=document.getElementById('hd');
hd.innerHTML='<b>cc-team DAG</b><span class="meta">gen '+esc(D.gen)+' · seal '+esc((D.seal||'').slice(0,10))+' · '+esc(D.generated)+'</span>'
 +(D.remaining.length===0?badge('run_complete ✓','#059669'):'<span class="meta">'+D.remaining.length+' 张未收口: '+D.remaining.slice(0,8).map(esc).join(',')+(D.remaining.length>8?'…':'')+'</span>')
 +((D.ready||[]).length?badge('ready '+D.ready.map(esc).join(','),'#3b82f6'):'')
 +(D.cycle.length?badge('环! '+D.cycle.map(esc).join(','),'#ef4444'):'')
 +(D.canceled_blocked.length?badge('CANCELED 卡死 '+D.canceled_blocked.map(esc).join(','),'#ef4444'):'')
 // MECH-VIS2：counts 徽章与彩色图例原本各占一行(32 卡时页眉必换行)——合并为「色点+状态+计数」一条，
 // 空心档(未激活)图例同描边规则，一行看全账势。
 +'<span class="legend">'+Object.entries(D.colors).map(([k,v])=>{
   const hollow=(k==='HOLD'||k==='DRAFT'||k==='CANCELED');const n=(D.counts&&D.counts[k])||0;
   return '<span><i style="background:'+(hollow?'#fff':v)+';box-shadow:0 0 0 1.5px '+v+'"></i>'+k+' '+n+'</span>';
 }).join('')+'</span>';
const svg=document.getElementById('svg'),NS='http://www.w3.org/2000/svg';
const el=(t,at)=>{const e=document.createElementNS(NS,t);for(const k in at)e.setAttribute(k,at[k]);return e};
const cyc=new Set(D.cycle);
// MECH-BOARD-UI-1：邻接在 JS 侧从卡账现算（账外坏依赖名照旧滤除——panel_dom_check 锁事实）。
const par={},kids={};
for(const[cid,c]of Object.entries(D.cards)){par[cid]=[];for(const d of(c.deps||[]))if(D.cards[d]){par[cid].push(d);(kids[d]=kids[d]||[]).push(cid);}}
function depOf(id,memo,guard){if(id in memo)return memo[id];if(guard.has(id))return 0;guard.add(id);
 const ps=par[id]||[];let v=1+(ps.length?Math.max.apply(null,ps.map(p=>depOf(p,memo,guard))):-1);guard.delete(id);memo[id]=v;return v;}
function draw(idsSet,r){
 const memo={},ids=Array.from(idsSet).sort(),lay={},cols={};
 ids.forEach(i=>{lay[i]=depOf(i,memo,new Set());});
 ids.forEach(i=>{(cols[lay[i]]=cols[lay[i]]||[]).push(i);});
 const pos={};let maxX=0,maxY=0;
 Object.keys(cols).map(Number).sort((a,b)=>a-b).forEach(dep=>{cols[dep].forEach((i,n)=>{
   pos[i]={x:70+dep*(2*r+72),y:64+n*(2*r+40)};maxX=Math.max(maxX,pos[i].x);maxY=Math.max(maxY,pos[i].y);});});
 const W=maxX+2*r+90,Hh=maxY+2*r+70;
 svg.setAttribute('viewBox','0 0 '+W+' '+Hh);svg.setAttribute('preserveAspectRatio','xMidYMid meet');
 svg.setAttribute('width',String(W));svg.setAttribute('height',String(Hh));   // 1:1 像素档：圆多大屏幕上就多大
 while(svg.firstChild)svg.removeChild(svg.firstChild);
 const defs=el('defs',{}),mk=el('marker',{id:'ar',viewBox:'0 0 8 8',refX:'6.5',refY:'4',markerWidth:'6',markerHeight:'6',orient:'auto'});
 mk.appendChild(el('path',{d:'M0,0 L8,4 L0,8 z',fill:'#9fb0c4'}));defs.appendChild(mk);svg.appendChild(defs);
 const inS=new Set(ids),cp=new Set();
 for(let i=1;i<(D.critical_path||[]).length;i++){const a=D.critical_path[i-1],b=D.critical_path[i];if(inS.has(a)&&inS.has(b))cp.add(a+'>'+b);}
 for(const cid of ids)for(const d of(par[cid]||[])){
  const a=pos[d],b=pos[cid];if(!a||!b)continue;
  const an=Math.atan2(b.y-a.y,b.x-a.x),sx=a.x+Math.cos(an)*(r+2),sy=a.y+Math.sin(an)*(r+2),ex=b.x-Math.cos(an)*(r+4),ey=b.y-Math.sin(an)*(r+4),mx=(sx+ex)/2;   // 贝塞尔边：两端缩到圆周，箭头顺切线落在圆上
  svg.appendChild(el('path',{class:'edge'+(cp.has(d+'>'+cid)?' cp':''),d:'M'+sx+' '+sy+' C'+mx+' '+sy+','+mx+' '+ey+','+ex+' '+ey,'marker-end':'url(#ar)'}));}
 for(const cid of ids){const st0=String(D.cards[cid].status||'?');
  const g=el('g',{class:'node'});const col=D.colors[st0]||'#64748b';
  if(st0==='CLAIMED')g.appendChild(el('circle',{class:'pulse',cx:pos[cid].x,cy:pos[cid].y,r:r}));   // 在制卡呼吸光环
  const hollow=(st0==='HOLD'||st0==='DRAFT'||st0==='CANCELED');                                       // 未激活态=空心，一眼分层
  const c=el('circle',{class:'dot',cx:pos[cid].x,cy:pos[cid].y,r:r,fill:hollow?'#fff':col});
  if(hollow){c.setAttribute('stroke',col);c.setAttribute('stroke-width','2.2')}
  if(cyc.has(cid)){c.setAttribute('stroke','#dc2626');c.setAttribute('stroke-width','3')}
  const lab=el('text',{class:'lab',x:pos[cid].x,y:pos[cid].y+r+15});lab.textContent=cid.length>14?cid.slice(0,13)+'…':cid;
  const tt=el('title',{});tt.textContent=cid+' ['+st0+'] '+(D.cards[cid].title||'');
  const hit=el('circle',{class:'hit',cx:pos[cid].x,cy:pos[cid].y,r:r+8});
  hit.addEventListener('click',()=>detail(cid,g));
  g.append(c,lab,hit,tt);svg.appendChild(g);}
}
draw(new Set(Object.keys(D.cards)),14);   // 默认全图=直径 28px 起（业主「圆太小看不见」的最低档）
function chainOf(cid){const up=new Set([cid]),down=new Set();let q=[cid];
 while(q.length){const x=q.pop();for(const p of(par[x]||[]))if(!up.has(p)){up.add(p);q.push(p);}}
 q=[cid];while(q.length){const x=q.pop();for(const k of(kids[x]||[]))if(!down.has(k)){down.add(k);q.push(k);}}
 return new Set(Array.from(up).concat(Array.from(down)));}
// 左列=DAG 卡历史列表（新→旧）：点一行，右画布只画该卡的上游+下游链路（直径 52px），不再默认铺全账
const side=document.getElementById('side');
if(side){const list=Object.keys(D.cards).map(k=>D.cards[k]);
 list.sort((a,b)=>String(b.updated_at||b.created_at||'').localeCompare(String(a.updated_at||a.created_at||'')));
 side.innerHTML='<div class="sh"><b>DAG 卡列表</b>（'+list.length+' 张 · 新→旧）<br>'
  +'<button id="dag-all">← 返回全图</button></div>'
  +list.map(c=>'<div class="side-row" data-cid="'+esc(c.id)+'" title="'+esc(c.id+' ['+(c.status||'?')+'] '+(c.title||''))+'">'
   +'<i style="background:'+(D.colors[String(c.status||'').toUpperCase()]||'#64748b')+'"></i>'
   +'<code>'+esc(c.id)+'</code><span class="tt">'+esc(c.title||'')+'</span>'
   +'<span class="ts">'+esc(String(c.updated_at||c.created_at||'').slice(5,16).replace('T',' '))+'</span></div>').join('');
 side.addEventListener('click',e=>{const t=e.target.closest?e.target.closest('.side-row'):null;
  if(t){const cid=t.getAttribute('data-cid');draw(chainOf(cid),26);detail(cid);return;}
  if(e.target&&e.target.id==='dag-all')draw(new Set(Object.keys(D.cards)),14);});}
function rows(cid){const cs=D.cards[cid];let h='<h3>'+esc(cid)+' '+badge(cs.status,D.colors[cs.status])+'</h3><dl>';
 h+='<dt>标题</dt><dd>'+esc(cs.title)+'</dd>';
 h+='<dt>owner / lanes</dt><dd>'+esc(cs.owner||'-')+' / '+((cs.lanes||[]).join(',')||'-')+'</dd>';
 h+='<dt>deps</dt><dd>'+((cs.deps||[]).map(esc).join(' → ')||'-')+'</dd>';
 const dn=Object.keys(D.cards).filter(k=>(D.cards[k].deps||[]).indexOf(cid)>=0);  // 下游=这张卡卡住谁（点图答疑的主问题）
 h+='<dt>被谁依赖（下游 '+dn.length+'）</dt><dd>'+(dn.map(esc).join(', ')||'— 谁也不卡')+'</dd>';
 // 时间账（业主 2026-11-01「台账要有开始/完成/耗时」；MECH-STATE-STD-1 定版）：完成=卡面 done_at（done 收口必写）/ 存量兜底 verdict.at·output.done_at·updated_at
 const tm=s=>{if(!s)return null;const t=Date.parse(String(s).replace(/([+-]\d{2})(\d{2})$/,'$1:$2'));return isNaN(t)?null:t;};
 const vd=cs.verdict&&typeof cs.verdict==='object'?cs.verdict:null;
 const done=tm((vd&&vd.at)||cs.done_at||(cs.output&&cs.output.done_at)||(['DONE','VERIFIED'].indexOf(cs.status)>=0?cs.updated_at:''));
 const stt=tm(cs.first_claimed_at||cs.claimed_at||cs.created_at);
 const dur=stt&&done?Math.max(0,Math.round((done-stt)/60000)):null;
 h+='<dt>时间账</dt><dd>创建 '+esc(cs.created_at||'-')+'<br>开始 '+esc(cs.first_claimed_at||cs.claimed_at||'-')+
   '<br>完成 '+esc((vd&&vd.at)||(cs.output&&cs.output.done_at)||'-')+
   (dur!=null?'（耗时 '+(dur<60?dur+' 分':(Math.round(dur/6)/10)+' 小时')+'）':'')+
   '<br>最后更新 '+esc(cs.updated_at||'-')+'</dd>';
 if(cs.commit)h+='<dt>发布</dt><dd><code>'+esc((cs.commit||'').slice(0,40))+'</code> repo='+esc(cs.repo||'-')+'</dd>';
 if(cs.evidence)h+='<dt>evidence</dt><dd>'+esc(cs.evidence)+'</dd>';
 if(cs.verify)h+='<dt>verify（判据）</dt><dd><pre>'+esc(cs.verify)+'</pre></dd>';
 if(cs.verify_run)h+='<dt>verify_run（机器复跑痕）</dt><dd><pre>'+esc(JSON.stringify(cs.verify_run,null,1))+'</pre></dd>';
 if(cs.files&&cs.files.length)h+='<dt>files 域</dt><dd>'+cs.files.map(esc).join('<br>')+'</dd>';
 if(cs.output&&Object.keys(cs.output).length)h+='<dt>output 产物指针</dt><dd><pre>'+esc(JSON.stringify(cs.output,null,1))+'</pre></dd>';
 if(cs.verdict)h+='<dt>verdict</dt><dd>'+esc(typeof cs.verdict==='string'?cs.verdict:JSON.stringify(cs.verdict))+'</dd>';
 if(cs.description)h+='<dt>desc</dt><dd>'+esc(String(cs.description).slice(0,400))+'</dd>';
 const ev=(D.events[cid]||[]).slice().reverse(),pr=(D.progress[cid]||[]).slice().reverse();
 h+='<dt>状态流水（'+ev.length+' 条，新→旧）</dt><dd><pre>'+ (ev.map(esc).join('\\n')||'(无)')+'</pre></dd>';
 h+='<dt>进度流（'+pr.length+' 行）</dt><dd><pre>'+ (pr.map(esc).join('\\n')||'(无)')+'</pre></dd>';
 if(cyc.has(cid))h+='<p class="warn">⚠ 此卡在环(死锁)名单里，必须拆卡</p>';
 if((D.canceled_blocked||[]).includes(cid))h+='<p class="warn">⚠ 被 CANCELED 依赖卡死，永不就绪</p>';
 return h+'</dl>';}
function detail(cid,g){document.querySelectorAll('.node.sel').forEach(e=>e.classList.remove('sel'));if(g)g.classList.add('sel');
 document.getElementById('dt').innerHTML=rows(cid);}
}
// MECH-VIS3 双模引导：①文件模式=panel 命令导出的自包含快照（内嵌账，file:// 离线可看）；
// ②壳模式=看板 /dag——无内嵌数据即 fetch /api/dag **现读真账**，中间文件不在显示链路上，
// 「面板慢一拍/浏览器缓存旧页」这两类 BUG 从结构上不存在（业主定调：核心就是 DAG 的读写）。
// MECH-RTY1：壳模式失败不自躺——每 15s 自愈重试直至读到（对齐主看板 tick 纪律；
 // 成功即停表：不做周期重画，保住业主点选的节点高亮与详情栏）。
(function(){var led=document.getElementById('ledger');
 if(led){main(JSON.parse(led.textContent));return;}
 var rt=null;
 function pull(){fetch('/api/dag',{cache:'no-store'}).then(function(r){return r.json()}).then(function(D){
   if(rt){clearInterval(rt);rt=null;}
   main(D);}).catch(function(e){
   document.getElementById('hd').textContent='读账失败：'+e+'（每 15 秒自愈重试，直至读到）';
   if(!rt)rt=setInterval(pull,15000);});}
 pull();})();
</script><footer>派生视图 = graph.json 同笔自动重生成 · 判据与事实都在账侧，这里只是眼睛</footer></body></html>
"""


LEDGER_TMPL = '<script type="application/json" id="ledger">%s</script>'


def template_html():
    """壳模式页面（MECH-VIS3）：零数据的模板，浏览器自己 fetch /api/dag 现读真账。
    看板 /dag 发这个——显示链路上没有中间文件，就没有"陈旧/慢一拍"这类 BUG 的容身之处。"""
    return HTML.replace("__LEDGER__", "")


def render(doc=None, out=None):
    """文件模式：自包含快照（`panel` 命令的导出产物，离线可看/可转发）。
    返回路径。账未开（无 graph）→ 返回 None 不抛。"""
    data = build_data(doc)
    if data is None:
        return None
    p = out or os.path.join(STATE, "panel.html")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    t = p + ".tmp"
    with open(t, "w", encoding="utf-8", newline="") as f:  # MECH-LEDGER-CRLF-1 钉LF
        f.write(HTML.replace("__LEDGER__", LEDGER_TMPL % blob))
    os.replace(t, p)
    return p
