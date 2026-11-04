#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# alarm-deage-check.py — MECH-ALARM-DEAGE-1 红绿锁：报警键稳/组销/去龄三件的行为断言。
# 判据（活卡面原文）：同题复发必并一键 / ack 一击全销 / 摘键稳段或误销未 ack 段=锁红。
# 锁红段展开=四路变异体全必须红、摘除变异后复跑回绿——每段修复都要「红得起来」
# （空检查比没有检查更糟；memory: verify-guards-can-fail / 半截变异即红）。
# 跑法: python3 -I cc-team/tests/alarm-deage-check.py   （exit 0 = 全绿）
# 沙箱: 独立临时目录 + 逐案重置 ccards.config.STATE，零碰真实账（state/alerts*.json 不落仓）。
import json, os, shutil, sys, tempfile, types
from datetime import datetime, timedelta

try:  # -I 隔离模式忽略 PYTHONIOENCODING，Windows 控制台默认 cp936 会把中文判据行打碎——进程内钉 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

sys.dont_write_bytecode = True   # 同 ccards/__init__ 纪律（REQ-OSSPATH-1 配套）
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # cc-team 根
sys.path.insert(0, SRC)
SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "cc-alarm-deage")
os.environ["CC_TEAM_HOME"] = SANDBOX_ROOT          # 兜底：ccards.config 的 TEAM 也钉沙箱
import ccards.config as CFG                         # noqa: E402

ALERTS_PY = os.path.join(SRC, "ccards", "alerts.py")
BASE_SRC = open(ALERTS_PY, encoding="utf-8").read()
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, detail)


def load(src, tag):
    """把（可变异后的）alerts 源码 exec 成独立模块对象；相对 import 由 __package__ 钉到 ccards。"""
    mod = types.ModuleType("alerts_under_test_" + tag)
    mod.__package__ = "ccards"
    mod.__file__ = ALERTS_PY
    exec(compile(src, ALERTS_PY + " (build:" + tag + ")", "exec"), mod.__dict__)
    return mod


def sandbox(tag):
    p = os.path.join(SANDBOX_ROOT, "sb-" + tag)
    shutil.rmtree(p, ignore_errors=True)
    CFG.STATE = os.path.join(p, "state")
    os.environ.pop("ALERT_DEAGE_DAYS", None)
    return CFG.STATE


def stamp(days=0, minutes=0):
    """账内 now() 同形时戳（+0800 无冒号偏移——正是 _ts 要兼容的那一形）。"""
    return (datetime.now().astimezone() - timedelta(days=days, minutes=minutes)
            ).strftime("%Y-%m-%dT%H:%M:%S%z")


def seed(alerts):
    os.makedirs(CFG.STATE, exist_ok=True)
    with open(os.path.join(CFG.STATE, "alerts.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": stamp(), "alerts": alerts}, f, ensure_ascii=False)


def ent(first, last, count, ack=None, **kw):
    d = {"msg": "seed", "first": first, "last": last, "count": count, "ack": ack}
    d.update(kw)
    return d


# ---------------- 正向断言（全绿=修复成立） ----------------

def s1_aggregate(m):
    """同题复发必并一键：龄Nmin 与 (+Nmin) 两形各复播两次→2 键；键内无时距；计数并进；最新原文进 msg 字段。"""
    sandbox("s1")
    m.record("待合·撞车(FUSE-S3) CARD-S1 龄30min merge-tree 冲突——红灯挂起")
    m.record("待合·撞车(FUSE-S3) CARD-S1 龄47min merge-tree 冲突——红灯挂起")
    m.record("NOT-ENERGIZED(FUSE-S4) 进程起时早于提交=ops/x.py(+9min)——修复≠生效")
    m.record("NOT-ENERGIZED(FUSE-S4) 进程起时早于提交=ops/x.py(+27min)——修复≠生效")
    e = m.entries()
    return (len(e) == 2
            and all(v["count"] == 2 for v in e.values())
            and not any(("龄" in k) or ("(+" in k) for k in e)
            and any("龄47min" in v["msg"] for v in e.values())
            and any("(+27min)" in v["msg"] for v in e.values()))


