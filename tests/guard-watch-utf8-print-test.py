#!/usr/bin/env python3
# guard-watch-utf8-print-test.py — guard_watch GBK 宿主打印侧红绿锁（BUG-GUARDWATCH-GBKPRINT-1）。
# 母卡 MECH-GBKDECODE-BOMB-1（VERIFIED）读侧钉 52 py + recycle 出口 reconfigure，本锁盯 guard_watch
# 出口推广面：2026-11-03 11:49/11:57 PM 现跑 `python -I ops/guard_watch.py` 探针，cp936 宿主
# stdout 全行乱码（"GUARD-WATCH ... NO-SELF-HEAL 恢复(尾…"形）——值守报警/看板取数可读性受损。
# 手法=子程注 PYTHONIOENCODING=gbk 模拟 cp936 宿主跑沙箱探活轮，读 stdout **原始字节**断言
# 严格 UTF-8 可解码且含预期中文关键词；撤修复（reconfigure 钉拔掉）→ 子程吐 GBK 字节 → 必红。
# S0 另设环境有效性哨：同一 gbk 环境跑无钉探针必须吐 GBK 字节——防模拟失效导致本锁空转假绿
# （守卫要验过它红得起来）。
# 跑法: python -I cc-team/tests/guard-watch-utf8-print-test.py   exit 0 = GUARD_WATCH_UTF8_PASS
import io, json, os, subprocess, sys, tempfile, time

# 本锁自身出口同钉（dogfood：PASS/FAIL 行含中文，GBK 宿主裸 print 会重现卡面乱码症状）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OPS = os.path.join(ROOT, "ops", "guard_watch.py")
BEGIN, END = "<!-- GUARD-BLOCK-BEGIN -->", "<!-- GUARD-BLOCK-END -->"
NO_WIN = 0x08000000 if os.name == "nt" else 0
fails = []


def ok(tag):
    print("PASS:", tag)


def bad(tag, *ctx):
    fails.append(tag)
    print("FAIL:", tag, *[str(c)[:200] for c in ctx])


def run_gbk(env, *args):
    """注 PYTHONIOENCODING=gbk 跑 guard_watch，返回 (rc, 原始 stdout bytes, 原始 stderr bytes)。"""
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "gbk"   # 模拟 cp936 宿主（卡面实发环境）
    e.update(env)
    r = subprocess.run([sys.executable, "-I", OPS] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=e, timeout=120, creationflags=NO_WIN)
    return r.returncode, r.stdout or b"", r.stderr or b""


def utf8_strict(raw, tag):
    """原始字节必须严格 UTF-8 解码（GBK 字节混进来=乱码实证）；解不出直接记红。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        bad(tag, "stdout 非 UTF-8 可解码（疑似 GBK 出口未钉）：%r；头 60 字节=%r" % (exc, raw[:60]))
        return None


def expect(env, args, keyword, tag, want_rc=None):
    rc, raw, err = run_gbk(env, *args)
    txt = utf8_strict(raw, tag + "（UTF-8 解码）")
    if txt is None:
        return
    if keyword not in txt:
        bad(tag + "（关键词缺失）", "缺 %r；尾 200=%r" % (keyword, txt[-200:]))
    elif want_rc is not None and rc != want_rc:
        bad(tag + "（rc 漂移）", "want=%s got=%s" % (want_rc, rc))
    elif chr(0xfffd) in txt:  # 用转义不用字面 U+FFFD：该字符在源码传输层易被吞成空串（本卡同族雷）
        bad(tag + "（含 U+FFFD 损字符）", txt[-120:])
    else:
        ok(tag)


def mk_md(guards):
    return ("%s\n```json\n%s\n```\n%s\n" % (BEGIN, json.dumps({"guards": guards}, ensure_ascii=False), END))


def fresh(path, secs_ago=0):
    io.open(path, "w", encoding="utf-8").write("line\n")
    os.utime(path, (time.time() - secs_ago, time.time() - secs_ago))


def main():
    tmp = tempfile.mkdtemp(prefix="guardwatch_utf8_")
    state = os.path.join(tmp, "state")
    os.makedirs(state)
    home = os.path.join(tmp, "cc-team")
    os.makedirs(os.path.join(home, "state"))
    md = os.path.join(tmp, "清单.md")
    hb = os.path.join(tmp, "hb.heartbeat")
    env = {"CC_TEAM_HOME": home, "GUARD_WATCH_STATE": state,
           "GUARD_WATCH_MD": md, "GUARD_WATCH_HB": hb}
    log_stale = os.path.join(tmp, "stale.log")
    fresh(log_stale, 99999)
    guards = [{"id": "U-STALE", "name": "陈旧log", "kind": "log", "path": log_stale, "window_s": 60}]
    io.open(md, "w", encoding="utf-8").write(mk_md(guards))

    # S0 环境有效性哨：同 gbk 环境跑无钉探针，必须真吐 GBK 字节（模拟失效=本锁空转，先自红）
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "gbk"
    r = subprocess.run([sys.executable, "-I", "-c", "print('保障哨语')"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=e, timeout=60, creationflags=NO_WIN)
    got = (r.stdout or b"").strip()
    if got == "保障哨语".encode("gbk"):
        ok("S0 gbk 宿主模拟有效（无钉探针确实吐 GBK 字节）")
    elif got == "保障哨语".encode("utf-8"):
        bad("S0 gbk 模拟失效（探针仍吐 UTF-8）——S1~S4 为假绿风险，本锁空转")
    else:
        bad("S0 哨字节不可判", "got=%r err=%s" % (got[:60], (r.stderr or b"")[:120]))

    # S1 翻劣报警行（_alarm_key 中文「心跳超阈→保障 DEGRADED（不自愈…」）
    expect(env, ["--apply"], "心跳超阈", "S1 翻劣轮 stdout UTF-8 且含中文报警关键词", want_rc=1)

    # S2 不自愈提示行（心跳回来「NO-SELF-HEAL 心跳已回来」；先让 log 变新鲜）
    fresh(log_stale)
    expect(env, ["--apply"], "心跳已回来", "S2 恢复提示轮 UTF-8 中文（11:49 实发乱码同款行）")

    # S3 清单异常行（「ALARM: GUARD-WATCH 保障清单异常」——探活停摆最大声路径）
    io.open(md, "w", encoding="utf-8").write("没有标记段")
    expect(env, ["--apply"], "保障清单异常", "S3 空账假绿防线行 UTF-8 中文", want_rc=1)

    # S4 reset 拒单行（「ERROR: REJECT-RESET … 不在 DEGRADED」）
    io.open(md, "w", encoding="utf-8").write(mk_md(guards))
    expect(env, ["--reset", "U-NOTEXIST"], "不在 DEGRADED", "S4 reset 拒单行 UTF-8 中文", want_rc=2)

    if fails:
        print("=== GUARD_WATCH_UTF8_FAIL n=%d ===" % len(fails))
        sys.exit(1)
    print("GUARD_WATCH_UTF8_PASS")


if __name__ == "__main__":
    main()
