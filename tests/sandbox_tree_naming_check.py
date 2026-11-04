#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sandbox_tree_naming_check.py — 红检/沙箱临时树命名禁令（REQ-BOARD-LANECLEAN-1 规格③尾）。

业主实底：看板泳道区的「躺着的 Down」一半来自红检/沙箱遗留——临时会话树直接叫
code-wf-<道>-tmp-*（实况样本 m5-tmp-ph-p0~p4），与真道混排、还删不干净。

禁令（今后所有车道/红检通用）：
  临时树/沙箱一律放 **仓外 $TMPDIR**（如 /tmp/cc-<卡号>-sandbox），会话目录命名不得
  出现 code-wf-*-tmp-*——CC 项目目录名由工作树路径派生，故等价禁则=临时工作树不得叫
  wf-<道>-tmp-*，放在 .claude/projects 派生命名空间里。

两层断言（常驻自检，随 board-laneclean-test.py 与 tests 套跑）：
  ① 实况哨兵：~/.claude/projects 下现存 code-wf-*-tmp-* 目录=违规残留 → 红（清账走
     cc-team/ops/lane_registry.py clean-sessions --apply）。
  ② 静态源码：仓内 py/sh/mjs 里出现「wf-」+「-tmp-」拼接式建目录语句 → 红
     （豁免文件=禁令自身与本卡锁/清理脚本——它们只描述禁则，不产生目录）。
     豁免以「文件名精确清单」写死，扩充须 PM 拍板。

用法: python -I cc-team/tests/sandbox_tree_naming_check.py [--projects-root DIR]
→ 末行 SANDBOX_NAMING_PASS；违规 exit 1。
"""
import argparse
import glob
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
# REQ-OSS-ENGINE-1：缺省会话根零宿主字面量——WSL 侧取挂载视图 /mnt/<盘>/Users/ 首个用户目录
# （本机与旧默认串逐字相同）；无该挂载（如 Windows 宿主直跑）退回 $HOME 视图，不再指不存在盘路。
def _default_projects_root():
    for u in sorted(glob.glob("/mnt/[A-Za-z]*/Users/*")):
        if os.path.isdir(u):
            return os.path.join(u, ".claude", "projects")
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


PROJ_DEFAULT = _default_projects_root()
WF_TMP_RE = re.compile(r"code-wf-[A-Za-z0-9_-]*-tmp-[A-Za-z0-9_-]+")
# 静态禁则：同一行里既碰「wf-」命名空间又拼「-tmp-」——即疑似产生 code-wf-*-tmp-* 的构造。
SRC_BAD_RE = re.compile(r"""(mkdir|rmtree|mkdtemp|makedirs|worktree\s+add|git\s+worktree).*wf-[A-Za-z0-9_"'.+-]*-tmp-"""
                        r"""|wf-[A-Za-z0-9_"'.+-]*-tmp-[A-Za-z0-9_"'.+%-]*.*(mkdir|mkdtemp|makedirs|worktree)""")
EXEMPT = {os.path.normcase(os.path.relpath(os.path.abspath(__file__), REPO)),
          os.path.normcase("scripts/e2e/board-laneclean-test.py"),
          os.path.normcase(os.path.join("cc-team", "ops", "lane_registry.py"))}


def check_projects(root):
    bad = []
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            if WF_TMP_RE.search(d):
                bad.append(os.path.join(root, d))
    return bad


def check_sources(repo=REPO):
    hits = []
    for base, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x not in (".git", "__pycache__", "node_modules",
                                                "backend", "web", "build", "deploy",
                                                "services", "logs", "tmp", "_archive")]
        for fn in files:
            if not fn.endswith((".py", ".sh", ".mjs")):
                continue
            p = os.path.join(base, fn)
            rel = os.path.normcase(os.path.relpath(p, repo))
            if rel in EXEMPT or rel.replace("\\", "/") in EXEMPT:
                continue
            try:
                for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                    s = line.strip()
                    if s.startswith("#") or "#'" in s[:2]:
                        continue
                    if SRC_BAD_RE.search(s.replace("\\", "/")):
                        hits.append("%s:%d %s" % (rel, i, s[:120]))
            except OSError:
                pass
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects-root", default=PROJ_DEFAULT)
    a = ap.parse_args()
    n = 0
    bad_dirs = check_projects(a.projects_root)
    if bad_dirs:
        print("FAIL: 现存 code-wf-*-tmp-* 会话目录（违规残留，清账走 lane_registry.py clean-sessions --apply）:")
        for d in bad_dirs:
            print("  - " + d)
    else:
        print("ok: 实况哨兵——无 code-wf-*-tmp-* 残留目录")
    n += 1
    hits = check_sources()
    if hits:
        print("FAIL: 源码出现禁则命中的临时树构造（wf-*-tmp-*）:")
        for h in hits:
            print("  - " + h)
    else:
        print("ok: 静态断言——仓内源码无 wf-*-tmp-* 建目录构造")
    rc = 1 if (bad_dirs or hits) else 0
    print("SANDBOX_NAMING_%s checks=%d residual=%d src_hits=%d" % (
        "PASS" if rc == 0 else "FAIL", n + 1, len(bad_dirs), len(hits)))
    sys.exit(rc)


if __name__ == "__main__":
    main()