def s2_fold(m):
    """存量变体自动并账：手工种入时距旧键（模拟旧账），新拍复报→折成稳定键一键，count 累加、first 保最早。"""
    sandbox("s2")
    f0 = stamp(days=0, minutes=90)
    seed({"CARD-S2 龄30min 冲突": ent(f0, stamp(minutes=30), 5)})
    m.record("CARD-S2 龄31min 冲突")
    e = m.entries()
    k = "CARD-S2 冲突"
    return (len(e) == 1 and k in e and e[k]["count"] == 6
            and e[k]["first"] == f0 and e[k]["msg"] == "CARD-S2 龄31min 冲突")


def s3_group_ack(m):
    """ack 一击全销（按题整组）：同题 3 变体+异题 1 键——跨题子串仍拒（返 str 列表让人缩小）；
    同题子串一击把 3 变体全落 ack，异题绝不殃及。"""
    sandbox("s3")
    seed({"CARD-S3 龄30min 冲突": ent(stamp(minutes=60), stamp(minutes=30), 1),
          "CARD-S3 龄31min 冲突": ent(stamp(minutes=59), stamp(minutes=29), 1),
          "CARD-S3 龄32min 冲突": ent(stamp(minutes=58), stamp(minutes=28), 1),
          "OTHER-S3 龄30min 冲突": ent(stamp(minutes=60), stamp(minutes=30), 1)})
    r = m.ack("S3", "PM-lock")
    if not (len(r) == 4 and all(isinstance(x, str) for x in r)):
        return False
    r2 = m.ack("CARD-S3", "PM-lock")
    if not (len(r2) == 3 and all(isinstance(x, dict) for x in r2)):
        return False
    e = m.entries()
    closed = [k for k in e if k.startswith("CARD-S3")]
    return (len(closed) == 3 and all(not m.still_open(e[k]) for k in closed)
            and m.still_open(e["OTHER-S3 龄30min 冲突"]))


def s4_deage(m):
    """去龄主案（record 写口触发）：已 ack 超窗移走、未 ack 必留、窗内必留、ack 后复发必留；归档件可回查且字段原样。"""
    sandbox("s4")
    ack1 = stamp(days=8, minutes=-5)
    seed({"P1 已ack超龄": ent(stamp(days=10), stamp(days=8), 3, ack1),
          "P2 未ack超龄": ent(stamp(days=9), stamp(days=8), 2, None),
          "P3 ack窗内": ent(stamp(days=3), stamp(days=2), 1, stamp(days=2, minutes=-5)),
          "P4 ack后复发": ent(stamp(days=10), stamp(days=9), 4, stamp(days=10))})
    m.record("P5 新鲜触发去龄")
    e = m.entries()
    arch = m.archive_entries()
    return (set(e) == {"P2 未ack超龄", "P3 ack窗内", "P4 ack后复发", "P5 新鲜触发去龄"}
            and set(arch) == {"P1 已ack超龄"}
            and arch["P1 已ack超龄"]["count"] == 3
            and arch["P1 已ack超龄"]["ack"] == ack1
            and bool(arch["P1 已ack超龄"].get("archived_at"))
            and m.still_open(e["P4 ack后复发"]))


def s5_days_config(m):
    """N 天可配：默认 7 不动 2 天窗内闭案；ALERT_DEAGE_DAYS=1 则移走；env 坏值回退默认不炸。"""
    def old2():
        return {"P6 两日前闭案": ent(stamp(days=3), stamp(days=2), 1, stamp(days=2, minutes=-5))}
    sandbox("s5a"); seed(old2()); m.record("触发")
    kept_default = "P6 两日前闭案" in m.entries() and not m.archive_entries()
    sandbox("s5b"); seed(old2()); os.environ["ALERT_DEAGE_DAYS"] = "1"; m.record("触发")
    moved_fast = "P6 两日前闭案" in m.archive_entries()
    sandbox("s5c"); seed(old2()); os.environ["ALERT_DEAGE_DAYS"] = "abc"; m.record("触发")
    kept_badenv = "P6 两日前闭案" in m.entries() and not m.archive_entries()
    os.environ.pop("ALERT_DEAGE_DAYS", None)
    return kept_default and moved_fast and kept_badenv


