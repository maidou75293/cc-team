#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 本文件=BUG-ALARM-AUTOACK-MASK-1 红绿锁（聚合账静默销账掩蔽持续故障）。
# 病根实锤（2026-11-04 夜）：叫醒通道整夜坏（FUSE-S3-PM-WAKE-FAIL cnt=187 在账），S5/S6 依赖的报警
#   条目却反复走 开放→超tick未续报→autoack→open 清零 的循环=账面「看起来没问题」而故障持续 3h；
#   变体键（GUARD-WATCH base 与「｜心跳已恢复待人工」互嵌）各记各账，族内轮流水=销账侧打地鼠。
# 判据（卡面原文三条）：
#   ①同题变体族**当日**反复到期/autoack 必升级点名（钉住=open 账不归零，S6/报警面看得见），
#     不许 autoack 后归零回到「从未发生」；
#   ②autoack 必留 prev_ack/次数审计痕，报警面展示「一夜自动销账 N 次」；
#   ③本锁=撤修复必红的差分对（伪造问题消失=锁绿不可得；空检查比没有检查更糟）。
# 附带面半边：S6 续报口径（ack 后又续报 last>ack=仍开放，复响/钉住键带旧 ack 也必须升级）。
# 跑法: python -I 本文件绝对路径   （exit 0 = 全绿）
# 沙箱: 独立临时目录 + 逐案重置 ccards.config.STATE，零碰真实账（state/alerts*.json 不落仓）。
import contextlib, io, json, os, shutil, sys, tempfile, types
from datetime import datetime, timedelta

try:  # -I 隔离模式忽略 PYTHONIOENCODING，Windows 控制台默认 cp936 会把中文判据行打碎——进程内钉 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

sys.dont_write_bytecode = True
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
sys.path.insert(0, SRC)
SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "cc-alarm-autoack-mask")
os.environ["CC_TEAM_HOME"] = SANDBOX_ROOT
import ccards.config as CFG                                          # noqa: E402

ALERTS_PY = os.path.join(SRC, "ccards", "alerts.py")
RECYCLE_PY = os.path.join(SRC, "ops", "recycle.py")
FUSE_PY = os.path.join(SRC, "ops", "fuse.py")
BASE_ALERTS = open(ALERTS_PY, encoding="utf-8").read()
BASE_RECYCLE = open(RECYCLE_PY, encoding="utf-8").read()
BASE_FUSE = open(FUSE_PY, encoding="utf-8").read()
fails = []

STALE = "超2tick未续报"           # recycle 到期销账的 reason 原文（判据认「未续报」字样）
MIGRATE = "卡状态迁移 OPEN"        # 生命周期正当收口形态（不计反复、不钉住）
CTX = types.SimpleNamespace(alerts=BASE_ALERTS, recycle=BASE_RECYCLE, fuse=BASE_FUSE, tag="base")


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, "||", detail)


def load(src, path, tag, package=""):
    """把（可变异后的）源码 exec 成独立模块对象；相对 import 由 __package__ 钉住。"""
    mod = types.ModuleType("autoack_mask_" + tag)
    mod.__package__ = package
    mod.__file__ = path
    exec(compile(src, path + " (build:" + tag + ")", "exec"), mod.__dict__)
    return mod


def modules():
    """本轮被测三件（alerts 副本 / recycle 副本 / fuse 副本），同源=CTX 里的源码（可变异）。"""
    return (load(CTX.alerts, ALERTS_PY, CTX.tag + "-al", package="ccards"),
            load(CTX.recycle, RECYCLE_PY, CTX.tag + "-rec"),
            load(CTX.fuse, FUSE_PY, CTX.tag + "-fz"))


def sandbox(tag):
    p = os.path.join(SANDBOX_ROOT, "sb-" + tag)
    shutil.rmtree(p, ignore_errors=True)
    CFG.STATE = os.path.join(p, "state")
    os.environ.pop("ALERT_DEAGE_DAYS", None)
    os.environ.pop("ALERT_AUTOACK_PIN", None)
    return CFG.STATE


def stamp(minutes=0, days=0):
    """账内 now() 同形时戳（+0800 无冒号偏移）。正=未来、负=过去（相对此刻）。"""
    return (datetime.now().astimezone() + timedelta(minutes=minutes, days=days)
            ).strftime("%Y-%m-%dT%H:%M:%S%z")


