# 贡献指南

一句话规矩：**这个仓库里每个机制都必须自带一把「会自己红的锁」**——
撤掉修复、注入故障，测试必须变红；红了没人管，PR 不合。

## 开发环境

- Python ≥ 3.9，纯标准库；不需要 pip install 任何东西就能跑测试。
- 终端里要有 `bash`（Git Bash / WSL / macOS 均可）与 `git`；看板/渲染层锁需要 `node`。
- Windows 注意事项：判据宿主统一走 `ccards.commands._resolve_verify_bash()` 解析出的 bash
  （裸 `bash` 可能命中 `System32\bash.exe`=WSL 启动器，产生剥路径假红——这是历史事故，别绕）。

## 跑测试

```bash
bash tests/all.sh                     # 五套并行：账本/对抗/回收守卫/判活/CC环境检测
python -I tests/oss-release-check.py  # 发布锁：敏感/边界/默认等价
```

全绿汇总行 `ALL_SUITES_PASS`；单跑某一子集见 `tests/select.sh`。
新增机制：写一个自包含沙箱测试（只写临时目录，绝不碰真实账），然后把它挂进 `tests/all.sh`
——token 只在行尾追加，历史判据 grep 面不许动。

## 改动边界

- 状态机的合法迁移、必填字段、属主，唯一数据源=`ccards/statemachine.py`；
  README 的四张标记表是机器契约，改代码必须同批改表（`state_std_test.py` 会红）。
- 发布白名单唯一权威=`ccards/oss_cli.py` 的 `RELEASE_DIRS/RELEASE_FILES`；
  改边界=同批改这里，`oss-release-check.py` B 判据逐文件对账。
- 配置键的新增走 `ccards/config.py` 单入口（env > config.toml > 内置默认），
  未识别键只告警不报错（旧引擎吃新配置不许炸）。
- 一切默认值禁含机器名/用户名/盘符字面量——由仓位置与宿主派生（`oss-release-check.py` A 判据实扫）。

## 提交与版本

- PR 标题用 `feat|fix|docs|test(scope): 摘要`；description 里贴 `bash tests/all.sh` 的汇总行输出。
- 版本号走语义化；CHANGELOG.md 按 Keep a Changelog 补条目。

## 运行纪律（与开发无关但值得知道）

引擎的账本哲学：文件即真相（graph.json 当前态 + events.log 追加史），断电进程被杀不丢账；
「第二套真相」（派生镜像/缓存态）在本仓是**结构性禁止**的（selftest #73 常驻锁把关）。
