# ccards.progress — 卡内执行进度流（MECH-LOG1：观测三件套的欠缺件）。
# 三件套定版（回答业主「实时节点运行日志怎么办/有回调么」）：
#   ① state/events.log   —— 状态流水（审计，已有，格式动不得）；
#   ② claims/lane 心跳   —— 判活（recycle 消费，已有）；
#   ③ state/progress.log —— 本流：worker 边跑边报「到哪了」（本卡新增）。
# 没有常驻回调——中枢=SPOF 定版不变：观测=文件+tail（watch 只读），写侧只是 append 一行。
# 纪律同 RET1 旁证层：单行清洗（换行注入=造行，events._san 同款）；超 1MB 只提示不自动轮转；
# 本流是旁证不是事实——坏一行=显示噪音，永不进账、永不拦写命令。
import os, sys
from .config import PROGRESS, STATE, now
from .events import _san


def record(cid, who, text):
    """写一行 `ts|cid|who|text`，返回该行。命令直调：写不进=红（与 hit 的旁证不抛不同，
    这里是显式动作，用户要看见成败）。"""
    os.makedirs(STATE, exist_ok=True)
    line = "%s|%s|%s|%s" % (now(), _san(cid), _san(who), _san(text))
    with open(PROGRESS, "a", encoding="utf-8", newline="") as f:  # MECH-LEDGER-CRLF-1 钉LF
        f.write(line + "\n")
    try:
        if os.path.getsize(PROGRESS) > 1048576:
            print("WARN: progress.log 已超 1MB，建议归档轮转（旁证流，不拦任何命令）", file=sys.stderr)
    except OSError:
        pass
    return line


def tail(n=20, card=None):
    """→ 最近 n 行（旧在前）。card=按卡过滤；n<=0=全量。无账=[]（新装不红），坏行(无|)忽略。"""
    try:
        with open(PROGRESS, encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip() and "|" in ln]
    except OSError:
        return []
    if card:
        lines = [ln for ln in lines if ln.split("|", 2)[1] == card]
    return lines[-n:] if n > 0 else lines
