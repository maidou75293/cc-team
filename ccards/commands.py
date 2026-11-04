# ccards.commands — 全部具名命令（状态机唯一通道）。裸改卡面=对账 ALARM 的来源，闸门都在这。
import fnmatch, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
from .config import (ARCHIVE, ID_RE, RESIDENT_DUTY, RESIDENT_DUTY_EDGES, RESIDENT_REQUIRED,
                    STATUS_ENUM, TEAM, claim_dir, die, git, is_resident, lane_book, now,
                    nowz, split)
from .events import _san, event
from .graph import (Graph, all_cards, deps_ok, deps_blockers, exists as card_exists, load,
                    refresh as dag_refresh, seal, save)
from .config import GRAPH, atomic_write_json
from .locks import (lane_beat_read, lane_beat_write, lane_epoch_advance, lane_epoch_read,
                    lane_lock_acquire, lane_lock_dir, lane_lock_read, lane_lock_release,
                    lane_q_drop_everywhere, lane_q_pop, lane_q_push, lane_queue_lines,
                    lock_claim, team_lock_acquire, team_lock_release)
from .config import FINISHED, _session_from_env, REJECTS, num_setting, setting
# MECH-STATE-STD-1：状态迁移唯一数据表（迁移矩阵）——各命令写状态前过 require_edge=同源闸，
# 表义见 ccards/statemachine.py（术语/属主/必填字段/矩阵的单一事实源，README 与 selftest 同读）。
from .statemachine import require_edge
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost


def cmd_rejectlog(a):
    """MECH-REJT-1（业主令 2026-11-01「被拒历史别黑盒」）：分类器/守卫对某次工具调用的拒绝入账。
    只 append 不改写；看板「被拒记录」页签经 /api/ops 现读渲染。格式 ts|工具|事由摘要。"""
    with open(REJECTS, "a", encoding="utf-8", newline="") as f:  # MECH-LEDGER-CRLF-1 钉LF
        f.write("%s|%s|%s\n" % (now(), _san(a.tool), _san(a.note)))
    print("REJECT-LOGGED")


def _check_lanes(lanes, cmd):
    # 道名源头闸（业主 2026-11-03 五点整改第 1 条）。实锤病根：旧 create 校验卡号/依赖/
    # 状态/判据四样，**独漏道名**——mech1-3/pm/ver1/wops 幽灵道因此能进账，卡躺进没人
    # 消费的队列。规矩：在册道（两册并集）或 ANY 才许写 lanes；新岗先过
    # lane_registry build 上册再立卡。无册（纯沙箱）=跳过（各测试房通道）。
    book = lane_book()
    if not book:
        return
    bad = [str(x).strip() for x in (lanes or []) if str(x).strip() not in book]
    if bad:
        die("%s 拒绝：道名 %s 不在岗位册（ANY=池通配；新岗位先 lane_registry build 上册；"
            "幽灵道就是从前这闸没立——业主 2026-11-03 定源头堵死）" % (cmd, ",".join(bad)))


_BASHN_CUT_RE = re.compile(r"""[\s;|&()\n<>`'"]+""")   # 判据文本粗分词：空白/shell 操作符/引号/尖括号
_BASHN_SHORTOPT_RE = re.compile(r"^-([A-Za-z]+)$")     # bash 单破折短选项（-n / -nx 连写）；--norc 类长选项不算 -n


def _verify_bashn_scan(text):
    """静态扫判据文本，返回 [(含n的选项串, 文件操作数)]——bash 语法检查模式目标非 .sh 的命中。
    每次 bash 出现只看**第一个文件操作数**（bash -n 只检脚本文件，其后操作数是传参不判）；
    $VAR 起步=动态值静态不可判、放行；`-o` 吞一个选项参数（pipefail 不当文件判）；
    非 -n 模式遇到 `-c` 即终止本段——引号里嵌套的真 `bash -n` 串会被外层循环独立再扫到。
    不起 shell、不跑命令——本闸是对判据文本的纯静态检查（MECH-GATE-BASHN-1）。"""
    toks = _BASHN_CUT_RE.split(text or "")
    hits = []
    i, n = 0, len(toks)
    while i < n:
        base = toks[i].replace("\\", "/").rsplit("/", 1)[-1]
        if base in ("bash", "bash.exe"):
            j, opt_n = i + 1, None
            while j < n:
                m = _BASHN_SHORTOPT_RE.match(toks[j])
                if m:
                    if "n" in m.group(1):
                        opt_n = toks[j]
                    elif toks[j] == "-c" and not opt_n:
                        break  # -c 直跑形态：命令串留给外层再扫（内含真毒照样抓到）
                    elif toks[j] == "-o" and j + 1 < n:
                        j += 1  # -o 带参数（pipefail 等），下一个 token 不是文件
                    j += 1
                    continue
                if not toks[j] or toks[j].startswith("-") or toks[j].startswith("$"):
                    j += 1
                    continue
                if opt_n and not toks[j].lower().endswith(".sh"):
                    hits.append((opt_n, toks[j]))
                break
            i = j
        else:
            i += 1
    return hits


def _check_verify_bashn(verify, cmd):
    # bash -n 判据目标源头闸（MECH-GATE-BASHN-1，2026-11-03 fuse S3 轮记档转正本）。
    # 实锤病例：MECH-TERM-PLAIN-1 卡面曾挂 `bash -n status-web.py`——Python 源过 bash
    # 语法闸必永红（两种语言），AUTO-VERIFY 空转把卡误归假红。规矩：**判据文本**含
    # `bash -n <非.sh>` 发卡三口（create/set/start）直接拒并归因；`bash <file>` 直跑
    # （含 .py 壳如 merge-train-check.py）与 `bash -n *.sh` 不受影响。锁=tests/gate-bashn-check.py。
    hits = _verify_bashn_scan(verify)
    if hits:
        opt, tgt = hits[0]
        die("%s 拒绝：判据含 %s %s——bash 语法闸(-n)的目标必须是 .sh 文件；非 .sh 源"
            "（Python/JS 等）过 bash -n 必永红（MECH-TERM-PLAIN-1 实锤：bash -n status-web.py）。"
            "直跑请用 `bash <file>`，语法闸只挂 *.sh" % (cmd, opt, tgt))


# python 解释器直跑 .sh 的形态：python/python3 + 任意连珠短旗 + 目标 .sh（文本级静态扫，
# 同 bashn 闸口径：嵌套在 bash -c 命令串里的真毒照样抓到；python -c '…x.sh' 不误伤——
# 旗后首个非旗 token 必须就是 .sh 文件才算）。
_PY_ON_SH = re.compile(r"\bpython\d?(?:\s+-\S+)*\s+\S+\.sh\b")


def _check_verify_crosslang(verify, cmd):
    """跨语言永红闸·反向（业主 seq=30，MECH-GATE-BASHN-1 同族补全）：判据含
    `python [-旗] <file>.sh` = Python 解释器解析 shell 脚本，SyntaxError 必永红。
    实锤病例：BUG-AGENT-KEY-FIX-1 卡面挂 `python -I scripts/ops/secret.sh check`
    （secret.sh=#!/usr/bin/env bash，line 112 `list) cmd_list ;;` 报 unmatched ')'），
    工人三轮返工照旧四连拒空转——判据形态病不是施工病，发卡三口必须拦截。
    锁=tests/stoploss-crosslang-test.py（两侧都锁：放行形态误伤即红）。"""
    m = _PY_ON_SH.search(verify or "")
    if m:
        die("%s 拒绝：判据含 `%s`——python 解释器直跑 .sh shell 脚本必 SyntaxError 永红"
            "（BUG-AGENT-KEY-FIX-1 实锤：python -I scripts/ops/secret.sh check，"
            "secret.sh 是 #!/usr/bin/env bash）。跑 shell 脚本请用 `bash <file>.sh`"
            % (cmd, m.group(0)[:100]))


# ── REQ-VERIFY-DRYRUN-1（业主信箱 seq=31③ 2026-11-04）：发卡即考试——判据试跑闸 ─────────
# 静态闸族（bashn/crosslang）拦得住「已知病形」，拦不住「格式对但跑不通」。病根两例（族谱
# docs/系统保障清单.md §八）：REQ-OSS-QUICKSTART-1 判据被 shell 提前求值烙进乱码碎片，
# rc=127 `$'\xxx': command not found` 连拍待验收；BUG-AGENT-KEY-FIX-1 `python -I <bash 脚本>`
# 跨语言永红六连拒人工截断。本闸=机读兜底：create(带 verify)/set --verify/start --verify 三口
# 过静态闸后**锁外**当场实跑一次（调用点唯一=cli.run 进锁前一行；实现唯一=本段函数，禁复刻——
# 与 verdict「长执行锁外+进锁重载」同纪律，写锁包住整个 cmd_* 函数体，试跑绝不进锁）。
# 超时缺省 60s（env CARD_TRIAL_TIMEOUT_SEC 可调）；非零=默认拒绝，rc+tail≤500字 落 events 痕
# （业主明文「痕迹落 events」；extra 前缀 TRIAL-，ops/reconcile_events.py 对非迁移痕跳过防幽灵卡红）。
# R2 red-first 豁免（业主既有纪律「守卫要验过它红得起来」，REQ-CLAIM-COUNTER-1 现账「首红基线=
# 测试未落盘 rc≠0」即此族）：失败输出点名的缺失文件 ∈ 卡面 --files 声明 → 放行并落 TRIAL-REDFIRST
# 痕（谁豁免、豁免了哪个文件，账上可查）；files 为空无豁免（产物路径必须声明，同越域闸口径）；
# rc=127 非 files 内/SyntaxError/乱码 $'/timeout/断言红 rc=1 一律拒。
# R3 边界：在 tempfile.mkdtemp 一次性目录跑，绝不进现网仓/道树/state；除 events 痕与拒/放行结论
# 外零写账；本闸只保证「跑得动」，不预判「跑得对」（done/verdict 语义不动）。
# 宿主=bash 解释形与 _run_verify 同源（_resolve_verify_bash：CARD_VERIFY_BASH 钉 Git Bash 防 WSL 假红）。

_TRIAL_MISS_RES = (  # R2「点名缺失路径」四类病形（实测串见 tests/verify-dryrun-gate-test.py 头注）
    re.compile(r"can't open file '([^']+)'"),               # python 直跑缺文件（绝对路径形）
    re.compile(r"cannot access '([^']+)'"),                 # ls 类缺文件
    re.compile(r"([\w./$@*~+-]+): No such file or directory"),
    re.compile(r"([\w./+-]+): command not found"),  # 乱码 $'…' 不匹配本类（字符类不含 $ 引号）
    # \w 含 CJK（本仓中文产物路径实锤：旧 [A-Za-z0-9_...] 把「docs/快速开始-…md」截成尾段
    # ASCII，与卡面声明全路径必 no-match=R2 豁免对中文名失效；PM 合流轮 2026-11-04 修复，
    # 锁=tests/verify-dryrun-gate-test.py T9 红基线 fails=2 在册）
)


def _trial_norm_path(p, tmp):
    """缺失路径归一：反斜杠→正斜杠、剥一次性 tmp 前缀（python 打的是 tmp 内绝对路径）、
    剥 ./、win 侧小写（NTFS 不分大小写，realpath 形与字面形大小写差属测量伪值非语义差）。"""
    p = re.sub(r"/{2,}", "/", (p or "").replace("\\", "/"))  # python3.10 缺文件报文=双反斜杠字面量，归一须并斜杠否则前缀/尾段双失配（实测红=本行补的）
    for tp in (tmp, os.path.realpath(tmp)):
        tp = re.sub(r"/{2,}", "/", tp.replace("\\", "/")).rstrip("/")
        if tp and p.lower().startswith(tp.lower() + "/"):
            p = p[len(tp) + 1:]
            break
    if p.startswith("./"):
        p = p[2:]
    p = p.strip("/")
    return p.lower() if os.name == "nt" else p


def _redfirst_declared_hit(out_text, tmp, files):
    """R2 判据：失败输出点名的缺失路径 ∈ 卡面 files → 返回**声明形**（痕上 missing= 用声明原样，
    账上可查的是卡面承诺而非 tmp 实测串）。files 空=无豁免。命中判据=归一后全等或以声明路径为尾段。"""
    norm = {}
    for f in files or []:
        f = str(f or "").strip()
        if f:
            norm[_trial_norm_path(f, "")] = f
    if not norm:
        return None
    for rx in _TRIAL_MISS_RES:
        for cand in rx.findall(out_text or ""):
            n = _trial_norm_path(cand, tmp)
            if not n:
                continue
            for k, declared in norm.items():
                if n == k or n.endswith("/" + k):
                    return declared
    return None


def _run_verify_trial(verify):
    """试跑唯一内核（REQ-VERIFY-DRYRUN-1 R1/R3 实跑体；REQ-CARD-TYPEGATE-1 G3 三态接入点）。
    一次性临时目录 bash 实跑一次，宿主与 _run_verify 同源；返回 (rc, out, tmp)，**tmp 由调用方
    清理**（R2 豁免匹配要同一 tmp 基准剥前缀）。TimeoutExpired=rc 124；OSError 上抛（三口/类型闸
    各自落自己的归因文案）。抽 kernel 不为改语义，为 G3 复用同一跑体（禁复刻铁令）。"""
    cmd = _unwrap_verify(verify)
    bashbin = _resolve_verify_bash()  # 宿主同源：判据试跑与 done/verdict 复跑同一 bash（防 WSL 假红双标）
    try:
        to = int(os.environ.get("CARD_TRIAL_TIMEOUT_SEC", "60"))
    except ValueError:
        to = 60
    tmp = tempfile.mkdtemp(prefix="card-trial-")  # R3：一次性目录，判据里任意命令不落现网仓/道树/state
    try:
        r = subprocess.run([bashbin, "-c", cmd], cwd=tmp, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=to, creationflags=NO_WIN)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace"), tmp
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss" % to, tmp
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _trial_verify(cid, verify, files, entry, ev_old="-", ev_new="-"):
    """三口共用实跑判定体（R1/R2/R3 判定全在这，禁复刻）。放行=只落痕；拒绝=die+rejectlog。
    REQ-CARD-TYPEGATE-1 重构注记：实跑核抽出为 _run_verify_trial（G3 同源接入），本函数
    **判定与归因语义一字未动**——set/start 两入口与 warn/off 档的 create 仍走本闸。"""
    try:
        rc, out, tmp = _run_verify_trial(verify)
    except OSError as e:
        event(cid, ev_old, ev_new, "PM", "TRIAL-REJECT entry=%s rc=- reason=R3-exec fail=%r" % (entry, e))
        die("%s 拒：判据试跑无法执行(%r)——宁红不猜（REQ-VERIFY-DRYRUN-1）" % (entry, e))
    try:
        _trial_verdict(cid, rc, out, tmp, files, entry, ev_old, ev_new)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _trial_verdict(cid, rc, out, tmp, files, entry, ev_old, ev_new):
    """_trial_verify 的判定半（拆函数=纯机械分段，行为与 REQ-VERIFY-DRYRUN-1 原版逐字一致）。"""
    tail = " ".join(out.split())[-500:]  # R3 归因上限
    via = "%s/%s" % (os.name, sys.platform)
    if rc == 0:
        event(cid, ev_old, ev_new, "PM", "TRIAL-PASS entry=%s rc=0 via=%s" % (entry, via))
        return
    miss = None if rc == 124 else _redfirst_declared_hit(out, tmp, files)
    if miss:
        event(cid, ev_old, ev_new, "PM",
              "TRIAL-REDFIRST entry=%s rc=%d rule=R2 missing=%s via=%s tail=%s"
              % (entry, rc, _san(miss), via, tail))
        print("WARN %s 试跑 red-first 豁免(R2)：缺失产物 %s 已声明于卡面 files，放行——首红基线属预期态；"
              "正式复跑在 done/verdict（REQ-VERIFY-DRYRUN-1）" % (entry, miss), flush=True)
        return
    reason = "R3-timeout" if rc == 124 else ("R2-empty-files" if not files else "R2-no-match")
    event(cid, ev_old, ev_new, "PM",
          "TRIAL-REJECT entry=%s rc=%d reason=%s via=%s tail=%s" % (entry, rc, reason, via, tail))
    _reject_note("verify-trial", "%s %s rc=%d %s tail=%s" % (entry, cid, rc, reason, tail))
    die("%s 拒：判据试跑非零退码 rc=%d（豁免规则=%s）via=%s｜tail=%s——发卡即考试"
        "（REQ-VERIFY-DRYRUN-1 业主 seq=31③：非零退码不许发卡/换判据）。本卡产物尚未落盘属预期者"
        "（red-first「守卫要验得红」），请 --files 声明缺失产物路径走 R2 豁免；"
        "断言红 rc=1/乱码 $'/超时/非声明路径不豁免。注意：--files 同时是 done 越域闸的域"
        "（commands 勾卡时非域文件必拒），申报=该卡交付文件全集，不是只报判据绿料"
        % (entry, rc, reason, via, tail))


