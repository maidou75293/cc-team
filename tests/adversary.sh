#!/bin/bash
# adversary.sh — 二轮对抗审计（MECH-ADVERSARY）：非法转移全矩阵 / 恶意卡面 / 注入 / 对账 / check-exit 两态 / 崩溃注入。
# 全程沙箱台账 CC_TEAM_HOME=/tmp/cc-adr + 临时 git 仓，不碰真实账。断言式：任一未拒/炸栈 → FAIL。
set -u
# MECH-TRAIN-ADR-1 第三层（恒红 2 条的实锤：M4 态A rc=0、注入3 卡态=VERIFIED）：生产任务环境
# CC_AUTO_VERIFY=1（ctrld 子环境），done 自动验绿即翻 VERIFIED——本套 M4/注入族的前置状态
# 「DONE 未验收」被环境掀掉，断言与环境污染对撞（非放宽：本套验的是状态机拒非法转移，auto@done
# 是另一条链，其自身红绿锁在 E2E-AUTOVERIFY 族）。selftest.sh L21 同式先例，只清会话污染。
unset CC_AUTO_VERIFY
# MECH-TRAIN-ADR-1 收口三线（火车实录 6 笔同签名 fails=5 + win 侧陈旧态串染 12 连红取证）：
# ①宿主探测与 selftest.sh 同式（旧缺省钉 /mnt/c：win 形态 python 不可见 → cards_dag 全
#    "can't open file" rc=2 且无 Traceback，rej() 误当「已拒」= 整片假绿的矩阵灾难）；
# ②沙箱根可移（CC_ADR_HOME/CC_ADR_REPO）：/tmp/cc-adr 全家共享，火车沙箱与泳道 worker
#    并跑互相 rm -rf——独占两跑各见 21/14 两种污染签名、win 基线 12 连红全是陈旧态串染
#    （.ctr 计数器没归零、卡号已存在、道锁新鲜连坐）。缺省路径行为逐字不变。
# ③hermetic（摘外部活依赖，不摘守卫）：reconcile_events 判据四（BUG-BOARD-SELF-AUDIT-1）
#    探活体看板 :8790——看板已迁 win（MECH-WIN-ALL-1），WSL 侧不可达 → 5 条对账断言连坐
#    恒红=火车恒打回的病根本体。本套 M3/M5 验的是账本对账三判据，活体看板不属其射程：
#    套件自带 hermetic stub（看板审计在生产对账环原样在跑，未放宽）。
# REQ-OSS-ENGINE-1：缺省=本套脚本所在仓根（自位置现算，不钉宿主用户名/盘符字面量）；
# 主仓原位运行时两宿主取到的串与旧默认逐字相同，泳道树/沙箱拷贝则自动跟随所在树。
# REQ-OSS-PREP-1（与 selftest.sh 同批同式）：套件根改自位置 ../ 现算（对目录名/布局免疫）；
# 主仓两宿主所得串与旧式逐字相同；CC_CODE_HOME 覆盖保留，嵌套缺目回落根形兼容发行仓布局。
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) _winfmt=1;;
  *) _winfmt=0;;
esac
# REQ-OSS-MACOS-PORT-1（macOS 移植）：GNU 专属 coreutils 件宿主中性化——
# ①`touch -d "N ago"` BSD touch 无 -d（M4 态B 笔记回拨夹具第七圈 macOS 实锤红），
#   改 python os.utime（POSIX API 三宿主同形）；②`sed -i` BSD 强制要备份后缀，
#   裸 -i 在 macOS 把脚本串当后缀报错，改 tmp+mv（两宿主行为一致的通用 POSIX 形）。
ago(){ python3 - "$1" "$2" <<'PYEOF'
import os, sys, time
t = time.time() - float(sys.argv[2])
os.utime(sys.argv[1], (t, t))
PYEOF
}
if [ -n "${CC_CODE_HOME:-}" ]; then
  CODE_HOME="$CC_CODE_HOME"
  N="$CODE_HOME/cc-team"; [ -d "$N" ] || N="$CODE_HOME"
else
  if [ "$_winfmt" = 1 ]; then
    N="$(cd "$(dirname "$0")/.." && pwd -W)"; CODE_HOME="$(cd "$N/.." && pwd -W)"
  else
    N="$(cd "$(dirname "$0")/.." && pwd)"; CODE_HOME="$(cd "$N/.." && pwd)"
  fi