def seed(alerts):
    os.makedirs(CFG.STATE, exist_ok=True)
    with open(os.path.join(CFG.STATE, "alerts.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": stamp(), "alerts": alerts}, f, ensure_ascii=False)


def read_book():
    try:
        return json.load(open(os.path.join(CFG.STATE, "alerts.json"), encoding="utf-8")).get("alerts", {})
    except (OSError, ValueError):
        return {}


def patch(key, **kw):
    """定点改账（确定性推进，不吃秒级颗粒度——同 alarm-deage 锁的「CHAIN 三段」纪律）。"""
    b = read_book()
    e = b.get(key)
    if isinstance(e, dict):
        e.update(kw)
        seed(b)


def reopen(key, gap=20):
    """复响=回开放（确定性复播）：ack 拨到 gap+20 分钟前、last 拨到 gap 分钟前。
    两笔都留在过去，好处有二：①钉住/复响判据（last>ack）成立；②人工 ack（此刻）落笔后必 > last，
    「处置即闭合」可达——若把 last 造成未来时刻，任何 ack 都关不上这道灯（假阴性夹具）。"""
    patch(key, ack=stamp(-(gap + 20)), last=stamp(-gap))


def ent(key, first=None, last=None, count=1, ack=None, **kw):
    d = {"msg": key, "first": first or stamp(-60), "last": last or stamp(-30),
         "count": count, "ack": ack}
    d.update(kw)
    return d


def capture(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


def with_alerts(mod, fn, *a, **kw):
    """把 ccards.alerts 临时换成被测副本（消费口=cmd_ack 的红绿差分用），跑完原样还原。"""
    import ccards
    slot = "ccards.alerts"
    old_mod, old_attr = sys.modules.get(slot), getattr(ccards, "alerts", None)
    sys.modules[slot] = mod
    setattr(ccards, "alerts", mod)
    try:
        return capture(fn, *a, **kw)
    finally:
        if old_mod is None:
            sys.modules.pop(slot, None)
        else:
            sys.modules[slot] = old_mod
        if old_attr is not None:
            setattr(ccards, "alerts", old_attr)


class Args:  # cmd_ack 入参桩（只吃 list_open/key/who 三件）
    def __init__(self, list_open=False, key=None, who=None):
        self.list_open, self.key, self.who = list_open, key, who


# ---------------- 正向断言（全绿=修复成立） ----------------

def p1_first_expiry_still_closes(al, rec, fz):
    """腐灭闸不破：当日**第一次**到期仍正常 autoack 落 ack（僵尸警该清还得清），不钉住。
    「不许过度钉住」的反例半边——阈=2 的意义就在这（recycle_guard §9.2 同源契约）。"""
    k = "IDLE-WARN 沙箱首次到期"
    sandbox("p1")
    seed({k: ent(k)})
    r = al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    e = al.entries()[k]
    return (r is True and e.get("ack") and e.get("ack_reason") == STALE
            and not e.get("pinned") and e.get("stale_ack_count") == 1
            and e.get("autoack_count") == 1 and e.get("autoack_day_count") == 1
            and not al.still_open(e) and al.open_entries() == [])


def p2_repeat_expiry_pins(al, rec, fz):
    """①同键当日反复到期必钉住：第二次到期**不写 ack**、灯留在 open 账里、msg 挂「一夜自动销账1次」；
    ②机器上一次那笔 ack 一并收回转 prev_*（时刻/人/理由可还原）——留着旧 ack 值，按 ack 判闭的
    报警面（看板/面板）照旧显示「已确认」=掩蔽只换了个地方，正是不许回到「从未发生」的那半边。"""
    k = "MERGE-TRAIN 沙箱反复到期"
    sandbox("p2")
    seed({k: ent(k)})
    if not al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE):
        return False                                 # 首次=正常收口
    reopen(k)
    ack_pre = al.entries()[k]["ack"]
    r = al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    e = al.entries()[k]
    return (r is False and e.get("pinned")                        # 第二次=不销账改点名
            and not e.get("ack") and not e.get("acked_by")        # 机器那次销账被收回=面板重新亮灯
            and e.get("ack_reason") is None
            and e.get("prev_ack") == ack_pre                       # 史进 prev_*：时刻原样可还原
            and e.get("prev_ack_reason") == STALE
            and e.get("pin_acks_day") == 1 and e.get("pin_acks") == 2
            and "一夜自动销账1次" in e.get("msg", "")
            and al.still_open(e) and len(al.open_entries()) == 1
            and e.get("stale_ack_count") == 1)                    # 钉住这次不落销账数


