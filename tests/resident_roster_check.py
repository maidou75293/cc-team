#!/usr/bin/env python3
# cc-team/tests/resident_roster_check.py — REQ-RESIDENT-ROSTER-1 常驻岗全量入册对账。
#
# 这是什么（类型=需求，业主 seq=8 第2/6点收尾）：
#   把「账本里登记过的岗」和「机器上真实活着的入口」一一对照。
#   判据只有一条硬规矩：**存在无主入口 或 无入口的岗卡 ⇒ 退出码非零**。
#   这就是以后互盯岗的种子——MECH-RESIDENT-WATCH-1 的 DEGRADED 报警走卡面 heartbeat，
#   本脚本管的是「有没有漏网的入口 / 空挂的岗卡 / 一入口两卡」，不是服务健康不健康。
#   （口径分界见 docs/机制-常驻岗名册-20261102.md 第三节；不自造，沿用 MECH-RESIDENT-1 三态语义。）
#
# 只读纪律：只跑 crontab -l / systemctl --user list-units / tmux ls / openclaw cron list /
#   schtasks /query —— 不重启、不改 crontab、不写账本（写账本一律走 cards_dag 写闸）。
#
# 用法：
#   python -I -X utf8 cc-team/tests/resident_roster_check.py               # 全量对账（卡面判据）
#   python -I -X utf8 cc-team/tests/resident_roster_check.py --card RESIDENT-X   # 单岗核验（岗卡 verify）
#   python -I -X utf8 cc-team/tests/resident_roster_check.py --probe       # 附跑 heartbeat 报在岗态（不改退出码）
#   python -I -X utf8 cc-team/tests/resident_roster_check.py --redcheck    # 自检：证明本守卫红得起来
import json
import os
import re
import subprocess
import sys

# 运行宿主双形态（MECH-VFY1 同源坑）：Windows 侧经 wsl.exe 取 WSL 视图，WSL 侧直跑。
IN_WSL = sys.platform.startswith("linux") and os.path.isdir("/mnt/c")


