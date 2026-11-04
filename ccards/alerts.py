# ccards.alerts — ALARM 聚合闭环（MECH-AUD1；MECH-ALARM-DEAGE-1 键稳/组销/去龄三件）。
# 病根①(MECH-AUD1)：ALARM 此前只进 stdout/cron 日志，同一个问题可以每 30 分钟正确报警一次、永远正确地被忽略。
# 病根②(2026-11-03 夜实锤)：时距串进键——「龄43min」「(+27min)」「龄=259s>3min」这类每拍漂移的字样拼在
#   消息里=_key 逐字取键，同题每拍变新键不聚合、ack 多命中不销（UX-BOARD 一题积 20 条红灯需 ack 20 遍；
#   NOT-ENERGIZED 一题 (+11min)/(+27min) 先后开放需 ack 两次；alerts.json 积到 167 键只增不减）。
# 病根③(2026-11-04 夜实锤，BUG-ALARM-AUTOACK-MASK-1)：机器销账把持续故障洗成「从未发生」——
#   叫醒通道整夜坏（FUSE-S3-PM-WAKE-FAIL cnt=187 在账），S5/S6 依赖的报警条目却反复走
#   开放→超tick未续报→autoack→open 清零 的循环，账面干净而故障持续 3h；变体键
#   （GUARD-WATCH base 与「｜心跳已恢复待人工」互嵌）各自只销一次、族内轮流水，销账侧复现打地鼠。
#   治法=见规则④（反复到期改钉住点名，机器不再有权把同题第二次到期归零）。
# 账本：state/alerts.json = {key: {msg, first, last, count, ack}}。key=问题身份（剥时距段，_key 现算）；
#   msg=最新原文（时距进字段，展示与取证不丢数）；count/first/last 照旧是字段。
# 规则：① record 检出即落账（稳定键 count++/last 刷新），账内同题变体存量键自动并账，病根旧账不靠人逐变体清；
#       ② ack <子串> 按题整组销——多命中若全属同一问题（剥时距后相同）一击全落 ack；跨问题仍返回键列表让人缩小；
#       ③ 去龄：已 ack、已停铃（非 still_open）、且 last 龄 > ALERT_DEAGE_DAYS 天（默认 7，env 可配，下限 1）
#         的条目整体移入 state/alerts-archive.json（archive_entries()/文件现读可回查）；
#         未 ack 键永不老；ack 后又复发的开放条目永不归档。零新定时器——去龄挂 record/autoack/ack
#         三个写口顺拍执行，存量清账另有 deage_now()（环内/手动），不给 OS 加闹钟；
#       ④ 反复到期不许静默销账(BUG-ALARM-AUTOACK-MASK-1)：**同题变体族**（_stem 并算）**当日**第二次及以上的
#         「未续报」到期，autoack 不写 ack 改**钉住**（pinned 字段+msg 挂「一夜自动销账N次」标）——
#         灯继续亮在 open 账里（S6 按龄照常唤/报警面可见），只有人工 ack 才解钉。
#         机器销账必留计数（autoack_count/stale_ack_count + 当日两笔），prev_ack 链不覆写丢史。
#         WAKE-STALL 同款语义=停自动动作+点名等处置，处置后自动恢复。
# 写失败绝不影响主命令（告警是旁证层，不是账本层）——但会打 WARN，不静默。
import os, re, sys
from datetime import datetime, timedelta
from .config import atomic_write_json, now, read_json


def _path():
    from .config import STATE
    return os.path.join(STATE, "alerts.json")


def _archive_path():
    from .config import STATE
    return os.path.join(STATE, "alerts-archive.json")


# 时距段=不许进键的字样：「龄30min」「龄=259s>3min」「lag 2 min」「(+27min)」。
# 只认「龄/lag 前缀」与「(+N单位) 括号」两形，普通带单位配置值（如「阈值40min」「1–500」）不动——
# 宁可漏剥（键照旧多一形态）不可滥剥（把问题身份里的实质数字吃掉=不同题假并）。
_TIME_SEG_RE = re.compile(
    r"(?:龄|lag)[=:]?\s*\d+(?:\.\d+)?\s*(?:secs|sec|min|ms|h|d|s)(?:\s*[<>]=?\s*\d+(?:\.\d+)?\s*(?:secs|sec|min|ms|h|d|s))?"
    r"|\(\s*\+\s*\d+(?:\.\d+)?\s*(?:secs|sec|min|ms|h|d|s)\s*\)"
)


