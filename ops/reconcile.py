#!/usr/bin/env python3
# reconcile.py — 常驻岗互盯兜底（MECH-RESIDENT-WATCH-1，业主 seq8 点5-6）。
#
# 一句话：reconcile 岗每周期扫全部「常驻岗卡」的卡面心跳判据；超阈 → 该岗卡
# duty ON-DUTY→DEGRADED + 告警入账（events 流水 + alerts 聚合账），**不自动自愈**——
# 心跳回来了岗卡也停在 DEGRADED，只有人跑 `--reset <卡号>` 才复原（人动作必留痕）。
# 根病对照：本次事故链死于「看门无人看」，所以互盯本体自己每次扫描落心跳痕迹
# （state/resident-watch.heartbeat），岗卡册里 reconcile 岗可把心跳判据指到本文件
# 或由 recycle 岗/看板点名判其 mtime；本文件失败一律大声 ALARM，绝不静默。
#
# 与卡面七态机的边界（seq8 点1 定版）：duty 三态 ON-DUTY/DEGRADED/OFF 是**独立字段**，
# STATUS_ENUM 不动；本扫描只写 duty，绝不改 status——reconcile_events 判据一
# （events 尾 vs 卡面 status）用「status->status」同值流水记账，duty 变化进 extra。
# 数据层（resident 布尔/三态/心跳判据四件套）由 MECH-RESIDENT-1(m2) 落地；卡面字段名
# 未定死前，这里按 DUTY_ALIASES/HB_ALIASES 宽容识别（读多候选、写走既有字段，缺省 "duty"），
# m2 定名后如不同名，只在下方别名表加一行，不改逻辑。
#
# 用法: python3 ops/reconcile.py [--json] [--dry] [--quiet]
#       python3 ops/reconcile.py --reset <卡号> [--who 名字]   # 人工自愈唯一通道
# 红绿锁: python -I tests/resident-watch-test.py（拔锚复现，可重跑，exit 0=全绿）
# 在岗形态: ops/reconcile_events.py(*/30 crontab) 末尾挂钩调 resident_watch()——
#           reconcile 岗即互盯岗，不加第二条 crontab。
import argparse, json, os, subprocess, sys, time
import os
NO_WIN = 0x08000000 if os.name == "nt" else 0  # 黑窗口根除(业主2026-11-03)：无控制台父派生的子进程不再各自分配 conhost

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ccards import alerts, locks
from ccards.config import EVENTS, STATE, now
from ccards.events import event
from ccards.graph import Graph, all_cards

ON_DUTY, DEGRADED, OFF = "ON-DUTY", "DEGRADED", "OFF"
MARK = "RESIDENT-WATCH"
# 岗卡 duty 字段候选（m2 数据层定名前宽容识别；写回优先落既有字段，全无时缺省 "duty"）
DUTY_ALIASES = ("duty", "on_duty", "duty_state", "post_state", "resident_state")
DUTY_WRITE_KEY = "duty"
# 心跳判据候选（seq8 点3「心跳判据=可执行命令」为主形态；字典档是给 log-mtime 这类
# 常见判据的免写壳便利，两种都收，判据里自带阈值）
HB_ALIASES = ("heartbeat_cmd", "hb_cmd", "watch_cmd", "alive_cmd", "probe_cmd",
              "heartbeat", "watch", "heartbeat_spec", "hb")
FRESH_KEYS = ("fresh_min", "max_age_min", "threshold_min", "fresh")
LOG_KEYS = ("log", "logfile", "heartbeat_log", "log_path")
CMD_KEYS = ("cmd", "shell", "command")
DEFAULT_FRESH_MIN = 30          # 字典档没给阈值时的兜底窗口（分钟）；岗卡应显式声明
CMD_TIMEOUT = int(os.environ.get("RESIDENT_WATCH_CMD_TIMEOUT", "30"))  # 判据命令超时=超阈
HB_SELF = os.path.join(STATE, "resident-watch.heartbeat")  # 互盯岗自身心跳痕