def p3_variant_family_pins(al, rec, fz):
    """附带面·销账侧打地鼠：变体族并算——base 键当日销账 1 次后，「｜…」变体键的**首次**到期
    即属同题当日反复=必钉住（旧形每键各销一次、族内轮流水=账面永远干净=掩蔽）。"""
    base = "GUARD-WATCH full 心跳超阈→保障 DEGRADED（人工 --reset 复原）"
    var = base + "｜心跳已恢复待人工"
    sandbox("p3")
    seed({base: ent(base)})
    if not al.autoack(base, "auto@recycle-sweep: " + STALE, reason=STALE):
        return False
    book = read_book()
    book[var] = ent(var)
    seed(book)
    r = al.autoack(var, "auto@recycle-sweep: " + STALE, reason=STALE)
    e = al.entries()[var]
    return (r is False and e.get("pinned") and "一夜自动销账1次" in e.get("msg", "")
            and al.still_open(e) and len(al.open_entries()) == 1
            and al._stem(base) == al._stem(var))


def p4_audit_trail(al, rec, fz):
    """②审计痕：每次真销账落计数与 prev_ack 链；「卡状态迁移」形态不计反复（生命周期正当收口），
    其后的到期才是第二次。"""
    k = "IDLE-WARN 沙箱链痕"
    sandbox("p4")
    seed({k: ent(k)})
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    reopen(k)
    ack_pre = al.entries()[k]["ack"]
    r2 = al.autoack(k, "auto@recycle: " + MIGRATE, reason=MIGRATE)   # 迁销=第二次机器销账，非到期
    e = al.entries()[k]
    ack2 = e.get("ack")
    ok4 = (r2 is True and e.get("prev_ack") == ack_pre and ack2 > ack_pre and not e.get("pinned")
           and e.get("autoack_count") == 2 and e.get("autoack_day_count") == 2
           and e.get("stale_ack_count") == 1 and e.get("stale_ack_day_count") == 1
           and e.get("ack_reason") == MIGRATE)
    reopen(k, 30)
    ack_pre3 = al.entries()[k]["ack"]        # 复播夹具把 ack 拨回过去（=迁销那次处置的时刻，钉住要收回的就是它）
    r3 = al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)  # 当日第二次到期→钉住
    e3 = al.entries()[k]
    return (ok4 and r3 is False and e3.get("pinned")
            and e3.get("prev_ack") == ack_pre3                      # prev_ack 链=最近那次机器处置，覆写不丢史
            and e3.get("prev_ack_reason") == MIGRATE
            and not e3.get("ack")                                   # 钉住=收回机器那次销账，灯回开放
            and al.autoack_stats(k)[4] is not None
            and al.autoack_stats(k)[0] == 2 and al.autoack_stats(k)[2] == 1)


def p5_human_ack_unpins(al, rec, fz):
    """①解钉唯一通道=人工 ack：钉住键落 ack、钉痕转 pin_cleared、展示标退场、计数痕不抹（可还原）。"""
    k = "FUSE-S3 沙箱钉住待处置"
    sandbox("p5")
    seed({k: ent(k)})
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    reopen(k)
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    if not al.entries()[k].get("pinned"):
        return False
    r = al.ack(k, "业主")
    e = al.entries()[k]
    return (isinstance(r, list) and not e.get("pinned") and e.get("pin_cleared")
            and "一夜自动销账" not in e.get("msg", "")
            and e.get("autoack_count") == 1 and e.get("stale_ack_count") == 1
            and not al.still_open(e))


def p6_pinned_key_machine_immutable(al, rec, fz):
    """钉住幂等：已钉住的键再到期，机器动不了——不写 ack、不重复计数、整条账目一字不动。"""
    k = "SYS-FLOWSTOP 沙箱钉住后再生效"
    sandbox("p6")
    seed({k: ent(k)})
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    reopen(k)
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    if not al.is_pinned(k):
        return False
    reopen(k, 30)                                     # 复播在前，快照在后（快照必须吃进这次改账）
    before = json.dumps(al.entries()[k], sort_keys=True)
    r = al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    return (r is False and json.dumps(al.entries()[k], sort_keys=True) == before
            and al.still_open(al.entries()[k]) and al.is_pinned(k))


