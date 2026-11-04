#!/usr/bin/env python3
# guard_watch.py — 系统保障互盯探活器（业主 seq=10 定版：卡=业务、系统保障=PM 常责无生命周期）。
#
# 一句话：逐轮实探 `docs/系统保障清单.md` GUARD-BLOCK 登记的每项保障心跳判据；超阈→
# state/guard-status.json 置 DEGRADED + alerts 聚合账入账，**只报警不自愈**——心跳回来了
# 也停在 DEGRADED，只有人跑 `--reset <保障id>` 才复原（人动作必留痕）。
# 根病对照：旧岗卡互盯（reconcile.resident_watch 读卡面）交付后从未接线=「看门无人看」
# 又差一环复发；本件=保障账本从卡迁移到清单文件后的唯一实探器，挂 ctrld `guards` 环每 300s。
#
# 与旧三态的关系：不再有卡面 duty/resident_status——探活状态只落 state/guard-status.json
# （OK/DEGRADED 粘性）。RESDIENT-* 卡已全部作废（seq=10），本件**不读 graph.json**。
#
# 用法: python -I cc-team/ops/guard_watch.py            # 探一轮只报（影子/手检）
#       python -I cc-team/ops/guard_watch.py --apply    # 写状态账+告警账（ctrld live 环用）
#       python -I cc-team/ops/guard_watch.py --json     # 机器输出
#       python -I cc-team/ops/guard_watch.py --reset <id> --who PM   # 人工复原唯一通道
# 红绿锁: python -I cc-team/tests/guard-watch-test.py（拔锚复现，exit 0=GUARD_WATCH_ALL_PASS）
import argparse, io, json, os, re, subprocess, sys, time, urllib.request
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

# 打印钉 UTF-8（BUG-GUARDWATCH-GBKPRINT-1，母卡 MECH-GBKDECODE-BOMB-1 同族**打印侧**推广，
# 配方同 recycle.py 07:3x 案：2026-11-03 11:49/11:57 PM 现跑本探针 cp936 宿主 stdout 全行
# 乱码（"NO-SELF-HEAL 恢复(尾…"形）——报警可读性/看板取数受损。只钉出口不赌 locale）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # cc-team
CODE = os.path.dirname(ROOT)                       # ...\code
sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)                       # BUG-GUARD-RINGWIN-CAL-1：读同目录 ctrld 注册表用
from ccards import alerts                           # noqa: E402
from ccards.config import atomic_write_json         # noqa: E402

MARK = "GUARD-WATCH"
MD = os.environ.get("GUARD_WATCH_MD", os.path.join(CODE, "docs", "系统保障清单.md"))
STATE_DIR = os.environ.get("GUARD_WATCH_STATE", os.path.join(ROOT, "state"))
STATUS = os.path.join(STATE_DIR, "guard-status.json")
HB_SELF = os.environ.get("GUARD_WATCH_HB", os.path.join(ROOT, "logs", "guard-watch.heartbeat"))
PROBE_TIMEOUT = int(os.environ.get("GUARD_WATCH_PROBE_TIMEOUT", "30"))
def _num(env, dflt):  # env 坏值/负值回退默认——读数面绝不炸环（环死=门铃停摆同款病根）
    try:
        v = float(os.environ.get(env, ""))
        return v if v >= 0 else dflt
    except (ValueError, TypeError):
        return dflt
RETRY_GAP_S = _num("GUARD_WATCH_RETRY_GAP_S", 8.0)              # argv 探针重试间隔（WSL 冷启秒级）
ARGV_PROBES = max(1, int(_num("GUARD_WATCH_ARGV_PROBES", 3)))   # argv 探针次数（默认3=跨~16s）
BEGIN, END = "<!-- GUARD-BLOCK-BEGIN -->", "<!-- GUARD-BLOCK-END -->"
OK_ST, DEG = "OK", "DEGRADED"


def _alarm_key(gid):
    # 聚合账靠同键 count++ 表现「持续未处置」——键里不掺逐轮 detail（MECH-AUD1 口径）
    return "ALARM: %s %s 心跳超阈→保障 DEGRADED（不自愈，人工 --reset 复原）" % (MARK, gid)


