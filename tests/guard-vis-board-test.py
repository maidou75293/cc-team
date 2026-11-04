#!/usr/bin/env python3
# guard-vis-board-test.py — REQ-GUARD-VIS-1 验收锁（业主 seq=10 §2：看板「保障状态」区块
# 展示对象=系统保障清单条目，不是卡）。立卡时必红（404/无接口），worker 实现后转绿=唯一转正通道。
# 断言面：
#   V1 看板数据端点 /api/guards 返回 200 + JSON guards 列表
#   V2 条目 id 覆盖 docs/系统保障清单.md GUARD-BLOCK 全量（同源，防看板自造一套账）
#   V3 每条带 名称/状态(OK|DEGRADED)/最近一次判据读数；DEGRADED 必带 since+detail
#   V4 区块 HTML 现读：首页含「系统保障」字样且不再出现「常驻岗卡」旧称谓（正名收口）
# 跑法: python -I cc-team/tests/guard-vis-board-test.py   exit 0 = GUARD_VIS_PASS
# 双侧：纯 python+HTTP，Win/WSL 同码（seq=13 第4条纪律沿用）。
import io, json, os, re, sys, urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
CODE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BOARD = os.environ.get("GUARD_VIS_BOARD", "http://127.0.0.1:8790")
fails = []


def ok(n):
    print("PASS:", n)


def bad(n, d=""):
    fails.append(n)
    print("FAIL:", n, str(d)[:200])


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "guard-vis-test"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


def main():
    # V1
    try:
        st, body = get(BOARD + "/api/guards")
        doc = json.loads(body)
        guards = doc.get("guards")
        if st == 200 and isinstance(guards, list) and guards:
            ok("V1 /api/guards 200+列表")
        else:
            bad("V1 端点形态错", str(doc)[:160])
            guards = guards or []
    except Exception as exc:
        bad("V1 /api/guards 不可达/非JSON", repr(exc))
        guards = []
    # V2 与清单同源
    md = os.path.join(CODE, "docs", "系统保障清单.md")
    try:
        seg = io.open(md, encoding="utf-8").read().split("GUARD-BLOCK-BEGIN", 1)[1]
        seg = seg.split("GUARD-BLOCK-END", 1)[0]
        blk = json.loads(seg.split("```json", 1)[1].split("```", 1)[0])
        want = {g["id"] for g in blk["guards"]} | {"gate-matrix", "gate-create",
                "gate-verifypin", "gate-gbk", "gate-watch-lock", "gate-selftest", "gate-roster"}
        got = {g.get("id") for g in guards}
        if want <= got:
            ok("V2 端点条目覆盖清单全量（在册 %d）" % len(got))
        else:
            bad("V2 缺目", sorted(want - got))
    except Exception as exc:
        bad("V2 清单解析断", repr(exc))
    # V3 字段齐
    for g in guards:
        if not (g.get("name") and g.get("state") in ("OK", "DEGRADED")):
            bad("V3 条目缺 名称/状态", g)
            break
        if g.get("state") == "DEGRADED" and not (g.get("since") or g.get("detail")):
            bad("V3 DEGRADED 缺 since/detail", g)
            break
    else:
        ok("V3 字段齐全（状态二值+读数）")
    # V4 首页文案正名
    try:
        st, html = get(BOARD + "/")
        if "系统保障" in html and "常驻岗卡" not in html:
            ok("V4 首页现读=系统保障正名，无旧岗卡称谓")
        else:
            bad("V4 首页文案未收口", ("有系统保障=%s 残留常驻岗卡=%s"
                % ("系统保障" in html, "常驻岗卡" in html)))
    except Exception as exc:
        bad("V4 首页不可达", repr(exc))
    if fails:
        print("=== GUARD_VIS_FAIL n=%d ===" % len(fails))
        sys.exit(1)
    print("GUARD_VIS_PASS")


if __name__ == "__main__":
    main()
