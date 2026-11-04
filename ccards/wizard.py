# ccards.wizard — cc 交互向导（REQ-CC-WIZARD-1，业主 2026-11-04 18:4x 原话 + 19:0x 拍板全采纳）。
# 六步全链：①欢迎+外接终端 agent 探测并明示 ②自然语言提需求 ③方案书四栏（模块拆解/
# 每模块功能/如何测试/后面怎么用；装了 agent 交 `claude -p` 会话生成，没装降级内置结构化
# 模板手填，两形态同 schema）④清单可改可确认，确认=批量立卡（每模块一卡，卡面 verify=
# 方案书「如何测试」栏原文，三段式/判据闸原样走 ledger create，不豁免不代填）⑤开工后
# 持续播报（工位/领卡/交卷/验收绿/打回红/合入主干；数据源=现账 events.log+progress.log
# 偏移轮询，秒级）⑥首次进入先在一次性临时沙箱把全链跑给用户看，同意才绑真实仓库。
#
# 产品入口层纪律：只复用既有派单/回收/合流环与账本读写，不新增常驻环/报警/登记面
# （机制冻结令不适用=零新增控制面）；播报复用 watch/idle_pulse 同款「文件+tail 只读」口径。
# 写账一律走**子进程 CC_TEAM_HOME**（oss_cli.cmd_init 账本闭环冒烟同形制）：沙箱演示
# 绝不可能回流正账，测试据此全沙箱非交互驱动（tests/cc-wizard-test.py）。
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗根除纪律同 commands（子进程不落可见 conhost）

# ── 方案书 schema（两形态同构=立卡与验收只认这一份形状；改键名=差分对必红）──
# 四栏=业主拍板的规格（模块拆解/每模块功能/如何测试/后面怎么用）；
# files=可选第五件（交付产物路径）：不是第五栏，是 red-first 卡的 R2 申报通道——
# 引擎 create 必试跑判据（REQ-VERIFY-DRYRUN-1），「产物还没落盘」的判据红必须
# 靠卡面 files 点名缺失路径才放行，向导据此替用户申报，不豁免任何闸。
PLAN_KEYS = ("requirement", "source", "modules")
MODULE_KEYS = ("name", "functions", "test", "usage")
MODULE_OPT_KEYS = ("files",)
CARD_ID_BASE = "REQ-WIZ"

STAGE1 = "[步骤1/6]"
STAGE2 = "[步骤2/6]"
STAGE3 = "[步骤3/6]"
STAGE4 = "[步骤4/6]"
STAGE5 = "[步骤5/6]"
STAGE6 = "[步骤6/6]"


def validate_plan(plan):
    """方案书结构校验：两形态共用；不合格 raise ValueError（宁红不猜，不静默修形）。"""
    if not isinstance(plan, dict):
        raise ValueError("方案书必须是 JSON 对象")
    for k in PLAN_KEYS:
        if k not in plan:
            raise ValueError("方案书缺键: " + k)
    if plan["source"] not in ("agent", "template"):
        raise ValueError("方案书 source 必须是 agent/template，收到 %r" % plan["source"])
    if not isinstance(plan["modules"], list) or not plan["modules"]:
        raise ValueError("方案书没有模块（modules 空）")
    for i, m in enumerate(plan["modules"], 1):
        if not isinstance(m, dict):
            raise ValueError("模块 %d 不是对象" % i)
        for k in MODULE_KEYS:
            if k not in m:
                raise ValueError("模块 %d 缺键: %s" % (i, k))
        if not isinstance(m["functions"], list):
            raise ValueError("模块 %d 的 functions 必须是列表" % i)
        if "files" in m and not isinstance(m["files"], list):
            raise ValueError("模块 %d 的 files（可选交付产物）必须是列表" % i)
    return True


def _norm_module(raw):
    fns = raw.get("functions") or []
    if isinstance(fns, str):
        fns = [x for x in re.split(r"[;；]", fns) if x.strip()]
    mods = {"name": str(raw.get("name") or "").strip(),
            "functions": [str(x).strip() for x in fns if str(x).strip()],
            "test": str(raw.get("test") or "").strip(),
            "usage": str(raw.get("usage") or "").strip()}
    files = raw.get("files") or []
    if isinstance(files, str):
        files = [x for x in re.split(r"[,;；]", files) if x.strip()]
    if files:
        mods["files"] = [str(x).strip() for x in files if str(x).strip()]
    return mods


# ── ①欢迎+agent 探测（复用 oss_cli._cc_status：只查 PATH+--version，不联网不安装）──
def detect_agent():
    from .oss_cli import _cc_status
    return _cc_status()


