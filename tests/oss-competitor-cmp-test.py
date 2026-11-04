#!/usr/bin/env python3
# cc-team/tests/oss-competitor-cmp-test.py — REQ-OSS-CMP-1 五维竞品对比红绿锁。
#
# 卡面定位：发行可验证性增强，非新增治理机制（不加常驻环/报警/登记面）。
# 本锁只回答一件事：README 竞品表里那五句「我们不一样」，每一句能不能换成一条
# 可执行断言、跑出一个数。竞品侧一律只登官方声明+URL（未实机验证），与自家实测
# 数字分栏禁混（见 docs/对比-竞品五维实测与声明-20261104.md）。
#
# 明确**不比**四类（业主 11-04 定档，防后人再加，原话见卡 REQ-OSS-CMP-1 与上述文档）：
#   agent 能力 / 功能条数 / star 数 / 吞吐性能。
#
# 五维与差分对（守卫要验过它红得起来——每一维都跑「反向一笔」证明断言摘水即红）：
#   D1 断电续跑   ：真起子进程写账途中 SIGKILL → 重启对账：事实图完好、派生视图报过期
#                  （差异条数>0），reindex/dag 修复后对账归零。
#                  差分对=崩溃现场 reconcile_events 必能点名过期（若修复器瞎掉，归零断言红）。
#   D2 交付靠什么算完：故意交「判据不过」的活（标记翻 BAD）→ done 后 AUTO-VERIFY 红、
#                  verdict PASS 被机器拒（卡留 DONE，不落 VERIFIED、不记 PASS）。
#                  差分对=标记复原 OK 后同卡 verdict 必绿（证明拒收不是全通道假红）。
#   D3 可追责     ：任取 D2 那张卡，从 events.log 逐条答 谁/何时/依据什么（含 verdict
#                  痕与 verify_run.cmd_sha 与卡面判据现算一致）。
#                  差分对=写口外裸改 graph.json 状态 → reconcile_events 必 ALARM（篡改可查），
#                  还原字节后归零。
#   D4 接入代价   ：AST 扫发布包（cards_dag.py + ccards/*.py）三方 import 数=0、
#                  网络/账号类 stdlib 客户端=0、pyproject 无 dependencies；冷启动=空沙箱
#                  首笔 create 实测秒数。
#                  差分对=对沙箱副本注入 `import requests` → 扫描器必点名（摘水即红）。
#   D5 僵尸处置   ：worker 心跳断流 → recycle --apply 自动 release：卡回队头（next 现算+
#                  镜像行首）+ 回收秒数实测；旧班迟到的 progress/done/note 三笔全被名牌
#                  闸拒收（EPOCH-STALE 入 events/rejects，账面零脏写）。
#                  差分对=心跳新鲜时同环必「ALIVE 不动」（若判活被摘成无条件回收，此断言红）。
#
# 纪律：全程一次性临时沙箱（临时 git 仓 + CC_TEAM_HOME 临时账根），零真实账触碰、
#       不联网（reconcile 判据四用桩替换看板自审计；recycle 会话普查/杀进程走测试 env 缝）。
# 用法：python -I cc-team/tests/oss-competitor-cmp-test.py        # 全绿末行 CC_CMP_PASS
#       判据产物未落盘属预期（red-first R2），本文件首跑前不存在=卡面判据的豁免对象。
import ast, glob, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # cc-team 根（被测源码正主）
CARDS = os.path.join(ROOT, "cards_dag.py")
RECON = os.path.join(ROOT, "ops", "reconcile_events.py")
RECYCLE = os.path.join(ROOT, "ops", "recycle.py")
PY = sys.executable
PYS = PY.replace("\\", "/")                       # Git Bash 宿主可执行的盘符正斜杠形
RESULTS = []
for _s in (sys.stdout, sys.stderr):  # 出侧钉 UTF-8（同 cli.main BUG-CCARDS-UTF8-1 纪律）
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def ck(name, cond, note=""):
    RESULTS.append((name, bool(cond), note))
    if isinstance(note, (list, tuple)):
        note = " | ".join(str(x) for x in note)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, ("｜" + note) if note else ""))
    return bool(cond)


def w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