def verify_trial_precheck(a):
    """create/set/start 三口统一入口——**唯一调用点=cli.run 进锁前**（锁外试跑；写锁包住整个
    cmd_* 函数体，长执行不得进锁，同 verdict 纪律）。先过既有静态闸（同源函数复用零改写；
    crosslang 同族闸落树即随跑=globals 现查，不在此复刻），再实跑。verify 空=无题可考，跳过
    （OPEN 无判据仍由 cmd_create 本体闸拒；DRAFT 清判据通道不动）。"""
    v = (getattr(a, "verify", None) or "").strip()
    if not v:
        return
    _check_verify_bashn(v, a.cmd)  # 静态闸先于试跑：跑不通的乱码归试跑管，已知病形归静态闸管（归因不互糊）
    _cross = globals().get("_check_verify_crosslang")  # seq=30 同族静态闸：在树即先跑（本道基线未含=静默跳过）
    if _cross:
        _cross(v, a.cmd)
    _single = globals().get("_check_verify_single")  # G4 单行为测试静态闸（REQ-CARD-TYPEGATE-1，同族在树即跑）
    if _single:
        _single(v, a.cmd)
    if a.cmd == "create":
        _open = globals().get("_type_open_check")  # G3 按类型开卡检查（enforce 档专属；父卡 R1/R2 语义在 warn/off 原样）
        if _open and _type_epoch_mode()[0] == "enforce":
            _open(a, v)  # G3-CALLSITE-TYPEGATE
            return
        _trial_verify(a.id, v, list(a.files or []), "create")
        return
    if not card_exists(a.id):
        return  # 卡不存在=账不在此，缺卡归因交回 cmd_set/cmd_start 本体闸（试跑不借他人账）
    c = load(a.id)
    st = c.get("status") or "-"
    files = list(a.files) if (a.cmd == "set" and a.files is not None) else list(c.get("files") or [])
    _trial_verify(a.id, v, files, a.cmd, st, st)


# ── REQ-CARD-TYPEGATE-1（业主信箱 seq=32 定版 2026-11-04）：type 字段正式化+按类型开卡检查+三要素 ──
# 业主定版原则「一卡一行为测试，用例可以输、不能不能赛」。四闸分工：
#   G1 判据三要素必填（op 操作什么 / input 输入什么 / expect 预期输出什么）——闸只判非空+人话
#      形态基线（expect 超长散文=提示级入 events 不硬拒；「可赛」归 G3 实跑，此闸不重复审文）；
#      REQ expect=本卡首次定义（写不出=需求未定退回不开卡），BUG expect=本就存在当前坏掉的行为。
#   G2 type 字段正式化：PM 定版枚举 REQ/BUG/TEST/MECH/OPS；create 必带 --type 且与卡号前缀一致，
#      前缀约定升级为账本字段+闸门校验。存量卡**零写账回填**（同 reject_count 缺省 0 纪律，防
#      235 卡大迁移）：读侧 card_type() 按卡面字段→前缀→REQ 缺省三级推导；engine.tick/看板消费面
#      零改动（防连锁，账本+闸门 only）。
#   G3 按类型开卡检查（核心差别，只 create 一口按类型走；set/start 保持父卡 R1/R2 语义）：
#      BUG=必须干净红（绿=假案子拒；崩溃形/缺案子拒并归因「先修用例」；红输出落
#      docs/evidence/card-open/<cid>.log=bug 存在证据+events 痕回写）；
#      REQ/TEST/MECH/OPS=可赛：rc∈{0,断言红}放行，崩溃族拒；父卡 R2 red-first 豁免为配套保留。
#      实跑核复用父卡 _run_verify_trial（禁复刻铁令），本段只做分类与按类型裁决。
#   G4 判据单行为测试（seq=31① 机读化，全类型全三口统一）：顶层 && 拼接且两段各起独立解释器/
#      测试=硬拒；`bash -c "cd <dir> && python -I …"` 包裹形首段 cd=环境切换放行（剥壳+引用区
#      感知切分，正则区分）。连锁修正=stoploss X07 夹具随规矩演进翻「拒绝」（范本即病根）。
# 生效阶梯（config.setting：env CARD_TYPE_EPOCH > TEAM/config.toml gates.cardtype_epoch > 缺省，
# 照 CARD_3FORM_EPOCH 先例）：未设/空=warn 只回显；off=完全静默；YYYY-MM-DD 界后 enforce 拒立。
# G4 为静态闸族（同 bashn/crosslang）不受档位管——病形拦截零成本，存量 create 脚本无此形不炸。
# 锁=tests/card-type-gate-test.py（K1~K12，K12 撤闸差分对=守卫验得红）。

CARD_TYPE_ENUM = ("REQ", "BUG", "TEST", "MECH", "OPS")  # PM 定版（seq=32-2）：需求/缺陷修复/验收资产/框架机制/运维处置
_TYPE_EPOCH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def card_type(c):
    """读侧类型推导（G2 存量零回填纪律）：卡面 type 字段优先；缺字段按卡号前缀推导
    （前缀∈五枚举=其值；前缀∉枚举/无前缀=REQ 缺省）；字段乱值也回退前缀推导。纯读函数，
    engine/看板不消费=零连锁，闸门与对账侧用。"""
    t = str((c or {}).get("type") or "").strip().upper()
    if t in CARD_TYPE_ENUM:
        return t
    pfx = str((c or {}).get("id") or "").split("-", 1)[0].upper()
    return pfx if pfx in CARD_TYPE_ENUM else "REQ"


def _type_epoch_mode():
    """生效档判定（每调用现读，env 可逐笔翻转——测试与灰度同路；同 _three_form_mode 形制）。"""
    raw = str(setting("gates.cardtype_epoch", "CARD_TYPE_EPOCH", "") or "").strip().lower()
    if raw in ("off", "0", "none"):
        return "off", raw
    if _TYPE_EPOCH_RE.match(raw):
        return ("enforce" if now()[:10] >= raw else "warn"), raw
    if raw:
        print("WARN: 类型闸生效配置(CARD_TYPE_EPOCH/gates.cardtype_epoch)=%r 非日期非 off，按 warn 档处理"
              % raw, file=sys.stderr)
    return "warn", raw


def _check_card_type_gate(a):
    """G2 类型静态闸：enforce 必带 --type、type∈枚举、卡号前缀∈枚举且与 type 一致，违者拒+
    rejectlog；warn 只回显；off 静默。返回立卡落账值（缺 --type=前缀推导，界前哨兵/存量脚本不炸）。
    **两个调用点**（G3-CALLSITE 同纪律各带标记）：_type_open_check 试跑前 + cmd_create 落账。"""
    mode, raw = _type_epoch_mode()
    t = str(getattr(a, "card_type", "") or "").strip().upper()
    cid = str(getattr(a, "id", "") or "")
    pfx = cid.split("-", 1)[0].upper()
    derived = pfx if pfx in CARD_TYPE_ENUM else "REQ"
    problems = []
    if not t:
        if mode == "enforce":
            problems.append("缺 --type（枚举=%s）" % "/".join(CARD_TYPE_ENUM))
    elif t not in CARD_TYPE_ENUM:
        problems.append("--type=%r 不在枚举（%s）" % (t, "/".join(CARD_TYPE_ENUM)))
    elif pfx not in CARD_TYPE_ENUM:
        problems.append("卡号前缀 %r 不在枚举（%s）——卡号按类型重编" % (pfx, "/".join(CARD_TYPE_ENUM)))
    elif pfx != t:
        problems.append("--type=%s 与卡号前缀 %s- 不符" % (t, pfx))
    if problems:
        joined = "；".join(problems)
        if mode == "enforce":
            _reject_note("card-type", "%s %s" % (cid, joined))
            die("类型闸拒： %s——枚举 PM 定版 REQ=需求 BUG=缺陷修复 TEST=验收资产 MECH=框架机制 "
                "OPS=运维处置；卡号前缀约定已升级为账本字段+闸门校验（REQ-CARD-TYPEGATE-1 G2，"
                "业主 seq=32；生效配置=%s）" % (joined, raw))
        print("WARN: 类型闸：%s——warn 档不阻断，生效日起同形拒立（REQ-CARD-TYPEGATE-1 G2）"
              % joined, file=sys.stderr)
    if not t and mode == "warn" and getattr(a, "cmd", "") == "create":
        print("WARN: 卡 %s 未带 --type——按卡号前缀推导 type=%s；生效日起缺 --type 将拒立"
              "（REQ-CARD-TYPEGATE-1 G2）" % (cid, derived), file=sys.stderr)
    return t if t in CARD_TYPE_ENUM else derived


_C3_SPEC = (("c_op", "op", "操作什么(--c-op)"),
            ("c_in", "input", "输入什么(--c-in)"),
            ("c_expect", "expect", "预期输出什么(--c-expect)"))
_C3_EXPECT_LONG = 500  # 提示级阈值（人话形态基线）；「可赛」与否归 G3 试跑实判，此闸不复审文


def _check_criteria3_gate(a, c):
    """G1 判据三要素必填闸（seq=32-1）：任一缺失/空=enforce 拒+rejectlog，warn 回显。
    expect 超长=TRIAL- 前缀提示痕入 events（不硬拒；前缀=reconcile 幽灵卡跳过分支同纪律，
    被拒 create 的卡号不得变成每圈 ALARM）并回显。唯一调用点=cmd_create（G1-CALLSITE-TYPEGATE）。"""
    mode, raw = _type_epoch_mode()
    vals = {key: str(getattr(a, attr, "") or "").strip() for attr, key, _d in _C3_SPEC}
    c["criteria"] = dict(vals)
    if mode == "off":
        return
    miss = [d for _attr, key, d in _C3_SPEC if not vals[key]]
    if miss:
        joined = "、".join(miss)
        if mode == "enforce":
            _reject_note("criteria3", "%s 三要素缺失: %s" % (c.get("id"), joined))
            die("判据三要素必填（G1）拒：缺 %s——操作什么/输入什么/预期输出什么缺一不可"
                "（REQ expect=由本卡首次定义，写不出=需求未定退回不开卡；BUG expect=本就存在"
                "当前坏掉的行为；REQ-CARD-TYPEGATE-1 业主 seq=32；生效配置=%s）" % (joined, raw))
        if any(vals.values()):
            print("WARN: 判据三要素缺 %s——warn 档不阻断，生效日起同形拒立（REQ-CARD-TYPEGATE-1 G1）"
                  % joined, file=sys.stderr)
    exp = vals["expect"]
    if len(exp) > _C3_EXPECT_LONG:
        event(c.get("id") or "-", "-", "-", "PM",
              "TRIAL-CRITERIA-LONG entry=%s len=%d 阈=%d（散文级提示，不硬拒——「可赛」归 G3 试跑实判）"
              % (getattr(a, "cmd", "create"), len(exp), _C3_EXPECT_LONG))
        print("WARN: expect 长 %d>阈 %d 达散文级——「预期输出什么」应一句话可对拍（G1 提示，不阻断）"
              % (len(exp), _C3_EXPECT_LONG), file=sys.stderr)


# G4 切分辅助：判据是纯文本静态闸，不做完整 shell 语法——判定点只有「顶层 && 与段头是不是
# 解释器/环境切换」。引号区内的 && 不算顶层（bash -c "cd x && python y"=包裹形，非拼案）。
_INTERP_WORDS = frozenset(("python", "python3", "pytest", "bash", "sh", "dash", "ksh",
                           "node", "npm", "npx", "ruby", "perl", "mvn", "go", "deno", "bun"))
_ENV_SWITCH_HEADS = frozenset(("cd", "pushd", "popd", "export", "source", "set", "umask",
                               "unset", "alias", "."))
_PRECMD_WORDS = frozenset(("env", "command", "time", "nohup"))


def _word_base(w):
    b = str(w or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return re.sub(r"\.exe$", "", b)


def _split_toplevel_and(text):
    """按顶层 && 切段：跳过 '…'/"…" 引用区（dquote 内 \\ 转义下一字符）；引号内 && 不算顶层。"""
    segs, buf, q, i, n = [], [], None, 0, len(text or "")
    while i < n:
        ch = text[i]
        if q:
            buf.append(ch)
            if ch == "\\" and q == '"' and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
            i += 1
            continue
        if ch in "\"'":
            q = ch
            buf.append(ch)
            i += 1
            continue
        if text[i:i + 2] == "&&":
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    segs.append("".join(buf))
    return [s.strip() for s in segs]


def _seg_interp(seg):
    """段头是否起独立解释器/测试（env/command/time/nohup + VAR=xx 前导词剥掉再看）。"""
    toks = str(seg or "").split()
    i = 0
    while i < len(toks):
        b = _word_base(toks[i])
        if b in _PRECMD_WORDS:
            i += 1
            while i < len(toks) and not toks[i].startswith("-") and "=" in toks[i]:
                i += 1  # env FOO=bar 形赋值词
            continue
        return b in _INTERP_WORDS
    return False


def _check_verify_single(verify, cmd):
    """G4 判据单行为测试静态闸（业主 seq=31① 机读化，全类型全三口统一；REQ-CARD-TYPEGATE-1）：
    剥壳后顶层 && 且两段各起独立解释器/测试=硬拒——「一卡一行为测试，状态检查不许外挂」；
    实锤范本=stoploss X07 旧夹具 `python …py && bash …check`（该形正是被本规矩禁掉的拼案）。
    首段 cd/export/source 等=环境切换不算测试段（`bash -c "cd <dir> && python -I …"` 包裹形放行）；
    解释器段≥2 才拒（`cd x && python a && python b` 仍拒——cd 豁免只免首段，不免拼两个测试）。
    锁=tests/card-type-gate-test.py K10/K11。"""
    inner = _unwrap_verify(verify)
    if "&&" not in inner:
        return
    segs = _split_toplevel_and(inner)
    if len(segs) < 2:
        return
    first_head = _word_base((segs[0].split() or [""])[0])
    if first_head in _ENV_SWITCH_HEADS:
        segs = segs[1:]  # 环境切换段不占测试位（K11 包裹形不误伤）
    interps = [s for s in segs if _seg_interp(s)]
    if len(interps) >= 2:
        _reject_note("verify-single", "%s %s 拼案拒: %s" % (cmd, _san(verify)[:120], " | ".join(segs)))
        die("%s 拒绝：判据用顶层 && 拼接独立解释器/测试（%d 段）——%s｜一卡一行为测试，"
            "状态检查不许外挂（REQ-CARD-TYPEGATE-1 G4，业主 seq=31① 机读化：用例可以输、"
            "不能不能赛）。环境切换请用 `bash -c \"cd <dir> && <单一测试>\"` 包裹形；"
            "确属两个行为请拆两张卡" % (cmd, len(interps), _san(verify)[:160]))


_CRASH_RES = (("SyntaxError（解释器解析即炸）", re.compile(r"SyntaxError")),
              ("ModuleNotFound/ImportError（起不来）", re.compile(r"ModuleNotFoundError|ImportError")),
              ("command not found（命令不可见/乱码碎片）", re.compile(r"command not found")),
              ("文件不可开（can't open file/No such file）", re.compile(r"can't open file|No such file")))
# 断言判据形（seq=32 原话「明确报『预期X实际Y』」）：AssertionError / 预期…实际… /
# unittest FAILED / pytest failed 尾行。U+FFFD 类零字面量（本文件全 ASCII 码点安全）。
_ASSERT_RES = (re.compile(r"AssertionError"), re.compile(r"预期[^\n]{0,80}实际"),
               re.compile(r"\bFAILED\b"), re.compile(r"\bfailed\b"), re.compile(r"[Aa]ssertion"))


def _trial_classify(rc, out, tmp, files):
    """试跑三态分类（G3 判定表；复用父卡 _redfirst_declared_hit 匹配器，禁复刻）：
    green / redfirst(info=声明路径) / clean-red / crash(info=归因)。
    分类优先序：rc0→红first→崩溃码(124/126/127)与崩溃病形→断言形→其余非零（含裸 Traceback
    无断言、false 类静默）=不可赛归崩溃族——「能跑能终止有明确断言结果」才算可赛。"""
    out = out or ""
    if rc == 0:
        return "green", None
    miss = None if rc == 124 else _redfirst_declared_hit(out, tmp, files)
    if miss:
        return "redfirst", miss
    if rc == 124:
        return "crash", "超时（判据跑不终止=不能赛）"
    if rc in (126, 127):
        return "crash", "rc=%d（命令缺失/不可执行=起不来）" % rc
    for label, rx in _CRASH_RES:
        if rx.search(out):
            return "crash", label
    if any(rx.search(out) for rx in _ASSERT_RES):
        return "clean-red", None
    if "Traceback" in out:
        return "crash", "运行期异常无断言形（没跑到判定）"
    return "crash", "非零输出无断言判据形（可输不可赛的静默红）"


def _bug_evidence(cid, verify, rc, out):
    """BUG 干净红试跑输出落 docs/evidence/card-open/<cid>.log=bug 存在证据（业主 seq=32 原文
    「红的输出落 evidence 作 bug 存在证据」）。根=TEAM 上一级（仓根；沙箱=临时根不落现网仓）；
    卡号洗成文件名安全形；写不进=宁红不拒放（证据即开卡判据本体）。返回仓内相对 posix 路径。"""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(cid or "")) or "card"
    p = os.path.join(os.path.dirname(TEAM), "docs", "evidence", "card-open", safe + ".log")
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("# REQ-CARD-TYPEGATE-1 G3：BUG 卡开卡干净红证据（业主 seq=32）\n"
                    "# ts=%s cid=%s rc=%d\n# verify=%s\n\n%s\n" % (now(), cid, rc, _san(verify), out))
    except OSError as e:
        die("create 拒：BUG 证据写失败(%r)——红的输出不落 evidence=bug 存在无凭，不开卡"
            "（REQ-CARD-TYPEGATE-1 G3）" % e)
    return "docs/evidence/card-open/%s.log" % safe


