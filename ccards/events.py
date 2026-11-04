# ccards.events — append-only 状态流水（审计靠它不靠 diff）。
import os, sys
from .config import EVENTS, STATE, now


def _san(s):
    # 二轮对抗审计：理由/who 里的换行能伪造整行事件(对账脚本会被骗)，必须洗成单行
    return (s or "").replace("\r", " ").replace("\n", " ")


def event(cid, old, new, who, extra=""):
    os.makedirs(STATE, exist_ok=True)
    with open(EVENTS, "a", encoding="utf-8", newline="") as f:  # MECH-LEDGER-CRLF-1 钉LF
        f.write("%s|%s|%s->%s|%s|%s\n" % (now(), _san(cid), old, new, _san(who), _san(extra)))
    try:  # 只提醒不改协议：append-only 流水超 1MB 提示归档(ops/archive_events.sh 可接 cron)
        sz = os.path.getsize(EVENTS)
        if sz > 1048576:
            print("WARN: events.log 已超 1MB(%d bytes)，建议归档轮转" % sz, file=sys.stderr)
    except OSError:
        pass