fi
[ -n "$CODE_HOME" ] || { echo "FATAL: CODE_HOME 自位置解析失败（$0 不可达）"; exit 1; }
export CC_CODE_HOME="$CODE_HOME"
export CC_TEAM_SRC="$N"
export CC_TEAM_HOME="${CC_ADR_HOME:-/tmp/cc-adr}"
ADR="${CC_ADR_REPO:-/tmp/cc-adr-repo}"
export CC_ADR_REPO="$ADR"   # 内嵌 python（inj1/2/4 Namespace）与 bash 两侧同值（缺省串逐字不变）
rm -rf "$CC_TEAM_HOME" "$ADR"; mkdir -p "$CC_TEAM_HOME" "$ADR"
printf 'import sys\nsys.exit(0)\n' > $CC_TEAM_HOME/sa-hermetic-stub.py
export BOARD_SELFAUDIT_PATH="$CC_TEAM_HOME/sa-hermetic-stub.py"
echo 0 > $CC_TEAM_HOME/.ctr
cd $ADR && git init -q -b master && git config user.email a@a && git config user.name a
mkdir -p src && echo a > src/a.py && git add -A && git commit -qm "init"
CARD="python3 $N/cards_dag.py"
fails=0
ok(){ echo "PASS: $1"; }
bad(){ echo "FAIL: $1"; fails=$((fails+1)); }
rej(){ d="$1"; shift; out=$("$@" 2>&1 >/dev/null); rc=$?
  if [ $rc -eq 0 ]; then bad "未拒[$d] rc=0";
  elif echo "$out" | grep -q "Traceback"; then bad "炸栈[$d]";
  else ok "拒[$d]"; fi }
allow(){ d="$1"; shift; out=$("$@" 2>&1 >/dev/null); rc=$?
  if [ $rc -ne 0 ] || echo "$out" | grep -q "Traceback"; then bad "应放行[$d] rc=$rc :: $(echo "$out" | head -1)";
  else ok "放行[$d]"; fi }
nid(){ n=$(($(cat $CC_TEAM_HOME/.ctr)+1)); echo $n > $CC_TEAM_HOME/.ctr; echo A$n; }

gcard(){ python3 -c "import json,sys;g=json.load(open(sys.argv[2],encoding='utf-8'))
print(json.dumps((g.get('cards') or {}).get(sys.argv[1],{}),ensure_ascii=False,indent=2))" "$1" "$CC_TEAM_HOME/state/graph.json"; }
# mk <state>：用合法路径把新卡造到目标状态（echo 卡号；owner=o_<id>）
mk(){
  c=$(nid)
  case $1 in
    DRAFT) $CARD create $c --title t --status DRAFT --verify true >/dev/null;;
    OPEN) $CARD create $c --title t --verify true >/dev/null;;
    HOLD) $CARD create $c --title t --verify true --status HOLD >/dev/null;;
    CLAIMED) $CARD create $c --title t --verify true >/dev/null; $CARD claim $c --owner o_$c >/dev/null;;
    DONE) $CARD create $c --title t --verify true >/dev/null; $CARD claim $c --owner o_$c >/dev/null
          echo x >> src/a.py; git add -A; git commit -qm "feat($c): w"
          $CARD done $c --owner o_$c --commit $(git rev-parse HEAD) --repo $ADR >/dev/null;;
    VERIFIED) c2=$(mk DONE); $CARD verdict $c2 PASS >/dev/null; c=$c2;;
    CANCELED) $CARD create $c --title t --verify true >/dev/null; $CARD cancel $c >/dev/null;;
  esac
  echo $c
}

echo "===== M1 状态机非法转移全矩阵（跳态/回退/重复勾 全拒；法律动作不在此列） ====="
for st in DRAFT OPEN HOLD CLAIMED DONE VERIFIED CANCELED; do
  for badstat in CLAIMED DONE VERIFIED REJECTED WHATEVER; do
    c=$(mk $st); rej "$st:set->$badstat" $CARD set $c --status $badstat
  done