def p7_would_pin_readonly(al, rec, fz):
    """dry 口径与 apply 一致：探针判会钉住=True 且零写盘（dry 轮不许改账；旧「会销」预告=掩蔽预演）。
    迁销形态恒 False（生命周期收口不该被报成钉住）。"""
    k = "ALARM 沙箱dry探针"
    sandbox("p7")
    seed({k: ent(k)})
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    reopen(k)
    p = os.path.join(CFG.STATE, "alerts.json")
    before = open(p, encoding="utf-8").read()
    w1, w2 = al.would_pin(k, STALE), al.would_pin(k, MIGRATE)
    return (w1 is True and w2 is False and open(p, encoding="utf-8").read() == before
            and not al.is_pinned(k))


def p8_recycle_sweep_names_not_counts(al, rec, fz):
    """结算侧对表：第 1 轮腐灭正常销（计入 n_stale）；复响后第 2 轮打 PINNED 点名行且**不计腐灭数**
    （钉住≠销警，虚增计数=账面自欺）；第 3 轮继续点名、账不变；dry 轮点名不落笔。"""
    if not rec._AL:
        return False
    k = "GUARD-WATCH sweep 沙箱腐灭转钉"
    sandbox("p8")
    seed({k: ent(k, first=stamp(-90), last=stamp(-30))})
    b1, a1 = capture_sweep(rec, 1.0, True)
    if not (a1[1] == 1 and "AUTOACK" in b1 and not al.entries()[k].get("pinned")):
        return False
    patch(k, ack=stamp(-10), last=stamp(-8))      # 复响且已陈旧=第二次到期
    b2, a2 = capture_sweep(rec, 1.0, True)
    e = al.entries()[k]
    if not (a2[1] == 0 and "PINNED" in b2 and e.get("pinned") and al.still_open(e)):
        return False
    patch(k, last=stamp(-6))
    b3, a3 = capture_sweep(rec, 1.0, True)
    snap = json.dumps(al.entries(), sort_keys=True)
    b4, a4 = capture_sweep(rec, 1.0, False)
    return (a3[1] == 0 and "PINNED" in b3 and "PINNED" in b4 and "(dry)" in b4
            and json.dumps(al.entries(), sort_keys=True) == snap)


def capture_sweep(rec, tick, apply_mode):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = rec.autoack_sweep(tick, apply_mode)
    return buf.getvalue(), got


def p9_s6_reopen_counts_open(al, rec, fz):
    """附带面·S6 续报口径：开放判据=alerts.still_open 同式——复响（last>ack）与被钉住键带着旧 ack
    也必须进升级通道（旧口径「ack 非空=不升级」=叫不醒的另一半）。"""
    f = fz.s6_open
    return (f({"ack": None}) is True
            and f({"ack": "2026-11-04T01:00:00+0800", "last": "2026-11-04T00:30:00+0800"}) is False
            and f({"ack": "2026-11-04T01:00:00+0800", "last": "2026-11-04T02:00:00+0800"}) is True
            and f({"ack": "2026-11-04T01:00:00+0800"}) is False   # 无 last=未续报（fuse-check 夹具形）
            and f({"pinned": "2026-11-04T02:00:00+0800", "ack": "2026-11-04T01:00:00+0800",
                   "last": "2026-11-04T03:00:00+0800"}) is True
            and f("非账本条目") is False)


def p10_board_shows_nightly_total(al, rec, fz):
    """②报警面：ack --list 并列展示「今日机器自动销账 N 笔」（open=0 也要看得见夜里销了多少笔），
    钉住项带「一夜自动销账N次」点名标。"""
    from ccards import commands
    k = "IDLE-WARN 沙箱展示面"
    sandbox("p10")
    seed({k: ent(k)})
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    reopen(k)
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    out = with_alerts(al, commands.cmd_ack, Args(list_open=True))
    if not ("一夜自动销账1次" in out and "今日机器自动销账 1 笔" in out):
        return False
    al.ack(k, "业主")          # 掩蔽主案形：账面干净（无开放项）时总账仍要点名
    out2 = with_alerts(al, commands.cmd_ack, Args(list_open=True))
    return "无开放项" in out2 and "今日机器自动销账 1 笔" in out2


