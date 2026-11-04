#!/usr/bin/env python3
# mergetrain.py — REQ-MERGE-TRAIN-1 合并火车（规格 v3，业主 2026-11-02 20:0x 定版次序
# 「开发完→先校验→通过→等待合入」+ PM 补队列内复验防时间差）。挂在巡逻环 recycle.py 上跑：
#   ①**交卷即验**：卡 DONE 后巡逻环立刻在本泳道复验（宿主钉死 CARD_VERIFY_BASH，复用
#     REDAUTO 链的 run_argv/looks_host_mismatch/recorded_host 分流）；红→FAIL 归因+REJECT
#     退回返工（不占合并资源，沙箱不起）；绿→卡面试验状态记 passed（events 痕
#     stage=passed@commit，辅账 state/merge-train.json）。
#   ②**合并队列**：passed 卡按交工先后排队；队内逐张对「最新主干 + 本卡」做**试合沙箱复验**
#     （轻量三件：py_compile 改动 .py + 本卡判据 + 改动面锁=tests/select.sh 所选套件）；
#     绿→自动 `merge --no-ff` 真并线并走 cmd_verdict PASS 落 VERIFIED
#     （REQ-MERGE-GATE-1 终态断言在合入后自动满足，通道保留不旁路）；
#     红（主线已变导致）→打回①重验，归因「队列复验FAIL@新主干」。
#   ③干跑 `merge-tree --write-tree` 有冲突→integrations=blocked，看板亮「待合·撞车·龄Xm」，
#     **不自动拆**；②③滞留 >30min（MERGE_TRAIN_STALE_MIN）→报警行进聚合账。
#     **外合自动转正（MECH-BLOCKED-EXTMERGE-1，2026-11-03 实锤）**：queue_of 只纳 passed，
#     blocked 卡不进队列→线480 is_ancestor 让路分支永不可达；done-sweep 跳 HELD_STAGES（含
#     blocked/merged），旧文「交 done-sweep 转正」不成立——PM 把 blocked 卡外合后卡悬置
#     DONE、只能手工补章，>30min 还照冲 BLOCKED-STALE 错铃。修：辅账内 blocked/passed 卡
#     每圈探 is_ancestor，已在外线→外线 tip detach 沙箱复跑本卡判据（_run_verify 继承 cwd，
#     真并线判据复跑同式）→ cmd_verdict PASS 正式通道，转正过了才记 stage=merged(外线)，
#     零force零改哈希主干分毫不动；已外合卡不再冲「撞车滞留」行（转正失败另报 EXT-FAILED）。
#     **①面悬置形态补路（BUG-TRAIN-HOSTPIN-1 增补，PM 13:0x 现证）**：辅账缺账（机械清理/
#     refailed 出窗清账）或退避窗内 refailed 而交付已被外手进主干时，blocked 圈与 passed 队列
#     两条外合入口都扫不到=旧式「火车让路」裸 continue 实为悬置等人工（REQ-OSS pm_reflip）。
#     修：DONE 未合卡与辅账对表，上述形态同走 _ext_route→外合闭环（沙箱复验→verdict）。
#   ⑤**生效锁（版本钉）**：火车环每圈末比对常驻件「进程起时 vs 其命脉文件最后提交时刻」，
#     进程起时早于提交时=未通电→dispatch.log 报警行 NOT-ENERGIZED <件> <滞后分钟>，
#     白名单件自动 systemctl --user restart（业主 22:3x 裁定「修好≠生效，系统没生效才是
#     根因」——TRACE 留痕卡 20:04 交卷、派发器旧进程跑到 22:39 重启才通电，中间工人再死
#     零留痕）。看板 cc-status-dashboard 由 AUTORELOAD 热换**除外**；cron 巡逻环每分新进程
#     天然即时生效**不钉**。
# 纪律：零 force 零改哈希（合入只 merge --no-ff / update-ref 带旧值 CAS）；泳道活树只读不碰
#   （试合在独立沙箱 worktree，用完即删）；自动动作全走 git + 写闸痕（verdict/note 通道，
#   events 流水 who=mergetrain）。BUG-RECON-TAILRACE-1（2026-11-03）：补流水尾一律经
#   git_ev_now **落笔前现读卡面**（old=new=写闸现场态），禁环初快照硬笔 DONE->DONE——
#   外合转正/verifier 抢先等双通道交错下尾行恒与卡面自洽，reconcile 判据一不再长红。dry=只点名零写零合线（git 只走 rev-parse/merge-base 读探测，
#   merge-tree 干跑与沙箱只在 apply 起——同 done-sweep「dry 不起判据子进程」纪律的火车版）。
# 红绿锁：cc-team/tests/merge-train-check.py 五场景（干净卡全链自动转正 / 主线推进后先绿后红
#   必被队列复验拦住打回=时间差红绿锁 / 冲突卡必 blocked / 主干提交后不重启必检出、重启后
#   报警消 / blocked 卡 PM 外合自动转正 VERIFIED——针=is_ancestor 外合扫描，撤针必悬置），
#   各场景带撤针自证。流水尾自洽另立专锁 cc-team/tests/recon-tailrace-test.py
#   （BUG-RECON-TAILRACE-1：双通道交错复现 + 撤针必红 + reconcile 判据一不放宽静态钉）。
import json, os, re, subprocess, sys, tempfile, time
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

HERE = os.path.dirname(os.path.abspath(__file__))            # cc-team/ops
ROOT = os.path.dirname(HERE)                                  # cc-team 根
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

STATE_NAME = "merge-train.json"
TRAIN_LOCK = ".merge-train.lock"
SB_PREFIX = "cc-merge-train-"
WHO = "mergetrain"
# 火车持管态：这些卡的 DONE 复跑归火车管，done-sweep 旧路不重复跑（防双验/防主干闸空响）
HELD_STAGES = ("passed", "blocked", "merged", "refailed")