done
for st in DRAFT OPEN HOLD CLAIMED DONE VERIFIED CANCELED; do
  for op in claim start done vp vr rel; do
    case $op:$st in
      claim:OPEN|start:OPEN|start:HOLD|start:DRAFT|done:CLAIMED|vp:DONE|vr:DONE|vr:VERIFIED|rel:CLAIMED) continue;;  # 合法组合跳过
    esac
    c=$(mk $st)
    case $op in
      claim) rej "$st:claim" $CARD claim $c --owner zz_$st;;
      start) rej "$st:start" $CARD start $c --owner zz_$st;;
      done) rej "$st:done" $CARD done $c --owner zz_$st --commit $(git rev-parse HEAD) --repo $ADR;;
      vp) rej "$st:verdictPASS" $CARD verdict $c PASS;;
      vr) rej "$st:verdictREJECT" $CARD verdict $c REJECT;;
      rel) rej "$st:release" $CARD release $c;;
    esac
  done
done
# 专属非法项
c=$(mk CLAIMED); rej "CLAIMED:set->OPEN(绕release留幽灵锁)" $CARD set $c --status OPEN
c=$(mk CLAIMED); rej "CLAIMED:set->HOLD" $CARD set $c --status HOLD
c=$(mk CLAIMED); rej "CLAIMED:done属主不符" $CARD done $c --owner notme --commit $(git rev-parse HEAD) --repo $ADR
c=$(mk CLAIMED); rej "CLAIMED:cancel无force" $CARD cancel $c
c=$(mk DONE); rej "DONE:set->OPEN(绕verdict留旧commit)" $CARD set $c --status OPEN
c=$(mk DONE); rej "DONE:cancel(无需作废)" $CARD cancel $c
c=$(mk DONE); rej "DONE:release" $CARD release $c
c=$(mk VERIFIED); rej "VERIFIED:重复PASS" $CARD verdict $c PASS
c=$(mk VERIFIED); rej "VERIFIED:set->OPEN" $CARD set $c --status OPEN
c=$(mk VERIFIED); rej "VERIFIED:cancel" $CARD cancel $c
c=$(mk CANCELED); rej "CANCELED:全部动作" $CARD claim $c --owner zz
# 合法路径抽查（防新闸过劲误杀）
c=$(mk OPEN); allow "OPEN:claim 合法" $CARD claim $c --owner o1k
c=$(mk DRAFT); allow "DRAFT:start 合法" $CARD start $c --owner o2k
c=$(mk HOLD); allow "HOLD:start(无hold有verify) 合法" $CARD start $c --owner o3k
c=$(mk DONE); allow "DONE:verdict REJECT 回退合法" $CARD verdict $c REJECT
c=$(mk CANCELED); allow "CANCELED:set->OPEN 复活(PM口子,带verify)" $CARD set $c --status OPEN

echo "===== M2 恶意卡面（cards_dag 全不炸：拒可以，Traceback 不行） ====="
rej "id含../" $CARD create "../evil" --title t --verify true
rej "id含slash" $CARD create "a/b" --title t --verify true
rej "id含点json" $CARD create "x.json" --title t --verify true
LONGID=$(python3 -c "print('L'+'q'*300)")
rej "id超300字符" $CARD create "$LONGID" --title t --verify true
c=$(mk OPEN); rej "deps自引用(set)" $CARD set $c --deps $c
rej "deps自引用(create)" $CARD create SELFX --title t --verify true --deps SELFX
c=$(mk OPEN); rej "deps含../" $CARD set $c --deps "../etc"
allow "files空串" $CARD create EMPTY1 --title t --verify true --files ""
BIGT=$(python3 -c "print('T'*9999)")
allow "title超长9999" $CARD create BIGT --title "$BIGT" --verify true
allow "create不认--out(argparse 干净拒 rc=2 非炸栈)" bash -c "python3 $N/cards_dag.py create OUTX2 --title t --verify true --out junk 2>&1 | grep -q usage"
allow "out-json通道正常落账(MECH-LIT1唯一通道)" $CARD create OUTX --title t --verify true && $CARD set OUTX --out-json '{"k":"v;=;"}'
rej "非法--out-json干净拒" $CARD set OUTX --out-json "junk"
rej "--out legacy已退役(argparse干净拒)" $CARD set OUTX --out "a;b=;=c"
before=$(wc -l < $CC_TEAM_HOME/state/events.log)
INJ=$(printf 'r1\n|EVIL|ZZZ|DONE->VERIFIED|PM|fake')
allow "cancel reason带换行(应清洗成单行)" $CARD cancel EMPTY1 --reason "$INJ"
after=$(wc -l < $CC_TEAM_HOME/state/events.log)
[ $((after-before)) -eq 1 ] && ok "换行注入未增殖事件行(+1)" || bad "注入增殖行数=$((after-before))"
grep -q "^|EVIL|" $CC_TEAM_HOME/state/events.log && bad "伪造行独占成行" || ok "伪造行不成行"
allow "note带换行" $CARD note --owner o9 --text "$(printf 'x\n- fake entry')"
lines=$(wc -l < $CC_TEAM_HOME/notes/lane-o9.md); [ "$lines" -eq 1 ] && ok "note 单行化" || bad "note 行数=$lines"