def _stable(msg):
    """问题身份=剥去时距段并折叠空白后的文本（键只由问题身份构成，MECH-ALARM-DEAGE-1）。"""
    return " ".join(_TIME_SEG_RE.sub("", str(msg)).split())[:200]


def _key(msg):
    return _stable(msg)


# ==== 反复到期升级点名（BUG-ALARM-AUTOACK-MASK-1 规则④）====
PIN_ENV = "ALERT_AUTOACK_PIN"       # 钉住阈：同题变体族第 N 次「未续报」到期改钉住（默认 2）
PIN_MIN = 2                          # 下限 2——阈=1 会杀掉腐灭闸的正当首次收口（recycle_guard §9.2 契约）
_PIN_MARK = "｜一夜自动销账%d次·反复到期钉住点名"
_PIN_MARK_RE = re.compile(r"｜一夜自动销账\d+次·反复到期钉住点名")


def pin_threshold():
    """钉住阈：ALERT_AUTOACK_PIN 可配，默认 2，下限 2；坏值回退默认不炸写口。"""
    try:
        return max(PIN_MIN, int(os.environ.get(PIN_ENV, str(PIN_MIN))))
    except (TypeError, ValueError):
        return PIN_MIN


def _stem(k):
    """变体族归一键：剥「｜…」尾注后再过一遍稳定身份。
    GUARD-WATCH 的 base 键恒为「…｜心跳已恢复待人工」变体的前缀，两键各记各账=销账侧打地鼠
    （每键只销一次、族内轮流水=账面永远干净）；族内并算反复次数才治得住（病根③半边）。"""
    return _key(str(k).split("｜")[0])


def _is_stale(reason):
    """「未续报」形态的机器销账=到期销账（掩蔽病根的那一支）；「卡状态迁移」类是正当生命周期收口，不计反复。"""
    return "未续报" in str(reason or "")