def welcome(out=print, detect=None):
    exe, ver = (detect or detect_agent)()
    if exe:
        out("%s 外接终端 agent（Claude Code）已就绪：%s——方案书交它生成。" % (STAGE1, ver or exe))
    else:
        out("%s 未检测到外接终端 agent（Claude Code 不在 PATH）——方案书走内置结构化模板手填；"
            "引擎账本不依赖 agent，只影响方案书由谁起草。" % STAGE1)
    return exe, ver


# ── ③方案书生成 ──
AGENT_PROMPT_TMPL = (
    "你是方案架构师。用户要建设的东西（原话）：\n{req}\n\n"
    "请只输出一个 JSON 对象（不要解释、不要代码围栏），schema：\n"
    '{{"modules":[{{"name":"模块名","functions":["功能1","功能2"],'
    '"test":"如何测试（可复跑的验收判据命令或口径）","usage":"后面怎么用",'
    '"files":["本模块将交付的产物路径（相对仓根，可空）"]}}]}}\n'
    "modules=模块拆解，2-6 个；每个模块的 test 必须是机器可判的验收口径；"
    "产物还没落盘的判据（red-first）必须同时给出 files 声明交付路径。")


def _agent_run(exe, prompt, timeout=180):
    """外接 agent 会话唯一执行口（claude headless -p；测试打桩此函数，真 claude 不入测试前提）。"""
    r = subprocess.run([exe, "-p", prompt], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=NO_WIN)
    if r.returncode != 0:
        raise RuntimeError("agent 会话退出码 %d：%s" % (r.returncode, (r.stderr or "").strip()[:200]))
    return r.stdout or ""


def parse_agent_plan(text, requirement):
    """从 agent 输出提取 JSON 方案书（容忍围栏/前后杂话：取最外层 { … }）；不合规=raise。"""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("agent 输出里没有 JSON 对象")
    try:
        d = json.loads(m.group(0))
    except ValueError as e:
        raise ValueError("agent 输出 JSON 解析失败: %s" % e)
    plan = {"requirement": requirement, "source": "agent",
            "modules": [_norm_module(x) for x in (d.get("modules") or [])]}
    validate_plan(plan)
    return plan


def fill_plan_template(ask, out, requirement):
    """没装 agent（或 agent 生成失败）的降级通道：内置结构化模板，用户逐项手填。
    四栏=业主要求的规格（名称/功能/如何测试/怎么用）；交付产物为可选第五问，
    供 red-first 判据走 R2 豁免（引擎 create 必试跑，缺产物判据须在卡面 files 点名才放行）。"""
    out("%s 内置结构化模板：请按提示逐项填写（这是手填降级，装了 Claude Code 后同样需求由 agent 起草）。" % STAGE3)
    n = 0
    while n < 1:
        raw = (ask("模块数量（1-6 整数）：") or "").strip()
        try:
            n = int(raw)
        except ValueError:
            n = 0
            out("  数字没听懂，重答。")
        if n > 6:
            out("  超过 6 个模块请先砍需求，重答。")
            n = 0
    mods = []
    for i in range(1, n + 1):
        name = (ask("模块 %d 名称：" % i) or "").strip()
        out("模块 %d 功能（逐行输入，空行结束）：" % i)
        fns = []
        while True:
            line = (ask("  功能行：") or "").strip()
            if not line:
                break
            fns.append(line)
        test = (ask("模块 %d 如何测试（验收判据，须可复跑）：" % i) or "").strip()
        files = (ask("模块 %d 交付产物文件（逗号分隔，可空；判据引用未落盘产物时必填=R2 豁免凭据）：" % i) or "").strip()
        usage = (ask("模块 %d 后面怎么用：" % i) or "").strip()
        m = {"name": name, "functions": fns, "test": test, "usage": usage}
        fl = [x.strip() for x in re.split(r"[,;；]", files) if x.strip()]
        if fl:
            m["files"] = fl
        mods.append(m)
    plan = {"requirement": requirement, "source": "template", "modules": mods}
    validate_plan(plan)
    return plan


def build_plan(requirement, exe, ask, out, agent_run=None):
    """两形态同一 schema 出口；agent 失败如实喊降级，绝不悄悄丢内容。"""
    if exe:
        out("%s 交给外接终端 agent 会话起草方案书（需求：%s）" % (STAGE3, requirement))
        try:
            text = (agent_run or _agent_run)(exe, AGENT_PROMPT_TMPL.format(req=requirement))
            plan = parse_agent_plan(text, requirement)
            out("%s agent 起草完成：%d 个模块。" % (STAGE3, len(plan["modules"])))
            return plan
        except Exception as e:
            out("%s [降级] 外接 agent 起草失败（%s）——改走内置结构化模板手填。" % (STAGE3, e))
    return fill_plan_template(ask, out, requirement)