echo "===== M3 events 对账 reconcile_events（三判据）====="
allow "正常流水后对账干净" python3 $N/ops/reconcile_events.py --quiet
c=$(mk OPEN)
python3 - "$c" <<'PYEOF'
import json, os, sys
T = os.environ["CC_TEAM_HOME"]
gp = os.path.join(T, "state", "graph.json")
g = json.load(open(gp, encoding="utf-8"))
g["cards"][sys.argv[1]]["status"] = "VERIFIED"   # 裸改事实=graph 唯一端（MECH-PURE1 后无镜像可同改）
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
out=$(python3 $N/ops/reconcile_events.py 2>&1); rc=$?
[ $rc -eq 1 ] && echo "$out" | grep -q "ALARM" && ok "裸改卡面对账报 ALARM(rc=1)" || bad "对账未报警 rc=$rc"
# MECH-DONEANCHOR-TIP-1 交卷锚回归钉：DONE 事件行必须带「tip=<12位> <same|ahead=N>」事实段
# （自报≠树尖账上可对；红绿锁面全在 tests/done-anchor-tip-check.py，此处只钉流水格式不回退）。
if grep -qE '\->DONE\|[^|]*\|commit=[0-9a-f]+ tip=[0-9a-f]{12} (same|ahead=[0-9]+)' $CC_TEAM_HOME/state/events.log; then
  ok "交卷锚：DONE 流水行含实 tip 与分型(MECH-DONEANCHOR-TIP-1)"
else
  bad "交卷锚流水行缺失/格式回退(MECH-DONEANCHOR-TIP-1)"
fi
python3 - <<'PYEOF'   # 坏卡注入=事实图坏节点（旧 tasks 坏文件通道已随 MECH-PURE1 退役）
import json, os
gp = os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json")
g = json.load(open(gp, encoding="utf-8"))
g["cards"]["BROK"] = {"id": "BROK", "title": "坏节点"}   # 无 status = graph 访问器判坏点名
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
python3 $N/ops/reconcile_events.py 2>&1 | grep -q "坏节点" && ok "坏卡对账点名" || bad "坏卡对账漏报"
python3 - <<'PYEOF'   # 修复=删坏节点重封（以账为准，等价显式 dag 语义的直动版）
import json, os, sys
sys.path.insert(0, os.environ["CC_TEAM_SRC"])
from ccards.graph import seal
gp = os.path.join(os.environ["CC_TEAM_HOME"], "state", "graph.json")
g = json.load(open(gp, encoding="utf-8"))
del g["cards"]["BROK"]
g["seal"] = seal(g["cards"])
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
python3 $N/ops/reconcile_events.py 2>&1 | grep -q "坏节点" && bad "BROK 清除后仍点坏节点" || ok "坏节点已从事实图移除"
python3 - "$c" <<'PYEOF'
import json, os, sys
T = os.environ["CC_TEAM_HOME"]
gp = os.path.join(T, "state", "graph.json")
g = json.load(open(gp, encoding="utf-8"))
g["cards"][sys.argv[1]]["status"] = "OPEN"   # 恢复走文件级(直改会被闸拦，本段只验对账器)；graph 唯一端
json.dump(g, open(gp, "w", encoding="utf-8"))
PYEOF
$CARD dag --refresh >/dev/null 2>&1   # 强制重封（恢复 c 是裸改，digest 恰回原值→不带 --refresh 不触发重算→seal 停旧值）
printf 'garbage line without pipes\n' >> $CC_TEAM_HOME/state/events.log
out=$(python3 $N/ops/reconcile_events.py 2>&1); rc=$?
echo "$out" | grep -q "畸形行" && [ $rc -eq 0 ] && ok "畸形行 WARN 不崩不ALARM" || bad "畸形行处理 rc=$rc :: $(echo "$out" | grep -E "ALARM|损坏|不可信" | head -2)"
sed '/garbage line/d' $CC_TEAM_HOME/state/events.log > $CC_TEAM_HOME/state/events.log.sedtmp && mv $CC_TEAM_HOME/state/events.log.sedtmp $CC_TEAM_HOME/state/events.log

