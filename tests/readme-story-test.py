#!/usr/bin/env python3
# readme-story-test.py — REQ-OSS-README-UX-1 常驻判据：README 对话化改版 + 头牌动画演示。
# 业主 2026-11-04 直令四硬条的机器化（客户已有 Claude Code，给 git 地址就能开干）；
# R2 按 22:1x 打回新规格增锁：主线零命令行（T8）、体积上限放宽到 8MB（T2）。
# R3 按 23:0x 打回落定稿（业主原话「先不管看板，就是在对话框输出过程」「动态过程还是老的」）：
#      画面=单一 CC 对话框全链实录，T2c/T3 从「四画面含看板」改为「五段过程拍齐 + board=0
#      负锁」；看板出现在图注窗口=红（回退旧文案的差分靶）。
#   T1 头引图：全文第一个被引用的图片必须是 demo.gif，且位于第一个二级标题之前（置顶）；
#   T2 动画本体：demo.gif 存在、魔数 GIF89a、帧数≥10、体积<8MB（业主 11-04 22:1x 打回补画面后
#      放宽上限；重录变大/退化仍即红）；
#   T2c 画面构成（R3 定稿+R4 补「建成系统演示」业主 23:3x）：GIF 注释扩展块内 SCENES 清单
#      必须点名 init/dialog/gate_red/rework/green/app_demo 六段过程拍且各≥1 帧、board=0
#      （无看板画面），TOTAL 与逐块实数帧数一致——缺任何一拍（尤其「判据红不放行」「打回返工」
#      「系统现场问答」三拍＝业主点名要看的「过程」）即红；
#   T3 真实来源标注：图引用行 ±2 行窗口内含「沙箱/SANDBOX」与「加速/accelerated」两标，
#      且同窗点名「对话框」（单窗叙事）与「返工」（过程主角落文案），图注窗口出现「看板」=红
#      （业主 23:0x「先不管看板」），正文另提及 `cc demo`（复跑对照入口，禁摆拍凭据）；
#   T8 主线零命令行：附录之前的正文不得出现 shell 围栏（bash/sh/console…=可复制执行的命令），
#      且必须出现「粘 + git 地址 + 一句话 + 自举」句式与「不记命令」口径——业主 11-04 22:1x
#      打回的第一条：客户已有 CC，粘地址即可自举，命令行只许留在附录；
#   T4 前置三条：模型随 Claude Code 自带 / 引擎零外部依赖不上云 / 探测缺位只打印官方安装命令；
#   T5 语气闸门：剥除图片与链接语法后，正文第一个含中文段落不得出现感叹号（专业，不叫卖）；
#   T6 机器表不漂移：README 四张 STATE-* 标记表与 ccards/statemachine.py 逐行同源
#      （与 state_std_test S2 同判据——头部怎么改，机器契约区必须原样在位）；
#   T7 导出面同源（11-04 死图事故补锁）：README 引用的每个本地图片资产必须同时在发布
#      清单内（oss_cli.RELEASE_FILES 点名 或 RELEASE_DIRS 目录覆盖）——判据全绿≠公仓有图，
#      export 根文件按清单点名，漏一行=公仓首页 404 死图（demo.gif 实踩）。
# 跑法：python -I cc-team/tests/readme-story-test.py（exit 0=全绿；全程只读，不写任何账）
import io, os, re, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cc-team 根
README = os.path.join(SRC, "README.md")
GIF = os.path.join(SRC, "demo.gif")
try:      # 宿主 cp936 控制台防炸（全仓测试同规）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 只读判据也钉临时账根：万一 import 链触碰路径解析，物理够不着真账
os.environ["CC_TEAM_HOME"] = tempfile.mkdtemp(prefix="readme-story-home-")
sys.path.insert(0, SRC)
fails = []


def ok(name):
    print("PASS:", name)


def bad(name, detail=""):
    fails.append(name)
    print("FAIL:", name, str(detail)[:200])


def gif_frame_count(data):
    """纯 stdlib 解析 GIF 块链，数图像描述符(0x2C)帧数——判据不引 Pillow（发行版零依赖纪律）。"""
    n = len(data)
    if n < 13:
        return -1
    i = 13
    packed = data[10]
    if packed & 0x80:                                # 全局色表
        i += 3 * (2 << (packed & 0x07))
    frames = 0
    while i < n:
        b = data[i]
        if b == 0x3B:                                # trailer
            break
        if b == 0x21:                                # 扩展块
            i += 2
            while i < n and data[i] != 0:
                i += data[i] + 1
            i += 1
        elif b == 0x2C:                              # 图像描述符
            frames += 1
            if i + 10 > n:
                break
            lp = data[i + 9]
            i += 10
            if lp & 0x80:                            # 局部色表
                i += 3 * (2 << (lp & 0x07))
            i += 1                                   # LZW 最小码长
            while i < n and data[i] != 0:            # 子块链
                i += data[i] + 1
            i += 1
        else:
            i += 1
    return frames


