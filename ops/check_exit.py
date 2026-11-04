#!/usr/bin/env python3
# check_exit.py — CC Stop hook：退出前强制收口。exit 0 放行；exit 2 + 理由 = block。
# 用法: python3 ops/check_exit.py --owner m2 [--repo <worktree或主仓>] [--notes <lane笔记文件>]
# MECH-STD1：更名 snake_case 并住 ops/（提示语中的写入口同步改指 cards_dag.py）。
# 2026-10-30 机制审计改版：
#  (1) 旧版「笔记闸门」写在 block() 之后——两个分支都先 sys.exit(2)，那段永远不可达（死代码，
#      实锤见 docs/mech-audit-20261030.md R8）→ 拆成独立 Pass2，DONE 未验收卡也能查。
#  (2) Windows 建的 worktree 其 .git 文件里 gitdir 是 C:/ 反斜杠风格路径，WSL 侧 git -C 直接
#      fatal → 加 gitdir 映射重试(C:/x → /mnt/c/x + GIT_DIR/GIT_WORK_TREE 环境变量)；
#      仍读不通时 stderr 打 WARN 并跳过 commit 相关判据（不误 block 别人提交的假阳性，
#      假阴性以 WARN 留痕，由人补看）。
# 2026-10-30 MECH-S1 认领身份绑会话：
#  CC Stop hook 官方在 stdin 递 JSON(含 session_id)，旧版白白扔掉只按 --owner 泳道名认领，
#  同泳道旧会话崩了的残卡会逐打新会话。现优先按会话结账：
#  只 block 「claim.json 的 session_id == 本会话」或「session_id 为 -（未绑/存量）且 owner
#  同名」的卡；同泳道异会话残卡打 NOTE 行交 recycle/PM，不 block 退出。
#  非 JSON/空 stdin/无 session_id 字段 → 退回旧 --owner 模式（行为与现状完全一致）。
# 2026-11-02 REQ-EXIT-TIDY-1 散场自查交账（会话散场不收尾→卡躺 CLAIMED 数小时等 ZOMBIE）：
#  实测 4 例 worker 会话自然结束未做任何收尾；回收环只能救「带活锚」的批，无锚/锚判不了的
#  批永远救不了。本钩子加散场档：stdin JSON 的 stop_hook_active=True（=本钩子已 block 过、
#  会话仍要散——block 循环尽头）或 hook_event_name=SessionEnd（会话终结钩）时，若本道持有
#  的 CLAIMED 卡确属**本次会话**（两证其一：session_id 同值直证 / 锚 pid 落在本进程祖先族谱，
#  hook 进程即会话所生），则经 cards_dag 写闸 release 当场交账——不 done 不伪交付，只是把道
#  交还队列等重派；成交痕双写 stdout + logs/recycle.log 的 EXIT-RELEASE 行（判据：日志现
#  EXIT-RELEASE 而非等 ZOMBIE 15min）。归属证不成=保守不交（绝不替别会话交账）；release
#  通道坏了=EXIT-RELEASE-FAIL 留痕放行（不死锁会话，残局回收环照旧兜底，不伪成交）。
#  首次 Stop（未 block 过）不吃散场档——第一道闸照旧「完成/done」教育；无 stdin/非 JSON
#  手跑档与旧行为完全一致。边界：recycle 判活逻辑不动（那是 NS-1 卡的活）。
# 2026-11-03 MECH-HOOK-STDIN-WIN-1：上述两版的 stdin 读取在 Windows 原生宿主全线失效——
#  select() 在 nt 上只认 socket，对管道 stdin 必抛 OSError（10038/WSAENOTSOCK；宿主 Winsock
#  未初始化时是 10093/缺 WSAStartup，同源同病），而 hook_input 的 except 把它整个吞掉 =
#  不崩但 sid 恒 None，①②NOTE 放行 / 79b-f 散场交账在控制面（已 Windows 原生）静默永久降级。
#  修=nt 分支换 Win32 等价探测（GetStdHandle+GetFileType+PeekNamedPipe，见 _win_stdin_ready），
#  POSIX select 分支零改动；语义对齐：tty/空管道不等（=旧 select 超时），有字节才 read，
#  文件重定向直读（POSIX select 对 regular file 亦恒真）。
import argparse, contextlib, io, json, os, re, select, subprocess, sys, time
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