def _type_open_check(a, v):
    """G3 按类型开卡检查（enforce 档 create 专属；set/start 保持父卡 R1/R2 语义不动）。
    实跑核=_run_verify_trial（父卡唯一 kernel，禁复刻）；裁决表：
      BUG  : clean-red→放行+evidence 落账+events 痕；green→假案子拒；crash/redfirst(缺案子)→拒
             并归因「复现用例本身不能赛，先修用例再立卡」（业主 seq=32 崩溃红不许开卡）。
      其余 : green/clean-red→放行（可赛但输正常，不作必红要求）；redfirst→父卡 R2 豁免配套保留；
             crash→拒（崩溃族）。"""
    t = _check_card_type_gate(a)  # G2-CALLSITE-TYPEGATE
    try:
        rc, out, tmp = _run_verify_trial(v)
    except OSError as e:
        event(a.id, "-", "-", "PM", "TRIAL-REJECT entry=create rc=- reason=R3-exec fail=%r" % e)
        die("create 拒：判据试跑无法执行(%r)——宁红不猜（REQ-VERIFY-DRYRUN-1）" % e)
    try:
        state, info = _trial_classify(rc, out, tmp, list(a.files or []))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tail = " ".join((out or "").split())[-500:]
    via = "%s/%s" % (os.name, sys.platform)
    if state == "redfirst":
        if t == "BUG":
            event(a.id, "-", "-", "PM", "TRIAL-REJECT entry=create rc=%d reason=G3-bug-nocase "
                  "type=BUG missing=%s via=%s" % (rc, _san(info), via))
            _reject_note("card-open-type", "create %s BUG 复现用例未落盘(missing=%s)：R2 豁免不覆盖"
                         % (a.id, info))
            die("create 拒：BUG 卡复现用例未落盘（rc=%d missing=%s）——R2 豁免只护『首红资产』型需求卡；"
                "bug 卡的干净红输出本身就是开卡凭据，缺复现用例=无凭据——复现用例本身不能赛，"
                "先修用例再立卡（REQ-CARD-TYPEGATE-1 G3，业主 seq=32）" % (rc, info))
        event(a.id, "-", "-", "PM", "TRIAL-REDFIRST entry=create rc=%d rule=R2 missing=%s via=%s tail=%s"
              % (rc, _san(info), via, tail))
        print("WARN create 试跑 red-first 豁免(R2)：缺失产物 %s 已声明于卡面 files，放行——首红基线属"
              "预期态（REQ-VERIFY-DRYRUN-1 R2 配套保留，G3）" % info, flush=True)
        return
    if state == "green":
        if t == "BUG":
            event(a.id, "-", "-", "PM", "TRIAL-REJECT entry=create rc=0 reason=G3-fake-case type=BUG via=%s" % via)
            _reject_note("card-open-type", "create %s BUG 立卡即绿=假案子（bug 未复现）" % a.id)
            die("create 拒：BUG 卡复现用例跑出**绿**（rc=0）——假案子：bug 未复现，无可证行为，"
                "不开卡（REQ-CARD-TYPEGATE-1 G3，业主 seq=32『崩溃红与立卡即绿（假案子）均不许开卡』）")
        event(a.id, "-", "-", "PM", "TRIAL-PASS entry=create rc=0 type=%s via=%s" % (t, via))
        return
    if state == "clean-red":
        if t == "BUG":
            rel = _bug_evidence(a.id, v, rc, out)
            event(a.id, "-", "-", "PM", "TRIAL-CLEANRED entry=create rc=%d type=BUG evidence=%s via=%s"
                  % (rc, rel, via))
            print("BUG 卡干净红可证：试跑 rc=%d 断言形；红的输出已落 %s（G3=bug 存在证据，"
                  "REQ-CARD-TYPEGATE-1）" % (rc, rel), flush=True)
            return
        event(a.id, "-", "-", "PM", "TRIAL-LOSS entry=create rc=%d type=%s via=%s" % (rc, t, via))
        return
    event(a.id, "-", "-", "PM", "TRIAL-REJECT entry=create rc=%d reason=G3-crash(%s) type=%s via=%s tail=%s"
          % (rc, _san(info), t, via, tail))
    _reject_note("card-open-type", "create %s %s 崩溃形拒: %s rc=%d" % (a.id, t, info, rc))
    die("create 拒：%s 卡判据崩溃形（%s）rc=%d｜tail=%s——用例可以输、不能不能赛"
        "（REQ-CARD-TYPEGATE-1 G3，业主 seq=32）%s"
        % (t, info, rc, tail,
           "——复现用例本身不能赛，先修用例再立卡" if t == "BUG" else
           "；超时可调 CARD_TRIAL_TIMEOUT_SEC，改形后重立"))


# ── REQ-CARD-3FORM-1 卡面三段式（GitHub issue form 形态；业主信箱 seq=22 B 项）──────────────
# 三必填栏=①原始来源 --src ②整理后需求 --story ③执行计划 --plan，落卡面字段 src/story/plan
# （graph.json 纯增量，不动既有字段语义）。①栏字段与 C 项 CA3「同源至多一票反查」的 src
# **合并为同一字段**（本卡内拍板：CA3 反查键=①栏原文，防两字段语义重叠）。
# 式样拍板=三独立参数，弃「desc 首行式 来源：…；需求：…；计划：…」——issue form 本是一栏一
# 字段，逐段校验/逐段点名以字段为界最干净；首行式遇栏内含「；」即断列。定稿见 docs 与卡 note。
# 生效阶梯（config.setting：env CARD_3FORM_EPOCH > TEAM/config.toml gates.card3form_epoch > 缺省）：
#   未设/空串 = warn：只回显 WARN 不拒（判据生效日前的世界——各测试房/系统哨现形调用不误伤）；
#   off       = 完全静默；
#   YYYY-MM-DD= 今日>=该日逐段拒立。生效日=本卡 VERIFIED 落 main 之日，由 PM 烘进 config.toml
#   （一处生效，全会话同档）；界前存量与已 OPEN/CLAIMED 卡天然不返工——闸只挂 create。
# 缺段=当场 die 点名缺哪段，并落 rejectlog（MECH-REJT-1 现成通道，看板「被拒记录」可见）。
# 术语表联动（seq=4 机制）：②栏命中 README STATE-TERMS 别名列黑话→回显标准措辞，不阻断
# （命中清单=现有术语表数据源，不自造词表；解析失败=静默——提醒面不得反过来卡立卡）。

_3FORM_SECTIONS = (("src", "--src", "①原始来源(业主原话/信箱seq/前序卡号)"),
                   ("story", "--story", "②整理后需求(用户故事+验收标准)"),
                   ("plan", "--plan", "③执行计划(方案要点)"))

_3FORM_EPOCH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TERMS_CACHE = None  # [(黑话, 标准词, 状态码)]——README STATE-TERMS 别名列，进程内只解析一次


def _reject_note(tool, note):
    """守卫当场拒单进「被拒记录」（MECH-REJT-1 同格式 ts|工具|事由；append-only 钉LF）。
    记账失败不连坐闸本身——闸红是对的，账黑不了闸才是新病。"""
    try:
        os.makedirs(os.path.dirname(REJECTS), exist_ok=True)
        with open(REJECTS, "a", encoding="utf-8", newline="") as f:
            f.write("%s|%s|%s\n" % (now(), _san(tool), _san(note)))
    except OSError:
        pass


def _three_form_mode():
    """生效档判定，返回 (mode, raw)：mode ∈ warn/off/enforce；raw=配置原值（拒单归因用）。
    每笔 create 现读（env 可逐调用翻转——测试与灰度都靠这个）。"""
    raw = str(setting("gates.card3form_epoch", "CARD_3FORM_EPOCH", "") or "").strip().lower()
    if raw in ("off", "0", "none"):
        return "off", raw
    if _3FORM_EPOCH_RE.match(raw):
        return ("enforce" if now()[:10] >= raw else "warn"), raw
    if raw:
        print("WARN: 三段式生效配置(CARD_3FORM_EPOCH/gates.card3form_epoch)=%r 非日期非 off，按 warn 档处理"
              % raw, file=sys.stderr)
    return "warn", raw


def _terms_alias_table():
    """seq=4 术语表现有数据源=cc-team/README.md STATE-TERMS 区块三列表（状态|术语|别名；
    state_std_test 同源钉前两列，别名列此前只有文档面、这里首次变机器可读命中清单）。
    表缺/解析不出=返回 []（联动是提醒面，绝不卡立卡）。"""
    global _TERMS_CACHE
    if _TERMS_CACHE is None:
        rows = []
        try:
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")
            with open(p, encoding="utf-8") as f:
                txt = f.read()
            m = re.search(r"<!-- STATE-TERMS-BEGIN -->(.*?)<!-- STATE-TERMS-END -->", txt, re.S)
            for line in (m.group(1).splitlines() if m else []):
                cols = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cols) < 3 or cols[0] not in STATUS_ENUM:
                    continue
                for al in re.split(r"[／/]", cols[2]):
                    al = al.strip()
                    if al and al != cols[1]:
                        rows.append((al, cols[1], cols[0]))
        except OSError:
            rows = []
        _TERMS_CACHE = rows
    return _TERMS_CACHE


def _term_hint(story):
    """②栏文本命中黑话→逐条回显标准措辞（print 非 die；REQ-CARD-3FORM-1 联动 seq=4）。"""
    hits = [(al, t, st) for al, t, st in _terms_alias_table() if al in story]
    for al, t, st in hits:
        print("TERM-WARN: ②需求栏命中黑话「%s」→ 术语表(seq=4)标准措辞「%s」(%s)——不阻断，仅提醒"
              % (al, t, st))
    return hits


def _three_form_gate(a, c):
    """create 专属闸：三字段落账 → 缺段判定（enforce 拒+点名+rejectlog；warn 回显）→ 术语联动。"""
    vals = {k: (getattr(a, k, "") or "").strip() for k, _flag, _desc in _3FORM_SECTIONS}
    c.update(vals)
    mode, raw = _three_form_mode()
    if mode == "off":
        return
    miss = [desc for k, _flag, desc in _3FORM_SECTIONS if not vals[k]]
    if miss:
        joined = "、".join(miss)
        if mode == "enforce":
            _reject_note("create", "%s 三段式缺段拒立: %s" % (c["id"], joined))
            die("三段式缺段拒立：缺 %s——三必填=①原始来源(--src，兼 CA3 同源反查键) "
                "②整理后需求(--story) ③执行计划(--plan)（REQ-CARD-3FORM-1；生效配置=%s，"
                "阶梯 env CARD_3FORM_EPOCH / config.toml gates.card3form_epoch）" % (joined, raw))
        print("WARN: 卡面三段式缺 %s——现 warn 档不阻断；生效日起同形态缺段将拒立"
              "（REQ-CARD-3FORM-1，env CARD_3FORM_EPOCH / config.toml gates.card3form_epoch）"
              % joined, file=sys.stderr)
    if vals["story"]:
        _term_hint(vals["story"])


def cmd_create(a):
    if not ID_RE.match(a.id):
        die("非法卡号: " + a.id)
    if len(a.id) > 64:  # 防超长 id 把文件名撑爆(ext4 单段 255B)后裸崩栈
        die("卡号超 64 字符(文件名风险): " + a.id[:40] + "...")
    if card_exists(a.id):
        die("卡号已存在: " + a.id)
    if os.path.exists(os.path.join(ARCHIVE, a.id + ".json")):
        die("卡号 %s 是归档旧账名（state/archive/），不许复用——换号，或经业主拍板把归档账回灌后再立" % a.id)
    cards = all_cards()
    for d in a.deps:
        if d == a.id:
            die("依赖不许自引用: " + d)
        if d not in cards:
            die("依赖卡不存在: " + d)
    c = dict(id=a.id, title=a.title, description=a.desc or "", deps=a.deps,
             status=a.status, owner=None, files=a.files, lanes=a.lanes,
             verify=a.verify or "", ref_impl=a.ref_impl or "", hold=a.hold or "",
             repo=a.repo or "", parent=a.parent or "", commit=None, evidence=None,
             output={}, verdict=None, created_at=now(), updated_at=now())
    require_edge("-", c["status"], "create")  # 迁移矩阵同源闸（create 只放 DRAFT/OPEN/HOLD，与 cli choices 双钉）
    _check_lanes(c["lanes"], "create")
    _check_verify_bashn(c["verify"], "create")  # MECH-GATE-BASHN-1：判据文本含 bash -n 非 .sh 目标=发卡即拒
    _check_verify_crosslang(c["verify"], "create")  # seq=30 同族反向：python 直跑 .sh 亦永红=发卡即拒
    _three_form_gate(a, c)  # REQ-CARD-3FORM-1：三段式三必填（缺段拒立+rejectlog）+ 术语联动回显
    c["type"] = _check_card_type_gate(a)  # G2-CALLSITE-TYPEGATE：type 字段正式化落账（枚举/前缀一致闸）
    _check_criteria3_gate(a, c)  # G1-CALLSITE-TYPEGATE：判据三要素必填（缺=拒立；超长 expect 提示入痕）

    # MECH-RESIDENT-1：两族卡在立卡处分叉——岗卡四件缺一拒立；普通卡带驻字段拒立
    # （字段=族籍，不许混挂；在岗态初值 OFF，上岗唯一门=verdict PASS）。
    resident = bool(getattr(a, "resident", False))
    has_duty_fields = any((getattr(a, k, "") or "").strip() for k in RESIDENT_REQUIRED)
    if resident:
        missing = [flag for k, (flag, _sem) in RESIDENT_REQUIRED.items()
                   if not (getattr(a, k, "") or "").strip()]
        if missing:
            die("岗卡(resident)必填四件缺失: %s（岗责/实现锚/心跳判据/失败语义，seq=8 第3条）"
                % ",".join(missing))
        c["resident"] = True
        c["resident_status"] = "OFF"
        for k in RESIDENT_REQUIRED:
            c[k] = (getattr(a, k) or "").strip()
    elif has_duty_fields:
        die("普通卡不许带岗卡字段(%s)——常驻岗加 --resident 立岗卡；一次性卡走七态机不走三态"
            % ",".join(flag for k, (flag, _s) in RESIDENT_REQUIRED.items()
                       if (getattr(a, k, "") or "").strip()))
    if c["status"] in ("OPEN", "CLAIMED") and not c["verify"]:
        die("OPEN 卡必须带 --verify 可执行判据（完成=命令退出码0，不接受任何自然语言自述）")
    save(c)
    event(c["id"], "-", c["status"], "PM", "create")
    if c["status"] == "OPEN":  # 队列只收 OPEN（与 reindex GC 同一规则）；HOLD 解冻时由 thaw 补位
        for ln in a.lanes:
            lane_q_push(ln, c["id"])
    print(c["id"], c["status"])

_WATCHED = ("deps", "files", "lanes", "verify", "ref_impl", "hold", "title", "description", "output",
            "duty", "anchor", "heartbeat", "fail",
            "repo")  # MECH-RESIDENT-1 四件 + REQ-MERGE-GATE-1 repo 补登留痕

def _set_diffs(c, before, old_status):
    """MECH-AUD1：set 记字段 diff 进 events——此前只记一行「set」，改了什么全靠事后猜；
    闸门字段(verify/files)被偷换在旧 events 里是静默的。值截 30 字符防流水爆炸。"""
    diffs = []
    if old_status != c["status"]:
        diffs.append("status:%s>%s" % (old_status, c["status"]))
    for k in _WATCHED:
        nv = json.dumps(c.get(k), ensure_ascii=False)
        ov = before.get(k, "null")
        if nv != ov:
            diffs.append("%s:%s>%s" % (k, ov[:30] + ("…" if len(ov) > 30 else ""),
                                       nv[:30] + ("…" if len(nv) > 30 else "")))
    return "; ".join(diffs)