def gif_comments(data):
    """同样纯 stdlib 走块链，收集注释扩展块(0x21 0xFE)文本——渲染端把画面构成写在这里，
    判端才可能机器化「动画里有哪几段画面」，否则只能数帧看个热闹。"""
    n = len(data)
    i = 13
    packed = data[10]
    if packed & 0x80:
        i += 3 * (2 << (packed & 0x07))
    out = []
    while i < n:
        b = data[i]
        if b == 0x3B:
            break
        if b == 0x21:
            label = data[i + 1] if i + 1 < n else 0
            i += 2
            payload = b""
            while i < n and data[i] != 0:
                payload += data[i + 1:i + 1 + data[i]]
                i += data[i] + 1
            i += 1
            if label == 0xFE:
                out.append(payload.decode("utf-8", "replace"))
        elif b == 0x2C:
            if i + 10 > n:
                break
            lp = data[i + 9]
            i += 10
            if lp & 0x80:
                i += 3 * (2 << (lp & 0x07))
            i += 1
            while i < n and data[i] != 0:
                i += data[i] + 1
            i += 1
        else:
            i += 1
    return out


def readme_text():
    return io.open(README, encoding="utf-8").read()


# ─────────────────────────── T1 头引图=demo.gif 置顶 ───────────────────────────
def t1():
    txt = readme_text()
    m = re.search(r"!\[[^\]]*\]\(([^)]*)\)", txt)
    if not m:
        return bad("T1 头引图", "README 无任何图片引用")
    url = m.group(1).strip().lstrip("./")
    if url == "demo.gif":
        ok("T1 全文第一个被引图片=demo.gif")
    else:
        bad("T1 头引图", "第一个图片引用=%r（要求 demo.gif）" % url)
    h2 = txt.find("\n## ")
    if h2 >= 0 and m.start() < h2:
        ok("T1 demo.gif 位于第一个二级标题之前（置顶）")
    else:
        bad("T1 头引图未置顶", "img@%s h2@%s" % (m.start(), h2))


# ─────────────────────────── T2 gif 本体：魔数/帧数/体积 ───────────────────────────
def t2():
    if not os.path.isfile(GIF):
        return bad("T2 demo.gif 缺失", GIF)
    data = open(GIF, "rb").read()
    if data[:6] == b"GIF89a":
        ok("T2 魔数 GIF89a")
    else:
        bad("T2 魔数", data[:6])
    fc = gif_frame_count(data)
    if fc >= 10:
        ok("T2 帧数=%d（≥10）" % fc)
    else:
        bad("T2 帧数", "解析得 %s，要求≥10" % fc)
    size = len(data)
    if size < 8 * 1024 * 1024:
        ok("T2 体积 %.1fKB（<8MB，业主 11-04 22:1x 补画面后放宽）" % (size / 1024.0))
    else:
        bad("T2 体积", "%d 字节 ≥8MB，头牌动画必须瘦身" % size)


# ─────────────── T2c 画面构成（GIF 注释块 SCENES 清单=三画面补上的机器凭据） ───────────────
def t2c():
    if not os.path.isfile(GIF):
        return bad("T2c 画面构成", "demo.gif 缺失")
    data = open(GIF, "rb").read()
    cm = [c for c in gif_comments(data) if c.startswith("SCENES")]
    if not cm:
        return bad("T2c 画面构成", "GIF 注释扩展块无 SCENES 清单（渲染端未落画面凭据）")
    man = cm[0]
    need = ("init", "dialog", "gate_red", "rework", "green", "app_demo")
    counts, miss = {}, []
    for k in need:
        m = re.search(r"\b%s=(\d+)" % k, man)
        if not m or int(m.group(1)) < 1:
            miss.append(k)
        else:
            counts[k] = int(m.group(1))
    if miss:
        bad("T2c 画面构成", "缺过程拍 %s｜清单=%s（要求 装机自检/对话/判据红不放行/打回返工/验收绿/建成系统现场跑 各≥1 帧）"
            % (",".join(miss), man))
    else:
        ok("T2c 六段过程拍齐备 %s" % man)
    mb = re.search(r"\bboard=(\d+)", man)
    ok("T2c 单对话框无看板（board=0 在册）") if (mb and mb.group(1) == "0") \
        else bad("T2c 画面构成", "board 清单缺位或≠0（业主 23:0x 定稿=对话框外画面不许进）｜%s" % man)
    m = re.search(r"\bTOTAL=(\d+)", man)
    real = gif_frame_count(data)
    if m and int(m.group(1)) == real:
        ok("T2c 清单帧数与实帧一致（TOTAL=%d）" % real)
    else:
        bad("T2c 帧数漂移", "清单=%s 实帧=%s" % (m.group(1) if m else "无", real))


