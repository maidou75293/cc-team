#!/usr/bin/env python3
# cc-team/ops/inbox_watchdog.py — PM 信箱分钟级看门（MECH-PM-WAKE-1；业主信箱 2026-11-02 seq=6 两件套①；
#   冷却语义改「按拉成回执」=BUG-PM-WAKE-RECEIPT-1，业主 2026-11-04 04:06 班次指令，ctrld.log 04:0x 空转实证）。
# 根因在案：CC 会话=回合驱动，「每轮开工先读信箱」这条纪律没有唤醒源=没装门铃。纪律文字无定时能力，
# 修复必须带外部唤醒源——本器即该唤醒源：零 token 扫描（纯 python 无模型调用；唯一 token 开销
# =拉起 PM 会话那一次，headless 按次计费）。载体=cc-ctrld inbox 环每 60s 拉起本器 --apply
#   （零 OS 定时器铁律，2026-11-03 收编，OS crontab 已注销）。
# 合并缓冲（业主 seq=17）：业主常连发多条（话说一半补充/修正），来一条就叫 PM 一班=烧 token
#   +按半成品指令开工。开门铃条件=①未 ack≥3 立即拉；②未 ack≥1 且信箱静默≥5min（env
#   INBOX_WATCH_QUIET_MIN 可调，静默=距文件 mtime 最后写入）→拉，末条不饿死；③都不满足
#   =WAIT-BATCH 凑批。
# 防风暴（2026-11-04 改版：从「按拉起时间」改为「按拉成回执」——业主 04:06 令）：
#   旧形只记「拉过」不核「拉成」：被拉的 PM 会话死于 openclaw update 重启、信箱没清，看门在
#   30min 满窗冷却里持续 SKIP-COOLDOWN 空转（现网 04:04~04:09 连拍实证），近半小时不再重试。
#   新形四件：
#   ① 拉起落锚=记 (sig, unacked 计数 n) 快照；后续圈对快照核回执——签名变化 或 n 下降 = 拉成确认
#     （WAKE-RECEIPT），清锚、计数归零、重开闸按新节奏；
#   ② 同签名且无下降=上一拉作废（会话疑即死），短背棍窗口后重试：默认 5min（env
#     INBOX_WATCH_RETRY_MIN，非法值回落 5），不再傻等 30 分钟满窗；背窗内动作=SKIP-RETRY；
#   ③ 同签名连续 3 次拉起均无回执进展=WAKE-STALL：升 ALARM 进 ccards.alerts 同账（键=恒定文案、
#     零时距/漂移数字，防打地鼠；alerts.json 聚合 count/ack 闭环现成）+state 钉住停自动拉起，
#     等信箱签名变化或人工处置（CLAUDE.md 第七节）自动恢复；
#   ④ 防双开=lane-pmmail.lock 在场不叠拉；**残锁清理同族判据**（业主 04:06③，族=recycle sweeplock
#     ops/recycle.py:832）：判据①锁内可读锚 pid 且 pid_alive=False→残锁清后放行（LOCK-REAP 立拆）；
#     判据②锁内无任何 pid 证据→30min 病残窗才拆（收紧说明：sweeplock 的 2min 窗只适用于「锁主
#     自己毫秒内写 meta」的场景，run-cc.ps1 锁内形态仓内无凭据、且 PM 一班正常可跑 10~30min，
#     照抄 2min=误拆活锁造双开，比本 BUG 更糟——故无证据一律从严、有死锚才快拆）；
#     其余（锚活/新鲜/读不出/探测异常）保守判在飞，绝不误拆活锁。判活全仓归口 ccards.config.pid_alive
#     （BUG-FUSE-SELFKILL-1：Windows os.kill(pid,0)=CTRL_C 广播处决，禁用）。
#   ⑤ 普查补闸（BUG-INBOX-DOUBLEPULL-1，业主 11-04 20:3x「CC 并没有闲置就释放资源，还有十几个」
#     实锤）：run-cc.ps1 的 pmmail 锁无活 pid 证据，30min 病残窗到点必清 →「上一班进程还活着、
#     锁已没了」→ 下圈照拉，现网 19:14/19:45/20:15 三连叠班。锁只能证「有过班」，进程普查才证
#     「现在有班」——lane_inflight 放行后再过 session_census()（认 CCLANE=pmmail- 进程形，族=
#     recycle._ghost_session_pids），真会话在场=照拦不叠拉；普查不可查(None)=维持旧形放行不饿死
#     信箱。≥2 叠班只点名不代杀（清旧归回收岗）。测试缝 INBOX_WATCH_SESSION_CMD 仅沙箱设。
# 行为面：数 OWNER-INBOX.md 标题行（^## seq=）不含 [acked] 的条数 N；N=0 静默复位（清锚，
#   下封来信即刻可拉）；N>0 经既有 run-cc.ps1（起会话核路径=config.CC_RUNNER 单入口，宿主随配置，
#   与 ops/dispatch.py 同源复用、不改它）拉起 PM 会话，prompt=「你是ccPM，执行一轮正式开工：
#   按 CLAUDE.md 第七节处理全部未acked条目」。
# 参数面：--apply 真判定真拉起；--dry 只打读数（判据面，不读不写 state 不拉起）；缺省=dry（保守）；
#   --inbox/--state/--lock <路径> 覆盖（沙箱注入专用，不动真账）；
#   测试缝仅沙箱设、生产不设：INBOX_WATCH_SIM_LAUNCH=1（LAUNCH 落锚但不起 powershell）、
#   INBOX_WATCH_NOW=<epoch>（冻结时钟打时点回放）；
#   --selftest 内置自测=临时信箱+沙箱 state/lock+冻结时钟打全矩阵（背棍重试/拉成回执/
#   WAKE-STALL/残锁判据/合并缓冲门/env 读数/argv 形态/CLI），全过打 INBOX_WATCH_OK。
# 收工/开工轮的「处理完立即再查一次信箱」接续纪律=两件套②，见 CLAUDE.md 第七节补句。
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