def _ring_budgets():
    """BUG-GUARD-RINGWIN-CAL-1：环预算唯一正本=ctrld build_rings 的每环 every/timeout。
    本器**只读不改**（卡面边界：不动 ctrld/recycle 环运行体；冻结令=只校既有闸门口径）。
    import/执行任何异常=空表——ring 锚随之落回清单窗兜底，两样皆缺=窗0 保守报超阈，绝不静默。
    每次调用现读 env/config.toml 锚（不跨圈缓存，与 ctrld 自身节拍口径同步）。"""
    try:
        import ctrld
        return {r["name"]: (float(r["every"]), float(r["timeout"])) for r in ctrld.build_rings(False)}
    except Exception:
        return {}


def _ring_window(g):
    """ring 锚判窗=**机械派生** max(240, every*2+timeout)（卡面主案），禁止逐条硬编码——
    旧窗 240s 不随环预算伸缩：full 合法长轮(timeout 300s)/sweep 复验判据实测 330~513s
    必然破窗=DEGRADED 误判（08:45:00/08:49:57 复燃实锤：sweep 尾行龄=323s 窗=240s 而环
    08:39:35 rc=0 在场）。环名=清单 `ring` 字段优先，缺省从 grep 针 (`RING <名> rc=`) 抽取；
    注册表未命中（沙箱假环名/环改名）=清单 window_s 兜底；皆缺=0（当超阈大声报）。
    返回 (窗秒, 来源标)——来源进 detail，值班归因不用猜窗从哪来。"""
    name = g.get("ring") or ""
    if not name:
        m = re.search(r"RING\s+(\S+)\s+rc=", g.get("grep") or "")
        name = m.group(1) if m else ""
    b = _ring_budgets().get(name) if name else None
    if b is not None:
        every, timeout = b
        return max(240.0, every * 2 + timeout), "派生窗(every=%ds·timeout=%ds)" % (every, timeout)
    ws = g.get("window_s")
    if ws is not None and str(ws) != "":
        return float(ws), "清单窗(注册表未含环%r)" % name
    return 0.0, "无窗(环%r 注册表未含且清单未标)" % name


def load_guards(md_path=MD):
    """解析清单机器块（BEGIN/END 间的唯一 ```json 围栏）；账本坏了大声报，绝不静默空账。"""
    try:
        txt = io.open(md_path, encoding="utf-8").read()
    except OSError as exc:
        raise RuntimeError("保障清单读不到 %s: %r" % (md_path, exc))
    try:
        seg = txt.split(BEGIN, 1)[1].split(END, 1)[0]
    except IndexError:
        raise RuntimeError("保障清单缺 GUARD-BLOCK 标记段（%s）" % md_path)
    fence = seg.split("```json", 1)
    if len(fence) < 2:
        raise RuntimeError("GUARD-BLOCK 内无 ```json 围栏")
    doc = json.loads(fence[1].split("```", 1)[0])
    guards = doc.get("guards")
    if not isinstance(guards, list) or not guards:
        raise RuntimeError("GUARD-BLOCK guards 缺失/空表=永真空闸，拒")
    ids = [g.get("id") for g in guards]
    if len(ids) != len(set(ids)) or any(not i for i in ids):
        raise RuntimeError("guards id 重复/缺失: %s" % ids)
    return guards


