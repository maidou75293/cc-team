# -*- coding: utf-8 -*-
# cc_backup.py — 台账快照备份+恢复演练（信箱 seq=22 E 项，业主拍板 2026-11-03：
# 「cron 每日快照+保留窗：state/+docs/+inbox/ 打包 tar.gz 到 D:\backups\cc-team\（保留
# 14 天滚动），恢复演练一次（从快照还原到沙箱跑 selftest）留痕保障清单」）。
# 零 OS 定时器铁律（业主 2026-11-03 令）：不自建 OS cron——本器=纯函数式 CLI，
#   --ensure 供常驻环调用（超 26h 无快照即补一份，缺窗自愈）；
#   --backup/--drill 手动留痕。每日环注册进 cc-ctrld 候 BUG-SWEEPLOCK-DEADPID-1 通电
#   （改 ops/ctrld.py 即触发 S4 重启，若在途 sweep 轮被 kill_tree 会撞死主锁 40min 坑——
#   时序上先修锁再上环，一次重启吃两个变更）。
# 快照内容=台账三目录（state/ docs/ inbox/，即「账」本体）；不含代码（代码有 git）。
import argparse, json, os, re, shutil, subprocess, sys, tarfile, tempfile, time
from datetime import datetime, timedelta

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
TEAM = os.path.dirname(HERE)                      # cc-team 根
REPO = os.path.dirname(TEAM)
DEFAULT_DEST = os.environ.get("CC_BACKUP_DIR") or r"D:\backups\cc-team"
KEEP_DAYS = int(os.environ.get("CC_BACKUP_KEEP_DAYS") or 14)
OVERDUE_H = float(os.environ.get("CC_BACKUP_OVERDUE_H") or 26)
SUBDIRS = ("state", "inbox")                      # TEAM 下
SUBDIRS_REPO = ("docs",)                          # REPO 下（docs 在仓库根非 cc-team）


def _now():
    return datetime.now().astimezone()


def snapshot(dest, tag=""):
    """打一包 tar.gz，返回路径。命名含分钟级时间戳+tag，滚动清理按 mtime。"""
    os.makedirs(dest, exist_ok=True)
    name = "cc-team-%s%s.tar.gz" % (time.strftime("%Y%m%d-%H%M%S"), ("-" + tag) if tag else "")
    p = os.path.join(dest, name)
    with tarfile.open(p, "w:gz") as tf:
        for d in SUBDIRS:
            src = os.path.join(TEAM, d)
            if os.path.isdir(src):
                tf.add(src, arcname="cc-team/" + d)
        for d in SUBDIRS_REPO:
            src = os.path.join(REPO, d)
            if os.path.isdir(src):
                tf.add(src, arcname=d)
    prune(dest)
    return p


def prune(dest):
    """保留窗：删掉 KEEP_DAYS 天外的旧包（只认自家命名式样，别碰人别的文件）。"""
    cut = time.time() - KEEP_DAYS * 86400
    killed = []
    pat = re.compile(r"^cc-team-\d{8}-\d{6}.*\.tar\.gz$")
    try:
        for f in os.listdir(dest):
            fp = os.path.join(dest, f)
            if pat.match(f) and os.path.isfile(fp) and os.path.getmtime(fp) < cut:
                try:
                    os.remove(fp)
                    killed.append(f)
                except OSError:
                    pass
    except OSError:
        pass
    return killed


def latest(dest):
    pat = re.compile(r"^cc-team-\d{8}-\d{6}.*\.tar\.gz$")
    try:
        fs = [os.path.join(dest, f) for f in os.listdir(dest) if pat.match(f)]
    except OSError:
        return None
    if not fs:
        return None
    return max(fs, key=os.path.getmtime)