# ─────────────────────────── T3 sandbox/加速标注 + cc demo 指认 ───────────────────────────
def t3():
    txt = readme_text()
    lines = txt.splitlines()
    img_ln = next((i for i, ln in enumerate(lines)
                   if re.search(r"!\[[^\]]*\]\(\.?/?demo\.gif\)", ln)), None)
    if img_ln is None:
        return bad("T3 标注窗口", "找不到 demo.gif 引用行")
    win = " ".join(lines[max(0, img_ln - 1):img_ln + 3])
    has_sbx = ("沙箱" in win) or ("SANDBOX" in win.upper())
    has_acc = ("加速" in win) or ("ACCELERATED" in win.upper())
    if has_sbx and has_acc:
        ok("T3 图注窗口同现 sandbox 与 accelerated 两标")
    else:
        bad("T3 标注", "沙箱=%s 加速=%s｜win=%s" % (has_sbx, has_acc, win[:120]))
    if "看板" in win:
        bad("T3 画面点名", "图注窗口出现「看板」=旧四画面文案回退（业主 23:0x「先不管看板」）｜win=%s" % win[:160])
    elif "对话框" in win and "返工" in win:
        ok("T3 图注点名「单一对话框 + 返工」过程叙事（R3 定稿文案侧凭据）")
    else:
        bad("T3 画面点名", "图注窗口未点名 对话框/返工｜win=%s" % win[:160])
    if "cc demo" in txt:
        ok("T3 正文指认动画来源=cc demo 沙箱实录")
    else:
        bad("T3 来源指认", "正文未提及 cc demo")


# ─────────────────────────── T4 前置三条 ───────────────────────────
def t4():
    txt = readme_text()
    if not re.search(r"^#{1,6}.*前置", txt, re.M):
        bad("T4 前置小节", "未见含「前置」的标题")
    else:
        ok("T4 前置小节在场")
    lines = txt.splitlines()
    probes = [
        ("T4a 模型随 CC 自带（零模型配置）", lambda ln: "Claude Code" in ln and "自带" in ln),
        ("T4b 引擎零外部依赖、不上云", lambda ln: "零外部依赖" in ln and "不上云" in ln),
        ("T4c 探测缺位只打印官方安装命令", lambda ln: "只打印官方安装命令" in ln),
    ]
    for name, probe in probes:
        if any(probe(ln) for ln in lines):
            ok(name)
        else:
            bad(name, "全篇无一行同时含判据词")


# ─────────────────────────── T5 首段无感叹号 ───────────────────────────
def t5():
    txt = readme_text()
    body = txt.split("\n", 1)[1] if txt.startswith("#") else txt
    body = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", body)       # 剥图片与链接语法（徽章不背锅）
    for block in re.split(r"\n\s*\n", body):
        block = "\n".join(ln for ln in block.splitlines()
                          if not ln.strip().startswith("<!--")).strip()
        if not block or not re.search(r"[一-鿿]", block):
            continue
        if "!" in block or "！" in block:
            return bad("T5 首段感叹号", block.splitlines()[0][:80])
        ok("T5 首段（%s…）无感叹号" % block.strip("*（[ ")[0:12])
        return
    bad("T5 首段", "剥图章后找不到含中文的首段")