def _resolve_bash():
    if os.environ.get("CARD_VERIFY_BASH"):
        return os.environ["CARD_VERIFY_BASH"]
    w_ = shutil.which("bash")
    sr = (os.environ.get("SystemRoot") or r"C:\Windows").lower()
    if w_ and not os.path.normpath(w_).lower().startswith(sr):
        return w_
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    for cand in (os.path.join(pf, "Git", "bin", "bash.exe"),
                 r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if os.path.isfile(cand):
            return cand
    return "bash"                                  # POSIX 原样（判据宿主仍由引擎解析）


BASH = _resolve_bash()


def g(repo, *args):
    return subprocess.run(["git", "-c", "core.quotepath=false", "-C", repo] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace")


# ---------------- 沙箱基座 ----------------

def sandbox(tag, with_repo=True):
    base = tempfile.mkdtemp(prefix="cmp-sb-%s-" % tag)
    sb = {"base": base, "home": os.path.join(base, "home"), "repo": None}
    os.makedirs(os.path.join(sb["home"], "state"), exist_ok=True)
    # 判据四的看板自审计桩：哑脚本 rc0 零输出（对账本体三判据照跑，不联网不碰活体页）
    sb["stub_audit"] = os.path.join(base, "stub_audit.py")
    w(sb["stub_audit"], "# 沙箱桩：看板自审计=空操作（本锁不测看板面）\nraise SystemExit(0)\n")
    # recycle 会话普查桩：永远返回空 pid 表（测试 env 缝，生产不走；防 CIM/powershell）
    sb["stub_nopids"] = os.path.join(base, "stub_nopids.py")
    w(sb["stub_nopids"], "# 沙箱桩：无在跑会话（GHOST-KILL-SKIP 保守形态）\nraise SystemExit(0)\n")
    # 通用判据：常绿闸 + 标记闸（读 base/marker，内容非 OK 即 rc1 带「预期…实际」断言形）
    sb["gate_ok"] = os.path.join(base, "gate_ok.py")
    w(sb["gate_ok"], "print('CC_GATE_OK')\n")
    sb["marker"] = os.path.join(base, "marker.txt")
    sb["gate_mark"] = os.path.join(base, "gate_marker.py")
    w(sb["gate_mark"],
      "import sys\n"
      "got=open(sys.argv[1],encoding='utf-8').read().strip() if len(sys.argv)>1 else ''\n"
      "if got=='OK': print('CC_GATE_OK')\n"
      "else: sys.exit('预期 标记=OK 实际 标记=%r（判据不过=交付不合格）' % got)\n")
    if with_repo:
        repo = os.path.join(base, "repo")
        os.makedirs(repo, exist_ok=True)
        g(repo, "init", "-b", "main", ".")
        g(repo, "config", "user.name", "cmp-sb")
        g(repo, "config", "user.email", "cmp@sb.local")
        w(os.path.join(repo, "README.md"), "seed\n")
        g(repo, "add", ".")
        g(repo, "commit", "-m", "seed")
        sb["repo"] = repo
    return sb


def env_for(sb, extra=None):
    env = dict(os.environ)
    env["CC_TEAM_HOME"] = sb["home"]
    env["CARD_VERIFY_BASH"] = BASH
    env["BOARD_SELFAUDIT_PATH"] = sb["stub_audit"]
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def cli(sb, args, extra=None, timeout=180):
    return subprocess.run([PY, "-I", CARDS] + list(args), cwd=sb["base"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
                          errors="replace", env=env_for(sb, extra), timeout=timeout)


def vgate(path):
    return '"%s" -I "%s"' % (PYS, path.replace("\\", "/"))


def vgate_marker(sb):
    return vgate(sb["gate_mark"]) + ' "%s"' % sb["marker"].replace("\\", "/")


def show_card(sb, cid):
    try:
        with open(os.path.join(sb["home"], "state", "graph.json"), encoding="utf-8") as f:
            return (json.load(f).get("cards") or {}).get(cid)
    except (OSError, ValueError):
        return None


def events_lines(sb, cid=None):
    p = os.path.join(sb["home"], "state", "events.log")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            out = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        out = []
    return [ln for ln in out if (cid is None or ("|%s|" % cid) in ln)]


def run_recon(sb, extra=None):
    r = subprocess.run([PY, "-I", RECON, "--quiet"], cwd=sb["base"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
                       errors="replace", env=env_for(sb, extra), timeout=180)
    m = re.search(r"alarms=(\d+)", r.stdout or "")
    return r, (int(m.group(1)) if m else -1)


def commit_work(sb, relpath, content, msg):
    p = os.path.join(sb["repo"], relpath)
    w(p, content)
    g(sb["repo"], "add", ".")
    r = g(sb["repo"], "commit", "-m", msg)
    assert r.returncode == 0, "沙箱提交失败: " + (r.stdout or "")
    return g(sb["repo"], "rev-parse", "HEAD").stdout.strip()


# ---------------- D1 断电续跑 ----------------

def d1():
    sb = sandbox("d1")
    cid = "CMP-A-CRASH"
    lane = "slot-d1"
    r = cli(sb, ["create", cid, "--title", "D1 断电续跑", "--lanes", lane,
                 "--files", "src/*", "--repo", sb["repo"].replace("\\", "/"),
                 "--verify", vgate(sb["gate_ok"])])
    ck("D1 立卡（发卡即考试过）", r.returncode == 0, (r.stdout or "").strip().splitlines()[-1:])
    r = cli(sb, ["claim", cid, "--owner", lane, "--pid", "4242"])
    m = re.search(r"epoch=(\d+)", r.stdout or "")
    ck("D1 认领入任", r.returncode == 0 and m, "e%s" % (m.group(1) if m else "?"))
    ep = int(m.group(1)) if m else 1
    commit = commit_work(sb, "src/app.py", "print('work done')\n", "feat(CMP-A-CRASH): 交付先落树")
    # 真·进程被杀：子进程走库内正通道 cmd_done（save→claims/道锁清→events 全部落账），
    # 放锁之后、物化视图刷新之前 SIGKILL——正是 cli.run「cmd 完→graph.refresh」之间的崩溃窗。
    child = os.path.join(sb["base"], "crash_done.py")
    w(child,
      "import sys, os, argparse\n"
      "sys.dont_write_bytecode = True\n"
      "sys.path.insert(0, sys.argv[1])\n"
      "from ccards import locks, commands\n"
      "locks.team_lock_acquire('done')\n"
      "try:\n"
      "    commands.cmd_done(argparse.Namespace(id=sys.argv[2], owner=sys.argv[3],\n"
      "        commit=sys.argv[4], evidence='D1-crash', repo=sys.argv[5],\n"
      "        epoch=int(sys.argv[6]), out_json=None, force=False))\n"
      "finally:\n"
      "    locks.team_lock_release()\n"
      "os.kill(os.getpid(), 9)  # Windows=TerminateProcess 硬杀：refresh/auto-verify 通道永不会执行\n")
    r = subprocess.run([PY, "-I", child, ROOT.replace("\\", "/"), cid, lane,
                        commit, sb["repo"].replace("\\", "/"), str(ep)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8",
                       errors="replace", env=env_for(sb))
    card = show_card(sb, cid)
    doc = None
    try:
        with open(os.path.join(sb["home"], "state", "graph.json"), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        pass
    seal_now = hashlib.sha1(json.dumps((doc or {}).get("cards") or {}, sort_keys=True,
                                        ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    ck("D1 进程中途暴卒（无清理通道走完）", r.returncode != 0, "子进程 rc=%d" % r.returncode)
    ck("D1 事实图无半行写：可读且 seal 自封完好", doc is not None and doc.get("seal") == seal_now,
       "seal=%s" % (doc or {}).get("seal"))
    ck("D1 断点账目如实：done 落账=卡面 DONE+commit 在树", (card or {}).get("status") == "DONE"
       and (card or {}).get("commit") == commit, "账上 DONE 是真交过卷的那笔")
    _r0, alarms_crash = run_recon(sb)
    chk = cli(sb, ["dag", "--check"])
    ck("D1 重启对账报差异（派生视图过期被点名，差异条数=%d>0）" % alarms_crash,
       alarms_crash > 0 and chk.returncode != 0, "reconcile alarms=%d, dag --check rc=%d"
       % (alarms_crash, chk.returncode))
    ri = cli(sb, ["reindex"])
    _r1, alarms_fixed = run_recon(sb)
    chk2 = cli(sb, ["dag", "--check"])
    ck("D1 修复后账面与真实树零差异（差异条数=%d）" % alarms_fixed,
       ri.returncode == 0 and alarms_fixed == 0 and chk2.returncode == 0,
       "reindex 修正=%s｜对账 alarms=%d" % ((ri.stdout or "").splitlines()[:1], alarms_fixed))
    print("D1-NUMBER 断电续跑：崩溃现场差异=%d条 → 修复后=%d条" % (alarms_crash, alarms_fixed))
    return sb


# ---------------- D2 交付靠什么算完 ----------------

def d2(sb=None):
    sb = sb or sandbox("d2")
    cid = "CMP-B-VERIFY"
    lane = "slot-d2"
    with open(sb["marker"], "w", encoding="utf-8") as f:
        f.write("OK")
    r = cli(sb, ["create", cid, "--title", "D2 交付验收闸", "--lanes", lane,
                 "--files", "src/*", "--repo", sb["repo"].replace("\\", "/"),
                 "--verify", vgate_marker(sb)])
    ck("D2 立卡（首考 rc0 过闸；产物态以现场标记为凭）", r.returncode == 0,
       (r.stdout or "").strip().splitlines()[-1:])
    r = cli(sb, ["claim", cid, "--owner", lane, "--pid", "4243"])
    m = re.search(r"epoch=(\d+)", r.stdout or "")
    ck("D2 认领", r.returncode == 0 and m)
    ep = int(m.group(1)) if m else 1
    # 故意交判据不过的活：交付物本身提交，判据现场标记翻 BAD
    commit = commit_work(sb, "src/out.py", "print('delivered')\n", "feat(CMP-B-VERIFY): 交活")
    with open(sb["marker"], "w", encoding="utf-8") as f:
        f.write("BAD")
    r = cli(sb, ["done", cid, "--owner", lane, "--commit", commit, "--evidence", "D2",
                 "--repo", sb["repo"].replace("\\", "/"), "--epoch", str(ep)],
            extra={"CC_AUTO_VERIFY": "1"})
    card = show_card(sb, cid)
    ck("D2 判据不过→自动拒收：勾卡成功但 AUTO-VERIFY 红、不落 VERIFIED",
       r.returncode == 0 and "AUTO-VERIFY" in (r.stdout or "")
       and (card or {}).get("status") == "DONE",
       [ln for ln in (r.stdout or "").splitlines() if "AUTO-VERIFY" in ln][-1:])
    rv = cli(sb, ["verdict", cid, "PASS", "--reason", "试着强判"])
    card = show_card(sb, cid)
    ev_ok = not any("->VERIFIED" in ln for ln in events_lines(sb, cid))
    ck("D2 verdict PASS 被机器拒（rc≠0+卡留 DONE+账上无 PASS 痕+无 VERIFIED 事件）",
       rv.returncode != 0 and (card or {}).get("status") == "DONE"
       and not (card or {}).get("verdict") and ev_ok,
       (rv.stdout or "").strip().splitlines()[-1:])
    # 差分对：现场修复后同通道必须能绿（证明上面的拒收不是把通道整个摘红的假闸）
    with open(sb["marker"], "w", encoding="utf-8") as f:
        f.write("OK")
    rv2 = cli(sb, ["verdict", cid, "PASS", "--reason", "判据现场已修复"])
    card = show_card(sb, cid)
    ck("D2 差分对：判据转绿后 verdict PASS 正常落章 VERIFIED（通道没被摘成永红）",
       rv2.returncode == 0 and (card or {}).get("status") == "VERIFIED",
       (rv2.stdout or "").strip().splitlines()[-1:])
    return sb, cid, lane


# ---------------- D3 可追责 ----------------

def d3(sb, cid, lane, commit):
    ev = events_lines(sb, cid)
    parsed, who_set = [], set()
    for ln in ev:
        p = ln.split("|")
        if len(p) >= 5:
            try:
                datetime.strptime(p[0], "%Y-%m-%dT%H:%M:%S%z")
            except ValueError:
                continue
            parsed.append(p)
            who_set.add(p[3])
    seq = [(p[2], p[3]) for p in parsed if "->" in p[2]]
    need = ["->CLAIMED", "->DONE", "->VERIFIED"]
    have = [n for n in need if any(s.endswith(n) for s, _ in seq)]
    card = show_card(sb, cid)
    sha_cardface = (card or {}).get("verify_run", {}).get("cmd_sha")
    sha_recalc = hashlib.sha1((card or {}).get("verify", "").strip().encode("utf-8")).hexdigest()[:12]
    basis = (card or {}).get("verdict") or ""
    ck("D3 任取一卡逐条可答：每行=谁/何时/迁转，全生命周期 %d 笔含 %s" % (len(seq), "/".join(have)),
       len(parsed) >= 4 and have == need and "PM" in who_set and lane in who_set,
       "who∈%s" % sorted(who_set))
    ck("D3 依据什么=机器痕非自述：verdict 文本+verify_run.cmd_sha 与卡面判据现算一致",
       "PASS" in basis and sha_cardface == sha_recalc and (card or {}).get("commit") == commit,
       "cmd_sha=%s verdict=%r" % (sha_cardface, basis[:48]))
    # 差分对：写口外裸改状态（不重封不补 events）→ 对账器必须当场点名
    gp = os.path.join(sb["home"], "state", "graph.json")
    with open(gp, "rb") as f:
        orig = f.read()
    doc = json.loads(orig.decode("utf-8"))
    doc["cards"][cid]["status"] = "CLAIMED"          # 裸改：VERIFIED→CLAIMED，无事件无重封
    with open(gp, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=2))
    _r2, alarms_tamper = run_recon(sb)
    ck("D3 差分对：裸改账面必被对账点名（alarms=%d>0=篡改可查不是装饰）" % alarms_tamper,
       alarms_tamper > 0, "seal/判据一 双查其一必响")
    with open(gp, "wb") as f:
        f.write(orig)
    _r3, alarms_back = run_recon(sb)
    ck("D3 还原后对账归零（alarms=%d）" % alarms_back, alarms_back == 0)
    print("D3-NUMBER 可追责：lifecycle events=%d 笔｜裸改检出 alarms=%d｜还原=%d"
          % (len(seq), alarms_tamper, alarms_back))


# ---------------- D4 接入代价 ----------------

def _scan_third_party(src_dir, entry_names):
    """AST 扫发布包：返回三方/网络类 import 模块名集合。相对导入与 ccards 自引用不计。"""
    std = getattr(sys, "stdlib_module_names", frozenset())
    NETLIKE = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib",
               "telnetlib", "requests", "httpx", "aiohttp", "urllib3", "websocket", "grpc"}
    third, net, files = set(), set(), 0
    roots = []
    for n in entry_names:
        p = os.path.join(src_dir, n)
        if os.path.isfile(p):
            roots.append(p)
    for p in glob.glob(os.path.join(src_dir, "ccards", "*.py")):
        roots.append(p)
    for p in roots:
        files += 1
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except (OSError, SyntaxError):
            third.add("<解析失败:%s>" % os.path.basename(p))
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            for m in mods:
                if m in std or m == "ccards":
                    if m in NETLIKE:
                        net.add(m)
                    continue
                third.add(m)
    return third, net, files


def d4():
    src = ROOT
    third, net, nfiles = _scan_third_party(src, ["cards_dag.py"])
    ck("D4 运行时依赖数=%d（AST 扫 %d 个发布包源文件，三方 import 清零）" % (len(third), nfiles),
       not third, "检出=%s" % sorted(third))
    ck("D4 无账号/外部服务：核心包零网络客户端 import（含 stdlib 网络族）", not net,
       "检出=%s" % sorted(net))
    pp = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    proj = pp.split("[project]", 1)[1].split("[tool.", 1)[0] if "[project]" in pp else ""
    ck("D4 pyproject [project] 无 dependencies/可选依赖声明",
       "dependencies" not in proj, "")
    # 差分对：往沙箱副本注入一行 `import requests` → 扫描器必须点名（摘水即红自证）
    sb = sandbox("d4", with_repo=False)
    copy = os.path.join(sb["base"], "copy")
    shutil.copytree(os.path.join(src, "ccards"), os.path.join(copy, "ccards"))
    shutil.copy(os.path.join(src, "cards_dag.py"), os.path.join(copy, "cards_dag.py"))
    ep = os.path.join(copy, "ccards", "events.py")
    w(ep, "import requests  # 注入的假三方依赖（差分对用）\n" + open(ep, encoding="utf-8").read())
    third2, _n2, _f2 = _scan_third_party(copy, ["cards_dag.py"])
    ck("D4 差分对：副本注入 requests 后扫描器必点名（守卫红得起来）", "requests" in third2,
       "检出=%s" % sorted(third2))
    # 冷启动：空账根第一笔 create（python 解释器起+包 import+首写账，全含）
    t0 = time.time()
    r = cli(sb, ["create", "CMP-D-COLD", "--title", "冷启动", "--lanes", "ANY", "--status", "DRAFT"])
    cold = time.time() - t0
    ck("D4 冷启动实测（空沙箱首笔入账 rc0，%d 文件 import+首写）" % nfiles, r.returncode == 0,
       "耗时 %.2fs" % cold)
    print("D4-NUMBER 接入代价：运行时依赖数=%d｜网络客户端=%d｜冷启动=%.2fs｜差分对=注入必点名"
          % (len(third), len(net), cold))
    return sb


# ---------------- D5 僵尸处置 ----------------

def beat(sb, cid, lane, ep, ring):
    return cli(sb, ["progress", cid, "干了一笔 r%d" % ring,
                    "--lane", lane, "--epoch", str(ep), "--ring", str(ring)])


def recycle_run(sb, minutes):
    env_extra = {"RECYCLE_TRANSCRIPT_GRACE_MIN": "0",            # 测试缝：转录豁免显式停用
                 "RECYCLE_SESSION_PID_CMD": '"%s" -I "%s"' % (PYS, sb["stub_nopids"].replace("\\", "/"))}
    t0 = time.time()
    rr = subprocess.run([PY, "-I", RECYCLE, "--ring", "full", "--apply", "--minutes", str(minutes)],
                        cwd=sb["base"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        encoding="utf-8", errors="replace", env=env_for(sb, env_extra), timeout=300)
    dt = time.time() - t0
    m = re.search(r"recycle: stale=(\d+) zombie=(\d+) alive=(\d+)", rr.stdout or "")
    return rr, (int(m.group(2)) if m else -1), (int(m.group(3)) if m else -1), dt


def d5():
    sb = sandbox("d5")
    cid, lane = "CMP-E-ZOMBIE", "slot-d5"
    r = cli(sb, ["create", cid, "--title", "D5 僵尸回收", "--lanes", lane,
                 "--files", "src/*", "--repo", sb["repo"].replace("\\", "/"),
                 "--verify", vgate(sb["gate_ok"])])
    ck("D5 立卡", r.returncode == 0)
    r = cli(sb, ["claim", cid, "--owner", lane, "--pid", "7777"])
    m = re.search(r"epoch=(\d+)", r.stdout or "")
    ep1 = int(m.group(1)) if m else 1
    ck("D5 认领 kind=lane（有 pid 锚=回收主体，非 session 保守档）", r.returncode == 0)
    b1, b2 = beat(sb, cid, lane, ep1, 1), beat(sb, cid, lane, ep1, 2)
    ck("D5 心跳两拍入账 BEAT_OK", b1.returncode == 0 and b2.returncode == 0)
    # 差分对先行：心跳新鲜时同环必须「ALIVE 不动」——判活若被摘成无条件回收，此笔即红
    rr0, z0, a0, _d0 = recycle_run(sb, 15)
    card = show_card(sb, cid)
    ck("D5 差分对：活 worker 不被误杀（ALIVE=%d 卡留 CLAIMED，回收若滥权此断言红）" % a0,
       z0 == 0 and a0 == 1 and (card or {}).get("status") == "CLAIMED",
       [ln for ln in (rr0.stdout or "").splitlines() if ln.startswith("ALIVE")][:1])
    # worker 中途死：心跳断流（把 beat 的 t 回摆 60min，= 最后一次续写是一小时前）
    bp = os.path.join(sb["home"], "state", "lane-%s.beat" % lane)
    rec = json.load(open(bp, encoding="utf-8"))
    rec["t"] = time.time() - 3600.0
    rec["ts"] = datetime.fromtimestamp(rec["t"]).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(bp, "w", encoding="utf-8", newline="") as f:
        json.dump(rec, f, ensure_ascii=False)
    rr1, z1, a1, dt = recycle_run(sb, 15)
    card = show_card(sb, cid)
    nxt = cli(sb, ["next", lane])
    qf = os.path.join(sb["home"], "state", "lane-%s.queue" % lane)
    qhead = open(qf, encoding="utf-8").read().splitlines()[0].strip() if os.path.exists(qf) else "-"
    ck("D5 worker 死→卡自动回队头（zombie=%d｜next 现算=队头可领｜镜像行首=本卡｜状态回 OPEN）" % z1,
       z1 == 1 and (nxt.stdout or "").strip() == cid and qhead == cid
       and (card or {}).get("status") == "OPEN")
    print("D5-NUMBER 僵尸处置：自动回收耗时=%.2fs（含判活三问+release+回队头+DAG 刷新）" % dt)
    # 重派换班 + 僵尸迟报免疫：旧班三笔上报全拒、账面零脏写
    r = cli(sb, ["claim", cid, "--owner", lane, "--pid", "8888"])
    m = re.search(r"epoch=(\d+)", r.stdout or "")
    ep2 = int(m.group(1)) if m else 2
    ck("D5 重派发新班 e%d（旧班 e%d 作废）" % (ep2, ep1), r.returncode == 0 and ep2 > ep1)
    seed = g(sb["repo"], "rev-parse", "HEAD").stdout.strip()
    a1r = beat(sb, cid, lane, ep1, 99)                       # 旧班迟报心跳
    a2r = cli(sb, ["done", cid, "--owner", lane, "--commit", seed, "--epoch", str(ep1),
                   "--repo", sb["repo"].replace("\\", "/")])  # 旧班迟报交卷
    a3r = cli(sb, ["note", cid, "--owner", lane, "--text", "僵尸乱写", "--epoch", str(ep1)])
    card = show_card(sb, cid)
    stale_ev = sum(1 for ln in events_lines(sb, cid) if "EPOCH-STALE" in ln)
    rj = os.path.join(sb["home"], "state", "rejects.log")
    stale_rj = open(rj, encoding="utf-8").read().count("EPOCH-STALE") if os.path.exists(rj) else 0
    ck("D5 僵尸三笔全拒收（progress/done/note rc≠0；回收期间与死后零脏写）",
       a1r.returncode != 0 and a2r.returncode != 0 and a3r.returncode != 0
       and (card or {}).get("status") == "CLAIMED" and not (card or {}).get("commit"),
       "卡面仍=新班在制 e%d，commit=%r" % (ep2, (card or {}).get("commit")))
    ck("D5 拒收留痕可查：events EPOCH-STALE=%d 笔 + rejects=%d 笔（僵尸乱报不是静默黑洞）"
       % (stale_ev, stale_rj), stale_ev >= 2 and stale_rj >= 1)
    chk = cli(sb, ["dag", "--check"])
    ck("D5 收尾账面新鲜（dag --check 绿）", chk.returncode == 0)
    return sb


# ---------------- 主流程 ----------------

def main():
    print("== REQ-OSS-CMP-1 五维竞品对比锁（全沙箱，零真实账触碰，不联网）==")
    print("沙箱根=%s" % tempfile.gettempdir())
    d1()
    sb2, cid2, lane2 = d2()
    commit2 = (show_card(sb2, cid2) or {}).get("commit")
    d3(sb2, cid2, lane2, commit2)
    d4()
    d5()
    bad = [n for n, ok, _ in RESULTS if not ok]
    print("---")
    print("CHECKS total=%d pass=%d fail=%d" % (len(RESULTS), len(RESULTS) - len(bad), len(bad)))
    if bad:
        print("FAILS: " + " | ".join(bad))
        print("CC_CMP_FAIL")
        sys.exit(1)
    print("CC_CMP_PASS")


if __name__ == "__main__":
    main()
