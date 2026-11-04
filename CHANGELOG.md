# Changelog

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.1.0] - 2026-11-03

首个公开版本。引擎从生产多智能体车队中提炼为独立发行包（纯标准库、零外部依赖）。

### Added
- 文件账本引擎：卡状态机（DRAFT/OPEN/CLAIMED/HOLD/DONE/VERIFIED/CANCELED）+ 追加式事件日志 + 写锁；
  状态=属主/迁移矩阵为机器契约（`tests/state_std_test.py` 与 `ccards/statemachine.py` 同源对账）。
- 泳道与任期：卡绑定 git worktree 泳道；epoch/ring/心跳三元判活，断流自动回收（僵尸 worker 治理）。
- 验收闭环：卡面 `verify` 命令串在 done 后统一实跑，绿才 VERIFIED；REJECT 打回带审计痕。
- 常驻环：`ops/ctrld.py` 单进程时间轮承载派发/回收/看门/合流等全部周期（零操作系统定时器）；
  操作系统侧仅保留开机启动器（Windows AtLogon / systemd user unit / launchd 模板随包分发）。
- 一键面 `cc`：`init`（骨架+配置模板+定时模板+自检 ALL_PASS+**Claude Code 环境检测**）、
  `selftest`（五套红绿锁）、`export`（按白名单导发行包）；
  缺 agent 时只打印官方安装命令，`--install-agent` 才明示后代执行（绝不静默下载执行）。
- 配置单入口：环境变量 > config.toml > 内置默认；默认值全部由仓位置/宿主派生，零机器名字面量。
- 发布锁 `tests/oss-release-check.py`：敏感五形实扫+植入红自证 / 白名单边界+init@clone /
  默认等价对主干基线（独立仓自动 SKIP）；CI 双平台跑五套机制回归。

### Notes
- `board/`（运营看板层）属宿主定制，不在发行包内；引用它的五把锁在独立包中自动 SKIP（降档不放水）。
- 引擎对终端 agent 保持无关（`engine.runner_cmd` 外接任意 CLI agent）：Claude Code 只是缺省指引对象。