echo "===== M4 check-exit 两态 ====="
# 态A：名下 DONE 未验收、notes 文件不存在 → block 点名"不存在"
c=$(mk DONE)
out=$(python3 $N/ops/check_exit.py --owner o_$c --repo $ADR --notes /tmp/cc-adr-nonexist-dir/lane.md 2>&1); rc=$?
[ $rc -eq 2 ] && echo "$out" | grep -q "不存在" && ok "态A notes不存在→block点名" || bad "态A rc=$rc :: $out"
# 态B：commit 在主仓(odb 对象在)但不在任何分支(分支删掉) → %ct 仍取到 → 笔记旧=照拦(不误放不误崩)
c=$(mk OPEN); $CARD claim $c --owner o_$c >/dev/null
git checkout -q -b featoff
echo z >> src/a.py && git add -A && git commit -qm "feat($c): off"; CB=$(git rev-parse HEAD)
$CARD done $c --owner o_$c --commit $CB --repo $ADR >/dev/null
git checkout -q master && git branch -q -D featoff
touch $CC_TEAM_HOME/notes/lane-o_$c.md && ago $CC_TEAM_HOME/notes/lane-o_$c.md 7200
out=$(python3 $N/ops/check_exit.py --owner o_$c --repo $ADR --notes $CC_TEAM_HOME/notes/lane-o_$c.md 2>&1); rc=$?
echo "$out" | grep -q Traceback && bad "态B炸栈" || ok "态B不崩"
[ $rc -eq 2 ] && echo "$out" | grep -q "晚于笔记" && ok "态B 分支外commit仍解析并拦截(卡DONE未验收)" || bad "态B rc=$rc :: $out"
$CARD note --owner o_$c --text "沉淀B" >/dev/null
allow "态B 补note后放行" python3 $N/ops/check_exit.py --owner o_$c --repo $ADR --notes $CC_TEAM_HOME/notes/lane-o_$c.md
# 态B2：CLAIMED 卡、commit 在分支上 → Pass1 点名催 done
c=$(mk OPEN); $CARD claim $c --owner o_$c >/dev/null
echo w >> src/a.py && git add -A && git commit -qm "feat($c): p1"
out=$(python3 $N/ops/check_exit.py --owner o_$c --repo $ADR 2>&1); rc=$?
[ $rc -eq 2 ] && echo "$out" | grep -q "已有对应 commit" && ok "CLAIMED+分支上commit→点名催done" || bad "态B2 rc=$rc"
$CARD release $c >/dev/null

echo "===== M5 崩溃注入（done 验货中途 kill，原子写链） ====="
# 注入1：save 之后崩——卡完整=DONE(原子)，claims 残渣留、事件缺 → 对账 ALARM，verdict PASS 兜底清
c=$(mk OPEN); $CARD claim $c --owner o_$c >/dev/null
echo q >> src/a.py && git add -A && git commit -qm "feat($c): crash"; C1=$(git rev-parse HEAD)
python3 - "$c" "$C1" <<'PYEOF'
import argparse, os, sys
cid, commit = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.environ["CC_TEAM_SRC"])
from ccards import commands as m  # MECH-STD1：库命名空间可替换点即 save/shutil，注入语义不变
orig_save = m.save
def save_then_die(c):
    orig_save(c)      # 原子写完成后立刻"崩"
    os._exit(7)
m.save = save_then_die
a = argparse.Namespace(id=cid, owner="o_"+cid, commit=commit, repo=os.environ["CC_ADR_REPO"],
                       evidence="inj", out="", force=False)