def _env_num(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


STALE_MIN = max(1.0, _env_num("MERGE_TRAIN_STALE_MIN", 30.0))       # ②③滞留报警阈（分钟）
RETRY_MIN = max(1.0, _env_num("MERGE_TRAIN_RETRY_MIN", 10.0))       # 队列复验红打回后的重验退避窗
VERIFY_BATCH = max(1, int(_env_num("MERGE_TRAIN_VERIFY_BATCH", 2)))  # 每圈①最多验几张（串行防环超时）
MERGE_BATCH = max(1, int(_env_num("MERGE_TRAIN_BATCH", 1)))           # 每圈②最多真并几张（串行防竞态）
STEP_TIMEOUT = int(_env_num("MERGE_TRAIN_STEP_TIMEOUT", 900))        # 沙箱单步超时（秒）
ENERGIZE_GRACE_MIN = max(0.0, _env_num("MERGE_TRAIN_ENERGIZE_GRACE_MIN", 1.0))  # 时钟抖动宽限
RESTART_COOLDOWN_MIN = max(1.0, _env_num("MERGE_TRAIN_RESTART_COOLDOWN_MIN", 10.0))

# REQ-CLAIM-COUNTER-1 R6 冲突超阈上报阈值（卡面终值，业主 seq=29 采建议值）：
#   同卡 conflict_count>C_CARD 触发（第 3 次解）；同文件簇 events 实探
#   CONFLICT-RESOLVED 行累计>C_CLUSTER 触发（第 4 次解，跨卡合计）。比较符均为 >（严格大于）。
C_CARD = 2
C_CLUSTER = 3

# 常驻件版本钉名册（生效锁对象；看板/巡逻环按卡面⑤明文豁免）。
# lifelines=命脉文件（仓库相对路径，git log 取最后提交时刻；目录=整树随迁）。
RESIDENT_UNITS = [
    # PARKED（业主 2026-11-03 令删 WSL）：cc-dispatch.service 系 WSL systemd user unit，
    # 发行版注销后无宿主，energizen 扫留空。Windows 侧周期职责全在 cc-ctrld 进程内环
    # （零 OS 定时器，docs/机制-全Windows切流-20261103.md §五）；新常驻岗按名册 ring 型
    # 锚登记，勿再引 systemd unit。命脉文件版本钉语义由 ctrld 自持进程承接。
]


def nowz():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def spath(team, name=STATE_NAME):
    return os.path.join(team, "state", name)


def load_state(team):
    try:
        st = json.load(open(spath(team), encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("cards"), dict):
            st.setdefault("restarts", {})
            return st
    except (OSError, ValueError):
        pass
    return {"cards": {}, "restarts": {}}


def save_state(team, st):
    d = os.path.join(team, "state")
    os.makedirs(d, exist_ok=True)
    st["updated"] = nowz()
    tmp = spath(team) + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, spath(team))


def train_lock_try(team):
    """火车防重跑（mkdir 原子 + pid 认领，同 SWEEP_LOCK 语义）：锁主死/无可读 meta 超 2min/
    超龄 40min → 接管；抢不到本轮让路（宁漏一拍不叠两拍——拍合并发会互踩沙箱）。"""
    lock = os.path.join(team, "state", TRAIN_LOCK)
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    for _ in (0, 1):
        try:
            os.mkdir(lock)
        except FileExistsError:
            try:
                age = (time.time() - os.path.getmtime(lock)) / 60.0
            except OSError:
                age = float("inf")
            pid = None
            try:
                pid = json.load(open(os.path.join(lock, "meta.json"), encoding="utf-8")).get("pid")
            except (OSError, ValueError):
                pass
            alive = False
            if pid:
                # BUG-FUSE-SELFKILL-1 同族雷：os.kill(pid,0) 在 Windows=CTRL_C 广播，
                # 探活会把活锁主当场处决再顺手夺锁。一律走权威判活件。
                try:
                    from ccards import config as _cfg
                    alive = bool(_cfg.pid_alive(int(pid)))
                except Exception:
                    alive = True                      # 导不到=保守活主，勿夺（40min 龄兜底仍在）
            if (pid and not alive) or (not pid and age >= 2.0) or age >= 40.0:
                import shutil
                shutil.rmtree(lock, ignore_errors=True)
                continue
            return False
        try:
            with open(os.path.join(lock, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "ts": nowz()}, f)
        except OSError:
            pass
        return True
    return False


def train_lock_release(team):
    import shutil
    shutil.rmtree(os.path.join(team, "state", TRAIN_LOCK), ignore_errors=True)


def git_ev(team, cid, old, new, extra):
    """写闸痕原语（events 流水 who=mergetrain）：落笔失败点名不静默（同 note_event 纪律）。
    ⚠️ BUG-RECON-TAILRACE-1（2026-11-03 实锤 MECH-TRAIN-ADR-1）：调用方拿环初快照硬写
    old/new 是**毒尾源头**——环初读卡到本笔落盘之间，verifier 通道（cmd_verdict/外合转正）
    可能已把卡面 DONE→VERIFIED，再补 DONE->DONE 就造成「卡面=VERIFIED 但流水尾=DONE」，
    reconcile_events 判据一持续红灯（每环+1，guard DEGRADED 无法 reset）。
    火车各处一律改走 **git_ev_now**（落笔前现读卡面）；本原语只留给显式同态场合。"""
    try:
        from ccards import events as _EV
        _EV.event(cid, old, new, WHO, " ".join((extra or "").split()))
    except Exception as exc:
        print("WARN: %s events 落笔失败(%r)" % (cid, exc))


def _face_status(cid):
    """现读卡面状态（old 值以写闸现场为准，同 cmd_verdict 进锁重载语义）；读不到=None 不猜。"""
    try:
        from ccards import graph as _G
        return (_G.all_cards().get(cid) or {}).get("status")
    except Exception as exc:
        print("WARN: %s 卡面现读失败(%r)" % (cid, exc))
        return None


def git_ev_now(team, cid, extra):
    """流水尾补笔唯一入口（BUG-RECON-TAILRACE-1）：落笔前现读卡面，old=new=现场态——
    ①卡面已 VERIFIED（verifier/外合通道抢先转正）记 VERIFIED->VERIFIED 自环，审计痕保留
      且尾行恒与卡面自洽；②卡不存在/坏账=响亮跳过不补（宁缺痕不毒尾，reconcile 幽灵判据
      也拒无卡事件，盲补只会制造第二种红）。失败不抛，同 git_ev 纪律。"""
    cur = _face_status(cid)
    if not cur:
        print("WARN: %s 卡面读不到（无卡/坏账）→ 流水尾不补笔（防毒尾）: %s" % (cid, extra))
        return
    git_ev(team, cid, cur, cur, extra)


# ---------- git 面（宿主无关：ccards.config.git 自带 win↔/mnt 路径互解） ----------

def main_ref(repo, git):
    """主干 ref 解析，与 _merge_gate 同式：CARD_MAINLINE 显式钉（配了找不到=None 宁红不猜）
    → main→master→trunk 首个存在者。"""
    env_main = (os.environ.get("CARD_MAINLINE") or "").strip()
    refs = [env_main] if env_main else ["main", "master", "trunk"]
    for ref in refs:
        rc, _ = git(repo, "rev-parse", "--verify", "-q", ref)
        if rc == 0:
            return ref
    return None


def tip_of(repo, ref, git=None):
    if git is None:
        from ccards.config import git as _G
        git = _G
    rc, out = git(repo, "rev-parse", "--verify", "-q", "%s^{commit}" % ref)
    return out.strip() if rc == 0 else None


def is_ancestor(repo, commit, ref, git=None):
    """True=已在主干（①面缺账/refailed 悬置形态经 _ext_route 走外合闭环自动转正——
    BUG-TRAIN-HOSTPIN-1 增补；旧口径「火车不管交 PM 转正」=REQ-OSS 悬置现证已废）；
    False=未合入；None=查不了（不猜）。"""
    if git is None:
        from ccards.config import git as _G
        git = _G
    rc, _ = git(repo, "merge-base", "--is-ancestor", commit, ref)
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def merge_tree_probe(repo, ref, commit, git):
    """③ 干跑 merge-tree --write-tree：rc0=干净可合（tree=合并树 OID）；rc1=冲突
    （conflicts=冲突文件清单）；其余=探测失败（宁红不猜：不 merge）。零改哈希零落 refs。"""
    rc, out = git(repo, "merge-tree", "--write-tree", ref, commit)
    if rc == 0:
        tree = (out.splitlines() or [""])[0].strip()
        return {"ok": True, "clean": True, "tree": tree, "conflicts": [], "rc": rc}
    if rc == 1:
        parts = out.split("\n\n", 1)
        tree = (parts[0].splitlines() or [""])[0].strip()
        files = [l.strip() for l in (parts[1].splitlines() if len(parts) > 1 else []) if l.strip()]
        return {"ok": True, "clean": False, "tree": tree, "conflicts": files, "rc": rc}
    return {"ok": False, "clean": False, "tree": "", "conflicts": [], "rc": rc}


def changed_files(repo, ref, commit, git):
    """改动面=本卡相对 merge-base 带来的文件（三点 diff），供 py_compile/改动面锁点单。"""
    rc, out = git(repo, "diff", "--name-only", "%s...%s" % (ref, commit))
    if rc != 0:
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def main_worktree(repo, ref, git):
    """主干 ref 当前挂在哪个工作树（worktree list --porcelain 找 branch==refs/heads/<ref>）。
    找不到（主干没被任何树签出）=None→真并走 update-ref CAS 通道。"""
    rc, out = git(repo, "worktree", "list", "--porcelain")
    if rc != 0:
        return None
    cur, want = "", "refs/heads/" + ref
    for ln in out.splitlines():
        if ln.startswith("worktree "):
            cur = ln[len("worktree "):].strip()
        elif ln.strip() == "branch " + want:
            return cur
    return None


# ---------- 沙箱（试合复验面：独立 worktree，用完即删，不碰泳道活树/主干工作树） ----------

def sandbox_materialize(repo, tree, main_sha, card_sha, cid, git, log):
    """合并树→临时提交（commit-tree 双亲=干净 --no-ff 形）→ detach worktree 落沙箱目录。
    返回 (dir, sha) 或 (None, 原因)。"""
    rc, sha = git(repo, "commit-tree", tree, "-p", main_sha, "-p", card_sha,
                  "-m", "MERGE-TRAIN sandbox %s: %s+%s" % (cid, main_sha[:8], card_sha[:8]))
    sha = (sha or "").strip()
    if rc != 0 or not re.match(r"^[0-9a-f]{40}$", sha):
        return None, "commit-tree 失败 rc=%s" % rc
    root = os.environ.get("MERGE_TRAIN_TMPDIR") or tempfile.gettempdir()
    path = tempfile.mkdtemp(prefix=SB_PREFIX, dir=root)
    # mkdtemp 已建目录：git worktree add 要目标不存在——挪到下一层
    target = os.path.join(path, "wt")
    rc, out = git(repo, "worktree", "add", "--detach", target, sha)
    if rc != 0:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
        return None, "worktree add 失败 rc=%s tail=%s" % (rc, _tail(out))
    return path, sha


def sandbox_external(repo, sha, cid, git):
    """外合转正沙箱：主干 tip 直接 detach worktree（外合后内容=判据复跑的事实面）。
    不 commit-tree 不造新提交——零改哈希（外线已是那个 tip）。用完即删同 sandbox_cleanup。"""
    root = os.environ.get("MERGE_TRAIN_TMPDIR") or tempfile.gettempdir()
    path = tempfile.mkdtemp(prefix=SB_PREFIX, dir=root)
    target = os.path.join(path, "wt")
    rc, out = git(repo, "worktree", "add", "--detach", target, sha)
    if rc != 0:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
        return None, "worktree add 失败 rc=%s tail=%s" % (rc, _tail(out))
    return path, sha


def sandbox_cleanup(repo, path, git):
    import shutil
    if not path:
        return
    git(repo, "worktree", "remove", "--force", os.path.join(path, "wt"))
    git(repo, "worktree", "prune")
    shutil.rmtree(path, ignore_errors=True)


def _tail(s, n=160):
    return " ".join((s or "").split())[-n:]


def _run(cmd, cwd=None, to=None, env=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=to or STEP_TIMEOUT, creationflags=NO_WIN)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss" % (to or STEP_TIMEOUT)
    except OSError as exc:
        return None, "exec failed: %r" % exc


def py_compile_step(root, files):
    """沙箱复验步一：本卡带入的改动 .py 全部 py_compile（集成面最低闸——假绿符号/语法炸口）。
    删除/缺文件的改动项跳过（不存在=无需验）；无可编译件=绿（空面不装红）。"""
    targets = [os.path.join(root, "wt", *(f.split("/"))) for f in files
               if f.endswith(".py") and not f.startswith("/")]
    targets = [p for p in targets if os.path.isfile(p)]
    if not targets:
        return 0, "py_compile: 无改动 .py（空面）"
    rc, out = _run([sys.executable, "-I", "-m", "py_compile"] + targets, cwd=root, to=120)
    return (0 if rc == 0 else (rc or 1)), ("py_compile rc=%s %s" % (rc, _tail(out)))


EXIT_KEYS = ("PYTHONIOENCODING", "PYTHONUTF8")


def judge_env():
    """判据/套件子进程环境出口钉 UTF-8（BUG-TRAIN-HOSTPIN-1②）：**硬覆盖**不保继承——
    setdefault 式会放行 gbk/cp936 毒值=赌 locale，不赌。火车收口定义在此（scope_env 基座
    直用）；ctrld.child_env 为同钉第二面（两文件域平权，parity 由 train-hostpin-test T4e
    红绿锁钉死，一面漂移=锁红）。病根：fail_detail 原文直落 events.log——内层 cp936 吐字节
    被读侧 utf-8/replace 解码，U+FFFD+希伯来段落账不可逆（12:22:49 REQ-OSS 复验红行实锤）。
    边界：`python -I` 吞全部 PYTHON* 环境变量（本机实锤 -I→gbk），该形态脚本出口归各脚本
    入口 reconfigure（GBKPRINT 推广面，本函数不覆盖）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _env_pin_exit():
    """进程内 os.environ 出口窗口钉（BUG-TRAIN-HOSTPIN-1② 落步①②的形态）：硬钉 EXIT_KEYS
    两键，返回复原 callable（调用方 try/finally 用完即还）。为何窗口而不加 run_argv env 参：
    run_argv 是 REDAUTO 共享链签名（recycle 与 recon-tailrace 等他人卡假件同形），加参=越出
    本卡 files 域的连带改面（done 闸拒收实录）——子进程 spawn 继承 os.environ，边界窗口与
    env= 显式传参在判据落盘面等效，零扰动共享签名。火车环=ctrld 单起子进程单线程跑，
    窗口期毫秒级 finally 必复原零残留；方向只钉 utf-8 永不钉毒。"""
    saved = {k: os.environ.get(k) for k in EXIT_KEYS}
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"

    def restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return restore


def card_verify_step(root, verify, recycle_mod):
    """沙箱复验步二：本卡判据在合并后内容里重跑——宿主解析与步③/闸 _run_verify 同源
    （BUG-TRAIN-HOSTPIN-1①：旧式裸 getenv("CARD_VERIFY_BASH","bash") 在 env 缺位时于 win
    任务 PATH 解析到 System32 WSL 启动器，双态判据全上毒宿主=判定链污染（12:22:49 现证
    '/mnt/c' 探针+目录名漂移三连坐）；收口进 _scope_bash→ccards._resolve_verify_bash 宁红
    不猜后，缺位=None 响亮红进队列 FAIL 归因，绝不裸宿主跑判据）。出口钉 UTF-8=②：
    _env_pin_exit() 边界窗口（run_argv 继承面，共享链签名零扰动）。cwd=沙箱树（主干新内容+本卡）。"""
    if not verify:
        return 1, "本卡判据缺失（队列不放行空面——空检查比没有检查更糟）"
    bashbin, berr = _scope_bash()
    if berr:
        return None, "本卡判据 " + berr
    restore = _env_pin_exit()
    try:
        rc, out = recycle_mod.run_argv([bashbin, "-c", verify], STEP_TIMEOUT)
    finally:
        restore()
    return rc, _tail(out or "")


def _scope_bash():
    """改动面锁宿主解析（MECH-TRAIN-ADR-1）：与判据步同式复用 ccards._resolve_verify_bash，
    不造第三形态。病根实锤（6 笔火车实录同签名）：旧实现裸 `bash` 在 win 任务 PATH 解析到
    C:\\Windows\\System32\\bash.exe=WSL 桥，而看板等活依赖已迁 win（MECH-WIN-ALL-1）——
    套件在毒宿主上 selftest(WSL 无 node 设计必红)+adversary(判据四探不到 :8790 → 5 条对账
    断言连坐)双红=一切干净卡恒打回，火车形同虚设。die(SystemExit) 转本步红（守卫宁红不猜、
    不回落裸 bash；报错原文经 stderr 捕获带回归因）。"""
    import contextlib
    import io
    buf = io.StringIO()
    try:
        from ccards.commands import _resolve_verify_bash
        with contextlib.redirect_stderr(buf):
            return _resolve_verify_bash(), None
    except SystemExit:
        return None, "宿主钉死失败: %s" % _tail(buf.getvalue().replace("\n", " "))
    except Exception as exc:
        return None, "宿主钉死异常: %r" % exc


def scope_env(root):
    """改动面锁子环境（MECH-TRAIN-ADR-1 三钉，全部落在本圈沙箱 root 下，逐圈唯一）：
    ①CC_CODE_HOME=沙箱 wt——被测代码=本卡合并内容；旧行为裸继承，套件按 /mnt/c 缺省读
      主仓**工作树**，测的根本不是待合物（PM 并发改树=结果随机）。
    ②套件沙箱目录逐圈隔离（CC_ADR_HOME/CC_TEST_HOME/CC_RECGUARD_* 等）——旧硬编码
      /tmp/cc-adr 全家共享，火车与泳道 worker 并跑互相 rm -rf（实锤：独占两跑各见
      21/14 种污染签名；win 侧 12 连红全是陈旧态串染）。
    ③CCSUITE_DIR=沙箱 root——all.sh 汇总日志面同圈隔离，两路 all.sh 并跑不再互踩。"""
    # win 侧反斜杠串会被 bash 当转义、cd/mkdir 不认，全部正写（两宿通吃，POSIX 值逐字不变）
    fwd = lambda p: p.replace("\\", "/")
    wt = fwd(os.path.join(root, "wt"))
    root = fwd(root)
    env = judge_env()          # 基座含出口钉 UTF-8（BUG-TRAIN-HOSTPIN-1②，与步①②同收口单源）
    env.update({
        "CC_CODE_HOME": wt,
        "CC_TEAM_SRC": fwd(os.path.join(wt, "cc-team")),
        "CC_ADR_HOME": fwd(os.path.join(root, "adr", "home")),
        "CC_ADR_REPO": fwd(os.path.join(root, "adr", "repo")),
        "CC_TEST_HOME": fwd(os.path.join(root, "st", "home")),
        "CC_TEST_REPO": fwd(os.path.join(root, "st", "repo")),
        # selftest 个案草稿/子沙箱根（MECH-TRAIN-ADR-1② 补刀：100+ 个 /tmp 固定名并跑互踩）
        "CC_ST_TMP": fwd(os.path.join(root, "st", "tmp")),
        "CC_RECGUARD_HOME": fwd(os.path.join(root, "guard", "home")),
        "CC_RECGUARD_REPO": fwd(os.path.join(root, "guard", "repo")),
        "CCSUITE_DIR": fwd(os.path.join(root, "ccsuite")),
    })
    return env


def scope_lock_step(root, changed, run=None):
    """沙箱复验步三：改动面锁=tests/select.sh 按本卡改动点单的套件（FULL/PARTIAL/NONE 协议）。
    select.sh 不在（非 cc-team 形态仓）=SCOPE-SKIP 可见行，不装绿。
    宿主=判据步同钉（_scope_bash，绝不裸 bash 走 WSL 桥）；环境=scope_env(root) 三钉。"""
    run = run or _run
    sel = os.path.join(root, "wt", "cc-team", "tests", "select.sh")
    if not os.path.isfile(sel):
        return 0, "SCOPE-SKIP(无 select.sh 面)"
    bashbin, berr = _scope_bash()
    if berr:
        return 1, "改动面锁 " + berr
    env = scope_env(root)
    # 相对路径起 bash：Windows 反斜杠绝对路径会被 /bin/bash 当转义串吃（实锤 23:4x 本锁 S1 首跑
    # rc=127，报错里盘符与主目录名连写成一片），cwd 钉在 wt/cc-team 后用 tests/select.sh 两宿通用。
    rc, out = run([bashbin, "tests/select.sh"] + list(changed),
                  cwd=os.path.join(root, "wt", "cc-team"), env=env)
    if rc != 0:
        return rc or 1, "select.sh rc=%s %s" % (rc, _tail(out))
    line = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
    if line.startswith("NONE"):
        return 0, "改动面锁=%s" % line
    if line.startswith("FULL:"):
        cmds = [line.split(":", 1)[1].strip()]
    elif line.startswith("PARTIAL:"):
        cmds = [c.strip() for c in line.split(":", 1)[1].split(";") if c.strip()]
    else:
        return 1, "select.sh 协议不认识: %s" % _tail(line)
    for c in cmds:
        rc, out = run([bashbin, "-c", c], cwd=os.path.join(root, "wt", "cc-team"), env=env)
        if rc != 0:
            return rc or 1, "改动面锁 %s rc=%s %s" % (c, rc, _tail(out))
    return 0, "改动面锁绿（%s）" % "+".join(cmds)


# ---------- ① 交卷即验 ----------

def _ext_route(g, cid, rec, c, repo, ref, team, apply, log, recycle_mod, git, state=None):
    """增补共用出口（BUG-TRAIN-HOSTPIN-1 增补，PM 13:0x 现证）：DONE 未合卡、交付已在主干、
    辅账又不在外合扫描两入口面（blocked 圈/passed 队列皆扫不到缺账/refailed 形态）→ 纳入
    _ext_merge_closure 同一闭环，不得悬置等人工。缺账时 apply 下补锚：转正过=stage=merged(外线)
    落账可见，未过=留痕下圈重试（closure 内 EXT-PEND/EXT-SB-FAIL 可见行现成，不另冲铃）；
    dry=零写同环纪律。计数：ok→extmerged+1，fail→pending+1。撤修复哨=本缝打桩 no-op，
    卡必悬置零转正（train-hostpin-test T5 双相实证）。"""
    if apply and state is not None and cid not in state["cards"]:
        state["cards"][cid] = rec                    # 缺账补锚（转正成败都留账面痕迹）
    res = _ext_merge_closure(cid, rec, c, repo, ref, team, apply, log, recycle_mod, git)
    if res == "ok":
        g["extmerged"] = g.get("extmerged", 0) + 1
    elif res == "fail":
        g["pending"] += 1
    return res


def stage1_verify(cards, state, team, apply, log, recycle_mod, git=None):
    """DONE 未合入卡按交工先后立刻本泳道复验。绿=记 passed（events stage=passed@commit）；
    真红=REJECT 退回返工（不占合并资源）；环境性红=pending 可见不驳（REDAUTO-HOST-1 分流
    原样复用，链不是自造）。返回 {"passed":n,"rejected":n,"pending":n,"seen":n,
    "extmerged":n（增补：①面对表后外合转正数）}。"""
    from ccards.config import git as _G
    git = git or _G
    g = {"passed": 0, "rejected": 0, "pending": 0, "seen": 0}
    done = sorted(((cid, c) for cid, c in cards.items() if c.get("status") == "DONE"),
                  key=lambda x: str(x[1].get("done_at") or x[1].get("updated_at") or "") + str(x[0]))
    fresh = 0
    for cid, c in done:
        rec = state["cards"].get(cid)
        commit = (c.get("commit") or "").strip()
        repo = (c.get("repo") or "").strip()
        verify = (c.get("verify") or "").strip()
        if rec and rec.get("commit") == commit:
            stg = rec.get("stage")
            if stg in ("passed", "blocked", "merged"):
                continue                     # 火车已持管（②/③/终态）
            if stg == "refailed":
                ft = float(rec.get("fail_ts") or 0)
                if time.time() - ft < RETRY_MIN * 60.0:
                    # 增补：退避窗语义=等主线修复不空转**重验**；但交付已被外手整个进主干时
                    # 窗口悬置无出路（REQ-OSS pm_reflip 现证）。先探 is_ancestor：在外线即走
                    # 外合闭环（沙箱复验是终态动作非空转）；未外合/探不通维持旧退避零扰动。
                    refx = main_ref(repo, git) if (repo and commit) else None
                    if refx and is_ancestor(repo, commit, refx, git) is True:
                        _ext_route(g, cid, rec, c, repo, refx, team, apply, log,
                                   recycle_mod, git, state)
                    continue                 # 窗内到此为止（外合已试/未外合），不落②重验
                state["cards"].pop(cid, None)  # 出窗清账重走①（交卷内容没变=同 commit 重验）
        if not commit or not repo:
            log("MERGE-TRAIN-SKIP %s: DONE 缺 repo/commit（火车无货可合，交 PM 补素材）" % cid)
            continue
        if not verify:
            log("MERGE-TRAIN-SKIP %s: 无判据不验不合（缺判据≠货坏）" % cid)
            continue
        ref = main_ref(repo, git)
        if not ref:
            log("MERGE-TRAIN-SKIP %s: 主干 ref 解析不到（repo=%s）——宁红不猜不合线" % (cid, repo))
            continue
        anc = is_ancestor(repo, commit, ref, git)
        if anc is None:
            log("MERGE-TRAIN-SKIP %s: is-ancestor 查不了（commit GC/repo 失效）" % cid)
            continue
        if anc is True:
            # 增补（BUG-TRAIN-HOSTPIN-1，PM 13:0x 现证）：走到此=辅账悬置形态（缺账——机械清理
            # 或 refailed 出窗清账；或账上 commit≠本次交卷）。旧式裸 continue 注释「done-sweep/
            # verdict 通道收口」自我破绽：done-sweep 只认账、外合转正只从 blocked 圈/passed 队列
            # 起，两入口都扫不到缺账/refailed=悬置等人工（REQ-OSS 12:5x 账 vanish、PM 手工补登）。
            # 修：纳入外合闭环同式（外线 tip 沙箱复验→cmd_verdict PASS 正式转正，未过下圈重试）。
            _ext_route(g, cid, {"commit": commit, "repo": repo}, c, repo, ref,
                       team, apply, log, recycle_mod, git, state)
            continue
        g["seen"] += 1
        if fresh >= VERIFY_BATCH:
            log("MERGE-TRAIN-WAIT %s: 本圈①批满（MERGE_TRAIN_VERIFY_BATCH=%d）排队下圈" % (cid, VERIFY_BATCH))
            continue
        fresh += 1
        if not apply:
            log("MERGE-TRAIN-VERIFY %s: (dry)会交卷即验 verify=[%s]" % (cid, verify[:60]))
            continue
        # BUG-TRAIN-HOSTPIN-1①：宿主解析收口进 _scope_bash（与步②③/闸 _run_verify 同源）——
        # 旧式裸 getenv("CARD_VERIFY_BASH","bash") 缺位时在 win 任务 PATH 命中 System32 WSL
        # 启动器，双态判据上毒宿主=假红污染判定链。缺位=响亮 pending（环境性红不驳卡，
        # 同 REDAUTO-HOST-1②分流），绝不带裸宿主起判据子进程。
        bashbin, berr = _scope_bash()
        if berr:
            g["pending"] += 1
            detail = "交卷即验 " + berr
            log("MERGE-TRAIN-PENDING %s: %s——宿主钉死缺位，杜绝裸 bash 回退（BUG-TRAIN-HOSTPIN-1①），不驳先喊" % (cid, detail))
            git_ev_now(team, cid, "待验收: %s" % detail)
            continue
        prev_cwd, cw = None, recycle_mod.card_cwd(c)
        if cw:
            try:
                prev_cwd = os.getcwd()
                os.chdir(cw)
            except OSError:
                prev_cwd = None
        try:
            restore = _env_pin_exit()      # ② 出口窗口钉（run_argv 继承面，签名零扰动）
            try:
                rc, out = recycle_mod.run_argv([bashbin, "-c", verify],
                                               int(os.environ.get("CARD_VERIFY_TIMEOUT", "600")))
            finally:
                restore()
        finally:
            if prev_cwd:
                os.chdir(prev_cwd)
        if rc == 0:
            _rec = {"stage": "passed", "commit": commit, "repo": repo,
                    "passed_at": nowz(), "passed_ts": time.time()}
            _old = state["cards"].get(cid) or {}
            if _conflict_trace(_old):
                # R5 撞车痕随交接携带（REQ-CLAIM-COUNTER-1）：blocked 卡换提交重交后旧辅账被
                # 整体替换，不带 conflict_seen=痕随旧账死、真并线路永不可计。外线路不受影响
                # （rec 不被替换，blocked 痕直达 merged 落地）。conflict_files 随行供簇键归属。
                _rec["conflict_seen"] = True
                if _old.get("conflict_files"):
                    _rec["conflict_files"] = _old["conflict_files"]
            state["cards"][cid] = _rec
            git_ev_now(team, cid, "stage=passed@%s" % commit[:12])
            log("MERGE-TRAIN-PASSED %s: 交卷即验绿@%s → 入候合队列" % (cid, commit[:12]))
            g["passed"] += 1
            continue
        detail = "本机rc=%s via=%s tail=[%s]" % (rc, recycle_mod.via_host(), recycle_mod.flat_tail(out))
        if rc == 124:
            log("MERGE-TRAIN-TIMEOUT %s: 交卷复验超时无定论 → 下圈重判（不判不驳）" % cid)
            continue
        if rc is None or recycle_mod.looks_host_mismatch(rc, out):
            g["pending"] += 1
            log("MERGE-TRAIN-PENDING %s: %s 环境性失败未排除（REDAUTO-HOST-1②），不驳先喊" % (cid, detail))
            git_ev_now(team, cid, "待验收: %s" % detail)
            continue
        if recycle_mod.do_verdict(cid, "REJECT", "交卷即验FAIL: %s" % detail):
            g["rejected"] += 1
            state["cards"].pop(cid, None)
            log("MERGE-TRAIN-FAIL %s: 交卷FAIL归因[%s] → 退回返工（不占合并资源）" % (cid, detail))
        else:
            log("MERGE-TRAIN-GATE-RED %s: REJECT 被闸拒(执行窗竞态)→ 本轮不动账，下圈再判" % cid)
    return g


# ---------- ② 合并队列 + ③ 撞车 blocked ----------

def queue_of(cards, state):
    """候合队列：passed 且卡仍在 DONE 的，按交工（passed_at）先后排序。返回 [(cid, 位序1起, 龄分)]。"""
    items = []
    for cid, rec in state["cards"].items():
        if rec.get("stage") != "passed":
            continue
        c = cards.get(cid)
        if not c or c.get("status") != "DONE":
            continue
        pt = float(rec.get("passed_ts") or 0)
        items.append((cid, pt if pt else time.time()))
    items.sort(key=lambda x: (x[1], x[0]))
    now = time.time()
    return [(cid, i + 1, (now - pt) / 60.0) for i, (cid, pt) in enumerate(items)]


# ---------- REQ-CLAIM-COUNTER-1 R5/R6 · 冲突计数落定与超阈上报 ----------

def _conflict_trace(rec):
    """未清撞车痕=账面 stage/integrations=blocked 在案，或交接口 conflict_seen 随行
    （blocked→换提交重交时旧辅账被整体替换，痕由 stage1 落 passed 新账携带；不带=痕随旧账死）。
    从未撞车直合卡=False（不计，R5 原文）；仍 blocked 不进本判定的计账时刻（只在 merged 落地查）。"""
    return bool(rec) and (rec.get("stage") == "blocked" or rec.get("integrations") == "blocked"
                          or bool(rec.get("conflict_seen")))


_CLUSTER_FILE_RE = re.compile(
    r"Merge conflict in ([^\s,]+)|Auto-merging ([^\s,]+)"
    r"|CONFLICT \((?:modify/delete|rename/delete|rename/rename)\): ([^\s,]+)")


def _cluster_key(rec):
    """簇键=conflict_files 记录值**归一化的文件名**升序 join（排序去重；跨卡同痕聚一簇）。
    实探修正（T7甲实锤）：stage2 登记的是 git merge-tree 原文摘取（probe["conflicts"]），
    形如「Auto-merging README.md,CONFLICT (content): Merge conflict in README.md」——
    整段当键=每卡每圈措辞漂移都聚不了簇。归一化只认三类稳定文件名句式（Merge conflict in X
    /Auto-merging X/CONFLICT (modify/delete 等): X），提不到文件名时退回裸清单形（记录值
    本就含空格/括号则弃=不落垃圾键）。无记录值（真并 merge 失败型 blocked 没探明文件清单）
    ="?"——同 stage2 撞车行 `files or "?"` 兜底形，仍按固定键聚合，不静默丢这类冲突。
    文件名类 [^\s,]+ 止于逗号（T7甲③实锤二）：记录值是 git 详述行**逗号 join** 的多段
    （「Auto-merging README.md,CONFLICT (content)...」），\S+ 形会把 "README.md,CONFLICT"
    整吞成一个假文件名 → 簇键污染、跨卡聚合失效。逗号在 join 形里是分界符，文件名内
    含逗号的病态仓库不做保证（存储形本身有歧义，conflict_files 保持原样不动）。"""
    raw = str((rec or {}).get("conflict_files") or "?")
    names = [next(g for g in m.groups() if g) for m in _CLUSTER_FILE_RE.finditer(raw)]
    if not names:
        toks = [p.strip() for p in raw.split(",") if p.strip()]
        names = [t for t in toks if " " not in t and "(" not in t and ")" not in t]
    parts = sorted({n for n in names if n})
    return ",".join(parts) or "?"


def _cluster_total(team, cluster):
    """簇累计=实探 events.log 同 cluster 键 CONFLICT-RESOLVED 行计数（跨卡合计；R6 原文
    「实探」——辅账会被 prune/换提交重交改写，events 是唯一 append-only 账）。
    cluster= 为行尾字段（git_ev extra 落最后一节，_san 已洗单行），键内无空格，等号后整段对键。"""
    p = os.path.join(team, "state", "events.log")
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return 0
    n = 0
    for ln in lines:
        if "CONFLICT-RESOLVED" not in ln or "cluster=" not in ln:
            continue
        if ln.split("cluster=", 1)[1].strip() == cluster:
            n += 1
    return n


def _conflict_closure(rec, cid, team, log):
    """R5 解冲突落定 + R6 超阈落键——只在 stage=merged 落地时刻调（真并线与
    _ext_merge_closure 外线转正两处）。有痕才动：清辅账撞车痕（integrations/blocked*/
    conflict_seen 随笔退场=同次撞车多圈重试只计一次，禁双计），events 打机读行
    MERGE-TRAIN-CONFLICT-RESOLVED card=<cid> cluster=<簇键>，conflict_count 经
    commands.bump_count 走 R1 同一写锁内原子通道（字段缺省 0、存量不回填=同 R3），
    超阈经 alerts.record 落 CONFLICT-THRESHOLD 键（fuse-S6 既有升级闭环：≥30min 唤 PM 班、
    ≥60min 直达 PM-REPLIES [待拍板]；键名禁业主/FUSE-S6 头=否则不升级静默丢报；零新建机制）。
    摘本调用=计数与报警同冻——T9 撤闸差分对（业主纪律：守卫要验过它红得起来）。"""
    if not _conflict_trace(rec):
        return 0
    cluster = _cluster_key(rec)
    for k in ("integrations", "blocked_at", "blocked_ts", "conflict_seen"):
        rec.pop(k, None)
    git_ev_now(team, cid, "MERGE-TRAIN-CONFLICT-RESOLVED card=%s cluster=%s" % (cid, cluster))
    log("MERGE-TRAIN-CONFLICT-RESOLVED %s: 撞车落定 cluster=%s" % (cid, cluster))
    n = 0
    try:
        from ccards import commands as _CMD
        n = _CMD.bump_count(cid, "conflict_count")  # 与 R1 同一机件：账本写锁内递增落账
    except SystemExit as exc:
        log("MERGE-TRAIN-CONFLICT-BUMP-FAIL %s: conflict_count 入账失败(%s)——事件行已落账，PM 据实另对"
            % (cid, exc))
    try:
        from ccards import alerts as _AL
        if n > C_CARD:
            _AL.record("CONFLICT-THRESHOLD card=%s conflict_count=%d>%d" % (cid, n, C_CARD))
        total = _cluster_total(team, cluster)
        if total > C_CLUSTER:
            _AL.record("CONFLICT-THRESHOLD cluster=%s total=%d>%d" % (cluster, total, C_CLUSTER))
    except Exception as exc:
        log("WARN: CONFLICT-THRESHOLD 落键失败(%r)——可见行与 events 已在场" % exc)
    return n


def _ext_merge_closure(cid, rec, c, repo, ref, team, apply, log, recycle_mod, git):
    """MECH-BLOCKED-EXTMERGE-1 外合自动转正（blocked 圈扫描与队列 passed 外合分支共用）：
    交付已被火车外手（PM/人工 merge）进主干（is_ancestor=True）→ 外线 tip detach 沙箱里
    复跑本卡判据（线573 同式：_run_verify 继承进程 cwd，判据位=外合后内容）→ cmd_verdict
    PASS 走正式通道（REQ-MERGE-GATE-1 在外线已天然满足，不旁路不裸改）。
    **转正过了才翻 stage=merged(外线)**——done-sweep 对 HELD_STAGES 让路（merged 也让），
    先翻账=把卡悬得更死；转正失败维持现 stage 下圈重试（判据红/沙箱起不来/闸重载拒都响亮）。
    dry=只点名零写（同环纪律）。返回 "ok"/"fail"/"dry"。"""
    commit = str(rec.get("commit") or (c or {}).get("commit") or "")
    if not apply:
        log("MERGE-TRAIN-EXT %s: (dry)交付已被外手合入 %s → 会起外线 tip 沙箱复跑判据 + "
            "verdict PASS 正式转正" % (cid, ref))
        return "dry"
    tip = tip_of(repo, ref, git)
    if not tip:
        log("MERGE-TRAIN-EXT-SKIP %s: 外线=%s 但 tip 取不到 → 不转不假账，下圈重试" % (cid, ref))
        return "fail"
    root, why = sandbox_external(repo, tip, cid, git)
    if not root:
        log("MERGE-TRAIN-EXT-SB-FAIL %s: 外线沙箱起不来(%s) → 不转正，主干与账分毫未动" % (cid, why))
        return "fail"
    try:
        prev_cwd = os.getcwd()
        try:
            os.chdir(os.path.join(root, "wt"))   # 判据复跑位=外线合并后内容（闸 _run_verify 继承 cwd）
            ok = recycle_mod.do_verdict(cid, "PASS",
                                        "合并火车外合自动转正@%s(外线, MERGE-TRAIN ring=train)" % tip[:12])
        finally:
            os.chdir(prev_cwd)
    finally:
        sandbox_cleanup(repo, root, git)
    if ok:
        rec.update({"stage": "merged", "merge_sha": "(外线)", "merged_at": nowz(),
                    "merged_ts": time.time()})
        git_ev_now(team, cid,
                   "stage=merged@%s(外线合入) → verdict PASS 转正" % commit[:12])
        _conflict_closure(rec, cid, team, log)  # R5/R6 外线转正落地=解冲突落定时刻（blocked 痕在场才递增+落事件+超阈键）
        log("MERGE-TRAIN-EXT-VERIFIED %s: 外线已含交付(tip %s) → 外线沙箱判据复跑绿 → "
            "VERIFIED（stage=merged(外线)，零force零改哈希）" % (cid, tip[:12]))
        return "ok"
    log("MERGE-TRAIN-EXT-PEND %s: 外线已含交付但 verdict PASS 未过（外线内容判据红/锁内重载拒）"
        "→ 辅账维持 %s 下圈重试" % (cid, rec.get("stage")))
    return "fail"


def _stale_alarm(team, log, key, line, apply=True):
    """滞留报警：可见行恒发；写聚合账只在 apply（dry=只点名不写，同 done-sweep 纪律）。
    key 稳定文案不带分钟（OPT1：报警键禁数字，防每圈新键刷屏）。"""
    log(line)
    if not apply:
        return
    try:
        from ccards import alerts as _AL
        _AL.record(key)
    except Exception as exc:
        log("WARN: 报警落聚合账失败(%r)——可见行已发" % exc)


def stage2_merge(cards, state, team, apply, log, recycle_mod, git=None, run=None):
    """队内逐张（每圈 ≤MERGE_BATCH）：干跑 merge-tree→撞车=blocked 不自动拆；干净=试合沙箱
    复验→绿=真并 merge --no-ff + verdict PASS 落 VERIFIED；红=打回①（归因「队列复验FAIL@新主干」）。
    滞留 >STALE_MIN 分钟（候合或撞车）→报警行。候合/撞车卡每圈探 is_ancestor：交付已被
    PM 外合入主干→外线沙箱判据复跑 + verdict PASS 自动转正（_ext_merge_closure，
    MECH-BLOCKED-EXTMERGE-1——已外合不再冲撞车铃，转正未过另亮 EXT-FAILED）。"""
    from ccards.config import git as _G
    git = git or _G
    n = {"merged": 0, "blocked": 0, "refail": 0, "stale": 0, "probed": 0, "extmerged": 0}
    q = queue_of(cards, state)
    for cid, pos, age in q:
        if age > STALE_MIN:
            n["stale"] += 1
            _stale_alarm(team, log,
                         "MERGE-TRAIN-QUEUE-STALE %s: 候合滞留超阈未并线——PM 查火车环通电/沙箱红因" % cid,
                         "MERGE-TRAIN-STALE %s: 候合队列第%d位·龄%.0fmin>%.0fmin → 报警行"
                         % (cid, pos, age, STALE_MIN), apply=apply)
    for cid, rec in state["cards"].items():                # ③ 撞车账龄 + 外合扫描（MECH-BLOCKED-EXTMERGE-1）
        if rec.get("stage") != "blocked":
            continue
        c = cards.get(cid)
        if not c or c.get("status") != "DONE":
            continue                    # 卡已离开待验收池：prune 随迁退场，不给幽灵冲铃
        repo, commit = rec.get("repo") or "", rec.get("commit") or ""
        ref = main_ref(repo, git) if (repo and commit) else None
        anc = is_ancestor(repo, commit, ref, git) if ref else None
        if anc is True:                 # PM 已在火车外合入：自动转正，不再冲「撞车滞留」错铃
            res = _ext_merge_closure(cid, rec, c, repo, ref, team, apply, log, recycle_mod, git)
            if res == "ok":
                n["extmerged"] += 1
            elif res == "fail":
                n["stale"] += 1
                _stale_alarm(team, log,
                             "MERGE-TRAIN-EXT-FAILED %s: 交付已外合入主干但自动转正未过——PM 查"
                             "（外线判据红/沙箱/闸拒，非撞车）" % cid,
                             "MERGE-TRAIN-STALE %s: 外线已合入而转正未过 → 报警行（每圈重试）"
                             % cid, apply=apply)
            continue
        if ref and anc is None:
            log("MERGE-TRAIN-SKIP %s: blocked 卡 is-ancestor 探测不可行（repo=%s）→ 只报龄不猜" % (cid, repo))
        bt = float(rec.get("blocked_ts") or 0)
        age = (time.time() - bt) / 60.0 if bt else 0.0
        if age > STALE_MIN:
            n["stale"] += 1
            _stale_alarm(team, log,
                         "MERGE-TRAIN-BLOCKED-STALE %s: 待合撞车滞留超阈未解——泳道需 rebase/PM 处置" % cid,
                         "MERGE-TRAIN-STALE %s: 待合·撞车·龄%.0fmin>%.0fmin → 报警行（不自动拆）"
                         % (cid, age, STALE_MIN), apply=apply)
    for cid, pos, age in q[:MERGE_BATCH]:
        if not apply:
            log("MERGE-TRAIN-QUEUE %s: (dry)队#%d·龄%.0fmin 会干跑merge-tree+试合沙箱复验" % (cid, pos, age))
            continue
        rec = state["cards"][cid]
        c = cards[cid]
        repo, commit = rec.get("repo") or "", rec.get("commit") or ""
        ref = main_ref(repo, git)
        tip0 = tip_of(repo, ref, git) if ref else None
        if not ref or not tip0:
            log("MERGE-TRAIN-SKIP %s: 主干 ref/tip 取不到（repo=%s）" % (cid, repo))
            continue
        anc = is_ancestor(repo, commit, ref, git)
        if anc is True:               # 有人在火车外合了：外线自动转正（旧文「交 done-sweep 转正」
            res = _ext_merge_closure(cid, rec, c, repo, ref, team, apply, log, recycle_mod, git)
            if res == "ok":
                n["extmerged"] += 1
            continue                  # 转正未过维持 passed，下圈再试（候合滞留铃如实响）
        probe = merge_tree_probe(repo, ref, commit, git)
        n["probed"] += 1
        if not probe["ok"]:
            log("MERGE-TRAIN-PROBE-ERR %s: merge-tree 干跑 rc=%s（git 形态不支持/仓异常）→ 不并"
                % (cid, probe["rc"]))
            continue
        if not probe["clean"]:                                     # ---- ③ 撞车 ----
            files = ",".join(probe["conflicts"][:6]) or "?"
            rec.update({"stage": "blocked", "integrations": "blocked",
                        "blocked_at": nowz(), "blocked_ts": time.time(),
                        "conflict_files": files})
            git_ev_now(team, cid, "integrations=blocked@%s 撞车文件=%s" % (commit[:12], files))
            log("MERGE-TRAIN-BLOCKED %s: 待合·撞车（干跑 merge-tree 冲突=%s）→ 不自动拆，等泳道/PM"
                % (cid, files))
            n["blocked"] += 1
            continue
        ch = changed_files(repo, ref, commit, git)
        root, sb_sha = sandbox_materialize(repo, probe["tree"], tip0, commit, cid, git, log)
        if not root:
            log("MERGE-TRAIN-SB-FAIL %s: 沙箱起不来(%s) → 本轮不并" % (cid, sb_sha))
            continue
        prev_cwd = None
        try:
            steps = [py_compile_step(root, ch)]
            vr, vt = 1, "沙箱未起"
            _pc = os.getcwd()
            try:
                os.chdir(os.path.join(root, "wt"))     # 判据同闸 _run_verify「继承 cwd」位=沙箱树
                vr, vt = card_verify_step(root, (c.get("verify") or "").strip(), recycle_mod)
            finally:
                os.chdir(_pc)
            steps.append((vr, vt))
            sr, stx = scope_lock_step(root, ch, run=run)
            steps.append((sr, stx))
            bad = [(i, rc, t) for i, (rc, t) in enumerate(steps) if rc not in (0,)]
            if bad:
                i, rc, t = bad[0]
                step_name = ("py_compile", "本卡判据", "改动面锁")[i]
                cur_now = tip_of(repo, ref, git)
                tip_txt = cur_now[:8] if cur_now else "?"
                if i == 1 and rc == 124:
                    log("MERGE-TRAIN-TIMEOUT %s: 沙箱判据超时 → 不判不驳，下圈重判" % cid)
                    continue
                rec.update({"stage": "refailed", "fail_ts": time.time(), "fail_at": nowz(),
                            "fail_step": step_name, "fail_detail": t})
                git_ev_now(team, cid,
                           "stage=refailed@%s 队列复验FAIL@新主干@%s[%s:%s]" % (commit[:12], tip_txt, step_name, t))
                log("MERGE-TRAIN-REQUEUE %s: 队列复验FAIL@新主干@%s（步=%s rc=%s tail=[%s]）→ 打回①重验"
                    % (cid, tip_txt, step_name, rc, t))
                n["refail"] += 1
                continue
            # ---- 绿：真并线 ----
            cur_now = tip_of(repo, ref, git)
            if cur_now != tip0:
                log("MERGE-TRAIN-TIP-MOVED %s: 主线在试合期间又进(%s→%s) → 本轮不并，下圈重算"
                    % (cid, tip0[:8], (cur_now or "?")[:8]))
                continue
            wt = main_worktree(repo, ref, git)
            if wt:
                rc, out = _merge_in_wt(wt, commit, cid)
                if rc != 0:
                    _merge_abort(wt)
                    rec.update({"stage": "blocked", "integrations": "blocked",
                                "blocked_at": nowz(), "blocked_ts": time.time()})
                    git_ev_now(team, cid, "integrations=blocked@%s 真并冲突(主干已变)" % commit[:12])
                    log("MERGE-TRAIN-BLOCKED %s: 真并 merge rc=%s（试合后主干已变/工作树挡道）→ 已 abort 不残留"
                        % (cid, rc))
                    n["blocked"] += 1
                    continue
                rc, head = git(wt, "rev-parse", "HEAD")
                msha = head[:12] if rc == 0 else "?"
            else:
                # 主干未被任何工作树签出：update-ref 带旧值 CAS（零 force：旧值不符=拒动）
                rc, _ = git(repo, "update-ref", "refs/heads/" + ref, sb_sha, tip0)
                if rc != 0:
                    log("MERGE-TRAIN-CAS-FAIL %s: update-ref CAS 拒（主干并发已进）→ 下圈重算" % cid)
                    continue
                msha = sb_sha[:12]
            rec.update({"stage": "merged", "merge_sha": msha, "merged_at": nowz(),
                        "merged_ts": time.time()})
            git_ev_now(team, cid, "stage=merged@%s" % msha)
            _conflict_closure(rec, cid, team, log)  # R5/R6 真并线落地=解冲突落定时刻（blocked 痕在场才递增+落事件+超阈键）
            log("MERGE-TRAIN-MERGED %s: merge --no-ff 真并线 %s → 转正" % (cid, msha))
            n["merged"] += 1
            # VERIFIED：REQ-MERGE-GATE-1 已满足（交付在主干），走 cmd_verdict PASS 正式通道，
            # 判据在**合并后沙箱**里复跑（_run_verify 继承进程 cwd——与 done-sweep 同位纪律）
            prev_cwd = os.getcwd()
            os.chdir(os.path.join(root, "wt"))
            ok = recycle_mod.do_verdict(cid, "PASS",
                                        "合并火车队列复验绿并线@%s（MERGE-TRAIN ring=train）" % msha)
            os.chdir(prev_cwd)
            prev_cwd = None
            if not ok:
                log("MERGE-TRAIN-GATE-RED %s: 已并线但 verdict PASS 被闸拒（执行窗竞态）→ 卡留 DONE，"
                    "已合入主干交 done-sweep/verdict 补章" % cid)
        finally:
            if prev_cwd:
                try:
                    os.chdir(prev_cwd)
                except OSError:
                    pass
            sandbox_cleanup(repo, root, git)
    return n


def _merge_in_wt(wt, commit, cid):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", wt, "merge", "--no-ff", "--no-edit",
                        "-m", "merge(%s): 合并火车自动并线 %s" % (cid, commit[:8]), commit],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, creationflags=NO_WIN)
    out = (r.stdout or b"").decode("utf-8", "replace")
    return r.returncode, out


def _merge_abort(wt):
    try:
        subprocess.run(["git", "-C", wt, "merge", "--abort"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, creationflags=NO_WIN)
    except Exception:
        pass


def prune_state(state, cards, log, max_age_days=14):
    """终态/离池账清理：merged 记录保 14 天（卡转 VERIFIED 后看板仍要能指「并入哪个提交」），
    超龄静默销账；卡离开待验收池（撤/重派/取消）账随迁退场（可见点名）。"""
    cut = time.time() - max_age_days * 86400
    for cid in list(state["cards"].keys()):
        rec = state["cards"][cid]
        st = (cards.get(cid) or {}).get("status")
        if rec.get("stage") == "merged":
            if st not in ("DONE", "VERIFIED"):
                log("MERGE-TRAIN-DROP %s: 卡面现态=%s → 火车账(merged)随迁清除" % (cid, st or "无卡"))
                state["cards"].pop(cid, None)
            elif float(rec.get("merged_ts") or 0) < cut:
                state["cards"].pop(cid, None)                    # 满 14 天销账（终态不再占看板）
        elif st != "DONE":
            log("MERGE-TRAIN-DROP %s: 卡面现态=%s 非待验收 → 火车账随迁清除" % (cid, st or "无卡"))
            state["cards"].pop(cid, None)


# ---------- ⑤ 生效锁：版本钉（NOT-ENERGIZED） ----------

IN_WSL = sys.platform.startswith("linux") and os.path.isdir("/mnt/c")


def sh_wsl(cmdline, run=None):
    """WSL 侧只读/受控命令（同 resident_roster_check 双宿形态）；Windows 侧经 wsl.exe 时
    必须 MSYS2_ARG_CONV_EXCL='*'（防 Git Bash 把命令串里的 /mnt 路径改写成 C:/ —— 老坑）。"""
    full = (["bash", "-lc", cmdline] if IN_WSL else ["wsl.exe", "-e", "bash", "-lc", cmdline])
    env = dict(os.environ)
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    try:
        r = subprocess.run(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=60, env=env, creationflags=NO_WIN)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "SOURCE-UNREADABLE(%r)" % exc


def _parse_props(out):
    d = {}
    for ln in (out or "").splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def _ts_of(systemd_ts, run=None):
    """ExecMainStartTimestamp（"Mon 2026-11-02 22:58:01 CST"）→ epoch 秒；解析不了=None（宁缺不猜）。"""
    if not systemd_ts or systemd_ts == "n/a":
        return None
    rc, out = (run or sh_wsl)("date -d '%s' +%%s" % systemd_ts.replace("'", ""))
    try:
        return float((out or "").strip())
    except ValueError:
        return None


def lifeline_commit_ts(repo_root, lifelines, git=None):
    from ccards.config import git as _G
    git = git or _G
    rc, out = git(repo_root, "log", "-1", "--format=%ct", "--", *[str(x) for x in lifelines])
    if rc != 0:
        return None
    try:
        return float((out or "").strip())
    except ValueError:
        return None


def energizen_scan(repo_root, team, apply, log, state=None, run_sh=None, git=None, now=None):
    """版本钉：进程起时 vs 命脉文件最后提交时刻。滞后=未通电→dispatch.log 报警行
    NOT-ENERGIZED <件> <滞后分钟>；白名单件自动 systemctl --user restart（apply+冷却窗外）。
    返回 {"alarms":n,"restarts":n,"cleared":n}。"""
    g = {"alarms": 0, "restarts": 0, "cleared": 0}
    run_sh = run_sh or sh_wsl
    state = state if state is not None else load_state(team)
    for u in RESIDENT_UNITS:
        rc, out = run_sh("systemctl --user show %s -p MainPID -p ExecMainStartTimestamp "
                         "-p ActiveState -p UnitFileState" % u["unit"])
        p = _parse_props(out)
        active = (p.get("ActiveState") or "").lower()
        enabled = (p.get("UnitFileState") or "").lower()
        life = lifeline_commit_ts(repo_root, u["lifelines"], git=git)
        if life is None:
            log("ENERGIZE-SKIP %s: 命脉提交时刻取不到（repo=%s）→ 不判（宁漏不误杀）" % (u["part"], repo_root))
            continue
        if active == "active":
            start = _ts_of(p.get("ExecMainStartTimestamp"), run_sh)
            if start is None:
                log("ENERGIZE-SKIP %s: 进程起时解析失败(%r)" % (u["part"], p.get("ExecMainStartTimestamp")))
                continue
            lag_min = (life - start) / 60.0
            if lag_min > ENERGIZE_GRACE_MIN:
                g["alarms"] += 1
                line = "NOT-ENERGIZED %s %d" % (u["part"], int(lag_min))
                _stale_alarm(team, log,
                             "NOT-ENERGIZED %s: 常驻进程早于命脉文件最后提交=未通电（版本钉）" % u["part"],
                             "%s——进程起时早于命脉最后提交%.0fmin（修好≠生效）" % (line, lag_min),
                             apply=apply)
                if apply:
                    _dispatch_line(team, line)
                if u.get("auto_restart") and apply:
                    last = float((state.get("restarts") or {}).get(u["part"], 0) or 0)
                    if time.time() - last < RESTART_COOLDOWN_MIN * 60.0:
                        log("   NOT-ENERGIZED %s: 冷却窗内不重复 restart（%.0fmin 内已重启过）"
                            % (u["part"], RESTART_COOLDOWN_MIN))
                    else:
                        rrc, rout = run_sh("systemctl --user restart %s" % u["unit"])
                        state.setdefault("restarts", {})[u["part"]] = time.time()
                        log("   NOT-ENERGIZED %s: 白名单件自动重启 rc=%s %s" % (u["part"], rrc, _tail(rout, 80)))
                        g["restarts"] += 1
            else:
                g["cleared"] += 1
                log("ENERGIZED %s: 进程起时晚于命脉提交（滞后%.0fmin≤宽限）→ 通电正常" % (u["part"], lag_min))
        elif enabled == "enabled":
            g["alarms"] += 1
            line = "NOT-ENERGIZED %s 0 未运行" % u["part"]
            _stale_alarm(team, log, "NOT-ENERGIZED %s: 单元 enabled 未运行（版本钉）" % u["part"],
                         "%s——单元 enabled 但 inactive：常驻件根本没跑" % line, apply=apply)
            if apply:
                _dispatch_line(team, line)
            if u.get("auto_restart") and apply:
                rrc, _ = run_sh("systemctl --user restart %s" % u["unit"])
                log("   NOT-ENERGIZED %s: 自动拉起 rc=%s" % (u["part"], rrc))
                g["restarts"] += 1
        else:
            log("ENERGIZE-HUMAN %s: UnitFileState=%s（人显式停用，火车不夺铃）" % (u["part"], enabled or "?"))
    return g


def _dispatch_line(team, line):
    """报警行落 dispatch.log（卡面⑤指定；systemd stdout 同文件，追加一行不冲突）。"""
    d = os.path.join(team, "logs")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "dispatch.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%F %T"), line))
    except OSError:
        pass


# ---------- 环入口 ----------

def _repo_root(team):
    """账本上一级=机制仓根（版本钉命脉提交时刻的取材位；与 card_cwd 找 .git 同式）。"""
    d = os.path.abspath(team)
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")) or os.path.isfile(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.abspath(team))


def run_ring(team=None, apply=False, log=None, git=None, run=None, recycle_mod=None):
    """火车一圈：①交卷即验 → ②队列试合/真并 → ③撞车登记 → ⑤版本钉收口。
    dry=只点名零写零合线（仅 rev-parse/merge-base 只读探测）；apply=持火车锁（抢不到整圈让路）。
    返回计数 dict（recycle 摘要行/锁用）。"""
    if team is None:
        from ccards import config as _C
        team = _C.TEAM
    if log is None:
        log = lambda s: print(s, flush=True)
    if recycle_mod is None:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import recycle as recycle_mod  # noqa: F811  复用 REDAUTO 链（run_argv/分流/verdict 通道）
    from ccards import graph as _G
    out = {"seen": 0, "passed": 0, "rejected": 0, "pending": 0, "merged": 0,
           "blocked": 0, "refail": 0, "stale": 0, "extmerged": 0,
           "not_energized": 0, "restarts": 0}
    try:
        cards = _G.all_cards()
    except Exception as exc:
        log("MERGE-TRAIN-ERR: 账本读不到(%r) → 本圈整圈不判（空图≠无事可做，防误清账）" % exc)
        return out
    state = load_state(team)
    locked = False
    if apply:
        locked = train_lock_try(team)
        if not locked:
            log("MERGE-TRAIN-LOCKED: 上圈火车未毕 → 本圈让路（宁漏一拍不叠两拍）")
            return out
    try:
        out["queued"] = len(queue_of(cards, state))
        r1 = stage1_verify(cards, state, team, apply, log, recycle_mod, git=git)
        for k, v in r1.items():
            out[k] = out.get(k, 0) + v
        r2 = stage2_merge(cards, state, team, apply, log, recycle_mod, git=git, run=run)
        for k, v in r2.items():
            out[k] = out.get(k, 0) + v
        prune_state(state, cards, log)
        en = energizen_scan(_repo_root(team), team, apply, log, state=state, git=git)
        out["not_energized"] = en["alarms"]
        out["restarts"] = en["restarts"]
        out["queued"] = len(queue_of(cards, state))
        if apply:
            save_state(team, state)
    finally:
        if locked:
            train_lock_release(team)
    log("mergetrain: 候合=%d 本圈①验%d绿%d驳%d pending%d ②并%d 撞车%d 打回%d 外合转正%d 滞留%d ⑤未通电%d(重启%d)"
        % (len(queue_of(cards, state)),
           out["seen"], out["passed"], out["rejected"], out["pending"],
           out["merged"], out["blocked"], out["refail"], out.get("extmerged", 0), out["stale"],
           out["not_energized"], out["restarts"]))
    return out