def p11_reopen_keeps_marker(al, rec, fz):
    """钉住中的题再复报=展示标跟到最新原文（不许回到「从未发生」），record 不抹钉。"""
    k = "FUSE-S4 沙箱钉住续报"
    sandbox("p11")
    seed({k: ent(k, msg=k)})
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    reopen(k)
    al.autoack(k, "auto@recycle-sweep: " + STALE, reason=STALE)
    al.record(k + " 龄35min")          # 同题复播（剥时距后键=原键，并进钉住那条）
    e = al.entries()[k]
    return (e.get("pinned") and "一夜自动销账1次" in e.get("msg", "")
            and "龄35min" in e.get("msg", "") and e.get("count") == 2 and al.still_open(e))


def p12_threshold_configurable(al, rec, fz):
    """钉住阈可配（ALERT_AUTOACK_PIN，下限 2）：=3 时当日第二次到期仍正常收口、第三次才钉；
    1/坏值回退 2（阈<2 会杀掉腐灭闸的正当首次收口=recycle_guard §9.2 契约破坏）。"""
    k = "IDLE-WARN 沙箱阈可配"
    sandbox("p12")
    os.environ["ALERT_AUTOACK_PIN"] = "3"
    seed({k: ent(k)})
    c1 = al.autoack(k, "auto@recycle: " + STALE, reason=STALE)
    reopen(k)
    c2 = al.autoack(k, "auto@recycle: " + STALE, reason=STALE)
    reopen(k, 20)
    c3 = al.autoack(k, "auto@recycle: " + STALE, reason=STALE)
    e = al.entries()[k]
    pinned_third = (c1 is True and c2 is True and c3 is False and e.get("pinned")
                    and "一夜自动销账2次" in e.get("msg", ""))
    os.environ.pop("ALERT_AUTOACK_PIN", None)
    os.environ["ALERT_AUTOACK_PIN"] = "1"
    floor_ok = al.pin_threshold() == 2
    os.environ["ALERT_AUTOACK_PIN"] = "abc"
    bad_ok = al.pin_threshold() == 2
    os.environ.pop("ALERT_AUTOACK_PIN", None)
    return pinned_third and floor_ok and bad_ok


POS = [("P1 首次到期腐灭闸不破（不过度钉住）", p1_first_expiry_still_closes),
       ("P2 同键当日反复到期必钉住+不归零", p2_repeat_expiry_pins),
       ("P3 变体族并算（销账侧打地鼠治住）", p3_variant_family_pins),
       ("P4 prev_ack/次数链+迁销不计反复", p4_audit_trail),
       ("P5 人工 ack=唯一解钉通道", p5_human_ack_unpins),
       ("P6 钉住幂等（机器动不了钉住的键）", p6_pinned_key_machine_immutable),
       ("P7 would_pin 只读探针=dry 不写盘", p7_would_pin_readonly),
       ("P8 recycle 结算侧 PINNED 点名不计腐灭", p8_recycle_sweep_names_not_counts),
       ("P9 S6 续报口径（复响/钉住仍开放）", p9_s6_reopen_counts_open),
       ("P10 报警面展示一夜自动销账总账", p10_board_shows_nightly_total),
       ("P11 钉住态复报保留展示标", p11_reopen_keeps_marker),
       ("P12 钉住阈可配+下限 2 保护", p12_threshold_configurable)]