m.cmd_done(a)
PYEOF
st=$(gcard "$c" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status'))")
[ "$st" = "DONE" ] && ok "注入1: 卡原子落盘=DONE(无半写)" || bad "注入1: 卡态=$st"
[ -d $CC_TEAM_HOME/claims/$c ] && ok "注入1: claims 残渣如约存在(锁未清)" || bad "注入1: claims 竟已清(顺序异动,核对)"
out=$(python3 $N/ops/reconcile_events.py 2>&1); rc=$?
if [ $rc -eq 1 ] && echo "$out" | grep -q "ALARM"; then ok "注入1: DONE无事件→对账 ALARM"; else bad "注入1: 对账未报警"; fi
allow "注入1恢复: verdict PASS 清残渣" $CARD verdict $c PASS
[ ! -d $CC_TEAM_HOME/claims/$c ] && ok "注入1恢复: claims 残渣已清" || bad "注入1恢复: 残渣未清"
allow "注入1恢复: 对账转绿" python3 $N/ops/reconcile_events.py --quiet
# 注入2：save 之前崩——卡仍 CLAIMED + 锁在 = 自洽，对账绿，可直接重试 done
c=$(mk OPEN); $CARD claim $c --owner o_$c >/dev/null
echo e2 >> src/a.py && git add -A && git commit -qm "feat($c): crash2"
python3 - "$c" "$(git rev-parse HEAD)" <<'PYEOF'
import argparse, os, sys
cid, commit = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.environ["CC_TEAM_SRC"])
from ccards import commands as m
m.save = lambda c: os._exit(8)   # 写盘前崩
a = argparse.Namespace(id=cid, owner="o_"+cid, commit=commit, repo=os.environ["CC_ADR_REPO"],
                       evidence="inj2", out="", force=False)
m.cmd_done(a)
PYEOF
st=$(gcard "$c" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status'))")
[ "$st" = "CLAIMED" ] && ok "注入2: 卡仍 CLAIMED(未污染)" || bad "注入2: 卡态=$st"
allow "注入2: 写盘前崩=自洽,对账绿" python3 $N/ops/reconcile_events.py --quiet
allow "注入2恢复: 重试 done 成功" $CARD done $c --owner o_$c --commit $(git rev-parse HEAD) --repo $ADR
# 注入3：残留 .tmp 不污染读取，且后续 save 正常
echo '{broken' > $CC_TEAM_HOME/state/graph.json.tmp   # 原子写残点=graph.json.tmp（镜像 tmp 通道已随 PURE1 退役）
st=$(gcard "$c" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status'))")
[ "$st" = "DONE" ] && ok "注入3: .tmp 残渣不影响卡读取" || bad "注入3: 卡态=$st"
allow "注入3: 后续 save 正常" $CARD set $c --title renamed
# 注入4：rmtree 之后、event 之前崩——DONE+无锁+无事件 → 对账必须 ALARM（可检出），verdict 恢复
c=$(mk OPEN); $CARD claim $c --owner o_$c >/dev/null
echo e4 >> src/a.py && git add -A && git commit -qm "feat($c): crash4"; C4=$(git rev-parse HEAD)
python3 - "$c" "$C4" <<'PYEOF'
import argparse, os, shutil, sys
cid, commit = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.environ["CC_TEAM_SRC"])
from ccards import commands as m
orig_rm = shutil.rmtree
def rm_then_die(path, *a, **k):
    orig_rm(path, *a, **k)
    os._exit(9)
shutil.rmtree = rm_then_die
a = argparse.Namespace(id=cid, owner="o_"+cid, commit=commit, repo=os.environ["CC_ADR_REPO"],
                       evidence="inj4", out="", force=False)
m.cmd_done(a)
PYEOF
out=$(python3 $N/ops/reconcile_events.py 2>&1); rc=$?
[ $rc -eq 1 ] && echo "$out" | grep -q "$c" && ok "注入4: 清锁后缺事件→对账可检出" || bad "注入4: 对账漏报 rc=$rc"
allow "注入4恢复: verdict PASS" $CARD verdict $c PASS
allow "注入4恢复: 对账转绿" python3 $N/ops/reconcile_events.py --quiet

echo "=== adversary fails=$fails ==="
[ $fails -eq 0 ] && echo ADR_ALL_PASS