def _canon_duty(raw):
    if raw is None:
        return None
    s = str(raw).strip().upper().replace("_", "-")
    if s in ("ON-DUTY", "ONDUTY"):
        return ON_DUTY
    if s in ("DEGRADED",):
        return DEGRADED
    if s in ("OFF",):
        return OFF
    return "INVALID:" + s


def is_resident(c):
    """常驻岗判定：resident 布尔为真，或卡面已带可识别 duty 字段。"""
    r = c.get("resident")
    if isinstance(r, str):
        if r.strip().lower() in ("1", "true", "yes", "y", "on"):
            return True
    if r is True:
        return True
    return any(k in c and _canon_duty(c.get(k)) not in (None,) for k in DUTY_ALIASES)


def duty_of(c):
    """返回 (写入键, 归一态, 来源说明)。
    既有字段优先；全无时按 seq8 点1「VERIFIED=验收合格上岗」派生：VERIFIED→ON-DUTY
    （过渡档，数据层落地后岗卡必带显式字段），未 VERIFIED 的岗卡=还没上岗→(None)。"""
    for k in DUTY_ALIASES:
        if k in c and c.get(k) not in (None, ""):
            return k, _canon_duty(c.get(k)), "field:%s" % k
    if c.get("status") == "VERIFIED":
        return DUTY_WRITE_KEY, ON_DUTY, "derived:VERIFIED"
    return DUTY_WRITE_KEY, None, "not-on-duty-yet"


def hb_spec(c):
    """取心跳判据并归一为 {"cmd":str} 或 {"log":path,"fresh_min":N}；取不到=None。"""
    raw = None
    for k in HB_ALIASES:
        if c.get(k) not in (None, ""):
            raw = c.get(k)
            break
    if raw is None:
        o = c.get("output") or {}
        if isinstance(o, dict):
            for k in HB_ALIASES:
                if o.get(k) not in (None, ""):
                    raw = o.get(k)
                    break
    if raw is None:
        return None
    if isinstance(raw, str):
        return {"cmd": raw}
    if isinstance(raw, dict):
        cmd = next((raw[k] for k in CMD_KEYS if raw.get(k)), None)
        if cmd:
            return {"cmd": cmd}
        log = next((raw[k] for k in LOG_KEYS if raw.get(k)), None)
        if log:
            thr = next((raw[k] for k in FRESH_KEYS if raw.get(k) not in (None, "")), None)
            try:
                thr = float(thr) if thr is not None else DEFAULT_FRESH_MIN
            except ValueError:
                thr = DEFAULT_FRESH_MIN
            return {"log": log, "fresh_min": thr}
    return None


def check_hb(spec):
    """判据执行档：rc==0=心跳新鲜；rc!=0/超时/判据命令不可跑=超阈。
    log-mtime 档：文件不存在=从没摸过=超阈（在岗却无心跳痕迹正是真空）。
    返回 (alive: bool, detail: str)。"""
    if "cmd" in spec:
        try:
            r = subprocess.run(spec["cmd"], shell=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=CMD_TIMEOUT, creationflags=NO_WIN)
            return r.returncode == 0, "cmd rc=%d" % r.returncode
        except subprocess.TimeoutExpired:
            return False, "cmd timeout>%ds" % CMD_TIMEOUT
        except OSError as exc:
            return False, "cmd 不可执行(%r)" % exc
    p = os.path.expanduser(str(spec.get("log") or ""))
    try:
        age_min = (time.time() - os.path.getmtime(p)) / 60.0
    except OSError:
        return False, "log 缺失: %s" % p
    thr = spec.get("fresh_min", DEFAULT_FRESH_MIN)
    return age_min <= thr, "log mtime=%.1fmin 阈=%smin" % (age_min, thr)