def cmd_set(a):
    c = load(a.id)
    old = c["status"]
    before = {k: json.dumps(c.get(k), ensure_ascii=False) for k in _WATCHED}
    old_lanes = list(c.get("lanes") or [])
    if a.status:
        if a.status not in STATUS_ENUM:
            die("status 不在枚举: " + a.status)
        if c["status"] == "CLAIMED" and a.status != "CLAIMED":
            die("CLAIMED 卡不许 set 改状态(绕过 release 会留幽灵锁/旧锚)：回 OPEN 用 release，"
                "作废用 cancel(--force)，验收回退用 verdict REJECT")
        if c["status"] in ("DONE", "VERIFIED") and a.status != c["status"]:
            die("DONE/VERIFIED 卡改状态必须走 verdict(REJECT 退回重做)——set 是 PM 改字段通道，"
                "不是验收通道(二轮矩阵审计：DONE→set OPEN 会留旧 commit 字段绕开清锚)")
        if a.status == "OPEN" and not c.get("verify"):
            die("set → OPEN 需卡面已有 verify（先 --verify 补判据；OPEN 无判据=调度黑洞）")
        if a.status in ("CLAIMED", "DONE", "VERIFIED"):
            die("set 不许直写 %s(绕过锁/验货/验收)：CLAIMED 用 claim 或 start，DONE 用 done，"
                "VERIFIED 用 verdict PASS，回 OPEN 用 release/verdict REJECT" % a.status)
        if a.status == "CANCELED" and os.path.isdir(claim_dir(a.id)) and not a.force:
            die("有活跃 claims，作废请走 cancel(带 --force 明示)")
        require_edge(old, a.status, "set")  # 迁移矩阵同源闸：set 只在 DRAFT/OPEN/HOLD/CANCELED 互移
        c["status"] = a.status
    if getattr(a, "verify", None) is not None and not a.verify.strip() and c["status"] != "DRAFT":
        die("不许在非 DRAFT 状态清空 verify(领卡/解冻都靠它做闸门)；要重定判据先 set --status DRAFT")
    for k in ("deps", "files", "lanes"):
        v = getattr(a, k)
        if v is not None:
            if k == "lanes":
                _check_lanes(v, "set")
            if k == "deps":
                cards = all_cards()
                for d in v:
                    if d == a.id:
                        die("依赖不许自引用")
                    if d not in cards:
                        die("依赖卡不存在: " + d)
            c[k] = v
    if a.verify is not None:
        _check_verify_bashn(a.verify, "set")  # 换判据同源头闸（三口规则唯一实现=本函数，两侧不复刻）
        _check_verify_crosslang(a.verify, "set")  # seq=30 同族反向闸三口一致
        c["verify"] = a.verify
    if a.ref_impl is not None:
        c["ref_impl"] = a.ref_impl
    if a.hold is not None:
        c["hold"] = a.hold
    if a.title is not None:
        c["title"] = a.title
    if a.desc is not None:
        c["description"] = a.desc
    # MECH-RESIDENT-1：四件只许岗卡改；在岗态(resident_status)不在 set 通道——
    # 三态迁移唯一走 duty 闸（同「set 不许直写 CLAIMED/DONE/VERIFIED」纪律）。
    for k in RESIDENT_REQUIRED:
        v = getattr(a, k)
        if v is not None:
            if not is_resident(c):
                die("普通卡(%s)无岗卡四件可改——resident 族籍在 create 定死；要转岗卡请立岗卡新号" % a.id)
            if not v.strip():
                die("岗卡四件(%s %s)不许清空——锚/判据缺位=互盯环失明，先补再改"
                    % (k, RESIDENT_REQUIRED[k][0]))
            c[k] = v.strip()
    if getattr(a, "repo", None) is not None:
        # REQ-MERGE-GATE-1：存量 DONE 账的素材补登通道——主干闸要卡面 repo，旧 done 没落过；
        # 裸改 graph.json=对账 ALARM 来源，PM 侧唯一合法补法走此 set（_WATCHED 含 repo，进 diff 留痕）。
        c["repo"] = a.repo.strip()
    o = _parse_out(a)
    if o is not None:
        c["output"] = o
    save(c)
    d = _set_diffs(c, before, old)
    extra = "set" + ((" diff=" + d) if d else "")
    # BUG-FUSE-S5-SNIFF（2026-11-03 21:01 假 OWNER-RED 实锤）：机器自触发 set（唯一现用户=fuse gate
    # 的 S2 STOPLOSS 挂起，经 env CC_SELF_TRIGGER 明示）在痕尾带 " self=<名>"。静默钟据此按标记分人/机，
    # 不再靠文本嗅探——旧嗅探把 PM 归因轮必引 STOPLOSS 字样的真人痕整段误剔=假静默升级挂业主红条。
    st = (os.environ.get("CC_SELF_TRIGGER") or "").strip()
    if st:
        extra += " self=%s" % _san(st)
    event(a.id, old, c["status"], "PM", extra)
    # BUG-LANES-STALE-1（2026-11-02 dispatch.log 实锤）：改道同步不得看当前脸色——旧码把同步
    # 块包在 `status==OPEN` 里，DONE 卡 set --lanes 新道被整段跳过，随后 verdict REJECT 又按
    # prev_owner 把卡回填旧道，dispatcher 每圈 propose@旧道被 lanes 硬闸拒（空转+杀会话死循环）。
    # 修法=对「该卡的队列条目」做单卡 reindex：① 除名无条件（非白名单道的残渣现在就清，
    # 不等卡回 OPEN）；② 入队只在 OPEN 且一律按卡面 lanes 白名单补齐（lane_q_push 自带去重，
    # 幂等；改道/转 OPEN 同入口）——与 cmd_reindex 全表修复同一规则「冲突以卡面为准」。
    if a.lanes is not None:
        new = set(c["lanes"])
        for ln in old_lanes:
            if ln not in new:
                lane_q_pop(ln, a.id)
    if c["status"] == "OPEN":
        for ln in (c.get("lanes") or []):
            lane_q_push(ln, a.id)
    if a.status == "CANCELED":
        lane_lock_release(c.get("owner"), a.id)
        lane_q_drop_everywhere(a.id)
    print(a.id, c["status"])

def cmd_show(a):
    print(json.dumps(load(a.id), ensure_ascii=False, indent=2))

def cmd_list(a):
    cards = all_cards()
    for cid, c in cards.items():
        if a.status and c["status"] != a.status:
            continue
        if a.owner and c.get("owner") != a.owner:
            continue
        ready = "ready" if (c["status"] == "OPEN" and deps_ok(c, cards)) else ""
        line = "%s %s owner=%s deps=%s" % (c["id"], c["status"], c.get("owner") or "-",
                                           ",".join(c.get("deps", [])) or "-")
        if ready:
            line += " " + ready
        print(line + " " + (c.get("title") or "")[:30])

def _detect_pid_ns(choice):
    """MECH-AUD1：pid 属于哪侧 OS 的 pid 空间。auto=按执行侧判（dispatcher 在 WSL 起 worker
    传 WSL pid=wsl；Windows 侧手领=win）。ns 决定 recycle 的权威核验通道，杜绝跨空间糊账。"""
    if choice and choice != "auto":
        return choice
    return "win" if os.name == "nt" else "wsl"

def _anchor_truth(pid):
    """BUG-ANCHOR-NS-1：claim 锚记**真实** namespace，不信调用方声明的。
    dispatcher 从 WSL 起 powershell，Popen 的 proc.pid 是 WSL 侧编号（/proc/<pid>/comm 实测
    可读=interop 进程在场），却按 --pid-ns win 记账——recycle 拿这个号去问 Windows，
    同号异主进程一出现就永远装活（取证 2650385 案+ghost claim 复发，道被占死不再接活）。
    posix 侧读 /proc=零子进程成本：读得到=该号活在 WSL 空间，(ns,comm)=(wsl,实测名)；
    读不到=进程已死或真是 Windows 号，维持原声明（不恶化）。Windows 侧不反查：起 powershell
    只为钉锚=台账写锁里数秒挂锁，且本侧声明本就与事实同向。
    互操作 exec 有落地窗：fork 刚起的瞬间 /proc comm 还是继承的暂名（bash），短轮询直到
    名字落成为会话壳族（powershell/pwsh）才记——壳族外身份/没等到=None（只纠空间不记名，
    recycle 侧对无可信锚名的锚退回存在性判定，防「拿暂名当身份」误杀）。"""
    if os.name == "nt" or not pid:
        return None
    comm = ""
    for _ in range(4):
        try:
            comm = open("/proc/%d/comm" % int(pid), encoding="utf-8", errors="replace").read().strip()
        except (OSError, ValueError):
            return None
        if re.sub(r"\.exe$", "", comm.lower()) in ("powershell", "pwsh"):
            return "wsl", comm.lower()  # 原样带 .exe 也归一存小写；recycle 侧 _norm_name 再对拍
        time.sleep(0.3)
    return "wsl", None

def _lanes_gate(c, owner, cmd):
    # 2026-10-30 协议债①定版：lanes 非空=硬闸（原为 WARN+done --force 补票）
    # 2026-11-02 REQ-POOL-ELASTIC-1：lanes=["ANY"]（弹性工位池，业主定版「要多少是业务需求，
    # 性能控制并发就行了」）=通配白名单，任意 slot-<n> 工位皆可过闸；具体道仍是显式互斥声明。
    if c.get("lanes") and "ANY" not in c["lanes"] and owner not in c["lanes"]:
        die("%s 拒绝：owner=%s 不在卡面 lanes=%s 白名单（lanes 非空即硬闸；ANY=池通配；确需换道 PM 先 set --lanes）"
            % (cmd, owner, c["lanes"]))

def _requeue_card(c, fallback_owner):
    """REJECT/release 回 OPEN 的重派入队一律按卡面 lanes 白名单（BUG-LANES-STALE-1）——
    旧码按 prev_owner/owner0 入队：卡被「set --lanes 改道」后回填进的是**旧道**，
    dispatcher propose@旧道必被 _lanes_gate 硬闸拒，每圈空转杀会话。与闸门同源：
    lanes 非空=白名单即唯一入队身份；为空（自由卡）只能回旧 owner 道。回队头=重派优先。"""
    for ln in (c.get("lanes") or [fallback_owner]):
        lane_q_push(ln, c["id"], front=True)

def _declared_epoch(a):
    """上报者自证的任期号：--epoch 优先，其次会话环境变量 CC_LANE_EPOCH。
    名牌制(REQ-BEAT-ANCHOR-1)：任期必须由**上报方携带**（claim 输出/派发 brief 发的牌），
    绝不回账本代查——代查=僵尸现读现报、永远对得上，核验归零。"""
    ep = getattr(a, "epoch", None)
    if ep is not None:
        try:
            return int(ep)
        except (TypeError, ValueError):
            die("--epoch 需为整数，收到: %r" % (ep,))
    v = (os.environ.get("CC_LANE_EPOCH") or "").strip()
    try:
        return int(v) if v else None
    except ValueError:
        return None

def cmd_epoch(a):  # REQ-BEAT-ANCHOR-1：派发前发牌——该道任期 +1 入账并打印
    ep = lane_epoch_advance(a.owner, a.cid or "")
    print("EPOCH %s %s" % (a.owner, ep))

def _claim_epoch(owner, cid, declared):
    """claim 入账即盖当前任期（写闸规矩③）：
    带 --epoch=派发型——必须与道台账现值同班，错班拒（发牌后道被抢/牌过期=幽灵派发）；
    不带=手工/PM 型——自动 +1 发新班。"""
    if declared is not None:
        cur = lane_epoch_read(owner).get("epoch")
        if cur is None or int(cur) != declared:
            die("名牌核验拒：--epoch=%s 但道 %s 台账任期=%s（派发侧应先 `cards_dag.py epoch %s` 发牌；"
                "手工 claim 不带 --epoch 自动发新班）" % (declared, owner, cur, owner))
        return declared
    return lane_epoch_advance(owner, cid)

def _git_dirty(repo):
    """工作区脏件快照（业主 seq=15）：交付仓 `git status --porcelain`（未跟踪目录按
    目录行折叠，不逐文件展开——首测 HANDOVER-OUT n=200 触顶全因 tmp/parity 快照树逐文件
    刷屏，真交接信息被噪声淹没）→ [{p 路径, st XY 码, sz 大小, mt mtime}]。cap=200。
    失败返回 (None, [])，由调用方出声——判据双侧形态=git 原生命令 Win/WSL 同码。"""
    try:
        from .config import _resolve_repo
        root = _resolve_repo(repo or os.path.dirname(TEAM))  # 卡面 repo 缺省=主仓（dirname(TEAM)）
        # 自走 subprocess 不用 config.git()：它 strip 整段输出会把首行状态码前导空格吃掉
        # （" M base.py"→"M base.py"→路径丢一字符，W1 实锤）。bytes+显式 UTF-8 解码（GBK 纪律）。
        r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", root,
                            "status", "--porcelain"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WIN)
        if r.returncode != 0:
            return False, []
        files = []
        for line in r.stdout.decode("utf-8", "replace").splitlines()[:200]:
            if len(line) < 4:
                continue
            p = line[3:].strip()
            e = {"p": p, "st": line[:2]}
            try:
                s = os.stat(os.path.join(root, p))
                e["sz"] = s.st_size
                e["mt"] = time.strftime("%Y-%m-%dT%H:%M:%S+0800", time.localtime(s.st_mtime))
            except OSError:
                e["sz"], e["mt"] = -1, "-"   # 已删除的脏行照样登记（交接要看得到缺席）
            files.append(e)
        return True, files
    except Exception as exc:
        print("WARN: _git_dirty(%r) 异常：%r" % (repo, exc))  # 静默假不拦=最坏形态，出声
        return False, []


def _workarea_snapshot(c, who, ep):
    captured, files = _git_dirty(c.get("repo") or "")
    if not captured:
        print("WARN: WORKAREA 快照失败(repo=%r)——claim/release 不拦，但本任工作区账有缺口，"
              "下任 handover 不可信" % (c.get("repo") or ""))
    return {"repo": c.get("repo") or "(TEAM)", "at": now(), "epoch": ep, "by": who,
            "files": files, "captured": captured}


def _handover_out(c, who):
    """release/REJECT 共用（seq=15 第2条：回收不清工作区）：release 时点脏件清单落卡面
    handover，前任任期号+脏件表随行；快照空/失败=旧账原样保留（交接宁多勿造）。返回交接件数。"""
    wa = c.get("workarea") or {}
    snap = _workarea_snapshot(c, who, wa.get("epoch") or "-")
    n = len(snap["files"]) if snap["captured"] else 0
    if n:
        wa["handover"] = {"epoch": wa.get("epoch"), "by": who, "at": now(), "files": snap["files"]}
        wa.pop("handover_prev", None)   # 前任账已在本任消费，不再下传（防滚雪球）
    c["workarea"] = wa
    return n, wa.get("epoch")


# ── REQ-CLAIM-COUNTER-1（业主 seq=28/29/30）· R1/R5b 卡面计数 ────────────────────────────
# 递增唯一纪律：计数只在**账本写锁内**经 _bump 落——现读卡面→内存 +=1→同一次 save
# （与 graph.json 同笔写，禁读改分离竞窗）。调用方全部已持 team 写锁（claim/release 走
# WRITERS、verdict REJECT 自理锁）；无锁调用方（mergetrain R5 conflict 落定=写口外的火车
# 进程）一律走 bump_count——本文件内同一纪律自己进锁重载，业主原话「同一纪律」的落地。
# R3 存量不回填：字段缺席=读侧缺省 0（_as_count），不写回造实体；新账自落地日起。
# 超阈键（REJECT-THRESHOLD）经 ccards.alerts.record 落账，走 fuse-S6 既有升级闭环直达
# 业主；键名不得以「业主/FUSE-S6」开头（S6 不升级=静默丢报，R6 纪律同源）。
COUNTER_FIELDS = ("claim_count", "release_count", "conflict_count", "reject_count")
REJECT_CARD_MAX = 2  # R5b 同卡打回阈值（第 3 拒触发；比较符 >，与 mergetrain C_CARD 同值）


def _as_count(v):
    """计数读侧缺省（R3）：缺字段/坏值一律按 0 读——渲染与递增都容忍存量无字段，不报错。"""
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _bump(c, field):
    """写锁内递增（claim/release/reject 计数唯一写点）——返回新值。调用方必须已持写锁。"""
    if field not in COUNTER_FIELDS:
        die("_bump 未知计数字段: %s（只认 %s）" % (field, ",".join(COUNTER_FIELDS)))
    n = _as_count(c.get(field)) + 1
    c[field] = n
    return n


def bump_count(cid, field, cmd="bump"):
    """无锁调用方的原子入账入口（mergetrain R5 conflict_count 专用；REQ-CLAIM-COUNTER-1）。
    进锁重载卡面再 _bump（不信任调用方快照，同 cmd_verdict 进锁重载纪律），同笔 save+刷新。"""
    if field not in COUNTER_FIELDS:
        die("bump_count 字段须在计数字段内: %s" % field)
    team_lock_acquire(cmd)
    try:
        c = load(cid)
        n = _bump(c, field)
        save(c)
        dag_refresh()
    finally:
        team_lock_release()
    return n


# ── BUG-REJECT-STOPLOSS-1（业主 seq=30「下一次REJECT后不再重派（STOPLOSS标记上卡面）」+
#    seq=35「STOPLOSS=第2拒即停」）· 同卡连拒止血，落在 reject 写路径内，不托孤给报警认领链 ──
# 病根（BUG-VECMEM-STALE-1 九连拒 6 槽翻台实锤）：旧通道只有 alerts.record(REJECT-THRESHOLD)
# 零处置，而报警键 ~20min 被机器到期销账、早于 fuse S6 一级唤 PM 阈 FUSE_ALERT_ACK1_MIN=30min
# ——升级环从未闭环，业主「第2拒即停」空转。止血与 REJECT 同笔写锁事务落账：连拒达阈即在
# 账上 release(拒通道已含·留 HANDOVER 不清工作区)+set HOLD，hold 带 STOPLOSS:gate 前缀+归因
# 一句话；停派闸复用既有同源语义（cmd_claim 挂失闸 / engine._stoploss / fuse S2 同族）。
# 报警键照挂供看板（④上报阈 >REJECT_CARD_MAX 语义不动），其销账/认领与否不再承载止血。
REJECT_STOPLOSS_DEF = 2  # 业主定版「第2拒即停」；env REJECT_STOPLOSS_MAX 可配（下限 1）


def _reject_stoploss_max():
    """止血阈：env REJECT_STOPLOSS_MAX > 配置文件 gates.reject_stoploss_max > 默认 2。
    下限 1——0 不表示「停用」（防手滑 0 造全员误停），停用档由 NOACT 撤闸开关明示。"""
    return int(num_setting("gates.reject_stoploss_max", env="REJECT_STOPLOSS_MAX",
                           default=REJECT_STOPLOSS_DEF, minimum=1))


def _reject_stoploss_noact():
    """撤闸红检缝（BUG-REJECT-STOPLOSS-1 验收③）：REJECT_STOPLOSS_NOACT=1/true/on/yes 时
    止血整段空转（只报不救），与 fuse FUSE_NOACT 同式——测试差分对靠它验证断言红得起来。"""
    return str(os.environ.get("REJECT_STOPLOSS_NOACT") or "").strip().lower() in (
        "1", "true", "on", "yes")