def _cnt(v):
    """计数字段容错读：腐成非数回 0（保守不炸写口，同 _deage 判龄异常不动账的纪律）。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _day(ts=None):
    return (ts or now())[:10]


def _mark(msg, n):
    return _PIN_MARK_RE.sub("", str(msg)) + (_PIN_MARK % max(0, int(n)))


def _fam_stats(a, key):
    """同题变体族的机器销账历史（只读）：返回 (族累计到期销账, 族**当日**到期销账, 族当日全部机器销账)。
    反复判据只看当日（=卡面「一夜自动销账 N 次」的夜）：隔天复发是新 episode，仍从首次正常收口起步，
    免得腐灭闸（僵尸警正当清理）被跨周老账一票否决。"""
    st = _stem(key)
    today = _day()
    tot = stale_day = day_all = 0
    for x, e in a.items():
        if not isinstance(e, dict) or _stem(x) != st:
            continue
        tot += _cnt(e.get("stale_ack_count"))
        if e.get("stale_ack_day") == today:
            stale_day += _cnt(e.get("stale_ack_day_count"))
        if e.get("autoack_day") == today:
            day_all += _cnt(e.get("autoack_day_count"))
    return tot, stale_day, day_all


def is_pinned(key):
    """本键是否已钉住（钉住=机器不再有权销账，等人工 ack）。"""
    e = entries().get(key)
    return bool(isinstance(e, dict) and e.get("pinned"))


def would_pin(key, reason=None):
    """只读探针（dry 轮/展示层用）：此刻按 reason 销账是否会升级为钉住。不落账不写盘。
    已钉住的键=到期形态 True、生命周期收口形态 False（与 autoack 同式，dry 不骗人）。"""
    try:
        e = entries().get(key)
        if not isinstance(e, dict):
            return False
        if e.get("pinned"):
            return _is_stale(reason)   # 钉住+到期=机器动不了；钉住+生命周期收口=可解钉（与 autoack 同式）
        if e.get("ack") and (e.get("last") or "") <= e.get("ack"):
            return False
        if not _is_stale(reason):
            return False
        _, stale_day, _ = _fam_stats(entries(), key)
        return stale_day + 1 >= pin_threshold()
    except OSError:
        return False


def autoack_stats(key):
    """单键审计痕读数（展示/对账用）：(累计机器销账, 今日机器销账, 累计到期销账, 今日到期销账, 钉住时刻或 None)。"""
    e = entries().get(key)
    if not isinstance(e, dict):
        return 0, 0, 0, 0, None
    return (_cnt(e.get("autoack_count")), _cnt(e.get("autoack_day_count")),
            _cnt(e.get("stale_ack_count")), _cnt(e.get("stale_ack_day_count")),
            e.get("pinned"))


def autoack_today_total(a=None):
    """全账今日机器自动销账笔数（报警面「一夜自动销账 N 次」总账，含已闭案/含变体族）。
    账面 open=0 也要能看见夜里被机器销了多少笔——这就是掩蔽的反证位。"""
    try:
        book = entries() if a is None else a
        today = _day()
        return sum(_cnt(e.get("autoack_day_count")) for e in book.values()
                   if isinstance(e, dict) and e.get("autoack_day") == today)
    except OSError:
        return 0


def record(msg):
    try:
        d = read_json(_path()) or {"alerts": {}}
        a = d.setdefault("alerts", {})
        k = _key(msg)
        raw = " ".join(str(msg).split())[:200]
        # 变体并账（存量半边）：账里「写法不同、剥时距后同题」的旧键（龄Nmin/(+Nmin) 时代残留）
        # 折进稳定键——并 count、取最早 first、ack 痕取较新一次，旧键退场不丢处置史。
        e = a.get(k)
        for vk in [x for x in list(a) if x != k and _key(x) == k]:
            o = a.pop(vk)
            if not isinstance(o, dict):
                continue
            if e is None:
                e = o
                continue
            e["count"] = int(e.get("count", 0) or 0) + int(o.get("count", 0) or 0)
            if (o.get("first") or "9999") < (e.get("first") or "9999"):
                e["first"] = o.get("first")
            if (o.get("ack") or "") > (e.get("ack") or ""):
                e["ack"] = o.get("ack")
                e["acked_by"] = o.get("acked_by")
                e["ack_reason"] = o.get("ack_reason")
        if e is None:
            e = {"msg": k, "first": now(), "count": 0, "ack": None}
        e["count"] = int(e.get("count", 0)) + 1
        e["last"] = now()
        e["msg"] = raw  # 时距进字段：最新原文含「龄Nmin」等数仍可见；键里只留问题身份
        if e.get("pinned"):
            # 钉住中的题再复报=故障确实在续，展示标跟着最新原文走（规则④不许回到「从未发生」）
            e["msg"] = _mark(raw, _cnt(e.get("pin_acks_day")) or _cnt(e.get("pin_acks")) or 1)
        a[k] = e
        _deage(a)
        atomic_write_json(_path(), {"updated": now(), "alerts": a})
    except OSError as exc:
        print("WARN: alerts 落盘失败(不影响主命令): %r" % exc, file=sys.stderr)


def entries():
    d = read_json(_path()) or {}
    return d.get("alerts", {})


def archive_entries():
    """去龄归档账现读可回查：state/alerts-archive.json（key→条目+archived_at）。"""
    d = read_json(_archive_path()) or {}
    return d.get("alerts", {})


def open_entries():
    return sorted([e for e in entries().values() if still_open(e)],
                  key=lambda x: x.get("last") or "")


def still_open(e):
    """单条开放判据（与 open_entries 同式）：无 ack 即开放；ack 之后又续报(last>ack)=回开放。"""
    ack = e.get("ack")
    return (not ack) or ((e.get("last") or "") > ack)


def deage_days():
    """去龄窗（天）：ALERT_DEAGE_DAYS 可配，默认 7，下限 1；坏值回退默认不炸写口。"""
    try:
        return max(1, int(os.environ.get("ALERT_DEAGE_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


def _ts(s):
    """解析账内时戳：兼容 now() 的 "+0800"（py3.10 fromisoformat 不认，手工补冒号）
    与 nowz()/ISO 的 "+08:00"。解析不了或无时区=返回 None——判不了龄就不动（不造假账龄，
    与 recycle DONE-TS 同纪律）。"""
    if not s:
        return None
    t = str(s)
    if len(t) >= 5 and t[-5] in "+-" and t[-4:].isdigit():
        t = t[:-2] + ":" + t[-2:]
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo is not None else None


def deage_eligible(e, days=None, now_dt=None):
    """去龄判据三闸（缺一不移）：
    ①已 ack——未 ack 键永不老；
    ②已停铃——still_open（含 ack 后复发 last>ack）的活问题绝不归档；
    ③last 龄 > days（缺省 deage_days()）；时戳解析不了=不移。"""
    if not e.get("ack"):
        return False
    if still_open(e):
        return False
    t = _ts(e.get("last"))
    if t is None:
        return False
    dd = deage_days() if days is None else days
    ref = now_dt if now_dt is not None else datetime.now().astimezone()
    return (ref - t) > timedelta(days=dd)


def _deage(a, days=None):
    """对账本 dict a 原地执行一轮去龄：到期条目先落归档、归档成功后才从 a 摘除
    （归档没写成=不动账，绝不留窟窿）。返回移走键列表。"""
    try:
        moved = [(k, e) for k, e in a.items()
                 if isinstance(e, dict) and deage_eligible(e, days=days)]
    except (TypeError, ValueError):
        return []  # 判龄异常（count 等字段腐成非数）——本拍不去龄，保守不动账
    if not moved:
        return []
    t = now()
    try:
        arch = read_json(_archive_path()) or {"alerts": {}}
        aa = arch.setdefault("alerts", {})
        for k, e in moved:
            e2 = dict(e)
            old = aa.get(k)
            if isinstance(old, dict):  # 同题二次归档：取较新快照为体，count 取大、first 取早
                if (old.get("last") or "") >= (e.get("last") or ""):
                    keep = dict(old)
                    keep["count"] = max(int(old.get("count", 0) or 0), int(e.get("count", 0) or 0))
                    e2 = keep
                f = [x for x in (old.get("first"), e.get("first")) if x]
                if f:
                    e2["first"] = min(f)
            e2["archived_at"] = t
            aa[k] = e2
        atomic_write_json(_archive_path(), {"updated": t, "alerts": aa})
    except OSError as exc:
        print("WARN: alerts 归档落盘失败，本轮去龄放弃(不留窟窿): %r" % exc, file=sys.stderr)
        return []
    for k, _ in moved:
        a.pop(k, None)
    return [k for k, _ in moved]


def deage_now(days=None):
    """存量清账（环内/手动，只此一处读整账再写回）：账不在或空=直接 []，不造文件。"""
    try:
        d = read_json(_path())
        if not d:
            return []
        a = d.get("alerts") or {}
        moved = _deage(a, days=days)
        if moved:
            atomic_write_json(_path(), {"updated": now(), "alerts": a})
        return moved
    except OSError as exc:
        print("WARN: alerts 去龄清账落盘失败: %r" % exc, file=sys.stderr)
        return []


def autoack(key, who=None, reason=None):
    """机器代停铃（精确键，BUG-REAPER-SCOPE-1；2026-11-03 双定义合并：
    此前本函数与 BUG-ALARM-LIFECYCLE-1 的 autoack(key, reason, who) **同名两定义**，
    后定义静默覆盖前定义=ack_reason 三分字段契约无声丢失——recycle_guard §9 三红就是接住它的
    （类型=BUG：泳道补丁互相遮蔽，import 期谁赢看书写顺序=哑雷）。合并形：who 缺省 auto:recycle，
    reason 给则单列 ack_reason 落账。只动「当前开放」条目（last>ack 或从未 ack），
    已闭合的不空转。被覆盖的人工处置痕存进 prev_ack/prev_acked_by——停铃不抹痕：
    人 ack 过而问题复响、后来问题真消了的条目也要能清，但审计仍能还原人那次处置。
    复响天然复活（record 刷 last>ack 即回开放），所以清僵尸永不灭活问题。返回 True=停了铃。
    停铃同拍顺带去龄（MECH-ALARM-DEAGE-1 写口挂点）：刚停的铃若早已超龄，直接进归档。
    规则④(BUG-ALARM-AUTOACK-MASK-1)：同题变体族**当日**第 2 次及以上的「未续报」到期**不写 ack 改钉住**
    （open 账里亮着+S6 按龄升级），返回 False=没停铃（已闭合/查无此键/已钉住/本轮升级为钉住）。
    每次真销账必落计数痕：autoack_count(+当日) 全部机器销账、stale_ack_count(+当日) 到期销账，
    prev_ack 链保留上一次处置时刻（覆写不丢史）。"""
    try:
        d = read_json(_path())
        a = (d or {}).get("alerts") or {}
        e = a.get(key)
        if not isinstance(e, dict):
            return False
        ack0 = e.get("ack")
        if ack0 and (e.get("last") or "") <= ack0:
            return False  # 已闭合，不空转
        t = now()
        stale = _is_stale(reason)
        if e.get("pinned"):
            if stale:
                return False  # 钉住中的到期键机器动不了（等人工 ack，同 WAKE-STALL 停拉等处置）
            # 「卡状态迁移」这类生命周期正当收口可解钉落 ack（episode 真的结束了），钉痕转档留 pin_cleared
            e["pin_cleared"] = t
            for f in ("pin_acks", "pin_acks_day"):
                e.pop(f, None)
            e.pop("pinned", None)
            e["msg"] = _PIN_MARK_RE.sub("", str(e.get("msg") or ""))
        tot, stale_day, _day_all = _fam_stats(a, key)
        if stale and stale_day + 1 >= pin_threshold():
            # 升级点名（WAKE-STALL 式钉住）：不销账、不归零——灯亮在 open 账里等人工，
            # 报警面展示「一夜自动销账N次」（N=同题变体族当日已被机器到期销账次数）。
            # 机器那一次 ack 一并**收回**（转 prev_* 存史）：留着旧 ack 值，面板/看板按 ack 判闭=
            # 依旧显示「已确认」，掩蔽只换了个地方（面板 open 计数才是叫醒面读的那一眼）。
            n = max(1, stale_day)
            if ack0:
                e["prev_ack"] = ack0
                if e.get("acked_by"):
                    e["prev_acked_by"] = e["acked_by"]
                if e.get("ack_reason"):
                    e["prev_ack_reason"] = e["ack_reason"]
            e["ack"] = None
            e["acked_by"] = None
            e["ack_reason"] = None
            e["pinned"] = t
            e["pin_acks"] = tot + 1
            e["pin_acks_day"] = n
            e["msg"] = _mark(e.get("msg") or key, n)
            atomic_write_json(_path(), {"updated": t, "alerts": a})
            return False
        if ack0:
            e["prev_ack"] = ack0
            if e.get("acked_by"):
                e["prev_acked_by"] = e["acked_by"]
        e["ack"] = t
        e["acked_by"] = who or "auto:recycle"
        if reason is not None:
            e["ack_reason"] = reason
        e["autoack_count"] = _cnt(e.get("autoack_count")) + 1
        e["autoack_last"] = t
        if e.get("autoack_day") != _day(t):
            e["autoack_day"] = _day(t)
            e["autoack_day_count"] = 0
        e["autoack_day_count"] = _cnt(e.get("autoack_day_count")) + 1
        if stale:
            e["stale_ack_count"] = _cnt(e.get("stale_ack_count")) + 1
            if e.get("stale_ack_day") != _day(t):
                e["stale_ack_day"] = _day(t)
                e["stale_ack_day_count"] = 0
            e["stale_ack_day_count"] = _cnt(e.get("stale_ack_day_count")) + 1
        _deage(a)
        atomic_write_json(_path(), {"updated": t, "alerts": a})
        return True
    except OSError as exc:
        print("WARN: alerts autoack 落盘失败(不影响主命令): %r" % exc, file=sys.stderr)
        return False


def ack(substr, who):
    """子串匹配（大小写敏感原文）：0 命中=[]；跨问题多命中=返回键列表让人缩小（旧契约）；
    同题多命中=按题整组销（MECH-ALARM-DEAGE-1）——「龄30min/龄31min/…」存量变体一击全落 ack，
    不必人肉逐变体 ack 二十遍。停铃不覆痕：已闭合的变体不重写其 ack 时刻。
    返回=本组全部条目(dict 列表)；调用方（cmd_ack）按 r[0] 显示。"""
    a = entries()
    hits = [k for k in a if substr in k]
    if not hits:
        return []
    if substr in a:
        hits = [substr]  # 精确键优先（S6认领轮 2026-11-03 21:2x）：base 键恒为「｜心跳已恢复待人工」
        # 变体键的前缀，任何子串必双命中→跨题歧义拒收→红灯永远 ack 不掉、S6 反复唤 PM。
    if len({_key(k) for k in hits}) > 1:
        return hits  # 跨问题歧义：缩小子串再来
    d = read_json(_path()) or {"alerts": {}}
    aa = d.get("alerts") or {}
    t = now()
    out = []
    for k in hits:
        e = aa.get(k)
        if not isinstance(e, dict):
            continue
        if still_open(e):
            e["ack"] = t
            e["acked_by"] = who
        if e.get("pinned"):
            # 人工处置=唯一解钉通道（规则④）：计数痕留在账里不抹，钉住标与展示标退场
            e["pin_cleared"] = t
            for f in ("pinned", "pin_acks", "pin_acks_day"):
                e.pop(f, None)
            e["msg"] = _PIN_MARK_RE.sub("", str(e.get("msg") or ""))
        out.append(e)
    if out:
        _deage(aa)
        atomic_write_json(_path(), {"updated": t, "alerts": aa})
    return out
