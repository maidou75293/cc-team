# ccards.retire — 兼容层退役强制器（MECH-RET1 简化轮定稿；MECH-PURE1 清零 2026-10-31）。
# 控制本身一句话：兼容层文件「在档且连续 7 天没人用过」就可以删。台账化实现三件纪律不变：
#   ① 谁被用过记一笔（state/shim-hits.log append-only，小行追加并发安全）；
#   ② reconcile 满窗零命中 → REMOVAL-READY 点名进 ALARM 账（CARD_RETIRE_DAYS，默认7天）；
#   ③ ack=留档停铃；ack 后新命中再满窗 → 复宣。文件删了自动跳过。
# 机制保留、观察名单清空：首批兼容层（card.py/recycle.py/reconcile-events.py/check-exit.py/
# tools/migrate_v1.py）已随 MECH-PURE1 全部删除——今后再造兼容件，加进 WATCH 即回到同一纪律，
# 不允许任何人凭日历记忆判「该删了」。
import os, sys
from datetime import datetime, timedelta
from .config import STATE, now

# 退役观察名单（相对 cc-team 根）＝在档兼容件全集。清零完成=空名单；新兼容件必须在此挂号。
WATCH = []
HITS = "shim-hits.log"


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path():
    return os.path.join(STATE, HITS)


def window_days():
    try:
        return max(1, int(os.environ.get("CARD_RETIRE_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


def _ts(s):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _append(name):
    os.makedirs(STATE, exist_ok=True)
    p = _path()
    fresh = not os.path.exists(p)   # 必须在 open 前判：追加模式会先建文件，判晚=窗锚永丢
    with open(p, "a", encoding="utf-8", newline="") as f:  # MECH-LEDGER-CRLF-1 钉LF
        if fresh:
            f.write("# since=%s\n" % now())
        if name:
            f.write("%s|%s\n" % (now(), name))


def hit(src):
    """用一次记一笔。src 传 __file__ 即可——名字自动归一为相对 cc-team 根（消手写错名）。"""
    try:
        name = src
        p = os.path.abspath(src)
        if os.path.exists(p):
            name = os.path.relpath(p, _root()).replace("\\", "/")
        _append(name)
    except OSError as exc:
        print("WARN: retire 命中落账失败(不影响转发): %r" % exc, file=sys.stderr)


def bootstrap():
    """窗锚落不下也不错——对账/命中任何一侧先跑都能把账开起来。永不抛。"""
    try:
        _append(None)
    except OSError:
        pass


def read():
    """→ (窗锚|None, {名: 末次命中})；无账=(None,{})，坏行忽略不炸对账。"""
    since, last = None, {}
    try:
        with open(_path(), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None, {}
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("# since="):
            t = _ts(ln.split("=", 1)[1])
            if t and (since is None or t < since):
                since = t
        elif "|" in ln:
            ts, _, name = ln.partition("|")
            t, name = _ts(ts), name.strip()
            if t and name and (name not in last or t > last[name]):
                last[name] = t
    if since is None and last:
        since = min(last.values())
    return since, last


def assess(root):
    """逐对象判档：[{name, exists, ready, last}]。ready=文件在档 && 窗锚与末次命中都已满窗口。"""
    since, last = read()
    ref, need = datetime.now().astimezone(), timedelta(days=window_days())
    out = []
    for name in WATCH:
        l = last.get(name)
        exists = os.path.exists(os.path.join(root, *name.split("/")))
        out.append({
            "name": name, "exists": exists, "last": l,
            "ready": bool(exists and since) and ref - since >= need and ref - (l or since) >= need,
        })
    return out