# ── ④方案清单：可改可确认 ──
_FIELD_ALIAS = {"名": "name", "名称": "name", "name": "name",
                "功能": "functions", "functions": "functions",
                "测试": "test", "如何测试": "test", "test": "test",
                "用法": "usage", "后面怎么用": "usage", "usage": "usage",
                "产物": "files", "交付产物": "files", "files": "files"}


def render_plan(plan, out=print):
    out("%s 方案书清单（需求：%s｜来源：%s）：" % (STAGE4, plan["requirement"], plan["source"]))
    for i, m in enumerate(plan["modules"], 1):
        out("  [%d] 模块：%s" % (i, m["name"] or "(未命名)"))
        out("      功能：" + ("；".join(m["functions"]) or "(空)"))
        out("      如何测试：%s" % (m["test"] or "(空——确认时会被判据闸拦下)"))
        if m.get("files"):
            out("      交付产物：%s（立卡时带 --files=R2 申报域）" % "，".join(m["files"]))
        out("      后面怎么用：%s" % (m["usage"] or "(空)"))


def review_plan(plan, ask, out=print):
    """审阅环：改<编号> <栏> <新值> / 删<编号> / 确认。确认前不落一张卡。"""
    while True:
        render_plan(plan, out)
        line = (ask("可改可确认——输入「改<编号> <名|功能|测试|用法> <新值>」「删<编号>」或「确认」：") or "").strip()
        if line in ("确认", "confirm", "ok", "y"):
            return plan
        m = re.match(r"^改(\d+)\s*(\S+)\s+(.+)$", line)
        if m and int(m.group(1)) <= len(plan["modules"]):
            key = _FIELD_ALIAS.get(m.group(2))
            idx = int(m.group(1)) - 1
            if key:
                val = m.group(3).strip()
                plan["modules"][idx][key] = ([x.strip() for x in re.split(r"[;,；]", val) if x.strip()]
                                             if key in ("functions", "files") else val)
                out("  已改 [%d] %s。" % (idx + 1, key))
                continue
        m = re.match(r"^删(\d+)$", line)
        if m and 1 <= int(m.group(1)) <= len(plan["modules"]) and len(plan["modules"]) > 1:
            removed = plan["modules"].pop(int(m.group(1)) - 1)
            out("  已删模块「%s」。" % removed["name"])
            continue
        out("  没看懂这一行，帮助见上一句提示。")


# ── 账本子进程（沙箱=CC_TEAM_HOME 覆写；正账=环境缺省；永不 in-process 摸正账，同 cmd_init 形制）──
class Ledger:
    def __init__(self, team_home):
        self.team = os.path.abspath(team_home)

    def ensure(self):
        for sub in ("state", "claims"):
            os.makedirs(os.path.join(self.team, sub), exist_ok=True)

    def run(self, *argv, env_extra=None, out=None, cwd=None):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # cc-team 根
        # -I 不读 PYTHONIOENCODING（MECH-GBKDECODE-BOMB-1 坑）：回显中文前先把出口钉 utf-8
        code = ("import sys; "
                "sys.stdout.reconfigure(encoding='utf-8', errors='replace'); "
                "sys.stderr.reconfigure(encoding='utf-8', errors='replace'); "
                "sys.path.insert(0, %r); "
                "from ccards.cli import main; sys.exit(main(sys.argv[1:]))" % here)
        env = dict(os.environ, CC_TEAM_HOME=self.team)
        env.update(env_extra or {})
        # done/verdict 的 _run_verify 不另设 cwd=继承本进程——判据引用仓内相对产物时，
        # 工人侧命令必须钉 cwd=交付仓（create 试跑体自设一次性 tmp，与此无关）。
        r = subprocess.run([sys.executable, "-I", "-c", code] + [str(x) for x in argv],
                           env=env, capture_output=True, text=True, cwd=cwd,
                           encoding="utf-8", errors="replace", creationflags=NO_WIN)
        text = ((r.stdout or "") + (r.stderr or "")).strip()
        if out is not None:
            for ln in text.splitlines():
                out("    | " + ln)
        return r.returncode, text

    def cards(self):
        p = os.path.join(self.team, "state", "graph.json")
        try:
            with open(p, encoding="utf-8") as f:
                return (json.load(f) or {}).get("cards") or {}
        except (OSError, ValueError):
            return {}