def _msg(cid, phase=""):
    # 告警键=稳定文案（不含逐轮变化的 detail）——MECH-AUD1 聚合账靠同键 count++ 表现
    # 「持续未处置」；键里掺 mtime/秒数会让每轮生成新开放项，看门狗变成刷屏机器。
    return "ALARM: %s %s 心跳超阈→岗卡 DEGRADED（不自愈，人工 --reset 复原）%s" % (
        MARK, cid, ("｜" + phase) if phase else "")


def resident_watch(dry=False, quiet=False):
    """扫一轮。返回统计 dict（--json / reconcile_events 钩子共用这一个口）。"""
    out = dict(resident=0, scanned=0, off=0, skipped=0, degraded_new=0,
               persist=0, recovery=0, missing=0, invalid=0, alarms=0)
    cards = all_cards()
    posts = sorted(cid for cid, c in cards.items() if is_resident(c))
    out["resident"] = len(posts)
    if not dry:
        try:  # 互盯岗自身心跳痕：在岗扫描=摸一次狗（供岗卡册/其它岗点名判活）
            os.makedirs(os.path.dirname(HB_SELF), exist_ok=True)
            with open(HB_SELF, "a", encoding="utf-8"):
                os.utime(HB_SELF, None)
        except OSError as exc:
            print("ALARM: %s 自身心跳痕写不进(%r)——看门狗心跳断，别静默" % (MARK, exc))
            out["alarms"] += 1
    flips = []
    for cid in posts:
        c = cards[cid]
        dkey, dval, dsrc = duty_of(c)
        if dval == OFF:
            out["off"] += 1
            continue
        if dval is None:
            out["skipped"] += 1
            continue
        if dval.startswith("INVALID"):
            out["invalid"] += 1
            out["alarms"] += 1
            print("ALARM: %s %s 岗卡 duty 值非法(%s)——三态外无兜底，人查卡" % (MARK, cid, dval))
            if not dry:
                alerts.record("ALARM: %s %s duty 值非法(%s)" % (MARK, cid, dval))
            continue
        spec = hb_spec(c)
        if spec is None:
            out["missing"] += 1
            out["alarms"] += 1
            print("ALARM: %s %s 在岗但卡面无心跳判据=看门无人看真空（岗卡四件缺「心跳判据」，"
                  "补判据或转 OFF）" % (MARK, cid))
            if not dry:
                alerts.record("ALARM: %s %s 在岗无心跳判据(看门无人看)" % (MARK, cid))
            continue
        out["scanned"] += 1
        alive, detail = check_hb(spec)
        if alive:
            if dval == DEGRADED:
                out["recovery"] += 1
                out["alarms"] += 1
                if not quiet:
                    print("%s %s NO-SELF-HEAL 心跳已回来(%s)——按失败语义不自愈，岗卡保持 DEGRADED，"
                          "人跑 --reset 才复原" % (MARK, cid, detail))
                if not dry:
                    alerts.record(_msg(cid, "心跳已恢复但岗卡仍 DEGRADED（不自愈待人工）"))
            continue
        if dval == DEGRADED:
            out["persist"] += 1
            out["alarms"] += 1
            if not dry:
                alerts.record(_msg(cid))
            if not quiet:
                print("%s %s 仍 DEGRADED(超阈持续: %s)——告警计数刷新，不自愈" % (MARK, cid, detail))
            continue
        flips.append((cid, dkey, detail, dsrc))
    if flips:
        if dry:
            for cid, dkey, detail, dsrc in flips:
                print("%s DRY: %s 应转 DEGRADED(%s)——未落账" % (MARK, cid, detail))
            out["degraded_new"] += len(flips)
            out["alarms"] += len(flips)
        else:
            out = _apply_flips(flips, out)
    return out