def check_guard(g):
    """返回 (alive: bool, detail: str)。判据命令一律 argv 列表+shell=False（不踩 GBK/引号坑）。"""
    kind = g.get("kind")
    try:
        if kind == "log":
            p = g.get("path") or ""
            p = p if os.path.isabs(p) else os.path.join(CODE, p)
            try:
                age = time.time() - os.path.getmtime(p)
            except OSError:
                return False, "log 缺失: %s（在岗无心跳痕=真空）" % p
            if age > float(g.get("window_s", 0)):
                return False, "log mtime=%.0fs 窗=%ss" % (age, g.get("window_s"))
            needle = g.get("grep")
            if needle:
                try:
                    with io.open(p, encoding="utf-8", errors="replace") as fh:
                        tail = fh.read().rstrip("\n").splitlines()[-1]
                except OSError:
                    return False, "log 读不出: %s" % p
                if needle not in tail:
                    return False, "尾行不含 %r" % needle
            return True, "log ok(%.0fs)" % age
        if kind == "json-age":
            p = g.get("path") or ""
            p = p if os.path.isabs(p) else os.path.join(CODE, p)
            try:
                doc = json.load(io.open(p, encoding="utf-8"))
                beat = float(doc.get(g.get("field")))
            except (OSError, ValueError, TypeError) as exc:
                return False, "json-age 读不出 %s(%r)" % (p, exc)
            age = time.time() - beat
            return (age <= float(g.get("window_s", 0))), "age=%.0fs 窗=%ss" % (age, g.get("window_s"))
        if kind == "ring":
            # ctrld.log 逐行带 `%F %T` 时间戳：取**最后一条含 needle 的行**的落笔龄，
            # 而非文件 mtime（所有环共写一文件，mtime 会被邻环刷新=死环假绿）。
            # 判窗=按 ctrld 注册表机械派生（BUG-GUARD-RINGWIN-CAL-1；旧逐条硬编码 240s
            # 不随环预算伸缩=full/sweep 合法长轮必误判 DEGRADED，见 _ring_window）。
            p = g.get("path") or "cc-team/logs/ctrld.log"
            p = p if os.path.isabs(p) else os.path.join(CODE, p)
            needle = g.get("grep") or ""
            try:
                with io.open(p, "rb") as fh:
                    fh.seek(max(0, os.path.getsize(p) - 262144))
                    chunk = fh.read().decode("utf-8", "replace")
            except OSError:
                return False, "ctrld.log 读不出: %s" % p
            hit = None
            for line in reversed(chunk.splitlines()):
                if needle and needle in line:
                    hit = line
                    break
            if hit is None:
                return False, "尾窗 256KB 内无 %r（死环或账漂）" % needle
            try:
                beat = time.mktime(time.strptime(hit[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                return False, "命中行无时间戳头: %s" % hit[:60]
            age = time.time() - beat
            win, wsrc = _ring_window(g)
            return (age <= win), "尾行龄=%.0fs 窗=%.0fs·%s (%s)" % (age, win, wsrc, hit[:60])
        if kind == "argv":
            # 探针带重试（2026-11-03 relay 晨间四连环实锤：一次 3s 重试盖不住 WSL 冷启窗，
            # 07:0x 仍劣化）：默认 3 探、间隔 8s≈全程 16s 滤瞬失；真挂连红 N 次照旧进
            # DEGRADED（guard-watch-test G-ARGV3 确定性红不受影响，只慢两拍）。
            # 粘性不自愈纪律不变——重试滤的是测量噪声，不是把 DEGRADED 改成自愈。
            detail = "argv 未跑"
            for attempt in range(ARGV_PROBES):
                try:
                    r = subprocess.run(g["argv"], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=PROBE_TIMEOUT, creationflags=NO_WIN)
                    if r.returncode == 0:
                        return True, "argv rc=0%s" % ("（重试后活=瞬失）" if attempt else "")
                    detail = "argv rc=%d" % r.returncode
                except subprocess.TimeoutExpired:
                    detail = "argv timeout>%ds" % PROBE_TIMEOUT
                if attempt < ARGV_PROBES - 1:
                    time.sleep(RETRY_GAP_S)
            return False, detail + "（连红%d次）" % ARGV_PROBES
        if kind == "url":
            req = urllib.request.Request(g["url"], headers={"User-Agent": MARK})
            with urllib.request.urlopen(req, timeout=float(g.get("timeout_s", 10))) as resp:
                return resp.status < 400, "url http=%s" % resp.status
        return False, "未知 kind=%r" % kind
    except subprocess.TimeoutExpired:
        return False, "argv timeout>%ds" % PROBE_TIMEOUT
    except Exception as exc:  # 探不出=当超阈报，绝不静默当活
        return False, "探测异常 %r" % exc


def _read_status():
    try:
        return json.load(io.open(STATUS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_watch(apply=False):
    """探一轮全量。粘性 DEGRADED：翻劣写账+告警；持续劣化刷告警计数；心跳回来只提示不自愈。"""
    guards = load_guards()
    status = _read_status()
    out = dict(guards=len(guards), scanned=0, degraded_new=0, persist=0,
               recovery_notice=0, alarms=0, errors=0)
    flips = []
    for g in guards:
        gid = g["id"]
        alive, detail = check_guard(g)
        out["scanned"] += 1
        st = (status.get(gid) or {}).get("state", OK_ST)
        if alive:
            if st == DEG:
                out["recovery_notice"] += 1
                out["alarms"] += 1
                print("%s %s NO-SELF-HEAL 心跳已回来(%s)——保障仍 DEGRADED，人跑 --reset 才复原" %
                      (MARK, gid, detail))
                if apply:
                    alerts.record(_alarm_key(gid) + "｜心跳已恢复待人工")
            continue
        if st == DEG:
            out["persist"] += 1
            out["alarms"] += 1
            if apply:
                alerts.record(_alarm_key(gid))
            print("%s %s 仍 DEGRADED(持续: %s)——告警计数刷新，不自愈" % (MARK, gid, detail))
            continue
        flips.append((gid, detail))
        out["degraded_new"] += 1
        out["alarms"] += 1
        print(_alarm_key(gid) + "｜" + detail)
        if apply:
            alerts.record(_alarm_key(gid))
    if flips and apply:
        for gid, detail in flips:
            entry = status.get(gid) or {}
            entry.update({"state": DEG, "since": now_iso(), "detail": detail})
            status[gid] = entry
        atomic_write_json(STATUS, status)
    if apply:
        try:  # 互盯自身心跳痕（清单 `guards` 项即探它——看门也要有人看）
            os.makedirs(os.path.dirname(HB_SELF), exist_ok=True)
            with io.open(HB_SELF, "a", encoding="utf-8"):
                os.utime(HB_SELF, None)
        except OSError as exc:
            print("ALARM: %s 自身心跳痕写不进(%r)——别静默" % (MARK, exc))
            out["alarms"] += 1
    return out


def manual_reset(gid, who):
    """人工复原唯一通道（扫描永不走这里）：DEGRADED→OK，状态账+stdout 留痕。"""
    status = _read_status()
    entry = status.get(gid)
    if not isinstance(entry, dict) or entry.get("state") != DEG:
        print("ERROR: REJECT-RESET %s 不在 DEGRADED（当前 %s）——reset 只复原劣化保障" %
              (gid, (entry or {}).get("state") or "无账"))
        return 2
    entry.update({"state": OK_ST, "reset_by": who, "reset_at": now_iso()})
    atomic_write_json(STATUS, status)
    print("%s %s 人工复原 DEGRADED->OK by=%s（告警开放项另跑 ack 闭环）" % (MARK, gid, who))
    return 0


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S+0800", time.localtime())


def main():
    p = argparse.ArgumentParser(description="系统保障互盯探活器（seq=10）")
    p.add_argument("--apply", action="store_true", help="写状态账+告警账（live 环用；缺省只报）")
    p.add_argument("--json", action="store_true", help="结果打 JSON")
    p.add_argument("--reset", metavar="保障id")
    p.add_argument("--who", default="PM")
    a = p.parse_args()
    if a.reset:
        sys.exit(manual_reset(a.reset, a.who))
    try:
        r = run_watch(apply=a.apply)
    except RuntimeError as exc:  # 账本坏=最大声，绝不空账假绿
        print("ALARM: %s 保障清单异常：%s" % (MARK, exc))
        if a.apply:
            alerts.record("ALARM: %s 保障清单异常(探活停摆)" % MARK)
        sys.exit(1)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    print("%s scanned=%d guards=%d degraded_new=%d persist=%d recovery_notice=%d alarms=%d" % (
        MARK, r["scanned"], r["guards"], r["degraded_new"], r["persist"],
        r["recovery_notice"], r["alarms"]))
    sys.exit(1 if r["alarms"] else 0)


if __name__ == "__main__":
    main()