TEAM = os.environ.get("CC_TEAM_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAIMS = os.path.join(TEAM, "claims")
STATE = os.path.join(TEAM, "state")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # cc-team 根（ccards 所在）
try:  # 卡账读取唯一收口 graph（事实=graph.json 的唯一访问器，MECH-GRF1 与 store 合一）；
      # import 不通=降级跳判（WARN 留痕），hook 永不炸、绝不误 block 别人退场。
    from ccards import graph as _ledger
except Exception as _exc:
    print("WARN: check_exit 读不了 ccards(%r)，本轮跳过卡判据（请修账，勿删垫片）" % _exc, file=sys.stderr)
    _ledger = None


def _peek(cid):
    return _ledger.peek(cid) if _ledger else None


def _cards():
    return _ledger.all_cards() if _ledger else {}


def block(msg):
    print(msg)
    sys.exit(2)


def map_winpath(p):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if not m:
        return p
    return os.path.join("/mnt/" + m.group(1).lower(), m.group(2).replace("\\", "/"))


def git_ctx(repo):
    """返回 (repo, env|None)=可读仓；None=读不通（调用方 WARN 降级，不裸崩）。"""
    if not repo or not os.path.isdir(repo):
        return None
    dot = os.path.join(repo, ".git")
    env = None
    if os.path.isfile(dot):  # worktree/子模块：.git 是文件，指向 gitdir
        try:
            content = open(dot, encoding="utf-8", errors="ignore").read()
        except OSError:
            return None
        m = re.search(r"^gitdir:\s*(.+)\s*$", content, re.M)
        if not m:
            return None
        gd = m.group(1).strip()
        if not os.path.isdir(gd):
            gd = map_winpath(gd)
        if not os.path.isdir(gd):
            return None
        env = dict(os.environ, GIT_DIR=gd, GIT_WORK_TREE=repo)
    cmd = ["git"] if env else ["git", "-C", repo]
    r = subprocess.run(cmd + ["rev-parse", "--git-dir"], capture_output=True, text=True, encoding="utf-8", errors="replace", 
                       cwd=repo, env=env, creationflags=NO_WIN)
    if r.returncode != 0:
        return None
    return (repo, env)


def run(ctx, *args):
    repo, env = ctx
    cmd = ["git"] if env else ["git", "-C", repo]
    r = subprocess.run(cmd + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", 
                       cwd=repo if env else None, env=env, creationflags=NO_WIN)
    return r.returncode, r.stdout.strip()


def load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _win_stdin_ready(timeout=0.5):
    """MECH-HOOK-STDIN-WIN-1：nt 侧 stdin 可读探测，替代 select()（Win 上 select 只认
    socket，管道必抛 OSError 10038/10093，语义等价改造，宿主原生 python 实测 2026-11-03）。
    对齐 POSIX select 分支三态：
      FILE_TYPE_DISK(重定向文件)      → 恒可读（POSIX select 对 regular file 亦恒真），读至 EOF；
      FILE_TYPE_CHAR(tty/NUL 手跑)    → 不等，返回 False（=旧 select 超时走兼容档）；
      FILE_TYPE_PIPE(钩子喂 JSON)     → PeekNamedPipe 轮询 0.5s 有字节才 True（空管道不等，
                                        与 select 短超时同语义）；
      句柄异常/API 失败/非 nt         → False=降级旧 --owner 档。hook 永不炸。"""
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.WinDLL("kernel32")
        k32.GetStdHandle.argtypes = [ctypes.c_int]
        k32.GetStdHandle.restype = wt.HANDLE
        k32.GetFileType.argtypes = [wt.HANDLE]
        k32.GetFileType.restype = wt.DWORD
        h = k32.GetStdHandle(-10)                       # STD_INPUT_HANDLE
        if not h or h == ctypes.cast(wt.HANDLE(-1), wt.HANDLE).value:
            return False
        ft = k32.GetFileType(h)
        if ft == 1:                                     # FILE_TYPE_DISK
            return True
        if ft != 3:                                     # 2=CHAR(tty/NUL) / 0=UNKNOWN
            return False
        avail = wt.DWORD(0)
        k32.PeekNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                      ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
                                      ctypes.POINTER(wt.DWORD)]
        k32.PeekNamedPipe.restype = wt.BOOL
        deadline = time.monotonic() + timeout
        while True:
            if (k32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None)
                    and avail.value > 0):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
    except Exception:
        return False