def _apply_flips(flips, out):
    """转 DEGRADED 是唯一写账动作：进台账写锁、锁内现读复核（期间可能已被人 reset/转 OFF），
    只走 duty 字段，status 一字不动。锁被占超时=本轮放弃，下轮 cron 重扫——宁可晚报警不添乱。"""
    locks.team_lock_acquire(MARK.lower())
    try:
        g = Graph()
        for cid, dkey, detail, dsrc in flips:
            node = g.node(cid)
            if not isinstance(node, dict):
                continue
            now_key, now_val, now_src = duty_of(node)
            if now_val in (DEGRADED, OFF) or (now_val or "").startswith("INVALID"):
                print("%s %s 锁内复核已变(%s)——跳过写入" % (MARK, cid, now_val))
                continue
            if not node.get("resident"):
                continue
            # RESIDENT-WATCH-FLIP
            node[dkey] = DEGRADED
            g.put(node)
            event(cid, node.get("status", "?"), node.get("status", "?"), MARK.lower(),
                  "%s duty:%s->DEGRADED(%s) %s" % (MARK, now_val or dsrc, dsrc, detail))
            out["degraded_new"] += 1
            out["alarms"] += 1
            print(_msg(cid) + "｜" + detail)
            alerts.record(_msg(cid))
    finally:
        locks.team_lock_release()
    return out


def manual_reset(cid, who):
    """人工自愈唯一通道（扫描永不走这里）：DEGRADED→ON-DUTY，进锁+流水留痕。"""
    locks.team_lock_acquire("resident-watch-reset")
    try:
        g = Graph()
        node = g.node(cid)
        if not isinstance(node, dict):
            print("ERROR: 卡不存在: " + cid)
            return 2
        if not is_resident(node):
            print("ERROR: REJECT-RESET-NOT-RESIDENT %s 不是常驻岗卡(resident/duty 都没有)——reset 只对岗卡" % cid)
            return 2
        dkey, dval, dsrc = duty_of(node)
        if dval != DEGRADED:
            print("ERROR: REJECT-RESET-NOT-DEGRADED %s 岗态=%s 非 DEGRADED，无需/不可 reset（%s）"
                  % (cid, dval or dsrc, dsrc))
            return 2
        # RESIDENT-WATCH-RESET
        node[dkey] = ON_DUTY
        g.put(node)
        event(cid, node.get("status", "?"), node.get("status", "?"),
              who or "manual", "%s duty:DEGRADED->ON-DUTY manual-reset by=%s" % (MARK, who or "manual"))
        print("%s %s 人工复原 DEGRADED->ON-DUTY by=%s（告警开放项请另跑 ack 闭环）" % (MARK, cid, who or "manual"))
        return 0
    finally:
        locks.team_lock_release()


def main():
    p = argparse.ArgumentParser(description="常驻岗互盯兜底（MECH-RESIDENT-WATCH-1）")
    p.add_argument("--json", action="store_true", help="结果打 JSON（测试/看板消费）")
    p.add_argument("--dry", action="store_true", help="只报不写账")
    p.add_argument("--quiet", action="store_true", help="只打 ALARM 与汇总")
    p.add_argument("--reset", metavar="卡号", help="人工自愈：该岗卡 DEGRADED→ON-DUTY（唯一复原通道）")
    p.add_argument("--who", default="PM", help="--reset 留痕署名（默认 PM）")
    a = p.parse_args()
    if a.reset:
        sys.exit(manual_reset(a.reset, a.who))
    r = resident_watch(dry=a.dry, quiet=a.quiet)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    print("%s scanned=%d resident=%d off=%d skipped=%d degraded_new=%d persist=%d "
          "recovery_notice=%d missing_criterion=%d invalid=%d alarms=%d" % (
              MARK, r["scanned"], r["resident"], r["off"], r["skipped"], r["degraded_new"],
              r["persist"], r["recovery"], r["missing"], r["invalid"], r["alarms"]))
    sys.exit(1 if r["alarms"] else 0)


if __name__ == "__main__":
    main()