# cron 环境 LANG 可能为 POSIX——中文输出先钉 UTF-8，防 UnicodeEncodeError 静默死
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OPS = os.path.dirname(os.path.abspath(__file__))
TEAM = os.path.dirname(OPS)                                  # cc-team 根（双侧形态随仓）
REPO = os.path.dirname(TEAM)                                 # 会话工作树=仓库根
sys.path.insert(0, TEAM)
from ccards import config as _cfg  # REQ-OSS-ENGINE-1：runner/powershell/道锁随配置单入口
from ccards.config import pid_alive as _pid_alive  # 判活唯一归口（SK 案后禁各写各的探针）
DEF_INBOX = os.path.join(TEAM, "inbox", "OWNER-INBOX.md")
DEF_STATE = os.path.join(TEAM, "state", "inbox-watch.json")
PS_LINUX = _cfg.CC_PS      # 起会话 powershell（WSL 侧随盘根派生=旧 /mnt 形态同值；Windows 侧随 PATH）
RUNNER = _cfg.CC_RUNNER    # 起会话核（不改它；engine.runner_cmd 可覆写）
LANE = "pmmail"
LANE_LOCK_WIN = _cfg.lane_lock_path(LANE)   # 道锁 Windows 形（fleet.code_root 派生=旧值同串）
PROMPT = "你是ccPM，执行一轮正式开工：按 CLAUDE.md 第七节处理全部未acked条目"
TITLE_RE = re.compile(r"^## seq=(\d+)")
ALARM_WINDOWS = 3            # 同签名连续 3 次拉起无回执 → WAKE-STALL
DEFAULT_RETRY_MIN = 5.0      # 拉成回执背棍窗（业主 04:06②「约5分钟」；env INBOX_WATCH_RETRY_MIN 可调）
DEFAULT_QUIET_MIN = 5.0      # 静默窗（分钟）：seq=17②：静默窗（PM 可调 env INBOX_WATCH_QUIET_MIN）；mtime 静默够久=业主打完字
BATCH_NOW = 3            # seq=17①：未 ack≥3 → 立即收班拉起
STALE_NOEVID_GRACE = 1800.0  # 残锁判据②收紧：锁内**无任何 pid 证据**时，2min sweeplock 窗会误拆
                             # 活会话（run-cc.ps1 锁内形态仓内无凭据，PM 一班正常可跑 10~30min）——
                             # 故无证据者用 30min 病残窗（≥正常班上限），有死锚 pid 者仍走判据①立拆


def ts():
    return time.strftime("%F %T")


def retry_min():
    """拉成回执背棍窗（分钟）。env 坏值回退 5.0，不炸环（环里死一次=门铃停摆）。"""
    try:
        v = float(os.environ.get("INBOX_WATCH_RETRY_MIN", ""))
        if v < 0:
            raise ValueError
        return v
    except ValueError:
        return DEFAULT_RETRY_MIN


def quiet_min():
    """静默窗（分钟）：seq=17 明写「长度 PM 可调，写进保障清单」。env 坏值回退默认，不炸环。"""
    try:
        v = float(os.environ.get("INBOX_WATCH_QUIET_MIN", ""))
        if v < 0:
            raise ValueError
        return v
    except ValueError:
        return DEFAULT_QUIET_MIN


def sig_of(seqs):
    return hashlib.sha1(",".join(sorted(seqs)).encode("utf-8")).hexdigest()[:12]


def read_unacked(path):
    """返回 (未acked seq 列表, err)。标题行=^## seq=；行内含 [acked] 视为已收口。"""
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        return None, "inbox 不可读(%r)" % (e,)
    seqs = []
    try:
        for line in f:
            m = TITLE_RE.match(line)
            if m and "[acked]" not in line:
                seqs.append(m.group(1))
    finally:
        f.close()
    return seqs, ""


def blank_state():
    return {"last_launch_ts": None, "last_sig": None, "last_n": None,
            "windows": 0, "alarmed": False, "launches": 0}


def load_state(path):
    try:
        j = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return blank_state()
    if not isinstance(j, dict):
        return blank_state()
    st = blank_state()
    for k in st:
        if k in j:
            st[k] = j[k]
    return st