def hook_input():
    """MECH-S1 + REQ-EXIT-TIDY-1：从 CC Stop hook stdin 一次读齐三件——
    (session_id, stop_hook_active, hook_event_name)。旧版只捡 session_id 把另两件白白扔掉，
    散场判据(stop_hook_active=block 过仍要散)吃不到，才有「散场不收尾卡躺尸」。
    非阻塞探测(select 短超时)：手跑/tty/空 stdin 均不卡住；非 JSON/缺字段一律退回
    (None, False, None)=旧 --owner 兼容档，行为与现状完全一致。
    MECH-HOOK-STDIN-WIN-1：探测分宿主——POSIX 照旧 select；nt 走 _win_stdin_ready
    （select 在 Win 对管道抛 OSError 被下方 except 吞掉=sid 恒 None 的静默永久降级，
    这正是本卡修的 BUG），read 前把解码钉回 UTF-8（钩子契约=UTF-8 JSON，Windows 原生
    python 默认随本地码页 cp936，非 ASCII 载荷会 UnicodeDecodeError 再掉回降级档）。"""
    try:
        if os.name == "nt":
            if sys.stdin is None or not _win_stdin_ready():
                return None, False, None
            try:
                sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass                                     # 尽力而为，失败按原码页读
            raw = sys.stdin.read()
        else:
            if not select.select([sys.stdin], [], [], 0.5)[0]:
                return None, False, None
            raw = sys.stdin.read()
    except (OSError, ValueError):
        return None, False, None
    if not raw or not raw.strip():
        return None, False, None
    try:
        j = json.loads(raw)
    except ValueError:
        return None, False, None
    if not isinstance(j, dict):
        return None, False, None

    def _s(key):
        v = j.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    return _s("session_id"), bool(j.get("stop_hook_active")), _s("hook_event_name")


def _proc_ancestors():
    """REQ-EXIT-TIDY-1「锚 pid=本次会话起的进程」的现场判据：返回 (pid空间, 含自身的祖先 pid 集)。
    hook 进程由会话进程一路生出——族谱里查得到锚，才算本会话的账。
    win=Toolhelp32 快照建 pid→ppid 上走；wsl/linux=/proc/<pid>/stat 第 4 字段上走。
    任何取不到=(None, 空集)→调用方保守不交账：宁躺回 CLAIMED 等回收环，绝不猜、不偷账。"""
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes as wt

            class _PE(ctypes.Structure):
                _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                            ("th32ProcessID", wt.DWORD),
                            ("th32DefaultHeapID", ctypes.c_void_p),
                            ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                            ("th32ParentProcessID", wt.DWORD),
                            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                            ("szExeFile", ctypes.c_wchar * 260)]
            k32 = ctypes.WinDLL("kernel32")
            k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
            snap = k32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
            invalid = ctypes.cast(wt.HANDLE(-1), wt.HANDLE).value
            if snap is None or snap == invalid:
                return None, set()
            par = {}
            try:
                pe = _PE()
                pe.dwSize = ctypes.sizeof(_PE)
                k32.Process32First.restype = wt.BOOL
                k32.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(_PE)]
                k32.Process32Next.restype = wt.BOOL
                k32.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(_PE)]
                more = k32.Process32First(snap, ctypes.byref(pe))
                while more:
                    par[int(pe.th32ProcessID)] = int(pe.th32ParentProcessID)
                    more = k32.Process32Next(snap, ctypes.byref(pe))
            finally:
                k32.CloseHandle(snap)
            chain, pid, guard = set(), os.getpid(), 0
            while pid and pid not in chain and guard < 128:      # 环路/失控保险
                chain.add(pid)
                pid = par.get(pid, 0)
                guard += 1
            return "win", chain
        except Exception:
            return None, set()
    if os.path.isdir("/proc"):
        try:
            chain, pid, guard = set(), os.getpid(), 0
            while pid and pid not in chain and guard < 128:
                chain.add(pid)
                with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as f:
                    raw = f.read()
                # comm 可含空格/括号：从最后')'之后再切字段，第 2 项=ppid
                pid = int(raw[raw.rfind(")") + 1:].split()[1])
                guard += 1
            return "wsl", chain
        except Exception:
            return None, set()
    return None, set()


