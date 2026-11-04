# ccards.config — 路径锚与通用工具。
# ⚠️ 路径纪律：TEAM 默认=包所在目录的上一级（cc-team 根）；env CC_TEAM_HOME 可覆盖（沙箱/多项目）。
# ⚠️ REQ-OSSPATH-1（2026-11-02）：宿主绝对路径单一配置入口=本文件末尾 CC_HOME/CC_PROJ/CC_RUNNER 三件
#   （env 可覆写；缺省全部由仓库位置**运行时探测**）。ccards/ops 源码禁用户目录字面量。
import json, os, re, subprocess, time
from datetime import datetime
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

TEAM = os.environ.get("CC_TEAM_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAIMS = os.path.join(TEAM, "claims")
STATE = os.path.join(TEAM, "state")
ARCHIVE = os.path.join(STATE, "archive")    # 归档账（MECH-PURE1 自 tasks/archive 迁来，reconcile 历史侧写）
GRAPH = os.path.join(STATE, "graph.json")   # MECH-ENG2：唯一事实（cards 全字段+视图字段+seal 自封）
EVENTS = os.path.join(STATE, "events.log")
PROGRESS = os.path.join(STATE, "progress.log")  # MECH-LOG1：旁证流（worker 报进度，不进账）
REJECTS = os.path.join(STATE, "rejects.log")     # MECH-REJT-1：分类器拒绝史（业主令 2026-11-01「否则黑盒」；append-only）
WLOCK = os.path.join(STATE, ".writer.lock")
# 补50（业主令 2026-11-01 22:0x「记账要串行，不是执行要串行」）：verdict **移出写锁集合**——
# 旧钉法把写锁横跨 1~2 分钟的 verify 套件复跑，期间全队 done/note/create 排队撞 45s 锁超时，
# 串行记账是对的、把执行也串行是机制自毒。cmd_verdict 内自行「锁外跑套件、进锁重载落账」。
# REQ-BEAT-ANCHOR-1 名牌制新增两笔：epoch=发牌入账；progress=带(道,任期,圈号)的续写事件
# （圈号单调性检查与落账必须在写锁内，旧「旁证流不进 WRITERS」仅指不带三件的通道——行为不变）。
WRITERS = {"create", "set", "claim", "start", "bind", "done", "release",
           "cancel", "push", "thaw", "dag", "reindex", "duty", "epoch", "progress",
           "purge"}  # purge=seq=11 出账通道（先归档后销账，锁内不可并发）
FINISHED = ("DONE", "VERIFIED")
# MECH-LIT1：REJECTED 已删——全库无任何命令能写入它（set 明确拒、verdict REJECT 回 OPEN），
# 属死枚举残留；状态机真值= DRAFT/OPEN/CLAIMED/HOLD/DONE/VERIFIED/CANCELED 七个。
STATUS_ENUM = ["DRAFT", "OPEN", "CLAIMED", "HOLD", "DONE", "VERIFIED", "CANCELED"]
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SELFHEAL_MIN = 5  # 孤儿锁自愈阈值(分钟)：防把并发中的活锁当孤儿抢走

# ==== MECH-RESIDENT-1（业主 seq=8）常驻岗卡子系统 · 数据层 ====
# 同一 schema 两族卡：普通卡走上面七态机（STATUS_ENUM 不动，seq=4 边界第5条）；
# 岗卡（卡面 resident=True）的**在岗状态**走独立三态，与七态正交——七态管这张卡
# 的建卡/验收生命周期，三态管这个岗现在活不活：
#   VERIFIED=验收合格上岗（verdict PASS 自动置 ON-DUTY）；长期停岗用 OFF，
#   **不复用七态收口态**（CANCELED 等），杜绝「停岗=作废卡」的语义绑架。
# seq=4 矩阵并轨同源：七态矩阵真值在 MECH-STATE-STD-1；常驻维度真值=本文件
# RESIDENT_DUTY_SEM/EDGES——代码闸同源读这一份，防任意态互窜（岗卡族已随 seq=10/11 退役出账，
# 登记件见 docs/系统保障清单.md；卡册史档在 docs/归档/）。
RESIDENT_DUTY = ["ON-DUTY", "DEGRADED", "OFF"]
RESIDENT_DUTY_SEM = {  # 每态钉死「属主+出口」（与 seq=4 矩阵同构）
    "ON-DUTY":  {"owner": "互盯岗（reconcile/recycle 侧）",
                 "exit": "心跳判据不过→DEGRADED；撤岗→OFF"},
    "DEGRADED": {"owner": "PM",
                 "exit": "锚件人工恢复且心跳复测过→ON-DUTY（只报警不自愈）；撤岗→OFF"},
    "OFF":      {"owner": "PM",
                 "exit": "verdict PASS 上岗或 duty 复岗（须 VERIFIED 卡）→ON-DUTY；停岗态不判劣化，OFF→DEGRADED 非法"},
}
RESIDENT_DUTY_EDGES = {  # 合法迁移边全集（cmd_duty 唯一闸门依据）
    ("OFF", "ON-DUTY"), ("ON-DUTY", "DEGRADED"), ("DEGRADED", "ON-DUTY"),
    ("ON-DUTY", "OFF"), ("DEGRADED", "OFF"),
}
# 岗卡必填四件（seq=8 第3条）：字段→CLI flag→语义
RESIDENT_REQUIRED = {
    "duty":      ("--duty",      "岗责：扫什么/多久"),
    "anchor":    ("--anchor",    "实现锚：crontab 行 | automation jobId | 脚本绝对路径"),
    "heartbeat": ("--heartbeat", "心跳判据：可执行命令（退出码 0=心跳活）"),
    "fail":      ("--fail",      "失败语义：DEGRADED 时怎么报警（报警不自愈，须写明）"),
}


def is_resident(c):
    """岗卡判定；旧卡无 resident 字段=普通卡，七态行为一切照旧。"""
    return bool((c or {}).get("resident"))


def now():
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

def nowz():
    # 卡面 claimed_at 定版格式：ISO8601 带冒号偏移(+08:00)，py3.7+ fromisoformat 直接可解析；
    # events/claims 的 ts 维持旧 now() 格式(+0800)不动，审计流水格式不破坏。
    return datetime.now().astimezone().isoformat(timespec="seconds")

_K32 = None  # kernel32 惰性单例（MECH-PIDALIVE-WIN-1，仅 Windows 侧建）

def _win_pid_alive(pid):
    """Windows 判活=显式 Win32 三分，不用 os.kill(pid,0) 当存在性探针。
    实探定谳（CPython3.10/Win11，2026-11-03 MECH-PIDALIVE-WIN-1）：
      查无此进程 → OSError(winerror=87=ERROR_INVALID_PARAMETER)——旧实现被下面保守分支
        整口吞掉=确死恒活 → .writer.lock 死锚自清/夺锁、claim 自愈、僵尸道锁接管全失效；
      受保护进程(如 pid4) → SystemError("returned a result with an exception set"，
        2026-10-30 claim 自愈实锤崩栈)——进程确在，保守判活；
      退了未回收的僵尸 → os.kill 不抛恒回活——句柄表对象还在，os.kill 语义根本测不出死；
      pid>DWORD → OverflowError 裸崩（判活链永不许崩，下面兜底）。
    本函数改走 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)+GetExitCodeProcess：
      句柄开到 → 退出码==259(STILL_ACTIVE)=活，!=259=已退（僵尸也能确死）；
      开不到 → 87/2=查无此进程判死，5=ACCESS_DENIED(进程在、句柄开不到)判活，
      未知错误码保守活（宁少收一把该回收的锁，绝不误抢活会话的锁；5min 龄兜底仍在）。
    已知坑与 psutil 同款：合法拿 259 当退出码的进程会误判活——由 5min 自清兜底，不堵。"""
    global _K32
    if pid > 0xFFFFFFFF:      # Win32 pid 是 DWORD，超界=不可能存在的锚（同 pid<=0 档判死）
        return False
    try:
        import ctypes
        if _K32 is None:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            k.OpenProcess.restype = ctypes.c_void_p
            k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            k.GetExitCodeProcess.restype = ctypes.c_int
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            _K32 = k
        k = _K32
        h = k.OpenProcess(0x1000, 0, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            err = ctypes.get_last_error()
            if err in (2, 87):              # ERROR_FILE_NOT_FOUND / INVALID_PARAMETER=查无此进程
                return False
            # 5=ERROR_ACCESS_DENIED 进程确在句柄开不到=活；其余未知错误码保守活
            return True
        try:
            code = ctypes.c_ulong(0)
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return True                 # 读不到退出码=不确定，保守活
            return code.value == 259        # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        # ctypes/入口本身异常（极老 CPython/非 Win 混跑）：保守判活，绝不炸判活链。
        # 禁回退 os.kill(pid,0)（BUG-FUSE-SELFKILL-1 追加定谳 2026-11-04 01:38:36 实锤：
        # Windows sig=0 恰是 CTRL_C_EVENT 值，os.kill 把它**广播给进程组**=探针变处决，
        # 控制台子 0xC000013A 即退、无控制台 pythonw 也收组级 CTRL+C，还回抛
        # SystemError「returned a result with an exception set」=既杀活人又漏异常）。
        return True

def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _win_pid_alive(pid)   # MECH-PIDALIVE-WIN-1：Win 侧 os.kill 测不出死，见上
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError, RuntimeError):
        # POSIX 侧未知一律保守按活：宁少收一把该回收的锁，绝不误抢活会话的锁
        #（回收侧还有静默时长兜底）。
        return True

def die(msg):
    print("ERROR: " + msg, file=__import__("sys").stderr)
    __import__("sys").exit(1)

def split(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else []

def claim_dir(cid):
    return os.path.join(CLAIMS, cid)

def _session_from_env():
    return os.environ.get("CLAW_SESSION_ID") or "-"

def _resolve_repo(repo):
    # MECH-V2：跨OS互解——原路径存在=原样优先；否则 C:\ / C:/ ↔ /mnt/c 双向等价互解；
    # 两边都不存在=原样返回，让 git 报用户传入的原错。
    if not repo or os.path.isdir(repo):
        return repo
    m = re.match(r"^([A-Za-z]):([\\/])(.*)$", repo)
    if m:
        alt = "/mnt/%s/%s" % (m.group(1).lower(), m.group(3).replace("\\", "/"))
        return alt if os.path.isdir(alt) else repo
    m = re.match(r"^/mnt/([A-Za-z])/(.*)$", repo)
    if m:
        alt = "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\"))
        return alt if os.path.isdir(alt) else repo
    return repo

def git(repo, *args):
    # core.quotepath=false（MECH-STD1 真实派发演练实锤）：默认输出把中文路径转义成
    # "docs/\346\234\272..."，与卡面 UTF-8 files 域 fnmatch 永不同→越域闸**假拒**合法勾卡。
    # 桩②bytes+显式 UTF-8 解码（同场实锤）：text=True 在 Windows 按 locale(cp936) 解码，
    # git 输出的中文提交信息炸 reader 线程→stdout=None→勾卡 AttributeError；WSL(UTF-8) 测不到。
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", _resolve_repo(repo), *args],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WIN)
    out = r.stdout.decode("utf-8", "replace") if r.stdout is not None else ""
    return r.returncode, out.strip()

def read_json(p, default=None):
    # MECH-UX1 读侧自愈：并发写者 os.replace 之后的一瞬间，/mnt/c 9P/DrvFs 可呈负缓存 ENOENT、
    # 或 mkdir 锁原子序被打断的瞬窗（实测 all.sh 三路并行 ~1/6 轮闪现「账空」假象→
    # #47 丢更新/#63 ccops 缺项两条红同源）。ENOENT 类 IO 错误重试三次×40ms 再落 default；
    # ValueError（真腐坏）**不重试**——坏了就如实红，不掩。
    for i in (0, 1, 2):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            if i < 2:
                time.sleep(0.04)
                continue
            return default
        except (OSError, ValueError):
            return default

def atomic_write_json(p, obj):
    # MECH-LEDGER-CRLF-1：newline="" 钉死落盘换行——Windows 文本模式默认把 "\n" 译成
    # "\r\n"，graph/dag/epoch/beat 等台账 JSON 全被污染（行尾锚定断言链被 \r 击穿）。
    # 写侧不转换=LF 跨 OS 确定；读侧维持通用换行翻译，旧 CRLF 账照常可读，不需清污染。
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    t = p + ".tmp"
    with open(t, "w", encoding="utf-8", newline="") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(t, p)


def lane_book():
    """岗位册道名白名单（业主 2026-11-03 五点整改第 1 条：道名校验只立在校验器）。
    唯一权威=state/registry.json（lane_registry build 生成的在册道）∪ "ANY"（池通配）。
    **不并 host/launcher 册**——t2~t5 在册生成器明文「本卡未收口前不入白名单」（DL 违规
    待收口），并进来等于校验器破名册自己的规矩；launcher 册只管「道能不能起」，不管
    「卡该不该落」。册缺/空=返回空集，调用方**跳过校验**（各测试房沙箱通道；正账恒有
    册，缺册归 reconcile 名册对账环看护）。晚绑 TEAM（函数内查模块全局）：自测可覆。"""
    book = set()
    d = read_json(os.path.join(TEAM, "state", "registry.json")) or {}
    for ln in (d.get("lanes") or []):
        if isinstance(ln, dict) and str(ln.get("lane") or "").strip():
            book.add(str(ln["lane"]).strip())
        elif isinstance(ln, str) and ln.strip():
            book.add(ln.strip())
    if not book:
        return set()
    book.add("ANY")
    return book


# ---------- REQ-OSSPATH-1 单一配置入口（env 覆写 → 缺省运行时探测，禁用户目录字面量） ----------
# REQ-OSS-ENGINE-1 增三层合一（业主 seq=20/21 定版「一切定制项走配置、配置必有默认值、
# 默认值=现网行为」）：每颗旋钮取值序 = 专用 env 锚 > 文件层 config.toml > 内置默认。
# 文件层发现序：env CC_CONFIG 指哪读哪 > CC_TEAM 根 config.toml > 无文件=纯默认（现网零改动照跑）。
# py<3.11 无 tomllib：内置零依赖 TOML 子集解析（注释/[table]/key=value，str/int/float/bool/字符串数组），
# 语法超出子集=该键忽略并 stderr 点名（静默吞=缺陷，禁）。

def _toml_scalar(s):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner, items, buf, q = s[1:-1], [], "", None
        for ch in inner:
            if q:
                buf += ch
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch; buf += ch
            elif ch == ",":
                if buf.strip():
                    items.append(_toml_scalar(buf))
                buf = ""
            else:
                buf += ch
        if buf.strip():
            items.append(_toml_scalar(buf))
        return [str(x) if not isinstance(x, (int, float, bool)) else x for x in items]
    if s[:1] in "\"'" and s[-1:] == s[:1] and len(s) >= 2:
        return s[1:-1]
    if s == "true":
        return True
    if s == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_toml_subset(text):
    """扁平点键 dict：段 [x] 内键记 "x.k"；顶层键记 "k"。返回 {str: str|int|float|bool|list}。"""
    out, sect = {}, ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[\s*([A-Za-z0-9_.-]+)\s*\]\s*(?:#.*)?$", line)
        if m:
            sect = m.group(1).strip(".")
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*(?:#.*)?$", line)
        if not m:
            print("WARN: config.toml 行不可解析(该键忽略): %r" % raw, file=__import__("sys").stderr)
            continue
        k = m.group(1)
        out[(sect + "." + k) if sect else k] = _toml_scalar(m.group(2))
    return out


_FILECFG = None

def file_config():
    global _FILECFG
    if _FILECFG is None:
        p = os.environ.get("CC_CONFIG") or os.path.join(TEAM, "config.toml")
        try:
            with open(p, encoding="utf-8") as f:
                _FILECFG = parse_toml_subset(f.read())
        except FileNotFoundError:
            _FILECFG = {}
        except OSError as e:
            print("WARN: 文件层配置读失败按无文件处理 %r" % (e,), file=__import__("sys").stderr)
            _FILECFG = {}
    return _FILECFG

def cfg_reset():
    """自测/沙箱换 CC_CONFIG 后重读用（模块缓存仅在进程内有意义）。"""
    global _FILECFG
    _FILECFG = None

def setting(key, env=None, default=None):
    """单颗旋钮：env 锚 > config.toml > 内置默认。空串视同未设（与现网 env 锚语义一致）。"""
    if env:
        v = os.environ.get(env)
        if v not in (None, ""):
            return v
    v = file_config().get(key)
    if v not in (None, ""):
        return v if isinstance(v, (int, float, bool, list)) else str(v)
    return default

def num_setting(key, env=None, default=0, minimum=None):
    raw = setting(key, env, None)
    try:
        n = int(str(raw).strip()) if raw is not None else default
    except ValueError:
        try:
            n = float(str(raw).strip()) if raw is not None else default
        except ValueError:
            n = default
    if minimum is not None:
        try:
            n = max(minimum, n)
        except TypeError:
            n = default
    return n


def to_win(p):
    # WSL 视角 /mnt/<d>/x → Windows 视角 <D>:\x；非 /mnt 形态原样返回（本机原生形即合法）。
    m = re.match(r"^/mnt/([A-Za-z])/(.*)$", str(p).replace("\\", "/"))
    return "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\")) if m else p


def _detect_user(path):
    # 用户主目录前缀=从仓绝对路径反推：/mnt/<d>/[...]Users/<u>/... 或 <D>:\...Users\<u>\...
    # 皆截到 Users/<u> 为止；探测不到=回落本进程 HOME（expanduser，运行时值非字面量）。
    m = re.match(r"^(.*?[\\/][Uu]sers[\\/][^\\/]+)", path)
    return m.group(1) if m else os.path.expanduser("~")


def _detect_win_root():
    # WSL 盘挂载根 /mnt/<d>：①TEAM 本身在盘挂载下=取该盘；②posix 且 /mnt 里有带
    # Windows/System32 的盘符目录=取首个（等价旧「isdir /mnt/c」探针，探测制无字面量）；
    # ③本机 Windows 侧=None（消费方各自回落 PATH 解析/空候选表）。
    m = re.match(r"^(/mnt/[A-Za-z])(/|$)", TEAM.replace("\\", "/"))
    if m:
        return m.group(1)
    if os.name == "posix":
        try:
            for d in sorted(os.listdir("/mnt")):
                p = "/mnt/" + d
                if re.fullmatch(r"/mnt/[A-Za-z]", p) and \
                        os.path.isdir(os.path.join(p, "Windows", "System32")):
                    return p
        except OSError:
            pass
    return None


CC_HOME = setting("host.fleet_root", "CC_HOME", os.path.dirname(os.path.dirname(TEAM)))  # 车队根：wf-<lane> 工作树与 .cc/ 所在
CC_USER = setting("host.user_home", "CC_USER", _detect_user(TEAM))          # 宿主用户主目录（双侧形态随仓）
CC_PROJ = setting("host.sessions_dir", "CC_PROJ", os.path.join(CC_USER, ".claude", "projects"))  # 会话转录目录
# BUG-RUNNER-GONE-1（2026-11-04 实锤）：旧默认锚 CC_USER/.openclaw/tmp/cc-msg/run-cc.ps1 是仓外单点——
# OpenClaw 原生切流把 tmp 整树归档后起会话核失踪，dispatch/inbox/fuse 三路拉起全部静默空转近 6h。
# 唯一正本改随仓走（bin/run-cc.ps1）；换宿主/远程道仍用 engine.runner_cmd / CC_RUNNER 覆写，不改码。
CC_RUNNER = setting("engine.runner_cmd", "CC_RUNNER", to_win(os.path.join(TEAM, "bin", "run-cc.ps1")))
CC_WIN_ROOT = setting("host.win_root", "CC_WIN_ROOT", _detect_win_root())   # WSL 盘根；None=本机非 WSL

# ---- REQ-OSS-ENGINE-1 派生锚（每颗默认=现网行为；文件层可覆写，见 config.toml 模板） ----
CC_CODE = setting("fleet.code_root", "CC_CODE", CC_HOME)   # 车队码根（wf-<lane> 所在=dispatch DISPATCH_CODE_ROOT 缺省同源）
CC_CC_STATE = os.path.join(CC_CODE, ".cc", "state")        # 车队级 .cc 状态目录（心跳/道锁所在）
CC_DISP_HB = setting("dispatch.heartbeat_path", "DISPATCH_HB",
                     os.path.join(CC_CC_STATE, "dispatcher.heartbeat"))
# 起会话 powershell 解释器：Windows 侧随 PATH；WSL 侧随探测到的盘根派生（=旧 /mnt 形态同值）。
CC_PS = setting("engine.powershell", "DISPATCH_PS", (
    "powershell.exe" if os.name == "nt" else
    ((CC_WIN_ROOT + "/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
     if CC_WIN_ROOT else "powershell.exe")))
# 宿主 Python 安装位（ctrld 任务形 PATH 仿真与 merge-train 解释器候选用；随用户主目录派生，非字面量）。
CC_PY310_DIR = setting("host.py310_dir", "CC_PY310_DIR",
                      to_win(os.path.join(CC_USER, "AppData", "Local", "Programs", "Python", "Python310")))
CC_PY312_EXE = setting("host.py312_exe", "CC_PY312_EXE",
                      to_win(os.path.join(CC_USER, "AppData", "Local", "Programs", "Python", "Python312",
                                          "python.exe")))
# Windows 计划任务名（业主收编定版：OS 侧仅剩启动器；换名走配置不改码）。
CC_TASK_CTRLD = setting("tasks.ctrld", "CTRLD_TASK_NAME", "cc-ctrld")
CC_TASK_BOOT = setting("tasks.boot", "BOOT_TASK_NAME", "cc-boot-bringup")
# 平台 API 入口（REQ-NIGHT-LEARN-1 夜环上报消费）。REQ-OSS-ENGINE-1 二轮收口：内网部署地址
# 属定制项，禁入发布集任何文件（oss-release-check A 内网段形）——现网值只落账本根 config.toml
# [platform] api_base（该文件在发布白名单之外）；内置默认=本机开发形态占位；env NIGHT_LEARN_BASE 可覆写。
CC_PLATFORM_BASE = setting("platform.api_base", "NIGHT_LEARN_BASE", "http://127.0.0.1:30090")


def lane_lock_path(lane):
    """车队道锁绝对形（ps1/看门侧 Windows 形；inbox_watchdog LANE_LOCK_WIN 等消费）。"""
    return to_win(os.path.join(CC_CC_STATE, "lane-%s.lock" % lane))


def code_lane_wd(lane, code_root=None):
    """wf-<lane> 工作树的 Windows 形绝对路径（起会话 ps1 侧 cwd；dispatch _win_wd 消费）。
    DISPATCH_CODE_ROOT 锚在场随锚（与 proj_dir_for 同纪律），生产缺省=CC_CODE。"""
    cr = code_root or os.environ.get("DISPATCH_CODE_ROOT") or CC_CODE
    return to_win(os.path.join(cr, "wf-%s" % lane))


def proj_dir_name(base, lane):
    # claude 会话转录目录名编码=绝对路径非字母数字全换 '-'（与 CC 自家 slug 同式）：
    # 例 <用户根>/code/wf-m3（win 形 C:\...\code\wf-m3）→ "C--Users-<u>-code-wf-m3"。
    return re.sub(r"[^A-Za-z0-9]", "-", to_win(os.path.join(base, "wf-%s" % lane)))


def proj_dir_for(lane, code_root=None, proj_root=None):
    """某道的会话转录目录绝对路径（dispatch._proj_dir / recycle.transcript_age_min /
    lane_registry 前缀三处消费同源；DISPATCH_CODE_ROOT 测试锚在场=随锚，与旧硬前缀
    的生产值逐字相等：encode(<车队码根>\\wf-<lane>)）。"""
    cr = code_root or os.environ.get("DISPATCH_CODE_ROOT") or CC_CODE
    pr = proj_root or CC_PROJ
    return os.path.join(pr, proj_dir_name(cr, lane))


# 配置快照：cc init 自检与 oss-release-check C 判据的共同数据源（派生值全览，禁再嵌字面量）。
def config_snapshot():
    return {
        "TEAM": TEAM,
        "host.fleet_root": CC_HOME,
        "host.user_home": CC_USER,
        "host.sessions_dir": CC_PROJ,
        "engine.runner_cmd": CC_RUNNER,
        "host.win_root": CC_WIN_ROOT,
        "fleet.code_root": CC_CODE,
        "dispatch.heartbeat_path": CC_DISP_HB,
        "engine.powershell": CC_PS,
        "host.py310_dir": CC_PY310_DIR,
        "host.py312_exe": CC_PY312_EXE,
        "tasks.ctrld": CC_TASK_CTRLD,
        "tasks.boot": CC_TASK_BOOT,
        "platform.api_base": CC_PLATFORM_BASE,
    }