def save_state(path, st):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    out = dict(st)
    out["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _clear_receipt(s):
    """拉成回执落定：清锚、计数归零、新 episode 重开闸（含 alarmed 解除——承接旧形「签名变化重开闸」语义）。"""
    s.update({"last_launch_ts": None, "last_sig": None, "last_n": None,
              "windows": 0, "alarmed": False})


def decide(st, n, sig, now, retry, inflight, idle=None, quiet_min=DEFAULT_QUIET_MIN):
    """纯决策（selftest/沙箱锁可冻结 now 打全矩阵）：返回 (action, new_state)。
    action ∈ OK/LAUNCH/WAIT-BATCH/SKIP-RETRY/SKIP-INFLIGHT/ALARM/ALARMED。
    语义钉死（业主 04:06 拉成回执版）：
    · 回执=签名变化 或 unacked 计数下降 → 清锚归零重开闸（拉成确认，不再受背窗约束）；
    · 同签名且无进展：背窗 retry（默认 5min）内=SKIP-RETRY 短背棍；到期未改善=上一拉作废 →
      计数+1 再拉；连续第 3 计数=ALARM（WAKE-STALL 停拉待签名变化/人工处置）；
    · inflight（道锁活锚在位）不叠拉；N=0 清零复位。
    seq=17 合并缓冲（只改「何时开门铃」）：N≥BATCH_NOW 立即拉；N≥1 且信箱静默≥quiet_min
    才拉（末条不饿死）；都不满足=WAIT-BATCH（凑批中，不动背窗计数）。idle=None
    （mtime 读不出）=按静默满放行——宁可多拉一班，不可让信饿死。"""
    if n == 0:
        ns = blank_state()
        ns["launches"] = st.get("launches", 0)
        return "OK", ns
    s = dict(st)
    lsig, ln = s.get("last_sig"), s.get("last_n")
    receipt = lsig is not None and (lsig != sig or (isinstance(ln, int) and n < ln))
    if receipt:
        _clear_receipt(s)
    if s.get("alarmed"):
        return "ALARMED", s
    if inflight:
        return "SKIP-INFLIGHT", s
    if n < BATCH_NOW and idle is not None and idle < float(quiet_min) * 60.0:
        return "WAIT-BATCH", s     # 凑批中：信没满 3 条且信箱还在动——门铃不响，背窗计数原样
    lts = s.get("last_launch_ts")
    if lts is not None and s.get("last_sig") == sig:
        if now - lts < retry * 60.0:
            return "SKIP-RETRY", s             # 背棍窗内：拉过、回执未落、窗还没到（旧形此处傻等 30min）
        s["windows"] = int(s.get("windows", 0)) + 1
        if s["windows"] >= ALARM_WINDOWS:
            return "ALARM", s                  # 连续第 3 次拉起无回执：WAKE-STALL 升 ALARM，不再拉
    return "LAUNCH", s                          # 首拉 / 签名为新 episode / 背窗满仍未清（未满3）


def ps_bin():
    if os.path.exists(PS_LINUX):
        return PS_LINUX
    return shutil.which("powershell.exe") or shutil.which("powershell")


def to_win(p):
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if m:
        return "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\"))
    return os.path.abspath(p)


def runner_argv(win_brief):
    """与 ops/dispatch.py 起 worker 同款 argv 形态（-Brief/-Workdir/-Lane），复用 run-cc.ps1 不改它。"""
    return [ps_bin(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", RUNNER,
            "-Brief", win_brief, "-Workdir", to_win(REPO), "-Lane", LANE]


def launch():
    """写 brief 文件→run-cc.ps1 拉起 PM 会话。返回 (brief路径, err)。
    测试缝：env INBOX_WATCH_SIM_LAUNCH=1（仅沙箱红绿锁设，生产不设）=不真起 powershell，
    走同款锚流程，供回放「被拉会话即死不清账」全链不拖真会话。"""
    sim = (os.environ.get("INBOX_WATCH_SIM_LAUNCH") or "").strip().lower()
    if sim in ("1", "true", "yes", "on"):
        return "sim://launch", ""
    p = ps_bin()
    if not p:
        return None, "powershell 不可达（PS=%r 不存在且 which 无果）" % (PS_LINUX,)
    # BUG-RUNNER-GONE-1 缺位闸：起会话核不在盘上=拉必空转（powershell 报 -File 不存在秒退，回执永不到，
    # 白白烧 WAKE-STALL 三窗）。响亮报 err 进本器台账+alerts，同 dispatch RUNNER-MISSING 口径。
    if not os.path.isfile(RUNNER):
        return None, "RUNNER-MISSING 起会话核缺席：%s（engine.runner_cmd/CC_RUNNER 改锚后自愈）" % (RUNNER,)
    logs = os.path.join(TEAM, "logs")
    os.makedirs(logs, exist_ok=True)
    fn = os.path.join(logs, "brief-%s-%s.txt" % (LANE, time.strftime("%H%M%S")))
    try:
        with open(fn, "w", encoding="utf-8") as f:
            f.write(PROMPT + "\n")
        subprocess.Popen(runner_argv(to_win(fn)), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WIN)
    except OSError as e:
        return None, "拉起失败(%r)" % (e,)
    return fn, ""


def _lock_pid(path):
    """读锁锚 pid：目录形=meta.json{pid|session_pid}；文件形=JSON{pid} 或纯整数。读不出=None。"""
    try:
        if os.path.isdir(path):
            j = json.load(open(os.path.join(path, "meta.json"), encoding="utf-8"))
            if isinstance(j, dict):
                for k in ("pid", "session_pid"):
                    if j.get(k) is not None:
                        return int(j[k])
        else:
            raw = open(path, encoding="utf-8").read().strip()
            try:
                j = json.loads(raw)
            except ValueError:
                return int(raw)          # 纯整数形态（int 失败由函数级 except 兜成 None）
            if isinstance(j, dict) and j.get("pid") is not None:
                return int(j["pid"])
            if isinstance(j, int):
                return j                 # JSON 整数形态
    except (OSError, ValueError, TypeError):
        return None
    return None


def _lock_age(path, now):
    mt = 0.0
    try:
        if os.path.isdir(path):
            for dp, _dd, fs in os.walk(path):
                for fn in fs:
                    try:
                        mt = max(mt, os.path.getmtime(os.path.join(dp, fn)))
                    except OSError:
                        pass
            if mt == 0.0:
                mt = os.path.getmtime(path)
        else:
            mt = os.path.getmtime(path)
    except OSError:
        return None
    return max(0.0, now - mt)


def lane_inflight(lock_path, now=None):
    """防双开闸+残锁清理（业主 04:06③；判据族=ops/recycle.py sweeplock 陈旧锁接管段）。
    返回 (inflight, note)：锚死或崩残超窗=清残锁放行（LOCK-REAP）；其余一律保守判在飞。"""
    if now is None:
        now = time.time()
    try:
        if not os.path.exists(lock_path):
            return False, ""
    except OSError as e:
        return True, "锁在场探测异常(%r) 保守判在飞" % (e,)
    pid = _lock_pid(lock_path)
    if pid is not None:
        if _pid_alive(pid):
            return True, "锚活 pid=%s" % pid
        how = "锚死 pid=%s（疑 update 重启所致）" % pid
    else:
        age = _lock_age(lock_path, now)
        if age is None or age < STALE_NOEVID_GRACE:
            return True, "锁内无pid证据 龄%s<病残窗%.0fmin 保守判在飞" % (
                "?" if age is None else "%.0fs" % age, STALE_NOEVID_GRACE / 60.0)
        how = "锁内无pid证据且龄%.0fs≥病残窗%.0fs（病残）" % (age, STALE_NOEVID_GRACE)
    try:
        if os.path.isdir(lock_path):
            shutil.rmtree(lock_path)
        else:
            os.remove(lock_path)
    except OSError as e:
        return True, "LOCK-REAP 删锁失败(%r) 保守判在飞" % (e,)
    return False, "LOCK-REAP %s 残锁已清放行" % how


def session_census():
    """pmmail 真会话普查（BUG-INBOX-DOUBLEPULL-1 补闸，业主 11-04 20:3x「CC 不干活该释放资源」
    实锤三连叠班：锁无 pid 证据到 30min 病残窗即清，但班次进程仍活着 → 下圈照拉 → 叠班）。
    锁只能证「有过班」，普查才证「现在有班」——认人不认牌，族形=recycle._ghost_session_pids
    同口径（claude.exe CommandLine 含 CCLANE=pmmail-）。只读不改进程：≥2 叠班只点名拦截，
    不代杀（杀旧保新属回收岗职责面，另行处置）。
    测试缝 INBOX_WATCH_SESSION_CMD（%L%=道名，stdout 每行一 pid）仅沙箱设、生产不设。
    返回：pid 表（可空）=实查；None=不可查（非 Windows / 查询失败 / 无缝且平台不支持）
    ——调用侧把 None 当「不拦」维持旧锁形行为：饿死信箱比多拉一班贵（纪律①原文口径）。"""
    seam = (os.environ.get("INBOX_WATCH_SESSION_CMD") or "").strip()
    try:
        if seam:
            r = subprocess.run(seam.replace("%L%", LANE), shell=True, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=25, creationflags=NO_WIN)
        elif os.name == "nt":
            ps = ("(Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\") | "
                  "Where-Object { $_.CommandLine -match 'CCLANE=%s-' } | "
                  "ForEach-Object { $_.ProcessId }" % LANE)
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=30, creationflags=NO_WIN)
        else:
            return None
        if r.returncode != 0:
            return None
        return [int(x.strip()) for x in (r.stdout or "").splitlines() if x.strip().isdigit()]
    except Exception:
        return None


def inflight_gate(lock_path, now=None):
    """防双开合成闸=锁形判据 ∨ 进程普查。锁放行的下一关=真会话在场照拦（SESSION-ALIVE）；
    普查不可查(None)=按旧形放行（保守=放行拉，不饿死信箱）。"""
    inflight, note = lane_inflight(lock_path, now)
    if not inflight:
        pids = session_census()
        if pids:
            extra = "（≥2=叠班旧账在场：只拦新拉不代杀，清旧归回收岗）" if len(pids) >= 2 else ""
            inflight, note = True, "SESSION-ALIVE 普查在飞会话=%d%s" % (len(pids), extra)
    return inflight, note


def apply_round(inbox_path, state_path, now=None, lock_path=None):
    """--apply 一圈。返回退出码。now/lock_path 供 selftest/沙箱注入，实跑取真实值。"""
    seqs, err = read_unacked(inbox_path)
    if err:
        print("%s ERR %s——不静默" % (ts(), err))
        return 1
    n, sg = len(seqs), sig_of(seqs)
    if now is None:
        # 测试缝：INBOX_WATCH_NOW=epoch 冻结时钟打时点回放（仅沙箱设，生产不设）
        frozen = (os.environ.get("INBOX_WATCH_NOW") or "").strip()
        if frozen:
            try:
                now = float(frozen)
            except ValueError:
                now = time.time()
        else:
            now = time.time()
    st = load_state(state_path)
    inflight, note = inflight_gate(lock_path or LANE_LOCK_WIN, now)
    try:  # 静默度=距信箱文件最后写入的秒数（seq=17 合并缓冲的输入；读不出=None=放行拉起侧）
        idle = max(0.0, now - os.path.getmtime(inbox_path))
    except OSError:
        idle = None
    # 回执核账在决策前显痕：上次拉起的锚与新读数对得上号=拉成确认（业主 04:06① 要求可见）
    lsig, ln = st.get("last_sig"), st.get("last_n")
    if lsig is not None and (lsig != sg or (isinstance(ln, int) and n < ln)):
        print("%s WAKE-RECEIPT 上次拉起(sig=%s,n=%s)后信箱已进展(现 sig=%s,n=%d)——拉成确认，重开闸按新节奏"
              % (ts(), str(lsig)[:6], ln, sg[:6], n))
    if note:
        print("%s INBOX-LOCK %s" % (ts(), note))
    action, ns = decide(st, n, sg, now, retry_min(), inflight, idle=idle, quiet_min=quiet_min())
    ns["last_beat"] = now  # 在岗心跳：每圈必落——活着零痕=与死不可分（业主令 2026-11-02 岗先运行；同 BUG-LEDGER-ZEROROW 病根）
    if action == "OK":
        # 信箱干净也写盘：分钟级 IO negligible，换来「看门岗在岗」有凭据
        save_state(state_path, ns)
        return 0
    line = "%s %s unacked=%d sig=%s idle=%s" % (
        ts(), action, n, sg, ("?" if idle is None else "%.0fs" % idle))
    if action == "LAUNCH":
        fn, lerr = launch()
        if lerr:
            print("%s LAUNCH-FAIL unacked=%d：%s——锚不落，下圈重试" % (ts(), n, lerr))
            return 1
        ns["last_launch_ts"] = now
        ns["last_sig"] = sg
        ns["last_n"] = n
        ns["launches"] = int(ns.get("launches", 0)) + 1
        print("%s brief=%s launches=%d windows=%d——回执窗%.0fmin（拉成前不傻等满窗）"
              % (line, os.path.basename(fn), ns["launches"], int(ns.get("windows", 0)), retry_min()))
    elif action == "ALARM":
        ns["alarmed"] = True
        stall = ("ALARM WAKE-STALL PM信箱看门：同签名连续%d次拉起均无拉成回执（被拉会话疑反复即死，"
                 "openclaw update 重启族）——停自动拉起，等信箱签名变化或人工处置（CLAUDE.md 第七节）后自动恢复"
                 % ALARM_WINDOWS)
        print("%s %s unacked=%d sig=%s（详情只进本日志行；红灯账键=恒定文案防打地鼠）"
              % (ts(), stall, n, sg))
        try:
            from ccards import alerts as _AL
            _AL.record(stall)   # 键=剥时距后的问题身份，msg 无漂移数字=同 episode 单键聚合
        except Exception as e:
            print("%s WARN alerts 落账失败(%r)——红灯账缺痕但 state 钉闸照在" % (ts(), e))
    else:
        print(line)
    save_state(state_path, ns)
    return 0


def selftest():
    """临时信箱+沙箱 state/lock+冻结时钟打全矩阵，绝不碰真账/真拉起 powershell。全过=INBOX_WATCH_OK。"""
    d = tempfile.mkdtemp(prefix="inbox-watch-selftest-")
    fails = []

    def chk(name, cond):
        if not cond:
            fails.append(name)

    try:
        box = os.path.join(d, "OWNER-INBOX.md")
        with open(box, "w", encoding="utf-8") as f:
            f.write("# 业主来信\n---\n"
                    "## seq=1 2026-11-02T08:00+0800 [acked]\nx\n"
                    "## seq=2 2026-11-02T08:01+0800 [acked]\nx\n"
                    "## seq=3 2026-11-02T08:02+0800\nx\n")
        seqs, err = read_unacked(box)
        chk("读信箱无err", err == "")
        chk("unacked=1", seqs == ["3"])
        sig = sig_of(seqs)
        rt, t0 = 5.0, 1_000_000.0
        st = blank_state()
        a, st1 = decide(st, 1, sig, t0, rt, False)
        chk("首圈LAUNCH", a == "LAUNCH")
        st1["last_launch_ts"], st1["last_sig"], st1["last_n"], st1["launches"] = t0, sig, 1, 1
        a, _ = decide(st1, 1, sig, t0 + 60, rt, False)
        chk("背棍窗内(60s<5min)不叠拉=SKIP-RETRY", a == "SKIP-RETRY")
        a, st2 = decide(st1, 1, sig, t0 + 6 * 60, rt, False)
        # ↑ 本案主锚：拉起的会话死了、账没清——旧形此处要傻等到 30min（业主 04:06 指着的空转），
        #   新形 5min 短背棍到点即重试。把 retry 改回 30.0 的本断言必红（tests/pm-wake-receipt-test.py R1）。
        chk("窗满无进展短背棍重试(6min→LAUNCH w1，不再傻等30min)", a == "LAUNCH" and st2["windows"] == 1)
        st2["last_launch_ts"] = t0 + 6 * 60
        a, st3 = decide(st2, 1, sig, t0 + 12 * 60, rt, False)
        chk("再拉第2次(w2)", a == "LAUNCH" and st3["windows"] == 2)
        st3["last_launch_ts"] = t0 + 12 * 60
        a, _ = decide(st3, 1, sig, t0 + 18 * 60, rt, False)
        chk("第3次窗满升ALARM(WAKE-STALL)", a == "ALARM")
        s5 = dict(st3)
        s5["windows"] = 2
        s5["last_launch_ts"] = t0 + 18 * 60
        a, s6 = decide(s5, 1, sig, t0 + 24 * 60, rt, False)
        chk("连续3次拉起无回执→ALARM", a == "ALARM" and s6["windows"] == 3)
        s6["alarmed"] = True
        a, _ = decide(s6, 1, sig, t0 + 30 * 60, rt, False)
        chk("ALARM后停自动拉起", a == "ALARMED")
        a, s7 = decide(s6, 2, sig_of(["3", "4"]), t0 + 31 * 60, rt, False)
        chk("签名变化=拉成回执重开闸LAUNCH", a == "LAUNCH" and s7["alarmed"] is False
            and s7["last_launch_ts"] is None and s7["windows"] == 0)
        # 计数下降（同签名理论上不可能并存，防御性双判据）也认回执
        s8 = {"last_launch_ts": t0, "last_sig": sig, "last_n": 3, "windows": 2,
              "alarmed": True, "launches": 4}
        a, s9 = decide(s8, 2, sig, t0 + 4 * 60, rt, False)
        chk("unacked计数下降=回执（清锚+解除钉闸）", a == "LAUNCH" and s9["last_launch_ts"] is None
            and s9["alarmed"] is False)
        # inflight（前一轮会话未收口且锚活）不叠拉
        a, _ = decide(st1, 1, sig, t0 + 60, rt, True)
        chk("inflight不叠拉", a == "SKIP-INFLIGHT")
        # 清零复位
        a, s10 = decide(st1, 0, "", t0 + 70, rt, False)
        chk("清零OK复位锚", a == "OK" and s10["last_launch_ts"] is None and s10["last_sig"] is None)
        a, s11 = decide(s6, 0, "", t0 + 70, rt, False)
        chk("ALARMED态遇信箱清净=OK复位(钉闸随清账解除)", a == "OK" and s11["alarmed"] is False)
        # seq=17 合并缓冲门（st1=已拉一班态：anchor t0, sig, n=1；sig2/sig3 均变化=回执落定）
        sig2, sig3 = sig_of(["3", "4"]), sig_of(["3", "4", "5"])
        a, nsw = decide(st1, 2, sig2, t0 + 32 * 60, rt, False, idle=60)
        chk("N=2静默60s<5min=WAIT-BATCH（新episode凑批不逐条拉）", a == "WAIT-BATCH")
        chk("WAIT-BATCH落定回执但不叠拉", nsw["last_launch_ts"] is None and nsw["windows"] == 0)
        a, _ = decide(st1, 3, sig3, t0 + 33 * 60, rt, False, idle=10)
        chk("N≥3立即拉（静默窗未满也开门铃）", a == "LAUNCH")
        a, _ = decide(st1, 2, sig2, t0 + 40 * 60, rt, False, idle=5 * 60 + 1)
        chk("N=2静默5min+1s=满窗拉起（末条不饿死）", a == "LAUNCH")
        a, _ = decide(st1, 2, sig2, t0 + 34 * 60, rt, False, idle=60, quiet_min=1.0)
        chk("静默窗PM可调：窗=1min时idle=60s达标→拉", a == "LAUNCH")
        a, _ = decide(st1, 1, sig, t0 + 35 * 60, rt, False, idle=None)
        chk("mtime读不出(idle=None)=fail-open不拦拉（饿死比多拉一班贵）", a == "LAUNCH")
        a, _ = decide(st1, 2, sig2, t0 + 36 * 60, rt, True, idle=10)
        chk("在途不叠拉先于凑批门", a == "SKIP-INFLIGHT")
        # env 读数面：坏值回退默认不炸（环里死一次=门铃停摆）
        os.environ["INBOX_WATCH_QUIET_MIN"] = "abc"
        chk("env坏值回退5.0", quiet_min() == DEFAULT_QUIET_MIN)
        os.environ["INBOX_WATCH_QUIET_MIN"] = "-1"
        chk("env负值回退5.0", quiet_min() == DEFAULT_QUIET_MIN)
        os.environ["INBOX_WATCH_QUIET_MIN"] = "2.5"
        chk("env正常值生效", quiet_min() == 2.5)
        os.environ.pop("INBOX_WATCH_QUIET_MIN", None)
        os.environ["INBOX_WATCH_RETRY_MIN"] = "abc"
        chk("retry env坏值回退5.0", retry_min() == DEFAULT_RETRY_MIN)
        os.environ["INBOX_WATCH_RETRY_MIN"] = "0.5"
        chk("retry env正常值生效", retry_min() == 0.5)
        os.environ.pop("INBOX_WATCH_RETRY_MIN", None)
        # argv 同源形态：与 dispatcher 同款 -Brief/-Workdir/-Lane
        argv = runner_argv(r"C:\x\brief.txt")
        chk("argv含runner", RUNNER in argv)
        chk("argv含三参", [argv[argv.index("-Brief") + 1], argv[argv.index("-Workdir") + 1],
                           argv[argv.index("-Lane") + 1]] ==
            [r"C:\x\brief.txt", to_win(REPO), LANE])
        # ---- 残锁判据族（业主 04:06③；族=recycle sweeplock）----
        DEAD = 2 ** 31 - 2   # 沙箱必死 pid（本机不可能驻留；posix/Win 判活皆 False）
        lk = os.path.join(d, "lane-pmmail.lock")
        chk("无锁=不在飞", lane_inflight(lk, t0) == (False, ""))
        os.makedirs(lk)
        with open(os.path.join(lk, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid()}, f)
        infl, note = lane_inflight(lk, t0)
        chk("锚活=保守判在飞不拆", infl is True and "锚活" in note and os.path.isdir(lk))
        with open(os.path.join(lk, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": DEAD}, f)
        infl, note = lane_inflight(lk, t0)
        chk("锚死=LOCK-REAP清残放行", infl is False and "LOCK-REAP" in note and not os.path.exists(lk))
        os.makedirs(lk)
        with open(os.path.join(lk, "card"), "w", encoding="utf-8") as f:
            f.write("x")
        infl, note = lane_inflight(lk, t0)
        chk("无meta新鲜=保守在飞", infl is True and os.path.isdir(lk))
        os.utime(os.path.join(lk, "card"), (t0 - 180, t0 - 180))
        infl, note = lane_inflight(lk, t0)
        chk("无pid证据龄3min<病残窗=不误拆活锁(在飞)", infl is True and os.path.isdir(lk))
        os.utime(os.path.join(lk, "card"), (t0 - 1900, t0 - 1900))
        infl, note = lane_inflight(lk, t0)
        chk("无pid证据超病残窗=清放行", infl is False and "病残" in note and not os.path.exists(lk))
        lk2 = os.path.join(d, "lane-file.lock")
        with open(lk2, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        chk("文件形纯pid锚活=在飞", lane_inflight(lk2, t0)[0] is True)
        with open(lk2, "w", encoding="utf-8") as f:
            f.write(str(DEAD))
        infl, note = lane_inflight(lk2, t0)
        chk("文件形锚死=LOCK-REAP清放行", infl is False and "LOCK-REAP" in note
            and not os.path.exists(lk2))
        with open(lk2, "w", encoding="utf-8") as f:
            f.write("no-meta-text")
        os.utime(lk2, (t0 - 1900, t0 - 1900))
        infl, note = lane_inflight(lk2, t0)
        chk("文件形无pid证据超病残窗=清放行", infl is False and not os.path.exists(lk2))
        # ---- 普查补闸（纪律⑤：锁放行≠真无班——真会话在场照拦，叠班只点名不代杀）----
        os.environ["INBOX_WATCH_SESSION_CMD"] = "echo 4242"
        inf5, note5 = inflight_gate(os.path.join(d, "no-such.lock"), t0)
        chk("锁不在场但普查有班=SESSION-ALIVE 拦叠拉", inf5 is True and "SESSION-ALIVE" in note5)
        os.environ["INBOX_WATCH_SESSION_CMD"] = "echo 4242 && echo 4243"
        inf6, note6 = inflight_gate(os.path.join(d, "no-such.lock"), t0)
        chk("叠班≥2=点名（只拦新拉不代杀）", inf6 is True and "叠班" in note6)
        os.environ["INBOX_WATCH_SESSION_CMD"] = "echo"
        inf7, _ = inflight_gate(os.path.join(d, "no-such.lock"), t0)
        chk("普查空表=按旧形放行（锁净+无班才真放行）", inf7 is False)
        os.environ.pop("INBOX_WATCH_SESSION_CMD", None)
        r5 = session_census()
        chk("无缝形=可查(list)或不可查(None)，绝不抛", isinstance(r5, (list, type(None))))
        # 撤闸自证面：若删 inflight_gate 的普查分支，上面 4242 款必红（非空检查；族=guard 必红纪律）
        # ---- SIM_LAUNCH 测试缝：LAUNCH 不真起 powershell ----
        os.environ["INBOX_WATCH_SIM_LAUNCH"] = "1"
        fn, lerr = launch()
        chk("SIM缝=不真起会话", lerr == "" and fn == "sim://launch")
        os.environ.pop("INBOX_WATCH_SIM_LAUNCH", None)
        # ---- 沙箱全链（冻结时钟 INBOX_WATCH_NOW 打时点回放，state/lock 全在临时区）----
        box2 = os.path.join(d, "inbox2.md")
        with open(box2, "w", encoding="utf-8") as f:
            f.write("## seq=1 T\nx\n## seq=2 T\nx\n## seq=3 T\nx\n")
        st2p = os.path.join(d, "watch.json")
        lk3 = os.path.join(d, "lane-pmmail.lock")
        env_bak = dict(os.environ)
        state_bak = _cfg.STATE
        _cfg.STATE = d   # 红灯账改道沙箱：链⑥的 alerts.record 绝不许写现网 state/alerts.json
        os.environ.update({"INBOX_WATCH_SIM_LAUNCH": "1", "INBOX_WATCH_QUIET_MIN": "0",
                       # 全链回放钉定=普查空表（防 Windows 现网活班把 LAUNCH 拦成 SESSION-ALIVE 飘红）
                       "INBOX_WATCH_SESSION_CMD": "echo"})
        import io as _io
        import contextlib as _cl

        def run_round(now_epoch):
            os.environ["INBOX_WATCH_NOW"] = str(now_epoch)
            buf = _io.StringIO()
            with _cl.redirect_stdout(buf):
                rc = apply_round(box2, st2p, lock_path=lk3)
            return rc, buf.getvalue()

        rc, out = run_round(t0)
        chk("链①首拉LAUNCH且落锚", rc == 0 and " LAUNCH " in out and "回执窗" in out)
        stj = json.load(open(st2p, encoding="utf-8"))
        chk("链①锚含n快照", stj["last_n"] == 3 and stj["last_sig"] == sig_of(["1", "2", "3"]))
        os.makedirs(lk3)
        with open(os.path.join(lk3, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid()}, f)
        rc, out = run_round(t0 + 2 * 60)
        chk("链②会话在飞=SKIP-INFLIGHT", "SKIP-INFLIGHT" in out)
        with open(os.path.join(lk3, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": DEAD}, f)
        rc, out = run_round(t0 + 6 * 60)
        chk("链③会话死=残锁清理+短背棍重试", "LOCK-REAP" in out and " LAUNCH " in out)
        rc, out = run_round(t0 + 8 * 60)
        chk("链④新一轮背窗内=SKIP-RETRY（旧形此处=30min满窗空转）", "SKIP-RETRY" in out)
        rc, out = run_round(t0 + 12 * 60)
        chk("链⑤窗满再拉w2", " LAUNCH " in out)
        rc, out = run_round(t0 + 18 * 60)
        chk("链⑥第3窗=ALARM+WAKE-STALL", "ALARM" in out and "WAKE-STALL" in out)
        stj = json.load(open(st2p, encoding="utf-8"))
        chk("链⑥钉住在state", stj["alarmed"] is True)
        rc, out = run_round(t0 + 24 * 60)
        chk("链⑦钉住后停拉=ALARMED", "ALARMED" in out)
        with open(box2, "r", encoding="utf-8") as f:
            body = f.read()
        with open(box2, "w", encoding="utf-8") as f:
            f.write(body.replace("## seq=1 T", "## seq=1 T [acked]"))
        rc, out = run_round(t0 + 25 * 60)
        chk("链⑧签名变化=拉成回执解除钉闸重开闸", "WAKE-RECEIPT" in out and " LAUNCH " in out)
        rc, out = run_round(t0 + 29 * 60)   # 上次锚=t0+25min，+4min 仍在 5min 背窗内
        chk("链⑨背窗内新episode不叠拉", "SKIP-RETRY" in out)
        with open(box2, "w", encoding="utf-8") as f:
            f.write("## seq=1 T [acked]\nx\n## seq=2 T [acked]\nx\n## seq=3 T [acked]\nx\n")
        rc, out = run_round(t0 + 40 * 60)
        stj = json.load(open(st2p, encoding="utf-8"))
        chk("链⑩全清=OK复位锚(launches保留)", rc == 0 and stj["last_launch_ts"] is None
            and stj["alarmed"] is False and stj["launches"] >= 3)
        # 红灯账落点面（链⑥ ALARM 时 alerts.record 已写沙箱 STATE）：键=恒定文案、聚合可数
        try:
            alr = json.load(open(os.path.join(d, "alerts.json"), encoding="utf-8"))
            keys = [k for k in alr.get("alerts", {}) if "WAKE-STALL" in k]
            chk("链⑥WAKE-STALL进alerts聚合账(键稳定单条)", len(keys) == 1
                and alr["alerts"][keys[0]]["count"] >= 1)
        except (OSError, ValueError):
            chk("链⑥WAKE-STALL进alerts聚合账(文件缺失=未落账)", False)
        os.environ.clear()
        os.environ.update(env_bak)
        _cfg.STATE = state_bak
        # CLI 判据面：--dry 只打读数（子进程真跑一遍本文件，dry 不碰 state 不拉起）
        p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__),
                            "--dry", "--inbox", box], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WIN)
        chk("CLI-dry输出unacked=1", p.returncode == 0 and "unacked=1" in (p.stdout or ""))
        # CLI --apply 沙箱全链：state 落临时路径，首圈 LAUNCH 会真起 powershell 吗？——不能。
        # 故 CLI 侧只验 dry 与读面；LAUNCH 实起 powershell 的链路=decide→launch 单链，
        # launch() 本身不在 selftest 真执行（防误拉真会话，SIM 缝已验锚流程），argv 构造已由上两条钉住。
        # 红检：空检查比没检查糟——故意断一条必假路径（确认 chk 机制红得起来）
        red = []
        seqs_bad, err_bad = read_unacked(os.path.join(d, "no-such-inbox.md"))
        red.append(seqs_bad is None and "不可读" in err_bad)
        chk("缺信箱报err（红检可红）", all(red))
        if fails:
            print("SELFTEST-FAIL: " + ",".join(fails))
            return 1
        print("INBOX_WATCH_OK")
        return 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="PM 信箱分钟级看门（MECH-PM-WAKE-1；拉成回执版 BUG-PM-WAKE-RECEIPT-1）")
    ap.add_argument("--apply", action="store_true", help="真判定真拉起（ctrld inbox 环用）")
    ap.add_argument("--dry", action="store_true", help="只打读数（判据面）")
    ap.add_argument("--inbox", default=DEF_INBOX, help="覆盖信箱文件路径（沙箱注入专用）")
    ap.add_argument("--state", default=DEF_STATE, help="覆盖看门锚文件（沙箱注入专用）")
    ap.add_argument("--lock", default=LANE_LOCK_WIN, help="覆盖道锁路径（沙箱注入专用）")
    ap.add_argument("--selftest", action="store_true", help="内置自测，全过打 INBOX_WATCH_OK")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.dry or not a.apply:                    # 缺省保守=dry：判据面不写状态不拉起
        seqs, err = read_unacked(a.inbox)
        if err:
            print("%s ERR %s——不静默" % (ts(), err))
            sys.exit(1)
        try:
            idle_dry = max(0.0, time.time() - os.path.getmtime(a.inbox))
        except OSError:
            idle_dry = None
        print("unacked=%d idle=%s batch_now=%d quiet_min=%s retry_min=%s" % (
            len(seqs), ("?" if idle_dry is None else "%.0fs" % idle_dry),
            BATCH_NOW, os.environ.get("INBOX_WATCH_QUIET_MIN", DEFAULT_QUIET_MIN),
            os.environ.get("INBOX_WATCH_RETRY_MIN", DEFAULT_RETRY_MIN)))
        sys.exit(0)
    sys.exit(apply_round(a.inbox, a.state, lock_path=a.lock))


if __name__ == "__main__":
    main()