def bash(cmd, timeout=60):
    """在 WSL 侧执行只读命令，返回 (rc, text)。"""
    full = (["bash", "-lc", cmd] if IN_WSL else ["wsl.exe", "-e", "bash", "-lc", cmd])
    try:
        r = subprocess.run(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "SOURCE-UNREADABLE(%s)" % e
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


def win(args, timeout=60):
    """Windows 原生只读命令（不经 MSYS，免斜杠改写）。"""
    if not IN_WSL:
        cmd = list(args)
    else:
        head = "/mnt/c/Windows/System32/" + args[0]
        cmd = ([head] if os.path.exists(head) else list(args)) + list(args[1:])
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "SOURCE-UNREADABLE(%s)" % e
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


def norm(s):
    return " ".join((s or "").split())


def team_home():
    """账本根 = env CC_TEAM_HOME 优先（与 ccards/config.py 同一口径），否则本包上一级。"""
    return os.path.abspath(os.environ.get("CC_TEAM_HOME")
                           or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ==== 真实世界入口现读 ====
SCHED = re.compile(r"^\s*(?:\S+\s+){5}\S")            # 5 段时间字段 + 命令
UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"

def live_crontab():
    rc, out = bash("crontab -l 2>/dev/null")
    if rc != 0:
        return None, "crontab -l rc=%d" % rc
    return [norm(l) for l in out.splitlines()
            if l.strip() and not l.strip().startswith("#") and SCHED.match(l)], None


def live_units():
    rc, out = bash("systemctl --user list-units --type=service --state=active "
                   "--no-legend --no-pager 2>/dev/null")
    if rc != 0:
        return None, "systemctl --user list-units rc=%d" % rc
    return [m.group(1) for m in (re.match(r"\s*(\S+\.service)\s", l) for l in out.splitlines())
            if m], None


def live_tmux():
    rc, out = bash("tmux ls 2>/dev/null")
    if rc != 0 and not out.strip():
        return [], None                                # 无 server = 空集，不是“读不到”
    return [l.split(":", 1)[0].strip() for l in out.splitlines() if l.strip()], None


def live_automations():
    rc, out = bash("openclaw cron list 2>/dev/null", timeout=120)
    if rc != 0 and not out.strip():
        return None, "openclaw cron list rc=%d" % rc
    return [m.group(1) for m in (re.match(r"^\s*(%s)" % UUID, l, re.I) for l in out.splitlines())
            if m], None


def live_wintask(name):
    rc, _ = win(["schtasks.exe", "/query", "/tn", name])
    return rc == 0


# 非平台岗豁免（列明理由；留空=假装没看见=本卡要治的病）
EXEMPT_UNITS = {"dbus.service": "WSL 用户会话自带 D-Bus 总线，非平台常驻岗"}
EXEMPT_TMUX = {}

KINDS = ("crontab", "unit", "tmux", "automation", "wintask", "ring")


def live_rings():
    """cc-ctrld 常驻进程内的时间轮环清单现读（心跳首行 rings= 字段）。
    业主 2026-11-03 令「定时任务不要用操作系统定时，一个常驻的 Python 进程就可以了」——
    OS 定时器收编进环后，环名就是入口，对账口径随之加这一类（单向往返：只核在册名在不在）。"""
    p = os.path.join(team_home(), "logs", "ctrld.heartbeat")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError as e:
        return None, "心跳读不到 %s：%s" % (p, e)
    m = re.search(r"rings=(\S+)", first)
    if not m:
        return None, "心跳无 rings= 字段：%s" % first[:80]
    return [x.split("/")[0] for x in m.group(1).split(",") if x], None


# ==== 账本岗卡清单 ====
def load_posts(only=None):
    p = os.path.join(team_home(), "state", "graph.json")
    try:
        with open(p, encoding="utf-8") as f:
            cards = (json.load(f).get("cards") or {})
    except (OSError, ValueError) as e:
        return None, "账本读不到 %s：%s（判据宿主需能读 state/graph.json，必要时 export CC_TEAM_HOME）" % (p, e)
    posts = [c for c in cards.values() if c.get("resident") and c.get("status") != "CANCELED"]
    if only is not None:
        posts = [c for c in posts if c["id"] == only]
        if not posts:
            return None, "岗卡 %s 不存在或非岗卡（resident 未落账）" % only
    return posts, None


def signature(c):
    """入口签名 (kind, key)：优先 output.anchor_kind/anchor_key（本卡定版的机器可读面），
    回落解析 anchor 文本——PM/别的道手工立的岗卡也能进对账。"""
    o = c.get("output") or {}
    kind, key = norm(o.get("anchor_kind")).lower(), norm(o.get("anchor_key"))
    if kind in KINDS and key:
        return kind, (norm(key) if kind == "crontab" else key)
    a = (c.get("anchor") or "").strip()
    low = a.lower()
    if "crontab" in low:
        m = re.search(r"`([^`]+)`", a)
        line = norm(m.group(1) if m else a)
        return ("crontab", line) if SCHED.match(line) else ("unknown", a)
    if "systemd" in low or ".service" in a:
        m = re.search(r"[\w@.\-]+\.service", a)
        return ("unit", m.group(0)) if m else ("unknown", a)
    if "tmux" in low:
        m = re.search(r"`([^`]+)`", a)
        return ("tmux", norm(m.group(1) if m else a))
    if "automation" in low:
        m = re.search("(%s|[0-9a-f]{8})" % UUID, a, re.I)
        return ("automation", m.group(1).lower()) if m else ("unknown", a)
    if "计划任务" in a or "scheduledtask" in low or "schtasks" in low:
        m = re.search(r"`([^`]+)`", a)
        return ("wintask", norm(m.group(1) if m else a))
    if "环" in a:                                   # 常驻进程内时间轮环（锚内反引号=环名）
        m = re.search(r"`([^`]+)`", a)
        return ("ring", norm(m.group(1) if m else a))
    return "unknown", a


def reconcile(inject=None, add_claim=None, add_claim2=None):
    """全量对账 → (errors, notes, posts)。
    inject=(kind,key) 模拟真实世界多出一个入口；add_claim/add_claim2=(cid,kind,key,卡状态,在岗态)
    模拟账上多一/两张岗卡——参数只为 --redcheck 证明守卫红得起来，平时调用不传。"""
    errs, notes = [], []
    posts, err = load_posts()
    if err:
        return [err], [], []
    cron, e1 = live_crontab()
    units, e2 = live_units()
    tmux, e3 = live_tmux()
    if cron is None or units is None:
        # 读不到真实世界=盲跑，盲不算过（互盯岗不许有看门无人看的真空）
        return ["真实入口现读失败：%s %s %s" % (e1, e2, e3)], [], posts

    claims, state_of = {}, {}
    for c in posts:
        cid = c["id"]
        miss = [k for k in ("duty", "anchor", "heartbeat", "fail") if not norm(c.get(k))]
        if miss:
            errs.append("岗卡 %s 四件缺位 %s（MECH-RESIDENT-1 立卡时就拒，出现=有人绕过写闸改账）"
                        % (cid, ",".join(miss)))
        kind, key = signature(c)
        if kind == "unknown" or not key:
            errs.append("岗卡 %s 锚点解析不出入口签名（anchor=%s）" % (cid, (c.get("anchor") or "")[:80]))
            continue
        if kind == "crontab" and not SCHED.match(key):
            errs.append("岗卡 %s 的 crontab 锚不是调度行原文：%s（口径：锚=crontab 行全等，"
                        "禁止用子串——否则 --apply 会吞掉 --apply --ring sweep 那行=假平账）"
                        % (cid, key[:120]))
            continue
        claims.setdefault((kind, key), []).append(cid)
        state_of[cid] = (norm(c.get("status")).upper(), norm(c.get("resident_status")).upper() or "OFF")
    for ac in (add_claim, add_claim2):
        if ac:
            cid, kind, key, st, rs = ac             # 注入只服务红检，卡状态/在岗态都显式给，不靠猜
            claims.setdefault((kind, key), []).append(cid)
            state_of.setdefault(cid, (st, rs))

    live = {"crontab": list(cron), "unit": list(units), "tmux": list(tmux or [])}
    if inject and inject[0] in live:
        live[inject[0]].append(inject[1])

    # 方向一：机器上有、账上无 → 无主入口（防漏网的根判据）
    for k in live["crontab"]:
        cid = claims.get(("crontab", norm(k)))
        if not cid:
            errs.append("无主入口（crontab 行未入册）：%s" % norm(k)[:150])
        elif len(cid) > 1:
            errs.append("一入口两卡（crontab 行被多张岗卡认领）：%s ← %s" % (norm(k)[:80], ",".join(cid)))
    for u in live["unit"]:
        if u in EXEMPT_UNITS:
            notes.append("豁免单元 %s：%s" % (u, EXEMPT_UNITS[u]))
            continue
        cid = claims.get(("unit", u))
        if not cid:
            errs.append("无主入口（active 单元未入册）：%s" % u)
        elif len(cid) > 1:
            errs.append("一入口两卡（unit 被多张岗卡认领）：%s ← %s" % (u, ",".join(cid)))
    for s in live["tmux"]:
        if s in EXEMPT_TMUX or not s.startswith("cc-"):
            continue                                    # 口径：cc-* 前缀=平台常驻会话，其余不册
        cid = claims.get(("tmux", s))
        if not cid:
            errs.append("无主入口（tmux 会话未入册）：%s" % s)

    # 方向二：账上有、机器上无 → 无入口的岗卡（空挂）。
    # 只有「验过收口又主动撤岗(VERIFIED+resident_status=OFF，卡面：cancel 同笔置 OFF)」才算有意停岗、
    # 不判红只提示；其余（含 DRAFT 新岗卡）查不到入口就是谎账——卡面③「无入口的岗卡即非零」。
    have = {("crontab", norm(k)) for k in live["crontab"]} | \
           {("unit", u) for u in live["unit"]} | \
           {("tmux", s) for s in live["tmux"]}
    for (kind, key), cid in sorted(claims.items()):
        if kind not in ("crontab", "unit", "tmux"):
            continue
        if (kind, key) in have:
            continue
        stood_down = [x for x in cid if state_of.get(x, ("", ""))[1] == "OFF"
                      and state_of.get(x, ("", ""))[0] in ("VERIFIED", "DONE")]
        if len(stood_down) == len(cid):
            notes.append("停岗岗卡不判空挂：%s（锚 %s:%s 查无；收口后又 duty OFF=有意停岗，只提示待撤卡）"
                         % (",".join(cid), kind, key[:70]))
            continue
        errs.append("无入口的岗卡 %s：%s 锚 %s 在真实世界不存在（撤了锚件请走 cancel/duty OFF）"
                    % (",".join(cid), kind, key[:150]))

    # 方向三（单向）：automation jobId / Windows 计划任务 只核「锚点在不在」
    #（卡册第三节 PM 口径：openclaw 其余 job 属业主侧自有、Windows 计划任务海量，不做入口反查）
    auto = [(k, cid) for (kd, k), cid in sorted(claims.items()) if kd == "automation"]
    if auto:
        ids, aerr = live_automations()
        if ids is None:
            errs.append("automation 锚无法核验（openclaw 不可读：%s）——盲不算过" % aerr)
        else:
            notes.append("openclaw 现读 job=%d（单向核验：只查在册 jobId 在不在，其余属业主侧自有）" % len(ids))
            for k, cid in auto:
                if not any(i.lower().startswith(k.lower()) for i in ids):
                    errs.append("无入口的岗卡 %s：automation jobId %s 不在 openclaw 现读清单里"
                                % (",".join(cid), k))
    rings, rerr, rblind = None, None, False
    for (kind, key), cid in sorted(claims.items()):
        if kind == "wintask":
            if live_wintask(key):
                notes.append("Windows 计划任务 %s 在册（%s）：单向核验" % (key, ",".join(cid)))
            else:
                errs.append("无入口的岗卡 %s：Windows 计划任务 %s 不存在" % (",".join(cid), key))
        if kind == "ring":
            if rings is None and not rblind:
                rings, rerr = live_rings()
                if rings is None:
                    rblind = True
            if rblind:
                errs.append("无入口的岗卡 %s：环锚无法核验（%s）——盲不算过" % (",".join(cid), rerr))
            elif key in rings:
                notes.append("常驻进程环 %s 在心跳环清单（%s）：单向核验" % (key, ",".join(cid)))
            else:
                errs.append("无入口的岗卡 %s：ctrld 心跳环清单没有 %s（现读=%s）"
                            % (",".join(cid), key, "/".join(rings)))

    notes.insert(0, "对账规模：岗卡=%d 活crontab行=%d active单元=%d tmux会话=%d 认领入口=%d"
                 % (len(posts), len(live["crontab"]), len(live["unit"]), len(live["tmux"]), len(claims)))
    return errs, notes, posts


def run_heartbeat(c):
    """跑岗卡 heartbeat（只读命令），返回 (rc, 摘要)。命令本身按宿主选道。"""
    hb = norm(c.get("heartbeat"))
    if not hb:
        return 2, "无 heartbeat 判据"
    rc, out = bash(hb, timeout=90)
    return rc, " ".join(out.split())[-160:]


def main():
    a = sys.argv[1:]
    if "--redcheck" in a:
        return redcheck()
    only = a[a.index("--card") + 1] if "--card" in a else None
    probe = "--probe" in a
    errs, notes, posts = reconcile()
    if only:
        posts2, e = load_posts(only=only)
        if e:
            print("ERROR", e)
            return 1
        c = posts2[0]
        kind, key = signature(c)
        mine = [x for x in errs if only in x or key in str(x)]
        rc_h, tail = run_heartbeat(c)
        print("%s 锚=%s:%s 对账错=%d 心跳rc=%d %s" % (only, kind, key[:60], len(mine), rc_h, tail))
        for x in mine:
            print("  !", x)
        return 1 if (mine or rc_h != 0) else 0
    for n in notes:
        print("  ·", n)
    for e in errs:
        print("  !", e)
    if probe:
        print("-- heartbeat 实况（在岗=判据过；降岗=锚在但判据不过；不改上面的对账退出码）--")
        for c in posts:
            rc, tail = run_heartbeat(c)
            print("%s %s %s" % ("[在岗]" if rc == 0 else "[降岗]", c["id"], tail))
    if errs:
        print("ROSTER_CHECK=FAIL 缺口 %d 条（无主入口/无入口岗卡/一入口两卡）" % len(errs))
        return 1
    print("ROSTER_CHECK=PASS 岗卡与真实入口一一对应，无漏网")
    return 0


def redcheck():
    """自检：证明这守卫红得起来（本机记忆里的规矩：空检查比没检查更糟）。
    注入只在本进程内发生——不写 crontab、不写账本。"""
    base, _, posts = reconcile()
    if base:
        print("REDCHECK=BLOCKED 基线本就有 %d 条缺口，先平账再谈红检：" % len(base))
        for e in base:
            print("  !", e)
        return 2
    bad = 0
    for label, inj in [("无主 crontab 行", ("crontab", "*/7 * * * * /bin/true # REDCHECK-INJECT")),
                       ("无主 systemd 单元", ("unit", "redcheck-fake.service")),
                       ("无主 tmux 会话", ("tmux", "cc-redcheck-fake"))]:
        errs, _, _ = reconcile(inject=inj)
        hit = any("无主入口" in e for e in errs)
        print("%-20s 注入 → %s（错 %d 条）" % (label, "红" if hit else "居然绿了", len(errs)))
        bad += 0 if hit else 1
    for label, add, want in [
            ("空挂新岗卡", ("REDCHECK-FAKE", "unit", "no-such-post.service", "DRAFT", "OFF"), "无入口的岗卡"),
            ("空挂环岗卡（心跳无此环）", ("REDCHECK-FAKE", "ring", "no-such-ring", "DRAFT", "OFF"), "无入口的岗卡"),
            ("子串锚不认全文", ("REDCHECK-FAKE", "crontab", "ops/recycle.py --apply", "DRAFT", "OFF"),
             "无入口的岗卡")]:
        errs, _, _ = reconcile(add_claim=add)
        hit = any(want in e for e in errs)
        print("%-20s 注入应「%s」→ %s（错 %d 条）" % (label, want, "红" if hit else "居然绿了", len(errs)))
        bad += 0 if hit else 1
    # 一入口两卡：旧版写死 cc-dispatch.service 当「真实入口」——2026-11-03 全 Windows 切流后
    # 该 WSL 单元已停，场景随环境陈旧「居然绿了」。改为自给自足注入：虚拟单元入 live + 两张假卡
    # 同认一入口，不依赖任何现网服务在册（红检的规矩：场景自己造世界，不蹭环境旧账）。
    errs, _, _ = reconcile(inject=("unit", "redcheck-dup.service"),
                           add_claim=("REDCHECK-FAKE-A", "unit", "redcheck-dup.service", "DRAFT", "OFF"),
                           add_claim2=("REDCHECK-FAKE-B", "unit", "redcheck-dup.service", "DRAFT", "OFF"))
    hit = any("一入口两卡" in e for e in errs)
    print("%-20s 注入应「一入口两卡」→ %s（错 %d 条）" % ("一入口两卡(全注入)", "红" if hit else "居然绿了", len(errs)))
    bad += 0 if hit else 1
    # 反向也要成立：有意停岗(VERIFIED+OFF)不该判红，否则「撤了 cron 留卡」会把对账永久钉死
    errs, notes, _ = reconcile(add_claim=("REDCHECK-FAKE", "unit", "no-such-post.service", "VERIFIED", "OFF"))
    ok = not any("无入口的岗卡" in e for e in errs) and any("停岗岗卡不判空挂" in n for n in notes)
    print("%-20s 应不判红且留提示 → %s" % ("停岗卡豁免方向", "对" if ok else "错"))
    bad += 0 if ok else 1
    # 撤了再确认恢复绿（同一份账，两次跑必须一致）
    errs, _, _ = reconcile()
    print("%-20s 复跑基线 → %s" % ("注入退出后", "绿" if not errs else "红"))
    bad += 0 if not errs else 1
    print("REDCHECK=%s" % ("FAIL(守卫红不起来或收不回去)" if bad else "PASS(注入全红+停岗豁免也对+基线可复绿)"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