# ---------------- 变异自证（撤修复必红，每路各打一段） ----------------
# (标签, 文件, 锚, 替换, 对应正向段)
MUTS = [
    ("摘钉住分支（反复到期照旧静默销账=病根本体）", "alerts",
     "        if stale and stale_day + 1 >= pin_threshold():",
     "        if False and stale_day + 1 >= pin_threshold():", p2_repeat_expiry_pins),
    ("变体族退化字面同键（打地鼠复活）", "alerts",
     '    return _key(str(k).split("｜")[0])',
     "    return _key(str(k))", p3_variant_family_pins),
    ("摘到期销账计数（无历史=永不认定反复）", "alerts",
     '        if stale:\n            e["stale_ack_count"] = _cnt(e.get("stale_ack_count")) + 1',
     '        if False:\n            e["stale_ack_count"] = _cnt(e.get("stale_ack_count")) + 1',
     p2_repeat_expiry_pins),
    ("摘 prev_ack 链（处置史被覆写丢）", "alerts",
     '        if ack0:\n            e["prev_ack"] = ack0',
     '        if ack0:\n            e["prev_ack"] = ""', p4_audit_trail),
    ("人工 ack 不解钉（钉住永存=假升级）", "alerts",
     '        if e.get("pinned"):\n            # 人工处置=唯一解钉通道',
     '        if False:\n            # 人工处置=唯一解钉通道', p5_human_ack_unpins),
    ("摘钉住守卫（钉住键被再销=归零回从未发生）", "alerts",
     "            if stale:\n                return False  # 钉住中的到期键机器动不了",
     "            if False:\n                return False  # 钉住中的到期键机器动不了",
     p6_pinned_key_machine_immutable),
    ("摘展示标（一夜自动销账 N 次不可见）", "alerts",
     '            e["msg"] = _mark(e.get("msg") or key, n)',
     '            e["msg"] = str(e.get("msg") or key)', p2_repeat_expiry_pins),
    ("摘当日总账汇总（报警面看不到销账笔数）", "alerts",
     '        return sum(_cnt(e.get("autoack_day_count")) for e in book.values()\n'
     '                   if isinstance(e, dict) and e.get("autoack_day") == today)',
     "        return 0", p10_board_shows_nightly_total),
    ("摘 recycle 钉住识别（点名虚计入腐灭数）", "recycle",
     "            if not hit:\n                if stale_form and _AL.is_pinned(k):",
     "            if False:\n                if stale_form and _AL.is_pinned(k):",
     p8_recycle_sweep_names_not_counts),
    ("S6 口径退回 ack 非空即闭（复响/钉住叫不醒）", "fuse",
     '    return (not ack) or ((v.get("last") or "") > str(ack))',
     "    return not ack", p9_s6_reopen_counts_open),
    ("钉住不收回机器 ack（面板按 ack 判闭仍显示已确认=掩蔽换地方）", "alerts",
     '            e["ack"] = None\n            e["acked_by"] = None',
     '            e["acked_by"] = None', p2_repeat_expiry_pins),
]

FIELD = {"alerts": "alerts", "recycle": "recycle", "fuse": "fuse"}


def run_positive(name, fn, label=""):
    al, rec, fz = modules()
    try:
        r = fn(al, rec, fz)
    except Exception as exc:
        bad((label + " " if label else "") + name, "异常 %r" % (exc,))
        return False
    (ok if r else bad)((label + " " if label else "") + name)
    return bool(r)


def main():
    print("== alarm-autoack-mask BUG-ALARM-AUTOACK-MASK-1 沙箱=%s ==" % SANDBOX_ROOT)
    green = sum(1 for name, fn in POS if run_positive(name, fn))

    for tag, kind, anchor, repl, fn in MUTS:
        base = {"alerts": BASE_ALERTS, "recycle": BASE_RECYCLE, "fuse": BASE_FUSE}[kind]
        n = base.count(anchor)
        if n != 1:
            bad("变异锚 " + tag, "锚出现 %d 次≠1（半截变异=假锁）" % n)
            continue
        mut = base.replace(anchor, repl)
        if mut == base:
            bad("变异 " + tag, "替换未改变源码")
            continue
        CTX.alerts = mut if kind == "alerts" else BASE_ALERTS
        CTX.recycle = mut if kind == "recycle" else BASE_RECYCLE
        CTX.fuse = mut if kind == "fuse" else BASE_FUSE
        CTX.tag = "mut"
        try:
            al, rec, fz = modules()
            try:
                r = fn(al, rec, fz)
            except Exception:
                r = False   # 变异体崩判据也算实红
        finally:
            CTX.alerts, CTX.recycle, CTX.fuse, CTX.tag = BASE_ALERTS, BASE_RECYCLE, BASE_FUSE, "base"
        if r:
            bad("变异 " + tag, "摘除修复后竟全绿=检查是空的")
        else:
            ok("变异 " + tag + " → 实红(锁接得住)")

    # 摘除变异复跑回绿：上面的红来自变异本身，不是夹具噪声
    regen = sum(1 for name, fn in POS if run_positive(name, fn, "复绿"))
    shutil.rmtree(SANDBOX_ROOT, ignore_errors=True)
    total = len(POS) * 2 + len(MUTS)
    if fails or green != len(POS) or regen != len(POS):
        print("ALARM-AUTOACK-MASK-FAIL fails=%d" % (len(fails) + (len(POS) - green) + (len(POS) - regen)))
        sys.exit(1)
    print("ALARM-AUTOACK-MASK-PASS asserts=%d fails=0" % total)
    sys.exit(0)


if __name__ == "__main__":
    main()