def s6_stable(m):
    """键稳判据单点：今夜真实漂移键对全部并一；无时距普通键（含数字+单位实质词）不误剥。"""
    pairs = [
        ("待合·撞车(FUSE-S3) UX-BOARD-COLLAPSE-1 龄30min merge-tree 冲突——红灯挂起等 PM 人工解冲突，零force零改哈希",
         "待合·撞车(FUSE-S3) UX-BOARD-COLLAPSE-1 龄50min merge-tree 冲突——红灯挂起等 PM 人工解冲突，零force零改哈希"),
        ("NOT-ENERGIZED(FUSE-S4) cc-dispatch 进程起时早于命脉最后提交=ops/ctrld.py(+9min)——修复≠生效",
         "NOT-ENERGIZED(FUSE-S4) cc-dispatch 进程起时早于命脉最后提交=ops/ctrld.py(+27min)——修复≠生效"),
        ("FUSE-S1 派发心跳断流（dispatcher.heartbeat 龄=259s>3min）",
         "FUSE-S1 派发心跳断流（dispatcher.heartbeat 龄=512s>3min）"),
    ]
    keep = ["ALARM: events 有卡 DL-F2 但事实图无此卡",
            "backend v130 探测 MySQL-CDC 版本 阈值40min 保持",
            "分页契约 size 1–500 X-Total-Count"]
    return (all(m._key(a) == m._key(b) for a, b in pairs)
            and all(m._key(s) == " ".join(s.split())[:200] for s in keep)
            and m._key("NOT-ENERGIZED(FUSE-S4) cc-dispatch 进程起时早于命脉最后提交=ops/ctrld.py(+27min),ops/recycle.py(+27min)——修复≠生效")
                == "NOT-ENERGIZED(FUSE-S4) cc-dispatch 进程起时早于命脉最后提交=ops/ctrld.py,ops/recycle.py——修复≠生效")


def s7_autoack_hook(m):
    """autoack 写口挂点：机器停铃同拍带去龄——刚停的旧铃与既存超龄闭案一并进归档（停铃不抹痕字段照存）。"""
    sandbox("s7")
    seed({"P7 旧开放": ent(stamp(days=9), stamp(days=8), 2, None),
          "P8 旧闭案": ent(stamp(days=9), stamp(days=8), 1, stamp(days=8, minutes=-1))})
    r = m.autoack("P7 旧开放", reason="锁具驱动")
    e = m.entries()
    arch = m.archive_entries()
    return (r is True and set(e) == set()
            and set(arch) == {"P7 旧开放", "P8 旧闭案"}
            and arch["P7 旧开放"].get("ack_reason") == "锁具驱动"
            and bool(arch["P7 旧开放"].get("archived_at")))


def s8_deage_now(m):
    """deage_now 存量清账：点名移超龄闭案；账不在=直接 [] 且不造文件（现读可回查半边）。"""
    sandbox("s8a")
    seed({"P9 旧闭案": ent(stamp(days=10), stamp(days=9), 1, stamp(days=9)),
          "P10 在办": ent(stamp(days=1), stamp(days=1), 1, None)})
    moved = m.deage_now()
    one = (moved == ["P9 旧闭案"] and "P9 旧闭案" not in m.entries()
           and "P9 旧闭案" in m.archive_entries())
    sandbox("s8b")
    none_ok = m.deage_now() == [] and not os.path.exists(os.path.join(CFG.STATE, "alerts.json"))
    return one and none_ok