def _reject_stoploss(c, rn, note):
    """reject 落账通道内的原子止血（调用方必须已持账本写锁，与 reject 落账同一次 save）。
    返回刚落笔的 hold 文案=止血已发生；None=不触（未达阈/卡面已挂 STOPLOSS 不双写/撤闸档）。
    本通道进入时卡面恒为 OPEN（DONE/VERIFIED→OPEN 由 REJECT 分支刚完成，release 语义已含、
    HANDOVER 已由 _handover_out 留驻不清工作区）；若未来接在制(CLAIMED)卡的新通道，必须先
    走 release 带交接再调本函数（同 fuse seg_s2 do_stoploss 口径），此处宁可不触不造假。"""
    if rn < _reject_stoploss_max():
        return None
    if "STOPLOSS" in str(c.get("hold") or ""):
        return None  # 已挂失（STOPLOSS:gate 或 fuse 写停的 :fuse）——归因撤闸前不双写
    if _reject_stoploss_noact():
        return None  # 撤闸档：只报不救（差分对红检位）
    if c["status"] != "OPEN":
        return None  # 见 docstring：非 OPEN 通道未接，宁不触不造假
    require_edge("OPEN", "HOLD", "set")  # 同源闸：复用 set 通道合法边（本函数=机件版 set，
    # 痕 actor=gate 不伪装 PM 真人痕拨静默钟——requirement ⑤）
    hold = ("STOPLOSS:gate 同卡累计第%d拒≥阈%d(业主定版第2拒即停)自动止血停复派——%s；"
            "等 PM 归因，撤闸=set --hold ''" % (rn, _reject_stoploss_max(), note))
    c["hold"] = hold
    c["status"] = "HOLD"
    return hold


def cmd_claim(a):
    c = load(a.id)
    if c["status"] != "OPEN":
        die("状态 %s 不可领，只领 OPEN（PM 直做卡用 start）" % c["status"])
    if not c.get("verify"):
        die("卡缺 verify 可执行判据，不可领取（先补 set --verify）")
    cards = all_cards()
    miss = deps_blockers(c, cards)
    if miss:
        die("依赖未满足: " + ",".join(miss))
    if "STOPLOSS" in str(c.get("hold") or ""):  # 业主 seq=30 停复派闸（engine._stoploss 同源语义）
        die("挂失停派：hold 含 STOPLOSS，PM 归因解除（set --hold ''）前禁领——防判据病卡空转烧槽位")
    _lanes_gate(c, a.owner, "claim")
    require_edge("OPEN", "CLAIMED", "claim")  # 迁移矩阵同源闸
    pid = a.pid or None  # 不再默认 os.getpid()（命令进程秒死=天生僵尸锚）
    # kind 判据（R3 定版语义在 v3 的等价形）：有锚证据=lane——--pid 传了真值，或 v3 道锁在场
    # （dispatcher 侧建过道）；两者皆无=手工/PM 认领 → session 档，recycle 只 WARN 永不
    # release（E2E-T1 误杀防）。v1 文件锁兼容档已随 MECH-PURE1 清零（现场零残锁核实 2026-10-31）。
    has_anchor = bool(pid) or os.path.exists(lane_lock_dir(a.owner))
    kind = "lane" if has_anchor else "session"
    pid_ns = _detect_pid_ns(getattr(a, "pid_ns", None)) if pid else None  # 无锚 pid 就不落 ns（没得判）
    pid_comm = None
    if pid_ns:
        truth = _anchor_truth(pid)  # BUG-ANCHOR-NS-1：实测空间>声明，假 win 声明在此纠偏
        if truth:
            pid_ns, pid_comm = truth
    session = _session_from_env()
    ep = _claim_epoch(a.owner, c["id"], _declared_epoch(a))  # 名牌制：入账即盖任期
    lane_lock_acquire(c["id"], a.owner, pid, session, kind, cards,
                      pid_ns=pid_ns, pid_comm=pid_comm, epoch=ep)  # 一道一活=结构性（原 _one_wip 扫描有 TOCTOU）
    try:
        lock_claim(c["id"], a.owner, pid, session, kind, selfheal=True,
                   pid_ns=pid_ns, pid_comm=pid_comm, epoch=ep)
        old = c["status"]
        c["status"] = "CLAIMED"; c["owner"] = a.owner; c["claimed_at"] = nowz()
        c["claimed_epoch"] = ep  # 卡面名牌：done/note/progress 对班的唯一基准
        _bump(c, "claim_count")  # R1 认领计数（OPEN->CLAIMED；写锁内递增，随下方 save 同笔落账）
        # MECH-ANC1：PROTOCOL 2026-10-28 业主定版账锚在 v3 补落——first_claimed_at=首次领卡时刻
        # （累计耗时/成本唯一锚），仅缺失才写；release/REJECT 清 claimed_at 重派时**不清它**。
        if not c.get("first_claimed_at"):
            c["first_claimed_at"] = c["claimed_at"]
        save(c)
    except BaseException:
        lane_lock_release(a.owner, c["id"])  # 卡锁/落账失败→退道锁，不留占位
        raise
    event(c["id"], old, "CLAIMED", a.owner, "claim e%s" % ep)
    # seq=15 第1条：claim 即登记工作区（写锁内落卡面 workarea=本任 git 脏件快照）。
    # 前任有 handover 则随卡面继承并喊 HANDOVER-IN——下一任从工作区把活接过来续做，不从零返工。
    wa = _workarea_snapshot(c, a.owner, ep)
    prev_ho = (c.get("workarea") or {}).get("handover")
    if prev_ho:
        wa["handover_prev"] = prev_ho
        event(c["id"], "CLAIMED", "CLAIMED", a.owner, "HANDOVER-IN prev=e%s by=%s files=%d" % (
            prev_ho.get("epoch"), prev_ho.get("by"), len(prev_ho.get("files") or [])))
    elif wa["files"]:
        event(c["id"], "CLAIMED", "CLAIMED", a.owner, "WORKAREA n=%d captured=%s" % (
            len(wa["files"]), wa["captured"]))
    c["workarea"] = wa
    save(c)
    lane_q_pop(a.owner, c["id"])
    print(c["id"], "CLAIMED", a.owner, "epoch=%s" % ep)  # 工人把此号揣兜里，done/note/progress 对班用

def cmd_start(a):  # PM 直做卡/临时泳道：HOLD(已补判据)|DRAFT|OPEN → CLAIMED 一步到位；kind=session
    c = load(a.id)
    if a.verify:
        _check_verify_bashn(a.verify, "start")  # 补判据同源头闸（create/set/start 三口一致）
        _check_verify_crosslang(a.verify, "start")  # seq=30 同族反向闸三口一致
        c["verify"] = a.verify
    if c["status"] not in ("OPEN", "HOLD", "DRAFT"):
        die("start 只接 OPEN/HOLD/DRAFT，当前 " + c["status"] + "（CLAIMED 用 done 收口）")
    require_edge(c["status"], "CLAIMED", "start")  # 迁移矩阵同源闸
    if c.get("hold"):
        die("有人工 hold 条件(%s)，先清/set 解除再 start" % c["hold"])
    if not c.get("verify"):
        die("start 需可执行判据：带 --verify 补上，或后续走 thaw 闸门")
    cards = all_cards()
    miss = deps_blockers(c, cards)
    if miss:
        die("依赖未满足: " + ",".join(miss))
    _lanes_gate(c, a.owner, "start")
    pid = a.pid or None
    pid_ns = _detect_pid_ns(getattr(a, "pid_ns", None)) if pid else None
    pid_comm = None
    if pid_ns:
        truth = _anchor_truth(pid)  # BUG-ANCHOR-NS-1 同 claim：锚记真实空间+进程名
        if truth:
            pid_ns, pid_comm = truth
    session = _session_from_env()
    ep = _claim_epoch(a.owner, c["id"], _declared_epoch(a))  # 名牌制同 claim：入账即盖任期
    lane_lock_acquire(c["id"], a.owner, pid, session, "session", cards,
                      pid_ns=pid_ns, pid_comm=pid_comm, epoch=ep)
    try:
        old = c["status"]
        lock_claim(c["id"], a.owner, pid, session, "session", selfheal=True,
                   pid_ns=pid_ns, pid_comm=pid_comm, epoch=ep)
        c["status"] = "CLAIMED"; c["owner"] = a.owner; c["claimed_at"] = nowz()
        c["claimed_epoch"] = ep
        # MECH-ANC1：PROTOCOL 2026-10-28 业主定版账锚在 v3 补落——first_claimed_at=首次领卡时刻
        # （累计耗时/成本唯一锚），仅缺失才写；release/REJECT 清 claimed_at 重派时**不清它**。
        if not c.get("first_claimed_at"):
            c["first_claimed_at"] = c["claimed_at"]
        save(c)
    except BaseException:
        lane_lock_release(a.owner, c["id"])
        raise
    event(c["id"], old, "CLAIMED", a.owner, "start e%s" % ep)
    lane_q_pop(a.owner, c["id"])
    print(c["id"], "CLAIMED", a.owner, "epoch=%s" % ep)