def _anchor_why(claim, meta, sid):
    """REQ-EXIT-TIDY-1 散场归属：这张 CLAIMED 卡的账是不是**本次会话**的？两证其一即成——
    ①认领已绑会话且 sid 同值（claim/bind 落过 session_id 的直证；无锚 pid 批也救得到，
      正是「回收环只能救带锚的」外延）；
    ②锚 pid 落在本进程祖先族谱且 pid 空间一致（hook=会话所生，族谱即本会话进程家谱）。
    证不成返回 ""——回原 block/NOTE 通道，绝不替别会话交账；跨 pid 空间/族谱取不到=保守不动
    （与 MECH-AUD1「不互猜 pid 空间」同理）。"""
    a = dict(claim or {})
    a.update(meta or {})                                          # 道锁 meta 优先（与 recycle 同序）
    csid = a.get("session_id") or "-"
    if sid and csid != "-" and csid == sid:
        return "session_id 同值(本会话认领)"
    try:
        pid = int(a.get("pid"))
    except (TypeError, ValueError):
        return ""
    if pid <= 0:
        return ""
    cns, chain = _proc_ancestors()
    if cns is None:
        return ""                                                 # 族谱取不到=证不成，保守
    rns = a.get("pid_ns") or cns                                  # 未声明=按本侧空间自判
    if rns != cns:
        return ""                                                 # 跨空间对不上号，不猜
    return "锚 pid=%s 在本进程族谱(%s)" % (pid, cns) if pid in chain else ""