# ─────────────────────────── T6 机器表不漂移（S2 同判据） ───────────────────────────
def _readme_tbl(marker):
    txt = readme_text()
    try:
        seg = txt.split("<!-- %s-BEGIN -->" % marker, 1)[1].split("<!-- %s-END -->" % marker, 1)[0]
    except IndexError:
        return None
    rows = []
    for ln in seg.splitlines():
        ln = ln.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if cells[0] in ("状态", "从") or all(c and set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows


def t6():
    from ccards import statemachine as sm                    # noqa: E402
    from ccards.config import STATUS_ENUM                    # noqa: E402
    for marker in ("STATE-OWNER", "STATE-TERMS", "STATE-MATRIX", "STATE-FIELDS"):
        if ("<!-- %s-BEGIN -->" % marker) not in readme_text():
            bad("T6 机器契约区缺失", marker)
            return
    t = _readme_tbl("STATE-OWNER")
    exp = [[s, sm.TERMS[s], sm.OWNER_MATRIX[s][0], sm.OWNER_MATRIX[s][1], sm.OWNER_MATRIX[s][2]]
           for s in STATUS_ENUM]
    ok("T6 状态=属主矩阵同源") if t == exp else bad("T6 属主矩阵漂移", (t or "缺表") if t != exp else "")
    t = _readme_tbl("STATE-TERMS")
    ok("T6 术语表同源") if (t and [[r[0], r[1]] for r in t] == [[s, sm.TERMS[s]] for s in STATUS_ENUM]) \
        else bad("T6 术语表漂移", t)
    t = _readme_tbl("STATE-MATRIX")
    exp = sorted(map(tuple, [[f, to, v, n] for f, to, v, n in sm.TRANSITIONS]))
    ok("T6 迁移矩阵 %d 边同源" % len(exp)) if (t and sorted(map(tuple, t)) == exp) \
        else bad("T6 迁移矩阵漂移", "README行数=%s 期望=%s" % (len(t) if t else None, len(exp)))
    t = _readme_tbl("STATE-FIELDS")
    ok("T6 必填字段表覆盖七态") if (t and sorted(r[0] for r in t) == sorted(STATUS_ENUM)) \
        else bad("T6 必填字段表缺态", t)


# ─────────────────────── T7 导出面同源（引用资产必在发布清单） ───────────────────────
def t7():
    from ccards import oss_cli                                  # noqa: E402
    urls = [m.group(1).strip().lstrip("./")
            for m in re.finditer(r"!\[[^\]]*\]\(([^)]*)\)", readme_text())]
    local = [u for u in urls if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u) and u]
    if not local:
        return bad("T7 导出面", "README 无任何本地图片引用（头引图被删了？）")
    for u in local:
        if not os.path.isfile(os.path.join(SRC, u)):
            bad("T7 导出面", "引用资产不在仓内: %s" % u)
            continue
        top = "/" not in u
        covered = (u in oss_cli.RELEASE_FILES) if top else (u.split("/")[0] in oss_cli.RELEASE_DIRS)
        if covered:
            ok("T7 资产 %s 在发布清单内" % u)
        else:
            bad("T7 导出面", "%s 在仓但不在 RELEASE_FILES/DIRS——export 必漏=公仓死图" % u)


# ─────────────── T8 主线零命令行（业主 11-04 22:1x 打回第一条） ───────────────
_SHELL_FENCE = re.compile(r"^[ \t]*`{3,}[ \t]*(bash|sh|shell|console|zsh|ps1|powershell|bat|cmd)\b",
                          re.I | re.M)


def t8():
    txt = readme_text()
    m = re.search(r"^#{1,6}[ \t]*附录", txt, re.M)
    if not m:
        return bad("T8 附录边界", "找不到「附录」小节——手工命令没有收口处")
    main, tail = txt[:m.start()], txt[m.start():]
    in_main = _SHELL_FENCE.findall(main)
    in_tail = _SHELL_FENCE.findall(tail)
    if in_main:
        bad("T8 主线含可执行命令块", "附录之前有 %d 处 shell 围栏（首个=%r）——客户不该背命令"
            % (len(in_main), _SHELL_FENCE.search(main).group(0).strip()))
    else:
        ok("T8 主线（附录前）零 shell 围栏")
    if in_tail:
        ok("T8 手工命令仍在附录内（%d 处围栏）" % len(in_tail))
    else:
        bad("T8 附录缺命令面", "附录内也没有 shell 围栏——调试/无 daemon 的等价物不能丢")
    for name, pat in (
            ("T8e 粘 git 地址的句式在场", r"粘[^\n]{0,40}(地址|URL)"),
            ("T8f 一句话开干的口径在场", r"一句话"),
            ("T8g 自举的口径在场", r"自举"),
            ("T8h 不记命令的口径在场", r"(不记命令|零命令行|没有命令行|不碰命令行)")):
        if re.search(pat, main):
            ok(name)
        else:
            bad(name, "主线未见 /%s/" % pat)


if __name__ == "__main__":
    for fn in (t1, t2, t2c, t3, t4, t5, t6, t7, t8):
        try:
            fn()
        except Exception as e:                               # 判据自身异常=红，不许静默
            bad("%s 执行异常" % fn.__name__, repr(e))
    print("readme-story: %s (%d FAIL)" % ("ALL_GREEN" if not fails else "RED", len(fails)))
    sys.exit(0 if not fails else 1)