def ensure(dest):
    """环入口语义：距最新快照超 OVERDUE_H → 补一份；否则跳过（幂等）。"""
    lp = latest(dest)
    if lp and (time.time() - os.path.getmtime(lp)) < OVERDUE_H * 3600:
        print("CC_BACKUP_SKIP 最新快照龄%.1fh<%.0fh %s" % (
            (time.time() - os.path.getmtime(lp)) / 3600, OVERDUE_H, os.path.basename(lp)))
        return None
    p = snapshot(dest, tag="ensure")
    print("CC_BACKUP_DONE %s (%.1f KB)" % (p, os.path.getsize(p) / 1024))
    return p


def drill(dest):
    """恢复演练：解最新快照→临时沙箱→台账一致性 selftest（账读得出、卡数合理、
    events 尾行解析、fuse 水位可读）。返回 rc；打印逐项。"""
    lp = latest(dest)
    if not lp:
        print("CC_BACKUP_DRILL FAIL 无快照可还原 dest=%s" % dest)
        return 1
    sb = tempfile.mkdtemp(prefix="cc-backup-drill-")
    try:
        with tarfile.open(lp, "r:gz") as tf:
            tf.extractall(sb)
        home = os.path.join(sb, "cc-team")
        checks = []
        gp = os.path.join(home, "state", "graph.json")
        g = json.load(open(gp, encoding="utf-8")) if os.path.isfile(gp) else {}
        n = len(g.get("cards") or {})
        checks.append(("graph.json 可解析且卡数>150（快照=真账非空壳）", n > 150, "cards=%d" % n))
        ep = os.path.join(home, "state", "events.log")
        tail = ""
        if os.path.isfile(ep):
            with open(ep, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            tail = lines[-1] if lines else ""
        checks.append(("events.log 尾行可分 5 段", len(tail.split("|")) >= 5, tail[:60]))
        fp = os.path.join(home, "state", "fuse.json")
        ok_f = True
        try:
            json.load(open(fp, encoding="utf-8")) if os.path.isfile(fp) else None
        except ValueError:
            ok_f = False
        checks.append(("fuse.json 水位可读", ok_f, ""))
        alerts_p = os.path.join(home, "state", "alerts.json")
        ok_a = True
        try:
            json.load(open(alerts_p, encoding="utf-8")) if os.path.isfile(alerts_p) else None
        except ValueError:
            ok_a = False
        checks.append(("alerts.json 红灯账可读", ok_a, ""))
        docs_ok = os.path.isdir(os.path.join(sb, "docs"))
        checks.append(("docs/ 在册（快照含文档面）", docs_ok, ""))
        bad = [c for c in checks if not c[1]]
        for name, okv, note in checks:
            print("%s %s%s" % ("PASS" if okv else "FAIL", name, ("｜" + note) if note else ""))
        if bad:
            print("CC_BACKUP_DRILL FAIL %d 项红" % len(bad))
            return 1
        print("CC_BACKUP_DRILL PASS src=%s" % os.path.basename(lp))
        return 0
    finally:
        shutil.rmtree(sb, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(prog="cc_backup.py")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--backup", action="store_true", help="立即打一份快照+滚动清理")
    g.add_argument("--ensure", action="store_true", help="环入口：超 26h 无快照才补（幂等）")
    g.add_argument("--drill", action="store_true", help="恢复演练：解最新快照进沙箱跑台账 selftest")
    g.add_argument("--list", action="store_true", help="列出现存快照")
    ap.add_argument("--dest", default=DEFAULT_DEST)
    a = ap.parse_args()
    if a.backup:
        p = snapshot(a.dest, tag="manual")
        print("CC_BACKUP_DONE %s (%.1f KB)" % (p, os.path.getsize(p) / 1024))
        return 0
    if a.ensure:
        ensure(a.dest)
        return 0
    if a.drill:
        return drill(a.dest)
    lp = latest(a.dest)
    try:
        fs = sorted(os.listdir(a.dest))
    except OSError:
        fs = []
    for f in fs:
        fp = os.path.join(a.dest, f)
        print("%s %.1f KB %s" % (f, os.path.getsize(fp) / 1024,
                                 datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%F %T")))
    print("latest=%s dest=%s" % (os.path.basename(lp) if lp else "-", a.dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