def cmd_bind(a):  # MECH-S1：调度器起会话后回填认领的 session 身份（覆写幂等）；双锁同写
    c = load(a.id)
    if c["status"] != "CLAIMED":
        die("bind 只接 CLAIMED 卡（认领残渣才有所属会话），当前 " + c["status"])
    cj = os.path.join(claim_dir(a.id), "claim.json")
    try:
        with open(cj, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        die("claim.json 缺失/损坏，无法 bind（先 release 重领）: " + a.id)
    d["session_id"] = a.session
    with open(cj, "w", encoding="utf-8", newline="") as f:
        json.dump(d, f)
    owner = c.get("owner")
    if owner and os.path.isdir(lane_lock_dir(owner)):
        mp = os.path.join(lane_lock_dir(owner), "meta.json")
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except (OSError, ValueError):
            m = dict(owner=owner, pid=d.get("pid"), kind=d.get("kind", "lane"), ts=d.get("ts"))
        m["session_id"] = a.session
        with open(mp, "w", encoding="utf-8", newline="") as f:
            json.dump(m, f)
    event(a.id, c["status"], c["status"], a.session, "bind session_id")
    # REQ-SOP-CONFIRM-1 R1 签收痕（轻形态，业主 seq=34 S2/seq=35① 拍板：派了≠领了；不做工人
    # 主动回执，签收=dispatcher bind 成迹当刻）。与双锁（claim.json/lane meta）同一 WRITERS 锁段
    # 落卡面 c["confirm"]+events 机读行 CONFIRM card=<cid> lane=<道> epoch=<N> workarea=<快照锚>
    # ——语义「道已接手、快照已钉」，三件缺一不算签收（缺位只 WARN 不落痕：bind 照成主链不拦，
    # 「起而未签」超时由 SOP-MISS 兜，判据见 ops/dispatch.py R2 段）。幂等=同 (lane, epoch) 已签
    # 不再补行（backfill_binds 复绑不刷流水）；重派换任期=覆写再签（新 epoch 新行）。
    conf = c.get("confirm") or {}
    cf_lane = str(c.get("owner") or d.get("owner") or "")
    cf_ep = c.get("claimed_epoch")
    if cf_ep is None:
        cf_ep = d.get("epoch")
    cf_wa = (c.get("workarea") or {}).get("at")
    if cf_lane and cf_ep is not None and cf_wa:
        if str(conf.get("lane")) != cf_lane or str(conf.get("epoch")) != str(cf_ep):
            c["confirm"] = {"at": nowz(), "lane": cf_lane, "epoch": cf_ep, "workarea": cf_wa}
            save(c)
            event(a.id, c["status"], c["status"], a.session,
                  "CONFIRM card=%s lane=%s epoch=%s workarea=%s" % (a.id, cf_lane, cf_ep, cf_wa))
    else:
        print("WARN: CONFIRM 三件缺一(lane=%r epoch=%r workarea=%r)——bind 照成、签收不落，"
              "超 SOP_CONFIRM_TIMEOUT_MIN 由 SOP-MISS 升级: %s"
              % (cf_lane or None, cf_ep, cf_wa, a.id), file=sys.stderr)
    print(a.id, "BOUND", a.session)

def _file_in_domain(f, patterns):
    for p in patterns:
        if fnmatch.fnmatch(f, p) or f.startswith(p.rstrip("*")):
            return True
        # src/test 镜像自动放行：verify 要求跑单测时测试文件物理上只能在 src/test
        # （Maven 目录约定，业主 2026-10-30 DL-F2 事故定案），立卡忘圈镜像不再拦
        if "/src/main/" in p:
            mp = p.replace("/src/main/", "/src/test/")
            if fnmatch.fnmatch(f, mp) or f.startswith(mp.rstrip("*")):
                return True
    return False

def _parse_out(a):
    """--out-json 唯一通道（MECH-LIT1：legacy k=v;k=v 退役——`;`/`=` 分隔符语义先天碎裂，
    二轮起只 WARN 转正、存量交付实测已全走 JSON；给了坏 JSON=拒，不猜）。"""
    if getattr(a, "out_json", None):
        try:
            o = json.loads(a.out_json)
        except ValueError as e:
            die("--out-json 不是合法 JSON: %s" % e)
        if not isinstance(o, dict):
            die("--out-json 须为 JSON 对象（键值对），收到 %s" % type(o).__name__)
        return o
    return None

def _epoch_gate_card(c, declared, action):
    """卡面名牌对班（REQ-BEAT-ANCHOR-1 写闸规矩③ + 僵尸免疫②）：
    错班=旧班乱报 → **拒收**并记 events 痕（不改卡面，当场免疫）；
    未申报=过渡档（落地前派出的旧 brief 没带牌）→ 放行只 WARN——判活权威已迁事件流，
    补不补牌不影响回收环；存量无 claimed_epoch 的旧认领=无牌可对，照旧。"""
    cep = c.get("claimed_epoch")
    if cep is None:
        return
    if declared is None:
        print("WARN: %s 未申报任期(--epoch)，过渡放行——工人应回传 claim 发的牌（本卡在任 e%s）"
              % (action, cep), file=sys.stderr)
        return
    if int(declared) != int(cep):
        event(c["id"], c["status"], c["status"], "-",
              "EPOCH-STALE %s 拒收 decl=e%s claimed=e%s" % (action, declared, cep))
        die("错班拒收(REQ-BEAT-ANCHOR-1 名牌制)：%s 申报任期 e%s ≠ 卡面在任 e%s——"
            "该道若被回收重派即换班，旧班一切上报作废（僵尸乱报免疫）" % (action, declared, cep))

def _done_anchor_tip(c, repo, decl, force):
    """MECH-DONEANCHOR-TIP-1 交卷锚：交卷瞬间读交付仓 worktree 实 tip（HEAD）并和自报
    commit 分型，随 done 落账+进 events——「自报≠树尖」时账上有实据可对（23:3x 孤儿进程案/
    EPOCH-STALE done 拒收案当时只能口头主张）。分型三档：
      same     自报==树尖 → 正常落账
      ahead    自报是树尖祖先 → 交卷后 tip 合法再推进（证据拆独立提交=done 写闸既成惯例，
               本闸不得把它拦红），WARN+落 tip/ahead 差异
      diverged 自报不在树尖历史 = 孤儿进程/错仓/改写哈希签名 → **拒收交卷**（账不落）；
               force=PM 明示放行（痕落账、Verifier 可复核 REJECT）
    树尖读不到/探测失败=红（宁红不猜，与 REQ-MERGE-GATE-1 同纪律）。
    红绿锁 tests/done-anchor-tip-check.py：摘本记录段=锁红。"""
    rc, tip = git(repo, "rev-parse", "HEAD")
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{40}", tip or ""):
        die("交卷锚失败：repo=%s worktree HEAD 读不到（rc=%d）——树尖缺失则交卷无实据可对账，"
            "宁红不猜拒落账" % (repo, rc))
    rc2, branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch if (rc2 == 0 and branch and branch != "HEAD") else "detached"
    if tip == decl:
        rel, ahead = "same", 0
    else:
        rc3, _ = git(repo, "merge-base", "--is-ancestor", decl, tip)
        if rc3 == 0:
            rc4, n = git(repo, "rev-list", "--count", "%s..%s" % (decl, tip))
            ahead = int(n.strip()) if (rc4 == 0 and (n or "").strip().isdigit()) else 0
            rel = "ahead"
        elif rc3 == 1:
            rel, ahead = "diverged", 0
        else:
            die("交卷锚失败：自报 %s ↔ 树尖 %s is-ancestor 探测 rc=%d（repo 损坏/对象被 GC）"
                "——宁红不猜拒落账" % (decl[:12], tip[:12], rc3))
    anchor = dict(tip=tip, branch=branch, rel=rel, ahead=ahead, ts=nowz())
    if rel == "ahead":
        print("WARN: 交卷锚：自报 %s ≠ 树尖 %s（branch=%s，树尖比自报多 %d 笔——证据拆独立提交等"
              "合法形态照过；tip/ahead 已落卡面，交卷即验与火车据实对账）"
              % (decl[:12], tip[:12], branch, ahead), file=sys.stderr)
    if rel == "diverged":
        msg = ("交卷锚拒收（自报≠树尖且分叉）：自报 %s 不在 worktree 实 tip %s（branch=%s, repo=%s）"
               "历史——孤儿进程/错仓交卷/改写哈希签名，账不落"
               % (decl[:12], tip[:12], branch, repo))
        event(c["id"], c["status"], c["status"], "-",
              "DONEANCHOR-REJECT decl=%s tip=%s branch=%s" % (decl[:12], tip[:12], branch))
        if not force:
            die(msg + "；如对账后确需交卷，PM 用 --force 明示并 note 分叉原因")
        anchor["forced"] = True
        print("WARN: " + msg + " —— --force 明示放行（痕已落账，Verifier 复核可 REJECT）",
              file=sys.stderr)
    return anchor

def cmd_done(a):
    c = load(a.id)
    if c["status"] != "CLAIMED":
        if c["status"] in ("DONE", "VERIFIED"):
            die("卡已勾过(%s)，不可重复勾卡" % c["status"])
        die("只有 CLAIMED 卡可勾 DONE，当前 " + c["status"] + "（HOLD/OPEN 直做卡用 start 开工）")
    require_edge("CLAIMED", "DONE", "done")  # 迁移矩阵同源闸
    if not os.path.isdir(claim_dir(c["id"])):
        die("无认领记录(claims)，流程异常")
    if a.owner != c["owner"]:
        die("属主不符：卡 owner=%s，声称 owner=%s" % (c["owner"], a.owner))
    if c.get("lanes") and "ANY" not in c["lanes"] and a.owner not in c["lanes"] and not a.force:
        # ANY 通配与 _lanes_gate 同源（REQ-POOL-ELASTIC-1）：池卡由动态工位交卷，属主道不固定
        die("owner=%s 不在卡面 lanes=%s 白名单（ANY=池通配放行；PM 兼做/临时泳道加 --force）" % (a.owner, c["lanes"]))
    _epoch_gate_card(c, _declared_epoch(a), "done")  # 写闸规矩③：done 校验任期，错班拒收
    repo = a.repo or c.get("repo")
    if not repo:
        die("需要 --repo 或卡面 repo，否则无法验货")
    rc, _ = git(repo, "cat-file", "-e", a.commit + "^{commit}")
    if rc != 0:
        die("commit 在代码仓库不存在: " + a.commit)
    # MECH-DONEANCHOR-TIP-1 交卷锚：自报 commit 之外，交卷瞬间的 worktree 实 tip 一并算分型
    # 并落账（same/ahead/diverged；diverged 在助手内 die=账不落，见 _done_anchor_tip）。
    rcn, decl_full = git(repo, "rev-parse", a.commit + "^{commit}")
    if rcn != 0 or not re.fullmatch(r"[0-9a-f]{40}", (decl_full or "").strip()):
        die("交卷锚失败：自报 %r 无法归一化全哈希（repo=%s rc=%d）——宁红不猜"
            % (a.commit, repo, rcn))
    anchor = _done_anchor_tip(c, repo, decl_full.strip(), a.force)
    rc, out = git(repo, "show", "--pretty=format:", "--name-only", a.commit)
    changed = [l for l in out.splitlines() if l.strip()]
    if c.get("files"):
        outside = [f for f in changed if not _file_in_domain(f, c["files"])]
        if outside:
            die("越域改动(拒绝勾卡): " + " ".join(outside[:5]))
        # 聚合提醒（修 D7 的半步）：带本卡号"(cid):"的其它提交若有越域文件，打 WARN 给 Verifier
        # （commit-msg 钩子强制 动词(卡号): 格式，聚合素材现成；不阻断——域外指定产物是既成惯例）
        # MECH-LIT1：全史扫描改窗口——本仓 git log --all --name-only 整扫要数秒，而 WARN 素材
        # 只需覆盖验收周期；CARD_DOMAIN_SINCE 可调（默认 30 天），历史老号段的追溯归 reconcile 离线面。
        window = os.environ.get("CARD_DOMAIN_SINCE", "30.days")
        rc2, log2 = git(repo, "log", "--all", "--since", window, "-F", "--grep", "(%s):" % c["id"],
                        "--pretty=format:@@%H", "--name-only")
        if rc2 == 0:
            cur_hash, foreign = "", {}
            for ln in log2.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                if ln.startswith("@@"):
                    cur_hash = ln[2:].strip()
                    continue
                if cur_hash.startswith(a.commit) or a.commit.startswith(cur_hash[:7]):
                    continue
                if not _file_in_domain(ln, c["files"]):
                    foreign.setdefault(cur_hash[:8], []).append(ln)
            if foreign:
                print("WARN: 带 %s 号的其它提交有越域文件(Verifier 复核可 REJECT)：%s"
                      % (c["id"], "; ".join("%s:%s" % (h, ",".join(fs[:3])) for h, fs in list(foreign.items())[:5])),
                      file=sys.stderr)
                if os.environ.get("CARD_STRICT_DOMAIN") == "1":
                    die("CARD_STRICT_DOMAIN=1：聚合越域也拒绝勾卡")
    else:
        print("WARN: 卡无 files 域，跳过越域检查")
    outd = _parse_out(a) or {}
    old = c["status"]
    c["status"] = "DONE"; c["commit"] = a.commit; c["evidence"] = a.evidence or ""
    # REQ-MERGE-GATE-1：done 把现场验货用的 repo 落卡面——verdict PASS 的主干闸要在验收时刻
    # 按同一仓查 is-ancestor；此前 repo 只在本函数内用完即丢，验收侧无从知道交付在哪个仓。
    c["repo"] = repo
    # MECH-STATE-STD-1：done 收口落卡面 done_at（待验账龄唯一锚，DONE-SWEEP 滞留扫描消费；
    # 存量 16 张无此字段属正常欠账，补账归 sweep 环——缺字段闸默认 warn 档不误报满盘红）
    c["done_at"] = nowz()
    # MECH-DONEANCHOR-TIP-1：交卷瞬间 worktree 实 tip/branch/decl-tip 分型落卡面——自报只是主张，
    # 锚才是事实；交卷即验与火车（S3 卡锚单 commit 对多笔半成品失实的病根）据此据实对账。
    c["done_anchor"] = anchor
    c["output"] = outd
    save(c)
    shutil.rmtree(claim_dir(c["id"]), ignore_errors=True)  # 收卡即清卡锁
    lane_lock_release(a.owner, c["id"])                    # 同清道锁（一道一活解锁）
    event(c["id"], old, "DONE", a.owner,
          "commit=%s tip=%s %s" % (a.commit, anchor["tip"][:12],
                                   ("ahead=%d" % anchor["ahead"]) if anchor["rel"] == "ahead" else anchor["rel"]))
    print(c["id"], "DONE")

# MECH-VERIFYPIN-FALLBACK-1（交接①活证 d0b97b29/6b6d0be0，PM 已裁修法即卡面规格）：
# 旧缺省盲值 "bash" 本身即毒径——任务形 PATH 下 which("bash") 命中
# C:\Windows\System32\bash.exe（WSL 启动器），win 形态判据在毒宿主集体跑（MECH-VFY1
# 注释自证 11-01 劫持实锤，但默认值没收）；a67b1596 只封 child_env 声明层，此兜底层
# 未收。与 a67b1596 同式不造第三形态：win32 解析结果前缀落 %SystemRoot% 内=弃用；
# 未显式配=回退 Git Bash 绝对路径候选（现探），全缺 die；显式配了却指毒径=die 不改派
# （继承值 ctrld 可重钉，显式配是人手，错了必须喊=宁红不猜）；POSIX 分支零漂移。
def _git_bash_candidates():
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    return [os.path.join(pf, "Git", "bin", "bash.exe"),
            os.path.join(pf, "Git", "usr", "bin", "bash.exe"),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe"]


def _resolve_verify_bash():
    """判据宿主解析（MECH-VERIFYPIN-FALLBACK-1）：返回可交给 subprocess 的 bash；
    毒径且无干净替代=die——绝不把判据放上 WSL 启动器跑。"""
    explicit = os.environ.get("CARD_VERIFY_BASH")
    bashbin = explicit or "bash"
    w = shutil.which(bashbin)
    if os.name != "nt":
        if not w:
            die("CARD_VERIFY_BASH=%r 不可用——判据宿主钉死失败，宁红不猜" % bashbin)
        return bashbin  # POSIX 零动：MECH-VFY1 原样（含配而不得=红的旧语义一字不差）
    _sr = (os.environ.get("SystemRoot") or r"C:\Windows").lower()
    poison = bool(w) and os.path.normpath(w).lower().startswith(_sr)
    if explicit:
        if not w:
            die("CARD_VERIFY_BASH=%r 不可用——判据宿主钉死失败，宁红不猜" % bashbin)
        if poison:
            die("CARD_VERIFY_BASH=%r 解析到 %r 落 %%SystemRoot%% 内（WSL 启动器毒径）——"
                "显式配置=人手，宁红不猜不静默改派；请改钉 Git Bash 绝对路径" % (explicit, w))
        return explicit
    if w and not poison:
        return w
    for cand in _git_bash_candidates():  # 缺省盲值弃用后回退候选（现探，同 a67b1596 面）
        if os.path.isfile(cand.replace("/", os.sep)):
            return cand
    die("win32 无可用 bash：which('bash')=%r 落 %%SystemRoot%%（WSL 启动器）或不可见，"
        "且 Git Bash 候选全缺——宁红不猜；请配 CARD_VERIFY_BASH 显式钉 Git Bash 绝对路径" % w)


def _unwrap_verify(cmd):
    """剥壳派生唯一源（BUG-VERDICT-SHA-UNWRAP-1，2026-11-03 FUSE-S5 班）：双层 bash -c
    假闸根除（MECH-HOOK-STDIN-WIN-1 工人 28 班备案 + BUG-DEMO-DS-HYGIENE-1 done-sweep
    每 60s 复跑空转闪窗，业主「优先解决黑窗」当轮收口）的剥壳规则收口于此——执行侧
    (_run_verify) 与锁窗比对侧 (cmd_verdict sha_cur) **必须同源**：15:48 补丁(351b8458)
    只改执行侧哈希，比对侧仍按原始卡面文本哈希，套娃形卡面两侧 sha 恒不等=一切
    `bash -c '...'` 卡 verdict PASS 死锁（16:2x HYGIENE 三连拒实锤，文本未变仍报
    「判据在执行窗内被改」）。剥不动（含 ' 转义等异形）=原样，宁不剥不猜语义不变。"""
    import shlex
    cmd = (cmd or "").strip()
    try:
        _parts = shlex.split(cmd, posix=True)
        if len(_parts) == 3 and _parts[0] in ("bash", "/bin/bash", "sh") and _parts[1] == "-c":
            cmd = _parts[2]
    except ValueError:
        pass
    return cmd


def _run_verify(c):
    """MECH-VFY1：verdict PASS 前机器复跑卡面判据——「完成=退出码0」从纸面契约变闸门。
    无人工跳过通道（跳过=回到自然语言自述）；REJECT 不跑（拒收不需要判据绿）。
    证据只落卡面 verify_run（events 行格式有存量断言，不擅动）；跑不了=红，绝不静默放行。"""
    cmd = (c.get("verify") or "").strip()
    if not cmd:
        die("卡面无 verify，机器无法复跑判据——先 set --verify 补可执行判据")
    cmd = _unwrap_verify(cmd)
    import subprocess
    try:
        to = int(os.environ.get("CARD_VERIFY_TIMEOUT", "600"))
    except ValueError:
        to = 600
    # MECH-VFY1 宿主钉定 + MECH-VERIFYPIN-FALLBACK-1：缺省盲值收口进 _resolve_verify_bash
    # （win32 毒径弃用→Git Bash 候选→全缺 die；显式指毒 die；POSIX 零动）。
    bashbin = _resolve_verify_bash()
    try:
        r = subprocess.run([bashbin, "-c", cmd], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=to, creationflags=NO_WIN)
        rc, out = r.returncode, (r.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        rc, out = 124, "TIMEOUT after %ss" % to
    except OSError as e:
        die("verify 无法执行(%r)——请在能跑判据的 OS 侧验收，机制不猜" % e)
    tail = " ".join(out.split())[-300:]
    via = "%s/%s" % (os.name, sys.platform)  # 执行侧痕迹：判据必须「验收所在 OS 能跑」（WSL 形态判据在 Git Bash 跑=路径假红，MECH-VFY1 首跑实锤）
    if rc != 0:
        die("verdict 拒绝：verify 机器复跑 rc=%d via=%s｜tail=%s（判据不过=不能 PASS；若仅单侧 OS 可跑，请到该侧验收或改判据为双侧形态）" % (rc, via, tail))
    return dict(ts=nowz(), rc=0, via=via, cmd_sha=hashlib.sha1(cmd.encode("utf-8")).hexdigest()[:12], tail=tail)

def auto_verify(cid):
    """补50 业主设计：「各自干各自的，done 拉起校验，通过后写 DAG 只是瞬间」。
    在 cli 放锁**之后**调用——套件在本道锁外跑（各泳道天然并行），绿=调 cmd_verdict
    瞬锁落章 VERIFIED（by=auto@done 留痕）；红=卡留 DONE 如实喊，不静默。
    开关=CC_AUTO_VERIFY=1（run-cc.ps1 对 worker 会话注入；沙箱/人工通道默认不启）。"""
    if os.environ.get("CC_AUTO_VERIFY") != "1":
        return
    c = load(cid)
    if c["status"] != "DONE":
        return
    print("AUTO-VERIFY %s：done 拉起校验（锁外，本道并行自跑）..." % cid, flush=True)
    try:
        _merge_gate(c)  # REQ-MERGE-GATE-1 快闸前置：未合入主干不跑套件（泳道 done 后这是常态，不是故障）
        vr = _run_verify(c)
    except SystemExit:
        print("AUTO-VERIFY %s 红：主干闸拒（交付未合入主干）或判据复跑不过——卡留 DONE 属正常停留；"
              "未合入的等 PM merge 后由 verdict/滞留扫描补绿，owner 不必重 done（重复勾卡会被拒）" % cid,
              flush=True)
        return
    import argparse
    try:
        cmd_verdict(argparse.Namespace(id=cid, result="PASS", reason="auto@done 校验器（锁外跑绿，瞬锁落账）",
                                       as_role=None, reverify=False))
    except SystemExit:
        # 进锁窗内被 REJECT/改判据/主干闸重载拒：done 本身已落账成功，rc 不连坐（防看护侧把
        # 「done 成功+停留待验」误读成勾卡失败引发重派风暴）；如实喊，等 verdict 侧收口。
        print("AUTO-VERIFY %s 红：verdict 通道拒落章（进锁窗内卡面/判据/主干现态变化）——卡留 DONE" % cid,
              flush=True)
        return
    print("AUTO-VERIFY %s 绿落账 VERIFIED cmd_sha=%s" % (cid, vr.get("cmd_sha")), flush=True)


def _merge_gate(c):
    """REQ-MERGE-GATE-1（业主令 2026-11-02「验收绿要求交付已在主干」）：PASS→VERIFIED 前的
    主干合入硬闸——卡面 commit 必须 git merge-base --is-ancestor 落在主干 ref 上才许落章。
    病因：泳道 done 只证明交付在泳道分支；先绿后合=台账写 VERIFIED 而主干上没有这份交付
    （DEL-DEATHDOC-1 知识滞留泳道同类病：结论/代码只活在 wm3，主干读者拿到的是旧版）。
    素材：cmd_done 起 repo 自动落卡面（此前用完即丢）；存量 DONE 欠账由 PM 走 set --repo 补。
    主干 ref：CARD_MAINLINE 显式钉定（配了找不到=红，同 CARD_VERIFY_BASH 纪律）；
    缺省按 main→master→trunk 取首个存在的 ref；全找不到=红（宁红不猜）。
    无人工跳过通道（MECH-VFY1 同源纪律「跳过=回到自然语言自述」）：done 之后被 rebase/squash
    改写哈希属流程违规——合入用 merge，别改哈希，卡面 commit 就是主干上那个 commit。
    看护状态=DONE→VERIFIED 边（verdict PASS 通道）；REJECT 不跑此闸（拒收不需要交付上主干）。"""
    repo = (c.get("repo") or "").strip()
    commit = (c.get("commit") or "").strip()
    if not repo or not commit:
        die("REQ-MERGE-GATE-1：卡面缺 repo=%r/commit=%r，无从证实交付已在主干——存量账由 PM "
            "set %s --repo <交付仓> 补素材（本版本起 done 已自动落 repo）" % (repo, commit, c["id"]))
    env_main = (os.environ.get("CARD_MAINLINE") or "").strip()
    refs = [env_main] if env_main else ["main", "master", "trunk"]
    for ref in refs:
        rc, _ = git(repo, "rev-parse", "--verify", "-q", ref)
        if rc != 0:
            continue
        rc2, _ = git(repo, "merge-base", "--is-ancestor", commit, ref)
        if rc2 == 1:
            die("REQ-MERGE-GATE-1：验收拒 PASS——交付 %s 尚未合入主干 %s（repo=%s）；"
                "先把泳道 merge 进主干（别改写哈希），重跑 verdict 即绿" % (commit[:12], ref, repo))
        if rc2 != 0:
            die("REQ-MERGE-GATE-1：主干闸探测失败 rc=%d——%s 对 %s（repo=%s）查不了 is-ancestor"
                "（commit 被 GC/repo 路径失效）；宁红不猜，先修仓/锚再验" % (rc2, commit[:12], ref, repo))
        return ref
    die("REQ-MERGE-GATE-1：主干 ref 解析不到（尝试 %s，repo=%s）——PM 用 CARD_MAINLINE=<分支名> 钉定"
        % (",".join(refs), repo))

def cmd_verdict(a):
    """补50（业主令 2026-11-01 22:0x「记账要串行，不是执行要串行」）：verify 套件=长执行，
    在台账写锁**外**跑；绿后进锁**重载卡面**再落账——防执行窗内被 REJECT/释放/改判据的竞态，
    cmd_sha 不一致拒换痕。verdict 已移出 config.WRITERS，锁与刷新由本函数自理；
    REJECT 无套件，直接进锁。全部旧断言语义（身份痕/重复PASS拒/REJECT回退/reverify通道）不变。"""
    role = getattr(a, "as_role", None) or "Verifier"
    reverify = getattr(a, "reverify", False)
    c = load(a.id)
    vr = None
    if reverify:
        # MECH-V2 转正通道：独立 Verifier 复跑证据在案时摘 PM 兼验痕。只覆盖 verdict 文本+
        # 记 reverify 事件，**不动 DONE/VERIFIED 状态机链**；无 --reverify 的重复 PASS 仍被拒
        if role != "Verifier":
            die("--reverify 只许 --as Verifier：PM 双帽不可自转正（协议债③）")
        if a.result != "PASS":
            die("--reverify 是转正通道只承载 PASS；退回 VERIFIED 卡走正常 verdict REJECT")
        if c["status"] != "VERIFIED":
            die("--reverify 只作用于 VERIFIED 卡，当前 %s：DONE 验收走不带 --reverify 的 verdict PASS" % c["status"])
        vr = _run_verify(c)  # 长执行：锁外
    elif a.result == "PASS":
        if c["status"] not in ("DONE", "VERIFIED"):
            die("只有 DONE/VERIFIED 卡可判，当前 " + c["status"])
        if c["status"] == "VERIFIED":
            die("卡已 VERIFIED，拒绝重复 PASS(重复验收审计噪音；REJECT 重开走 verdict REJECT)")
        _merge_gate(c)  # REQ-MERGE-GATE-1 预检（锁外快闸）：未合入主干就不跑套件，省整套耗时
        vr = _run_verify(c)  # MECH-VFY1：PASS=机器复跑卡面 verify 退出码0；长执行在锁外
    else:
        if c["status"] not in ("DONE", "VERIFIED"):
            die("只有 DONE/VERIFIED 卡可判，当前 " + c["status"])

    team_lock_acquire("verdict")
    try:
        c = load(a.id)  # 进锁重载：套件跑的还是不是这张账，以重载为准
        if vr is not None:
            sha_cur = hashlib.sha1(_unwrap_verify(c.get("verify")).encode("utf-8")).hexdigest()[:12]
            if vr.get("cmd_sha") != sha_cur:
                die("verify 判据在执行窗内被改（cmd_sha 不符）——请重跑 verdict，不换旧跑结果")
        if reverify:
            if c["status"] != "VERIFIED":
                die("执行窗内卡面已变为 %s，reverify 需 VERIFIED" % c["status"])
            require_edge("VERIFIED", "VERIFIED", "verdict --reverify")  # 转正自环（不动状态机链）
            c["verdict"] = a.result + ((" | " + a.reason) if a.reason else "") + " | by=Verifier（reverify转正）"
            c["verify_run"] = vr
            save(c)
            event(c["id"], c["status"], c["status"], "verifier", "reverify PASS")
        elif a.result == "PASS":
            if c["status"] != "DONE":
                die("verdict 拒绝：执行窗内卡面已变为 %s，请按现态重判" % c["status"])
            # REQ-MERGE-GATE-1：进锁重载后查主干——判据绿≠可落章，交付必须已在主干。
            # 放锁内是为以重载后的卡面 repo/commit 为准（进锁前 set --repo 换仓不算数）。
            _merge_gate(c)
            old = c["status"]
            require_edge(old, "VERIFIED", "verdict PASS")  # 迁移矩阵同源闸
            c["status"] = "VERIFIED"
            # MECH-BOARD-STATS-1（业主令 2026-11-02 00:2x 顶栏「今日收口」按转正日历计数）：
            # verdict 痕补专用转正时间戳 at——PASS 落 VERIFIED 瞬间盖，与 updated_at 分离
            # （set 改 desc/note 一律碰不到它）；重做后重 PASS 重盖新戳。旧卡无此字段由看板
            # 读侧以 verify_run 痕内日期回填，账上不造假。
            c["at"] = now()
            c["verify_run"] = vr
            # MECH-RESIDENT-1（seq=8）：VERIFIED=验收合格上岗——岗卡落 VERIFIED 同笔置 ON-DUTY，
            # 这是 OFF→ON-DUTY 的**首上岗唯一门**（此后复岗才允许走 duty 通道）。
            if is_resident(c) and (c.get("resident_status") or "OFF") != "ON-DUTY":
                c["resident_status"] = "ON-DUTY"
                _first_duty_beat = True
            else:
                _first_duty_beat = False
            shutil.rmtree(claim_dir(c["id"]), ignore_errors=True)  # 终态清卡锁(done 漏网兜底)
            lane_lock_release(c.get("owner"), c["id"])
            c["verdict"] = a.result + ((" | " + a.reason) if a.reason else "") + " | by=" + role
            if role == "PM":
                c["verdict"] += "（PM兼验，未独立复核，待验）"  # 协议债③：双帽身份必须留痕
            save(c)
            event(c["id"], old, "VERIFIED", "PM" if role == "PM" else "verifier", a.result)
            if locals().get("_first_duty_beat"):
                event(c["id"], "VERIFIED", "VERIFIED", "PM" if role == "PM" else "verifier",
                      "duty OFF>ON-DUTY | 首上岗@verdict PASS")
        else:
            if c["status"] not in ("DONE", "VERIFIED"):
                die("只有 DONE/VERIFIED 卡可判，当前 " + c["status"])
            old = c["status"]
            require_edge(old, "OPEN", "verdict REJECT")  # 迁移矩阵同源闸
            prev_owner = c.get("owner")
            c["status"] = "OPEN"; c["owner"] = None; c["commit"] = None; c["output"] = {}
            c["claimed_at"] = None; c["done_at"] = None
            # MECH-DONEANCHOR-TIP-1：REJECT 与 commit/done_at 同清交卷锚——留旧锚=下一班工人
            # 对上的是上一班的树尖，据实对账反被污染。
            c["done_anchor"] = None
            c.pop("at", None)  # MECH-BOARD-STATS-1：退回重做清转正戳（留旧戳=再验收前会被顶栏当已收口）
            if is_resident(c):  # MECH-RESIDENT-1：验收退回=不合格离岗，在岗态同笔回 OFF
                c["resident_status"] = "OFF"
            shutil.rmtree(claim_dir(c["id"]), ignore_errors=True)
            ho_n, ho_ep = _handover_out(c, prev_owner)  # REJECT 打回同走交接环（seq=15 第2条）
            lane_lock_release(prev_owner, c["id"])
            c["verdict"] = a.result + ((" | " + a.reason) if a.reason else "") + " | by=" + role
            if role == "PM":
                c["verdict"] += "（PM兼验，未独立复核，待验）"
            rn = _bump(c, "reject_count")  # R5b（seq=30）打回计数：与 R1 同一机件（本锁内递增，随 save 同笔落账）
            # BUG-REJECT-STOPLOSS-1（业主 seq=30/35「第2拒即停」）：止血与拒落账同事务原子——
            # 不再托孤给「报警键→fuse-S6 认领升级」链（键 ~20min 被机器销账早于 30min 唤 PM 阈，
            # 该链实锤从未闭环=九连拒空转的病根）。hold 文案带归因一句话，卡面直读。
            # 调用行整行单句=tests/reject-stoploss-test.py 撤闸差分对的注入靶（禁折行）。
            sl_note = "末拒by=%s reason=%s" % (role, _san(a.reason)[:60] if a.reason else "-")
            sl_hold = _reject_stoploss(c, rn, sl_note)
            if "STOPLOSS" not in str(c.get("hold") or ""):
                # 未挂失才回队头（挂失卡不入调度，队列残渣由 reindex「队列只收 OPEN」规则清）；
                # REJECT 重做优先回队头（按卡面 lanes，不看旧 owner——实锤根因 BUG-LANES-STALE-1）
                _requeue_card(c, prev_owner)
            save(c)
            event(c["id"], old, "OPEN", "PM" if role == "PM" else "verifier", a.result)
            if sl_hold:
                # 止血痕 actor=gate（机制方）+ self=gate 尾标：对齐 fuse last_pm_action_ts 剔除
                # 口径，机件自触发不得拨 PM 静默钟（BUG-FUSE-SNIFF/S5-SNIFF 同族纪律，防自骗）
                event(c["id"], "OPEN", "HOLD", "gate",
                      "REJECT-STOPLOSS reject_count=%d≥阈%d 已release(留HANDOVER)+HOLD停复派"
                      "等PM归因 self=gate" % (rn, _reject_stoploss_max()))
            if rn > REJECT_CARD_MAX:  # R5b 同卡超阈（第 REJECT_CARD_MAX+1 拒触发）→ alerts 落键走 fuse-S6 既有升级闭环直达业主；键名非业主/FUSE-S6 头
                from . import alerts
                # BUG-REJECT-STOPLOSS-1 ④：上报阈(>2)与键主体文案不动，仅追加止血说明——
                # 认领/销账与否不影响「止血已发生」这一已在账事实
                alerts.record("REJECT-THRESHOLD card=%s reject_count=%d>%d%s" % (
                    c["id"], rn, REJECT_CARD_MAX,
                    " stoploss=止血已落HOLD(不依赖认领链)"
                    if "STOPLOSS" in str(c.get("hold") or "") else ""))
            if ho_n:
                event(c["id"], "OPEN", "OPEN", "PM" if role == "PM" else "verifier",
                      "HANDOVER-OUT e%s by=%s n=%d" % (ho_ep, prev_owner, ho_n))
        dag_refresh()  # 原由 cli 的 WRITERS 分支自动刷；verdict 出集后自己在放锁前刷
    finally:
        team_lock_release()
    if reverify:
        print(c["id"], c["status"], "REVERIFY")
    else:
        print(c["id"], c["status"])

def cmd_duty(a):
    """MECH-RESIDENT-1（seq=8）：岗卡在岗态三态迁移唯一通道。
    纪律：普通卡不适用三态（七态机不受影响）；矩阵外迁移拒（防任意态互窜）；
    DEGRADED 只报警不自愈（alerts 入账+events 留痕，绝不动锚件进程）；
    OFF→ON-DUTY 复岗须 VERIFIED 卡——首上岗门在 verdict，这里只服务『停过岗再上』。"""
    c = load(a.id)
    if not is_resident(c):
        die("duty 只用于岗卡(resident=True)——普通卡走七态机，三态不适用（族籍纪律）")
    frm = c.get("resident_status") or "OFF"
    to = a.state
    if to not in RESIDENT_DUTY:
        die("在岗态不在三态枚举: %s（只走 %s）" % (to, "/".join(RESIDENT_DUTY)))
    if frm == to:
        print(a.id, to, "SAME（无变化不落账，防空跳把 events 刷成心跳计数器）")
        return
    if (frm, to) not in RESIDENT_DUTY_EDGES:
        die("非法迁移 %s→%s：矩阵(RESIDENT_DUTY_EDGES)无此边——在岗态只认同源矩阵的边"
            % (frm, to))
    if to == "ON-DUTY" and c["status"] != "VERIFIED":
        die("岗卡复 ON-DUTY 必须验收合格(status=VERIFIED)，当前 %s——首上岗走 verdict PASS，"
            "未过验收的上不了岗" % c["status"])
    old_duty = frm
    c["resident_status"] = to
    save(c)
    who = a.by or "PM"
    event(c["id"], c["status"], c["status"], who,
          "duty %s>%s%s" % (old_duty, to, ((" | " + a.reason) if a.reason else "")))
    if to == "DEGRADED":  # 失败语义：报警入账，不自愈（自愈=掩盖锚件之死）
        from . import alerts
        alerts.record("DEGRADED 岗卡 %s(%s): %s → %s" %
                      (c["id"], (c.get("title") or "")[:30], old_duty, to))
    print(c["id"], "%s>%s" % (old_duty, to))


def cmd_release(a):  # 回收：STALLED 卡回 OPEN（recycle/lane-steal 用）
    c = load(a.id)
    if c["status"] != "CLAIMED":
        die("只回收 CLAIMED，当前 " + c["status"])
    require_edge("CLAIMED", "OPEN", "release")  # 迁移矩阵同源闸
    owner0 = c.get("owner")
    shutil.rmtree(claim_dir(c["id"]), ignore_errors=True)
    old = c["status"]; c["status"] = "OPEN"; c["owner"] = None; c["claimed_at"] = None
    _bump(c, "release_count")  # R1 退回计数（CLAIMED->OPEN 唯一闸；recycle 回收/手杀/交接 HANDOVER 重派全走本口）
    ho_n, ho_ep = _handover_out(c, owner0)   # seq=15 第2条：回收不清工作区，脏件清单随行落卡面
    save(c)
    event(c["id"], old, "OPEN", a.who or "dispatcher", "release")
    if ho_n:
        event(c["id"], "OPEN", "OPEN", a.who or "dispatcher",
              "HANDOVER-OUT e%s by=%s n=%d" % (ho_ep, owner0, ho_n))
    lane_lock_release(owner0, c["id"])
    _requeue_card(c, owner0)  # 被回收的卡回队头，重派优先（按卡面 lanes，不看旧 owner）
    print(c["id"], "OPEN")

def cmd_cancel(a):  # 仅 PM；必须先停进程清 claim（三步顺序铁律）
    c = load(a.id)
    if c["status"] in ("DONE", "CANCELED"):
        die("该状态无需作废")
    if c["status"] == "VERIFIED" and not is_resident(c):
        # 业主 seq=10（2026-11-03）：VERIFIED→CANCELED 边仅系统保障转性作废用；业务终态卡不享
        die("业务 VERIFIED 卡已验收收口，无需作废（撤章唯一下行通道=verdict REJECT）——"
            "VERIFIED→CANCELED 仅限 resident 保障/岗卡转性（seq=10）")
    if os.path.isdir(claim_dir(c["id"])) and not a.force:
        die("claims 有活跃认领——先停泳道进程(或 release)，再作废")
    require_edge(c["status"], "CANCELED", "cancel")  # 迁移矩阵同源闸（DONE/VERIFIED 已在上方拒）
    owner0 = c.get("owner")
    old = c["status"]; c["status"] = "CANCELED"; c["owner"] = None; c["claimed_at"] = None
    if is_resident(c):  # MECH-RESIDENT-1：卡作废则岗必随卡停——留 ON-DUTY=看板画个不存在的岗
        c["resident_status"] = "OFF"
    shutil.rmtree(claim_dir(c["id"]), ignore_errors=True)  # --force 作废不留幽灵锁
    save(c)
    event(c["id"], old, "CANCELED", "PM", a.reason or "")
    lane_lock_release(owner0, c["id"])
    lane_q_drop_everywhere(c["id"])
    print(c["id"], "CANCELED")

def cmd_purge(a):  # 出账唯一通道（业主 seq=11）：只收 CANCELED 卡，全量快照移 state/archive/。
    # 不是状态迁移（不碰迁移矩阵）——是账本手术：卡从 graph 消失、归档件永久占号
    # （cmd_create 已有归档名复用闸）。cancel=作废留痕，purge=彻底出账；顺序=先 cancel 后 purge。
    if not (a.reason or "").strip():
        die("purge 必须带 --reason（出账是审计事件，无理由不出账）")
    cards = all_cards()
    c = cards.get(a.id)
    if c is None:
        die("账面无此卡（不存在或已出账）: " + a.id)
    if c["status"] != "CANCELED":
        die("purge 只收 CANCELED（当前 %s）——作废先走 cancel 留痕；"
            "DONE/VERIFIED 出账=销毁验收史，永禁" % c["status"])
    if os.path.isdir(claim_dir(c["id"])):
        die("claims 目录还在——cancel --force 已清却仍有锁，先人工核")
    refs = sorted(k for k, v in cards.items()
                  if k != a.id and (a.id in (v.get("deps") or []) or v.get("parent") == a.id))
    if refs and not a.force:
        die("被引用为依赖/父卡: %s——先 set --deps 清引用，或 --force 并连同引用方名写进 reason"
            % " ".join(refs))
    path = os.path.join(ARCHIVE, a.id + ".json")
    if os.path.exists(path) and not a.force:
        die("归档已有同名账 %s（--force 覆盖需说明理由）" % path)
    c["purged_at"] = now()
    c["purged_by"] = a.who or "PM"
    c["purge_reason"] = a.reason
    os.makedirs(ARCHIVE, exist_ok=True)
    atomic_write_json(path, c)          # 先归档后销账：炸在中间=账还在可重跑，绝不反序
    g = Graph()
    if a.id not in (g.doc.get("cards") or {}):
        die("并发漂移：归档已写但账面上卡已消失——人工核对 %s" % path)
    g.doc["cards"].pop(a.id)
    g.doc["seal"] = seal(g.doc["cards"])
    atomic_write_json(GRAPH, g.doc)     # cli 收尾统一 refresh() 重算视图+面板
    event(a.id, "CANCELED", "ARCHIVED", a.who or "PM", a.reason)
    print(a.id, "PURGED -> state/archive/%s.json（事件行 ARCHIVED 留痕）" % a.id)


def cmd_push(a):  # PM 派卡入队：只排队不改状态；顺带把 lane 并进 lanes 白名单
    c = load(a.id)
    if c["status"] != "OPEN":
        die("只 OPEN 卡可入队（当前 %s：HOLD 先 thaw/DRAFT 补判据转 OPEN；与 reindex『队列只收 OPEN』同源规则）"
            % c["status"])
    _check_lanes(a.lanes, "push")
    for ln in a.lanes:
        lane_q_push(ln, c["id"], front=a.front)
    merged = sorted(set(c.get("lanes") or []) | set(a.lanes))
    if merged != (c.get("lanes") or []):
        c["lanes"] = merged
        save(c)
    event(c["id"], c["status"], c["status"], a.who or "PM", "push " + ",".join(a.lanes))
    print(c["id"], "queued->", ",".join(a.lanes))

def cmd_next(a):  # dispatcher 用：报本道队头第一张「可领」卡（不自动领，领取仍走 claim 全闸）
    # MECH-QDEL（业主 2026-11-02）：不再读 lane-*.queue 镜像判定——与 engine 同源，
    # 每调用从账本现算。镜像只剩 reindex 的装饰性输出，任何决策面不得据它判定。
    from .engine import _lane_order
    cards = all_cards()
    for cid in _lane_order(cards).get(a.owner, []):
        c = cards.get(cid)
        if not c or c["status"] != "OPEN" or not c.get("verify"):
            continue
        if deps_blockers(c, cards):
            continue
        print(cid)
        return
    print("-")
    sys.exit(1)

def _unslug(slug, card):
    # 文件名只比 owner 的净化形态；卡 owner 与 slug 净化后相等即认为同泳道
    from .locks import _lane_slug
    return card.get("owner") if _lane_slug(card.get("owner")) == slug else None

def cmd_reindex(a):  # 派生视图修复器：道锁/队列/DAG 永远可从卡面重建（铁律「冲突以卡为准」的落地）
    from .locks import _lane_slug, _read_lines, _write_lines
    cards = all_cards()
    fixed = 0
    # 1) 道锁：CLAIMED 卡应有锁；锁指错/残渣应清；遗留 .current 文件清除（v3 由道锁接管）
    expect = {}  # slug -> cid（正常一泳道一张；多张取最老并 ALARM）
    for cid, c in cards.items():
        if c["status"] == "CLAIMED" and c.get("owner"):
            expect.setdefault(_lane_slug(c["owner"]), []).append(cid)
    for slug, cids in expect.items():
        cids.sort()
        if len(cids) > 1:
            print("ALARM: 泳道 %s 名下多张 CLAIMED(%s)——道锁只记 %s，其余需 PM 收口"
                  % (slug, ",".join(cids), cids[0]), file=sys.stderr)
        cid = cids[0]
        c = cards[cid]
        d = lane_lock_dir(slug)
        cur, meta = lane_lock_read(slug)
        if cur != cid:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
            owner = c.get("owner")
            cj = os.path.join(claim_dir(cid), "claim.json")
            mirror = {}
            try:
                mirror = json.load(open(cj, encoding="utf-8"))
            except (OSError, ValueError):
                pass
            # 重建 meta：优先从卡锁镜像取身份；无镜像=裸改残渣，kind 按 session 保守（永不自动回收）
            lane_lock_acquire(cid, owner, mirror.get("pid"),
                              mirror.get("session_id") or "-",
                              mirror.get("kind") or "session", cards,
                              pid_ns=mirror.get("pid_ns"), pid_comm=mirror.get("pid_comm"))
            fixed += 1
    if os.path.isdir(lane_state_dir()):
        for fn in sorted(os.listdir(lane_state_dir())):
            # MECH-STD1 实锤修复：必须判 lane-* 前缀——只判 endswith(".lock") 会把在持的
            # .writer.lock 当残渣删掉=锁半程失守（真实 reindex 现场抓到）。
            if not fn.startswith("lane-") or not fn.endswith(".lock"):
                continue
            p = os.path.join(lane_state_dir(), fn)
            if os.path.isdir(p):
                slug = fn[len("lane-"):-len(".lock")]
                try:
                    cid = open(os.path.join(p, "card"), encoding="utf-8").read().strip()
                except OSError:
                    cid = ""
                c = cards.get(cid, {})
                if c.get("status") != "CLAIMED" or c.get("owner") != _unslug(slug, c):
                    print("WARN: 清理残渣道锁 %s(卡=%s 状态=%s)" % (fn, cid or "?", c.get("status", "无卡")),
                          file=sys.stderr)
                    shutil.rmtree(p, ignore_errors=True); fixed += 1
    # 2) 队列：GC 非 OPEN + GC 越界道残渣 + 补漏（规则与 create/push/set 同源：
    #    队列只收 OPEN，且 OPEN 卡只许站在其 lanes 白名单道里——BUG-LANES-STALE-1：
    #    旧码只按状态剔、不看道，改道卡的旧道条目 reindex 都修不掉=永久 propose 旧道）
    if os.path.isdir(lane_state_dir()):
        for fn in sorted(os.listdir(lane_state_dir())):
            if not fn.startswith("lane-") or not fn.endswith(".queue"):
                continue
            p = os.path.join(lane_state_dir(), fn)
            lane_slug = fn[len("lane-"):-len(".queue")]
            raw = _read_lines(p)
            out = []
            for l in raw:
                c2 = cards.get(l, {})
                if c2.get("status") != "OPEN" or l in out:
                    continue
                if c2.get("lanes") and lane_slug not in set(_lane_slug(x) for x in c2["lanes"]):
                    continue
                out.append(l)
            if out != raw:
                _write_lines(p, out); fixed += 1
    for cid, c in cards.items():
        if c["status"] == "OPEN":
            for ln in (c.get("lanes") or []):
                before = _read_lines(_lane_file_for(ln))
                lane_q_push(ln, cid)
                if _read_lines(_lane_file_for(ln)) != before:
                    fixed += 1
    # 3) 有状态 DAG 物化视图重刷（MECH-STD1：非 force——digest 同=空操作，
    #    防 dispatcher 每 tick reindex 把 generation 虚增成心跳计数器）
    snap = dag_refresh(cards=cards)
    fixed += 0  # 图刷新不计入「修正」数（幂等重建非账变）
    print("reindex: 修正=%d（道锁/队列/遗留视图，按卡面为准）+ DAG 重刷 gen=%s" % (fixed, snap.get("generation")))

def lane_state_dir():
    from .config import STATE
    return STATE

def _lane_file_for(owner):
    from .locks import _lane_file
    return _lane_file("queue", owner)

def cmd_ack(a):  # MECH-AUD1：告警闭环——--list 看开放项；ack <子串> 落处置痕；处置后问题复现自动回开放
    from . import alerts
    if a.list_open:
        es = alerts.open_entries()
        # BUG-ALARM-AUTOACK-MASK-1：报警面必须看得见夜里被机器销了几笔——
        # 账面 open=0 不等于「没问题」，自动销账计数（含已闭案）在此并列展示。
        tally = alerts.autoack_today_total()
        if not es:
            print("alerts: 无开放项" + ("｜今日机器自动销账 %d 笔（反复到期的已钉住留在开放项）"
                                        % tally if tally else ""))
            return
        print("alerts: 开放 %d 项｜今日机器自动销账 %d 笔" % (len(es), tally))
        for e in es:
            print("OPEN x%d last=%s %s" % (e["count"], e.get("last", "?"), e["msg"]))
        return
    if not a.key:
        die("ack 需要告警键子串（或 --list 看开放项）")
    r = alerts.ack(a.key, a.who or "PM")
    if not r:
        die("无匹配告警(检查子串或用 --list): " + a.key)
    if len(r) > 1 and isinstance(r[0], str):
        print("多命中 %d 项，请缩小子串:" % len(r))
        for k in r:
            print("  " + k)
        sys.exit(1)
    print("ACKED count=%s by=%s %s" % (r[0]["count"], a.who or "PM", r[0]["msg"]))

def cmd_note(a):  # worker 收尾沉淀：只追加到泳道笔记，不碰卡与代码
    # 写闸规矩③(REQ-BEAT-ANCHOR-1)：note 校验任期——错班=旧班乱报，拒收并落被拒台账
    # （note 无卡号，不能进 events.log——reconcile 幽灵判据按卡号集合对账，伪造 cid=制造 ALARM；
    #  拒绝留痕走现成 REJECTS 通道，同「被拒历史别黑盒」定版）。
    declared = _declared_epoch(a)
    cur = lane_epoch_read(a.owner).get("epoch")
    if cur is not None and declared is not None and int(declared) != int(cur):
        with open(REJECTS, "a", encoding="utf-8", newline="") as f:
            f.write("%s|note|EPOCH-STALE lane=%s decl=e%s lane-cur=e%s text=%s\n"
                    % (now(), _san(a.owner), declared, cur, _san(a.text)))
        die("错班拒收(REQ-BEAT-ANCHOR-1 名牌制)：lane-%s 在任 e%s，你报 e%s——旧班上报作废"
            % (a.owner, cur, declared))
    os.makedirs(os.path.join(TEAM, "notes"), exist_ok=True)
    fn = os.path.join(TEAM, "notes", "lane-%s.md" % _san(a.owner))
    cid = getattr(a, "cid", "") or ""
    with open(fn, "a", encoding="utf-8", newline="") as f:
        f.write("- %s %s%s\n" % (now(), ("[%s] " % _san(cid)) if cid else "", _san(a.text)))
    print("note -> " + fn)

def cmd_thaw(a):  # HOLD 且 deps 全 DONE 且无人工 hold 条件 → OPEN（并补位入队）
    cards = all_cards()
    n = 0
    for cid, c in cards.items():
        if c["status"] == "HOLD" and not c.get("hold") and deps_ok(c, cards):
            require_edge("HOLD", "OPEN", "thaw")  # 迁移矩阵同源闸
            old = c["status"]; c["status"] = "OPEN"
            if c.get("verify"):
                save(c); event(cid, old, "OPEN", "dispatcher", "thaw"); n += 1
                for ln in (c.get("lanes") or []):
                    lane_q_push(ln, cid)
            else:
                c["status"] = old  # 没验收命令的卡不许解冻进调度
                print("ALARM: HOLD 卡缺 verify，解冻拒绝: " + cid)
    print("thawed=%d" % n)

def cmd_upstream(a):  # 下游领卡人用：读上游卡的 output 产物指针
    for cid in a.ids:
        c = load(cid)
        if c["status"] == "CANCELED":
            print("ALARM: %s 已作废(CANCELED)，它的 output 不是交付物，下游必须改道" % cid)
            continue
        if c["status"] not in FINISHED:
            print("WARN: %s 未完成(status=%s)，output 不可信" % (cid, c["status"]))
        print(json.dumps({cid: {"status": c["status"], "commit": c.get("commit"),
                                 "output": c.get("output", {})}}, ensure_ascii=False, indent=1))


def cmd_progress(a):  # MECH-LOG1 旁证流 + REQ-BEAT-ANCHOR-1 名牌续写（判活事件源①）
    # 两态：带齐 --lane/--epoch/--ring = **续写事件**（写闸内校验对班+圈号递增后落
    # state/lane-<道>.beat，recycle 判活唯一优先源）；三件不全 = 旧旁证流原样照旧。
    # 写闸纪律边界：progress 进 WRITERS 只为本流成为台账写事件后「圈号单调」有锁保证，
    # 其余写命令的串行语义一分不动。
    from . import progress
    if not card_exists(a.id):  # 错卡号=红：流水里不许掺不存在的卡（对账/看势都靠卡号串流）
        die("卡不存在(进度不落错号): " + a.id)
    who = a.who or _session_from_env()
    beat = None
    if a.lane or a.epoch is not None or a.ring is not None:
        if not (a.lane and a.epoch is not None and a.ring is not None):
            die("progress 名牌续写三件必齐：--lane/--epoch/--ring（令「不搞复杂，一泳道一会话」——"
                "身份=道+任期+圈号，不含 pid/session）")
        c = load(a.id)
        if c.get("status") != "CLAIMED":
            die("progress 续写只接 CLAIMED（当前 %s）——旁证流旧通道不受此限，三件不带即可"
                % c.get("status"))
        if a.lane != c.get("owner"):
            die("名牌续写拒：--lane=%s 非本卡在任属主 %s（一泳道一会话）" % (a.lane, c.get("owner")))
        if c.get("claimed_epoch") is None:
            die("卡面无任期（机制落地前的存量认领），续写不入账——请走旁证流旧通道")
        _epoch_gate_card(c, int(a.epoch), "progress")  # 错班拒收+events 痕（僵尸乱报免疫）
        ring = int(a.ring)
        prev = lane_beat_read(a.lane)
        try:
            prev_ring = int(prev.get("ring") or 0)
            prev_ep = int(prev.get("epoch") or -1)
        except (TypeError, ValueError):
            prev_ring, prev_ep = 0, -1
        if prev_ep == int(c["claimed_epoch"]) and ring <= prev_ring:
            die("圈号必须递增：lane-%s e%s 现 ring=%s，你报 %s（旧圈号重报=没在干活）"
                % (a.lane, c["claimed_epoch"], prev_ring, ring))
        beat = lane_beat_write(a.lane, dict(lane=a.lane, epoch=int(c["claimed_epoch"]),
                                            ring=ring, cid=a.id, ts=now(), t=time.time()))
    line = progress.record(a.id, who, ("[beat lane=%s e%s r%s] " % (beat["lane"], beat["epoch"], beat["ring"])
                                       if beat else "") + a.text)
    print("progress " + line + (" BEAT_OK e%s r%s" % (beat["epoch"], beat["ring"]) if beat else ""))


def _named_lane_set():
    """在编道号集合：命令行带 CCLANE=<道号>-<岗位名> 名字标记的 claude.exe 进程（启动器注入，
    业主 2026-11-03 定版「不允许用进程号，进程名字就是岗位编号+名称」；与 dispatch._census_lanes
    同口径）。查询失败/非 Windows 侧=None（不可核≠无人，如实分开展示）。"""
    import subprocess as _sp
    if os.name != "nt":
        return None
    try:
        r = _sp.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\").CommandLine "
                     "| ForEach-Object { if ($_ -match 'CCLANE=([A-Za-z0-9_]+)-') { $Matches[1] } } "
                     "| Sort-Object -Unique"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25, creationflags=NO_WIN)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return {x.strip() for x in (r.stdout or "").splitlines() if x.strip()}


def cmd_watch(a):  # MECH-LOG1：只读尾流三节——在制卡活性/进度流/events 状态流尾。无回调无常驻，tail 即观测。
    from . import progress
    from .config import EVENTS, read_json

    def _progress_lines():
        return progress.tail(n=a.n, card=a.card or None)

    def _snapshot():
        cards = all_cards()
        claimed = sorted(cid for cid, c in cards.items() if c.get("status") == "CLAIMED")
        print("watch: claimed=%d progress=%d%s" % (
            len(claimed), len(_progress_lines()), " card=" + a.card if a.card else ""))
        names = _named_lane_set()   # 一次普查逐卡查表（名字=身份，业主 2026-11-03 定版禁 pid）
        for cid in claimed:
            c = cards[cid]
            owner = str(c.get("owner") or "")
            if names is None:
                live = "普查不可核(查询失败)"
            elif owner and owner in names:
                live = "在编(带名字进程 CCLANE=%s-* 在场)" % owner
            else:
                live = "无人(无 CCLANE=%s-* 名字进程)" % (owner or "?")
            print("claimed %s owner=%s claimed_at=%s %s" % (
                cid, c.get("owner") or j.get("owner") or "-", c.get("claimed_at") or j.get("claimed_at") or "-", live))
        print("--- progress ---")
        for ln in _progress_lines():
            print(ln)
        if not a.follow:  # follow 档不刷 events（流水尾只看不追，追的是进度流）
            print("--- events(tail %d) ---" % a.events)
            try:
                with open(EVENTS, encoding="utf-8", errors="replace") as f:
                    for ln in [x for x in f.read().splitlines() if x.strip()][-a.events:]:
                        print(ln)
            except OSError:
                print("(无 events 流水)")

    _snapshot()
    if not a.follow:
        return
    seen = len(progress.tail(n=0, card=a.card or None))
    ticks = a.ticks if a.ticks > 0 else 1 << 30  # --ticks 有限档(cron/测试)，缺省无限 Ctrl-C 退
    try:
        while ticks:
            time.sleep(a.interval)
            cur = progress.tail(n=0, card=a.card or None)
            for ln in cur[seen:]:
                print(ln, flush=True)
            seen = len(cur)
            ticks -= 1
    except KeyboardInterrupt:
        print("\nwatch: 退出")


def cmd_panel(a):  # MECH-VIS1：手动刷新/打开面板（只读派生视图，不进 WRITERS 不碰账——写命令本就同笔自动刷）
    from . import panel
    data = panel.build_data()
    if data is None:
        die("账未开（无 graph.json），面板无货可画")
    p = panel.render(out=a.out)
    print("panel -> %s nodes=%d gen=%s (%d bytes)" % (p, len(data["nodes"]), data["gen"], os.path.getsize(p)))
    if a.open_it:
        import webbrowser
        webbrowser.open("file:///" + os.path.abspath(p).replace("\\", "/"))


def cmd_plan(a):  # MECH-ENG1：单 tick 决策快照=只读面。dispatcher/看板/外部执行层只消费这一份。
    from . import engine
    d = engine.tick()
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    print("plan: gen=%s ready=%d claimed=%d run_complete=%s remaining=%d alarms_open=%d retire_ready=%d" % (
        d["generation"], len(d["ready"]), len(d["claimed"]), d["run_complete"],
        len(d["remaining"]), len(d["alarms_open"]), len(d["retire_ready"])))
    for lane, head in sorted(d["claimable"].items()):
        print("  lane %s -> %s" % (lane, head or "-"))