def _truncate(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def create_cards(plan, ledger, repo, out=print):
    """确认=批量立卡：每模块一卡；卡面 verify=「如何测试」栏**原文**（不改写不代填）；
    模块有交付产物声明时带 --files（red-first 的 R2 申报通道=引擎既有流程，非豁免）。
    不豁免闸：空 test=没有可执行判据，整批拒确认回审阅；闸拒 create=如实喊「立卡失败」。"""
    miss = [i for i, m in enumerate(plan["modules"], 1) if not m["test"]]
    if miss:
        out("%s [确认失败] 模块 %s 缺『如何测试』=无可执行判据——三段式/判据闸不豁免，"
            "回「改<编号> 测试 <可复跑判据>」补上再确认。"
            % (STAGE4, ",".join(str(x) for x in miss)))
        return []
    used = {int(m.group(1)) for cid in ledger.cards()
            for m in [re.match(r"^%s-(\d+)$" % re.escape(CARD_ID_BASE), cid)] if m}
    n = 0
    ids = []
    req = _truncate(plan["requirement"], 200)
    total = len(plan["modules"])
    for i, mod in enumerate(plan["modules"], 1):
        while True:
            n += 1
            if n not in used:
                break
        cid = "%s-%02d" % (CARD_ID_BASE, n)
        used.add(n)
        args = [cid, "--title", _truncate(mod["name"], 80) or ("模块%d" % i), "--status", "OPEN",
                "--verify", mod["test"],
                "--src", "cc 交互向导（REQ-CC-WIZARD-1）收单；用户需求原话：%s" % req,
                "--story", "模块：%s｜功能：%s｜后面怎么用：%s｜验收（方案书「如何测试」口径）：%s"
                % (mod["name"], "；".join(mod["functions"]) or "-", mod["usage"] or "-", mod["test"]),
                "--plan", "方案书《%s》第 %d/%d 张（起草=%s）：交付模块「%s」，自测后交卷，"
                "验收=卡面 verify 机器复跑" % (req, i, total, plan["source"], mod["name"]),
                "--type", "REQ",
                "--c-op", "实现并自测模块「%s」（功能：%s）" % (mod["name"], "；".join(mod["functions"]) or "-"),
                "--c-in", "用户按『后面怎么用』使用：%s" % (mod["usage"] or "-"),
                "--c-expect", "卡面判据机器复跑退出码 0：%s" % mod["test"]]
        if repo:
            args += ["--repo", repo]
        if mod.get("files"):
            args += ["--files", ",".join(mod["files"])]
        rc, text = ledger.run(*(["create"] + args), out=out)
        if rc != 0:
            out("%s [立卡失败] 模块 %d「%s」rc=%d——闸拒就拒，不绕闸（见上方 ledger 回显）"
                % (STAGE4, i, mod["name"], rc))
            continue
        ids.append(cid)
        out("%s [立卡] %s「%s」已入账（待领，派单器接手）" % (STAGE4, cid, mod["name"]))
    return ids


# ── ⑤播报：现账 events.log + progress.log 偏移轮询（秒级）；只读，零回调零常驻 ──
def _event_broadcast(ln):
    parts = ln.split("|")
    if len(parts) < 5 or "->" not in parts[2]:
        return None
    cid, old, new, who, extra = parts[1], *parts[2].split("->", 1), parts[3], "|".join(parts[4:])
    if new == "OPEN" and old == "-":
        return "[立卡] %s 已入账待领" % cid if extra.strip() == "create" else None
    if new == "CLAIMED" and (extra.startswith("claim") or extra.startswith("start")):
        return "[开工] 工位 %s 领了 %s" % (who, cid)
    if new == "DONE":
        return "[交卷] %s 已提交，待机器复跑判据（工位 %s）" % (cid, who)
    if new == "VERIFIED":
        return "[验收绿·合入主干] %s 判据复跑通过（REQ-MERGE-GATE-1：绿章前置=交付已在主干）" % cid
    if new == "OPEN" and extra.startswith("REJECT"):
        return "[打回红] %s 验收打回返工（%s）" % (cid, _truncate(extra, 80))
    return None


def _read_from(path, pos):
    """从字节偏移读到文件尾，只吐**完整行**；返回 (lines, newpos)。追加式流水专用（tail 即观测）。"""
    try:
        with open(path, "rb") as f:
            f.seek(pos)
            data = f.read()
    except OSError:
        return [], pos
    if not data:
        return [], pos
    complete = data.rfind(b"\n") + 1
    lines = data[:complete].decode("utf-8", "replace").splitlines()
    return [ln.strip() for ln in lines if ln.strip()], pos + complete


def lanes_report(team, out=print):
    """「创建了哪些工位」=三源现读：claims 目录（在领道）+ state/lane-*.beat（心跳名册）
    + registry 名册（两位置容错）。名字=身份（禁进程号口径）。"""
    names = set()
    claims = os.path.join(team, "claims")
    try:
        for d in os.listdir(claims):
            jp = os.path.join(claims, d, "claim.json")
            try:
                with open(jp, encoding="utf-8") as f:
                    o = (json.load(f) or {}).get("owner")
                if o:
                    names.add(str(o))
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    try:
        for p in os.listdir(os.path.join(team, "state")):
            m = re.match(r"^lane-(.+)\.beat$", p)
            if m:
                names.add(m.group(1))
    except OSError:
        pass
    for rp in (os.path.join(team, "registry.json"), os.path.join(team, "state", "registry.json")):
        try:
            with open(rp, encoding="utf-8") as f:
                for proj in (json.load(f) or {}).get("projects") or []:
                    for ln_ in proj.get("lanes") or []:
                        names.add(str(ln_.get("lane") if isinstance(ln_, dict) else ln_))
        except (OSError, ValueError):
            pass
    if names:
        out("%s [工位] 在编 %d 个：%s" % (STAGE5, len(names), ", ".join(sorted(names))))
    else:
        out("%s [工位] 当前账上无在编工位——派单器拉起泳道时这里会实时点亮" % STAGE5)
    return sorted(names)


def broadcast_once(team, st, only_ids=None, out=print):
    """单拍：读 events/progress 自上次偏移以来的新行→中文播报行；st 原地推进偏移。"""
    lines = []
    ev, npos = _read_from(os.path.join(team, "state", "events.log"), st.get("ev_pos", 0))
    st["ev_pos"] = npos
    for ln in ev:
        msg = _event_broadcast(ln)
        if msg and (only_ids is None or ln.split("|")[1] in only_ids):
            lines.append(msg)
    pg, npos = _read_from(os.path.join(team, "state", "progress.log"), st.get("pg_pos", 0))
    st["pg_pos"] = npos
    for ln in pg:
        p = ln.split("|")
        if len(p) >= 4 and (only_ids is None or p[1] in only_ids):
            lines.append("[进度] %s：%s" % (p[1], _truncate(p[3], 120)))
    for msg in lines:
        out("%s %s" % (STAGE5, msg))
    return lines


def broadcast_loop(team, only_ids=None, interval=1.0, ticks=0, st=None, out=print):
    """持续播报：interval 秒一拍（缺省无限，Ctrl-C 收；ticks>0=有限拍，测试/演示用）。"""
    st = st if st is not None else {"ev_pos": 0, "pg_pos": 0}
    lanes_report(team, out)
    broadcast_once(team, st, only_ids, out)
    n = 0
    try:
        while ticks <= 0 or n < ticks:
            time.sleep(interval)
            broadcast_once(team, st, only_ids, out)
            n += 1
    except KeyboardInterrupt:
        out("%s 播报退出（账本与引擎环照常运转，重新 `cc` 可续播）" % STAGE5)
    return st


# ── 全链核心（步骤2-5）：真实流与沙箱演示共用同一函数，差异只在注入的 team_home/ask/out ──
def run_chain(ledger, repo, ask, out=print, detect=None, requirement=None, agent_run=None):
    """②提需求→③方案书→④确认可改→立卡。返回 (plan, ids)；需求空/判据缺=不放行。"""
    exe, _ver = (detect or detect_agent)()
    if requirement is None:
        requirement = (ask("%s 请用自然语言说你要建什么（例：我要建设一个企业内部智能体）：" % STAGE2) or "").strip()
    if not requirement:
        out("%s 需求为空——没有东西可拆解，不收单、不立卡。" % STAGE2)
        return None, []
    out("%s 收到需求：%s" % (STAGE2, _truncate(requirement, 120)))  # 回显收单（播报面要有这一环）
    plan = build_plan(requirement, exe, ask, out, agent_run=agent_run)
    plan = review_plan(plan, ask, out)
    ids = create_cards(plan, ledger, repo, out)
    return plan, ids


# ── ⑥沙箱演示：一次性临时账+临时仓，把「领卡→交卷→绿→红不放行→打回」跑给用户看 ──
class QueueAsk:
    """脚本喂输入的 ask（演示与测试的非交互驱动；耗尽=EOFError，绝不读真键盘）。"""
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError("脚本输入已用尽（停在提示：%s）" % prompt)
        return self.answers.pop(0)


def _git(repo, *args):
    r = subprocess.run(["git", "-c", "user.name=cc-wizard-demo", "-c", "user.email=demo@cc.local"]
                       + list(args), cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NO_WIN)
    if r.returncode != 0:
        raise RuntimeError("git %s 失败：%s" % (" ".join(args), (r.stderr or r.stdout).strip()[:200]))
    return (r.stdout or "").strip()


def _stage_commit(repo, fname, content):
    with open(os.path.join(repo, fname), "w", encoding="utf-8") as f:
        f.write(content)
    _git(repo, "add", fname)
    _git(repo, "commit", "-m", "demo worker: %s" % fname)
    return _git(repo, "rev-parse", "HEAD")


def run_demo(out=print):
    """全链沙箱演示（README「三分钟现场」的可感版）：临时 team + 临时 git 仓，
    立卡 3 张后模拟工人：#1 交卷判据绿→验收绿；#2 判据红→不放行留待验；#3 验收后被抽查打回→返工重交转绿；
    终局后把三张泳道交付物组成的「文档问答」小系统真起进程现场跑一遍（业主 23:3x：要有建成的系统演示）。
    演示永远走手填模板（不依赖也不计费外接 agent）；结束销毁沙箱，正账零接触。"""
    tmp = tempfile.mkdtemp(prefix="cc-wizard-demo-")
    team = os.path.join(tmp, "team")
    repo = os.path.join(tmp, "repo")
    os.makedirs(team)
    os.makedirs(repo)
    try:
        if not shutil.which("git"):
            out("[演示跳过] 本机无 git——沙箱演示的交卷环节跑不了（真实流程立卡不受影响）。")
            return {"skipped": "no-git"}
        ledger = Ledger(team)
        ledger.ensure()
        _git(repo, "init")
        _stage_commit(repo, "README.md", "底仓种子\n")
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        # 名册现读件：演示「工位」写进沙箱 registry（slot-1/slot-2=池位示意）
        with open(os.path.join(team, "state", "registry.json"), "w", encoding="utf-8") as f:
            json.dump({"projects": [{"name": "cc-wizard-demo", "repo": repo, "lanes": [
                {"lane": "slot-1", "host": "win", "launcher": "run-cc.ps1"},
                {"lane": "slot-2", "host": "win", "launcher": "run-cc.ps1"}]}]},
                f, ensure_ascii=False)
        out("—— 沙箱演示开始（临时账 %s；你的仓库零改动）——" % team)
        # 判据形=仓内相对产物 grep（red-first：立卡时产物未落盘=判据点名缺失文件，
        # 卡面 --files 申报=R2 豁免在册，同 README 三分钟现场口径；闸一个不绕）
        # 业主 23:3x「还要有创建出来的系统演示」：三张卡的交付物=一个真能跑的
        # 「文档问答」小系统（入库/检索/入口），链尾真起进程跑主干代码问答——
        # 判据仍 ASCII marker（grep 中文参数=双侧 OS 编码坑，见 11-02 血泪注）。
        ask = QueueAsk([
            "我要建设一个企业内部智能体",       # ②需求（业主原话示例）
            "3",                                # 模块数
            "文档入库与索引", "上传的文档入库、供检索", "",
            "grep -q INDEX-OK demo_docs.py", "demo_docs.py",
            "提问时系统自动引用库内文档",
            "检索问答与出处", "关键词检索库内文档，答复必须带出处", "",
            "grep -q ANSWER-OK demo_answer.py", "demo_answer.py",
            "答案尾部就是出处文件名",
            "命令行入口与端到端", "一条命令提问，串联入库与回答", "",
            "grep -q E2E-OK demo_app.py", "demo_app.py",
            "python demo_app.py \"你的问题\" 即得带出处的答复",
            "确认",                             # ④审阅=确认批量立卡
        ])
        plan, ids = run_chain(ledger, repo, ask, out=out,
                              detect=lambda: (None, None))
        if len(ids) != 3:
            out("[演示异常] 立卡数=%d（期望 3）——链上环节断了，见上方 ledger 回显" % len(ids))
            return {"error": "create=%d" % len(ids), "ids": ids}
        st = {"ev_pos": 0, "pg_pos": 0}
        lanes_report(team, out)  # 先亮工位盘子（真实形态=派单器按需求现开泳道，此处名册示意）
        # 工人模拟（真实通道：claim/progress/done/verdict 全走 ledger CLI，闸一样不少）
        def worker(lane, cid):
            # claim 缺省=自动发新班（沙箱道无旧任期台账）；epoch 从输出回读，done/progress 对班
            rc, text = ledger.run("claim", cid, "--owner", lane, out=out)
            if rc != 0:
                out("[演示异常] claim %s rc=%d——后续播报缺环节" % (cid, rc))
                return None
            mo = re.search(r"epoch=(\d+)", text)
            ep = mo.group(1) if mo else "1"
            ledger.run("progress", cid, "%s 在写码（演示拍）" % lane, "--lane", lane,
                       "--epoch", ep, "--ring", "1", out=out)
            broadcast_once(team, st, None, out)
            return ep
        def ship(lane, cid, ep, commit, evidence):
            rc, _t = ledger.run("done", cid, "--owner", lane, "--repo", repo,
                                "--commit", commit, "--evidence", evidence,
                                "--epoch", ep, env_extra={"CC_AUTO_VERIFY": "1"},
                                out=out, cwd=repo)  # 判据含仓内相对路径→复跑窗钉在交付仓
            broadcast_once(team, st, None, out)
            return rc
        # ── 小系统三件交付物（泳道各自 commit 落盘，链尾真跑=代码全部出自合入主干的提交）──
        _DOCS = ('# INDEX-OK  演示知识库：两份企业文档内嵌（沙箱演示数据，非业务账）\n'
                 'DOCS = {\n'
                 '    "returns.txt": "退货期限为 30 天，保留小票即可到服务台办理。",\n'
                 '    "warranty.txt": "整机保修 12 个月，电池等耗材保修 6 个月。",\n'
                 '}\n')
        _ANSWER_WIP = ('# 半成品：检索可用但未按卡面要求内嵌自检标记——判据就是判据，不口头达标\n'
                       'import re\n'
                       '\n'
                       'from demo_docs import DOCS\n'
                       '\n'
                       '\n'
                       'def answer(question):\n'
                       '    q = re.sub(r"[？?。！!吗的了呢啊]", " ", question)\n'
                       '    toks = set()\n'
                       '    for seg in q.split():\n'
                       '        toks.add(seg)\n'
                       '        for i in range(len(seg) - 1):\n'
                       '            toks.add(seg[i:i + 2])\n'
                       '    for src, text in DOCS.items():\n'
                       '        for kw in sorted(toks, key=len, reverse=True):\n'
                       '            if kw and kw in text:\n'
                       '                return text, src\n'
                       '    return "库内暂无匹配文档，建议补充后再问。", "-"\n')
        _APP_V1 = ('# E2E-OK  命令行入口：提问→从库内文档找答复（演示 v1）\n'
                   'import sys\n'
                   '\n'
                   'from demo_answer import answer\n'
                   '\n'
                   'if __name__ == "__main__":\n'
                   '    q = " ".join(sys.argv[1:]) or "退货"\n'
                   '    text, src = answer(q)\n'
                   '    print("答复：%s（出处：%s）" % (text, src))\n')
        _APP_V2 = ('# E2E-OK  返工：答复与出处按输出规范分行，并标注口径版本 v2\n'
                   'import sys\n'
                   '\n'
                   'from demo_answer import answer\n'
                   '\n'
                   'if __name__ == "__main__":\n'
                   '    q = " ".join(sys.argv[1:]) or "退货"\n'
                   '    text, src = answer(q)\n'
                   '    print("答复：%s" % text)\n'
                   '    print("出处：%s" % src)\n')
        e1 = worker("slot-1", ids[0])
        c1 = _stage_commit(repo, "demo_docs.py", _DOCS)
        ship("slot-1", ids[0], e1, c1, "demo_docs.py")
        e2 = worker("slot-2", ids[1])
        c2 = _stage_commit(repo, "demo_answer.py", _ANSWER_WIP)
        ship("slot-2", ids[1], e2, c2, "demo_answer.py")
        red_status = ledger.cards().get(ids[1], {}).get("status")
        if red_status != "DONE":
            out("[演示异常] 判据红的卡竟放行了（status=%s）——不放行闸失效" % red_status)
        else:
            out("[判据红] %s 卡面 verify 复跑不过——不放行：卡停在『待验(DONE)』，"
                "不盖『已验收(VERIFIED)』；返工补绿或 PM 处置才收口" % ids[1])
        e3 = worker("slot-1", ids[2])
        c3 = _stage_commit(repo, "demo_app.py", _APP_V1)
        ship("slot-1", ids[2], e3, c3, "demo_app.py")
        ledger.run("verdict", ids[2], "REJECT",
                   "--reason", "演示：验收后抽查发现答复与出处未按规范分行（口径偏差），打回返工",
                   out=out, cwd=repo)
        broadcast_once(team, st, None, out)
        # 打回返工闭环（业主 2026-11-04 23:0x 直令「演示要有始有终」）：返工重交，验收章
        # 仍由机器复跑判据落——不是谁口头说改好了就算数。
        e3b = worker("slot-1", ids[2])
        if e3b is None:
            out("[演示异常] 返工重领失败——闭环缺环节")
            return {"error": "rework-claim", "ids": ids}
        c3b = _stage_commit(repo, "demo_app.py", _APP_V2)
        ship("slot-1", ids[2], e3b, c3b, "demo_app.py")
        out("%s [返工完成] %s 打回后返工重交——绿章=机器复跑判据通过，见上拍播报" % (STAGE5, ids[2]))
        statuses = {cid: ledger.cards().get(cid, {}).get("status") for cid in ids}
        out("[演示收] 三张卡终局：%s（判据红不放行=留待验；打回返工=重交转绿；全程闸都在）"
            % "，".join("%s=%s" % (k, v) for k, v in sorted(statuses.items())))
        # 建成系统现场跑一遍（业主 23:3x「还要有创建出来的系统演示」+次日头一轮反馈「演示呢？」
        # ——加厚：连问两题，两题都答得出=系统是真跑的不是摆的；答复与出处=泳道写进主干的代码）
        out("—— 建成的系统现场跑一遍（真起进程跑主干代码，连问两题）——")
        bad = 0
        for q in ("退货的期限是几天？", "电池保修多久？"):
            app_rc, app_lines = -1, []
            try:
                pr = subprocess.run([sys.executable, "demo_app.py", q],
                                    cwd=repo, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=60,
                                    creationflags=NO_WIN)
                app_rc = pr.returncode
                app_lines = [ln.strip() for ln in (pr.stdout or "").splitlines() if ln.strip()]
            except Exception as e:
                out("[演示异常] 系统演示起进程失败：%r" % (e,))
            out("[系统演示] 问：" + q)
            for ln in app_lines:
                out("[系统演示] " + ln)
            if app_rc != 0 or not app_lines:
                bad += 1
                out("[演示异常] 系统演示退出码=%s——建不起来的东西不配叫闭环" % app_rc)
        if bad:
            return {"error": "app-demo", "ids": ids, "statuses": statuses}
        out("—— 沙箱演示结束（临时账销毁；主干分支=%s）——" % branch)
        return {"skipped": None, "ids": ids, "statuses": statuses, "transcript_team": team}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 向导主入口（`cc` 无子命令=run_wizard）──
def _default_team_home():
    env = os.environ.get("CC_TEAM_HOME")
    if env:
        return os.path.abspath(env)
    cand = os.path.join(os.getcwd(), "cc-team")
    if os.path.isdir(os.path.join(cand, "state")) or os.path.isdir(cand):
        return os.path.abspath(cand)
    from . import config
    return os.path.abspath(config.TEAM)


def _derive_repo(team):
    root = os.path.dirname(team)
    return root if os.path.isdir(os.path.join(root, ".git")) else ""


def run_wizard(team_home=None, repo=None, ask=None, out=None, detect=None,
               broadcast_ticks=0, broadcast_interval=1.0, agent_run=None):
    """交互向导主流程。测试注入 ask(QueueAsk)/out(收集器)/team_home(沙箱)/broadcast_ticks(有限拍)
    即可全非交互驱动；真人使用全缺省=input/print 无限播报 Ctrl-C 收。"""
    ask = ask or (lambda p: input(p))
    out = out or (lambda s="": print(s))
    team = os.path.abspath(team_home or _default_team_home())
    ledger = Ledger(team)
    ledger.ensure()
    out("%s 欢迎用 cc 向导（需求→方案书→确认→立卡→播报）。账本=%s" % (STAGE1, team))
    exe, ver = welcome(out=out, detect=detect)
    marker = os.path.join(team, "state", "wizard-demo.seen")
    if not os.path.isfile(marker):
        out("%s 首次进入：先在一次性临时沙箱把全链跑给你看（真实仓库与正账零接触）。" % STAGE6)
        run_demo(out=out)
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write("demo seen\n")
        except OSError:
            pass
        try:
            ans = (ask("演示完毕。要在你的真实仓库开工吗？(y/n)：") or "").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            out("%s 未绑仓——你的仓库零改动。下次 `cc` 直接进正式流程。" % STAGE6)
            return 0
    repo2 = repo if repo is not None else _derive_repo(team)
    try:
        plan, ids = run_chain(ledger, repo2, ask, out=out, detect=detect, agent_run=agent_run)
    except EOFError:
        out("%s 输入流结束——未确认的方案不立卡，你的账本零改动。" % STAGE5)
        return 1
    if not ids:
        out("%s 未立卡（需求空/判据缺/闸拒）——无账可播。" % STAGE5)
        return 1
    out("%s 开工播报已挂：秒级轮询现账 state/events.log+progress.log（Ctrl-C 退播，环照常跑）。" % STAGE5)
    try:
        broadcast_loop(team, only_ids=set(ids), interval=broadcast_interval,
                       ticks=broadcast_ticks, out=out)
    except KeyboardInterrupt:
        pass
    out("%s 播报结束；卡已入账，后续=派单/回收/合流环自动接力（看板 `python -I cards_dag.py panel`）。" % STAGE5)
    return 0