def s9_rearchive_merge(m):
    """同题二次归档：last 较新者为体、count 取大、first 取早——归档账不覆盖丢史。"""
    sandbox("s9")
    l8 = stamp(days=8)
    seed({"P11 同题": ent(stamp(days=10), l8, 3, stamp(days=8, minutes=-1))})
    m.deage_now()
    f12 = stamp(days=12)
    seed({"P11 同题": ent(f12, l8, 5, stamp(days=8, minutes=-1))})
    m.deage_now()
    a = m.archive_entries()["P11 同题"]
    return a["count"] == 5 and a["first"] == f12


# ---------------- 变异自证（每路必须红） ----------------

MUT_STABLE = ('    return " ".join(_TIME_SEG_RE.sub("", str(msg)).split())[:200]',
              '    return " ".join(str(msg).split())[:200]')
MUT_UNACKED = ('    if not e.get("ack"):\n        return False\n'
               '    if still_open(e):\n        return False',
               '    if False:\n        return False\n'
               '    if False:\n        return False')
MUT_REOPEN = ('    if still_open(e):\n        return False',
              '    if False:\n        return False')
MUT_GROUP = ('    if len({_key(k) for k in hits}) > 1:',
             '    if len(hits) > 1:')
MUTS = [("摘键稳段", MUT_STABLE, s1_aggregate),
        ("误销未ack段(摘停铃闸整体)", MUT_UNACKED, s4_deage),
        ("误销复发段(摘ack后复响闸)", MUT_REOPEN, s4_deage),
        ("组销退化(多命中一律拒=旧病)", MUT_GROUP, s3_group_ack)]


def main():
    print("== alarm-deage-check MECH-ALARM-DEAGE-1 沙箱=%s ==" % SANDBOX_ROOT)
    real = load(BASE_SRC, "base")
    positives = [
        ("S1 同题复发必并一键", s1_aggregate),
        ("S2 存量变体自动并账", s2_fold),
        ("S3 ack 一击全销(同题整组)", s3_group_ack),
        ("S4 去龄:旧acked移走/未ack必留", s4_deage),
        ("S5 去龄窗 N 天可配(ALERT_DEAGE_DAYS)", s5_days_config),
        ("S6 键稳单判据+不误剥集", s6_stable),
        ("S7 autoack 挂点同拍去龄", s7_autoack_hook),
        ("S8 deage_now 存量清账", s8_deage_now),
        ("S9 二次归档并账", s9_rearchive_merge),
    ]
    for name, fn in positives:
        try:
            r = fn(real)
        except Exception as exc:
            bad(name, "异常 %r" % (exc,))
            continue
        (ok if r else bad)(name)

    for tag, (anchor, repl), fn in MUTS:
        n = BASE_SRC.count(anchor)
        if n != 1:
            bad("变异锚 %s" % tag, "锚出现 %d 次≠1（半截变异=假锁）" % n)
            continue
        mut = BASE_SRC.replace(anchor, repl)
        if mut == BASE_SRC:
            bad("变异 %s" % tag, "替换未改变源码")
            continue
        try:
            r = fn(load(mut, "mut-" + tag))
        except Exception:
            r = False  # 变异体异常崩判据也算实红
        if r:
            bad("变异 %s" % tag, "摘除修复后竟全绿=检查是空的")
        else:
            ok("变异 %s → 实红(锁接得住)" % tag)

    # 摘除变异复跑回绿：证明上面的红来自变异本身，不是夹具噪声
    regen = load(BASE_SRC, "regen")
    for name, fn in positives:
        try:
            r = fn(regen)
        except Exception as exc:
            bad("复绿 " + name, "异常 %r" % (exc,))
            continue
        (ok if r else bad)("复绿 " + name)

    if fails:
        print("ALARM-DEAGE-LOCK-FAIL fails=%d" % len(fails))
        sys.exit(1)
    print("ALARM-DEAGE-LOCK-PASS asserts=%d fails=0" % (len(positives) * 2 + len(MUTS)))
    sys.exit(0)


if __name__ == "__main__":
    main()