def _exit_log(line):
    """REQ-EXIT-TIDY-1 交账痕双写：stdout（会话转录可见）+ logs/recycle.log（与 cron 回收
    同账本——业主判据「回收日志现 EXIT-RELEASE 行而非等 ZOMBIE」就落这里）。
    写不进只 WARN 不炸 hook：痕在 stdout 已留，判据不因此红。"""
    print(line)
    try:
        os.makedirs(os.path.join(TEAM, "logs"), exist_ok=True)
        with open(os.path.join(TEAM, "logs", "recycle.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), line))
    except OSError as exc:
        print("WARN: EXIT-RELEASE 落回收日志失败(%r)——stdout 痕已打，如实降级" % exc, file=sys.stderr)


def exit_release(cid, card, claim, meta, why):
    """REQ-EXIT-TIDY-1 散场自查交账：经 cards_dag 写闸 release（与 recycle.do_release 同通道，
    写锁/双锁清/回队头/DAG 刷新全在库闸内）——不 done 不伪交付，只是把道交还队列等重派。
    release 失败=账本通道本身坏了：WARN 留痕后放行退出（block 一个正在散场且自己交不了账的
    会话只会空转，残局回收环照旧兜底），绝不伪称成交。"""
    _exit_log("EXIT-RELEASE %s: owner=%s 散场自查交账(%s) — Stop 已拦过仍要散/SessionEnd，"
              "卡仍 CLAIMED，release 回 OPEN 等重派（不 done 不伪交付）"
              % (cid, card.get("owner"), why))
    buf = io.StringIO()
    rc, out = 0, ""
    try:
        from ccards import cli as _cli
        with contextlib.redirect_stdout(buf):
            _cli.run(["release", cid, "--who", "exit-hook"])
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:
        rc = 1
    out = buf.getvalue().strip()
    if rc != 0:
        _exit_log("EXIT-RELEASE-FAIL %s: release rc=%s %s → 放行退出，残局交 recycle/PM（不伪成交）"
                  % (cid, rc, out or "(无输出)"))
        return False
    _exit_log("EXIT-RELEASE-DONE %s: %s" % (cid, out or "OPEN"))
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--owner", required=True)
    p.add_argument("--repo", default="")
    p.add_argument("--notes", default="")
    a = p.parse_args()

    sid, stop_active, hook_ev = hook_input()  # sid=None=旧 --owner 泳道名模式（非 JSON/空 stdin 兼容）
    # REQ-EXIT-TIDY-1 散场档：stop_hook_active=True=上次 Stop 已被钩子拦过、会话仍要散
    # （block 循环尽头，再拦只会空转）；SessionEnd=会话终结钩（挂哪个事件都吃）。
    # **首次** Stop 不吃散场档——第一道闸照旧「完成/done」教育，不缴自己人的在制卡。
    exit_attempt = bool(stop_active) or hook_ev == "SessionEnd"

    ctx = git_ctx(a.repo) if a.repo else None
    if a.repo and ctx is None:
        print("WARN: --repo %s git 读不通(Windows worktree 路径/非仓)，本轮跳过 commit 相关判据"
              % a.repo, file=sys.stderr)

    # Pass1: 收口结账（v3 运行时对象：**道锁优先**——state/lane-<owner>.lock/card 直读
    # 「本泳道在跑哪张卡」，不再扫 claims 反查）。道锁命中即只结该卡；道锁陈旧/缺失 →
    # 自动退回 claims 扫描（自愈，派生视图坏了不致误放/误锁）。MECH-S1 会话校验两档同效：
    # 异会话卡 NOTE 放行交 recycle/PM。REQ-EXIT-TIDY-1：异会话 NOTE 在前——本会话的散场
    # 自查绝不碰别人账；本会话卡+散场档=当场 release 交账（成败都放行，见 exit_release）。
    def judge(cid, card, meta=None):
        claim = load_json(os.path.join(CLAIMS, cid, "claim.json")) or {}
        csid = ((meta or {}).get("session_id") or claim.get("session_id") or "-")
        if sid and csid != "-" and csid != sid:
            print("NOTE: 卡 %s 认领绑在异会话(sid=%s，旧会话残渣?)——本次退出不拦，"
                  "交 recycle/PM 收口" % (cid, csid))
            return
        if exit_attempt:
            why = _anchor_why(claim, meta, sid)
            if why:
                exit_release(cid, card, claim, meta, why)
                return
        found = ""
        if ctx:
            rc, out = run(ctx, "log", "--all", "--grep", cid, "--oneline", "-1")
            if rc == 0:
                found = out
        if found:
            block("你名下卡 %s 已有对应 commit(%s)，但状态仍是 CLAIMED——"
                  "先跑 cards_dag.py done %s --owner %s --commit <hash> --repo %s 再退出"
                  % (cid, found, cid, a.owner, a.repo))
        block("你名下卡 %s (%s) 还是 CLAIMED——完成它并 cards_dag.py done，"
              "或向 inbox 报告阻塞后由 PM release 回收，禁止裸退"
              "（确收不了口/会话要散：再停一次即触发散场自查 release 交账，EXIT-RELEASE 留痕）"
              % (cid, card.get("title", "")))

    handled = False
    ld = os.path.join(STATE, "lane-%s.lock" % re.sub(r"[^A-Za-z0-9_.-]", "_", a.owner))
    if os.path.isdir(ld):  # v3 道锁目录
        cid = ""
        try:
            cid = open(os.path.join(ld, "card"), encoding="utf-8").read().strip()
        except OSError:
            cid = ""
        meta = load_json(os.path.join(ld, "meta.json")) or {}
        card = _peek(cid) if cid else None
        if card and card.get("status") == "CLAIMED" and card.get("owner") == a.owner:
            judge(cid, card, meta)  # 道锁命中：只结这张账
            handled = True
        # 陈旧道锁(卡已 DONE/属主不符/卡没了) → 落入反查兜底；reindex 会清残渣
    if not handled and os.path.isdir(CLAIMS):
        for cid in sorted(os.listdir(CLAIMS)):
            claim = load_json(os.path.join(CLAIMS, cid, "claim.json"))
            card = _peek(cid)
            if not claim or not card:
                continue
            if claim.get("owner") != a.owner or card.get("status") != "CLAIMED":
                continue
            judge(cid, card)

    # Pass2: 本泳道 DONE 未验收卡的 commit 比笔记新 → 必须先沉淀再退（v2 改版后才真正生效）
    if a.notes and ctx:
        newest = 0
        for c in _cards().values():
            if not c or c.get("owner") != a.owner or c.get("status") != "DONE" or not c.get("commit"):
                continue
            rc, ct = run(ctx, "show", "-s", "--format=%ct", c["commit"])
            if rc == 0 and ct:
                try:
                    newest = max(newest, int(ct))
                except ValueError:
                    pass
        if newest:
            if not os.path.exists(a.notes):
                block("本泳道有 DONE 未验收的 commit 但笔记 %s 不存在——先 "
                      "cards_dag.py note --owner %s --text \"...\" 沉淀再退" % (a.notes, a.owner))
            if os.path.getmtime(a.notes) < newest:
                block("你名下卡 DONE 但最新提交晚于笔记修改时间——先 cards_dag.py note --owner %s "
                      "--text \"...\" 沉淀本轮教训(≤10行,只要复现过的)再退出" % a.owner)
    sys.exit(0)


if __name__ == "__main__":
    main()
